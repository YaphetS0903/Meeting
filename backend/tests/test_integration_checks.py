# ruff: noqa: F811 -- imported pytest fixture
import json
import smtplib
import ssl
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AuditLog, Department, OutboxEvent, User
from app.services import integration_checks, integrations
from app.services.integration_checks import check_integration
from tests.test_admin_people import client, headers  # noqa: F401


def configure_smtp(monkeypatch):
    settings = get_settings()
    for key, value in {
        "smtp_host": "smtp.example.invalid",
        "smtp_from": "meetings@example.invalid",
        "smtp_username": "private-user",
        "smtp_password": "private-password",
        "smtp_starttls": True,
        "smtp_ssl": False,
    }.items():
        monkeypatch.setattr(settings, key, value)


def configure_wecom(monkeypatch):
    settings = get_settings()
    for key, value in {
        "wecom_corp_id": "corp",
        "wecom_agent_id": "1",
        "wecom_secret": "private-secret",
    }.items():
        monkeypatch.setattr(settings, key, value)


def test_missing_config_performs_no_network_and_records_admin_only_results(client, db, team, monkeypatch):
    settings = get_settings()
    for key in ["smtp_host", "wecom_corp_id"]:
        monkeypatch.setattr(settings, key, "")
    smtp = Mock(side_effect=AssertionError("must not connect"))
    wecom = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(integrations.smtplib, "SMTP", smtp)
    monkeypatch.setattr(integration_checks, "WeCom", wecom)
    for target in ["smtp", "wecom"]:
        assert (
            client.post(
                "/api/admin/integration-checks", headers=headers(team["a"]), json={"target": target}
            ).status_code
            == 403
        )
        response = client.post(
            "/api/admin/integration-checks", headers=headers(team["host"]), json={"target": target}
        )
        assert response.status_code == 200 and response.json()["state"] == "not_configured"
    assert client.get("/api/admin/integration-checks", headers=headers(team["a"])).status_code == 403
    assert set(client.get("/api/admin/integration-checks", headers=headers(team["host"])).json()) == {
        "smtp",
        "wecom",
    }
    assert (
        db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "integration.check"))
        == 2
    )
    smtp.assert_not_called()
    wecom.assert_not_called()
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.parametrize("ssl_mode", [False, True])
def test_smtp_check_validates_tls_authentication_and_never_sends(monkeypatch, ssl_mode):
    configure_smtp(monkeypatch)
    monkeypatch.setattr(get_settings(), "smtp_ssl", ssl_mode)
    smtp = Mock()
    smtp.noop.return_value = (250, b"OK")
    factory = Mock(return_value=smtp)
    monkeypatch.setattr(integrations.smtplib, "SMTP_SSL" if ssl_mode else "SMTP", factory)
    result = check_integration("smtp")
    assert result["state"] == "ready"
    context = factory.call_args.kwargs["context"] if ssl_mode else smtp.starttls.call_args.kwargs["context"]
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    smtp.login.assert_called_once_with("private-user", "private-password")
    smtp.noop.assert_called_once()
    smtp.quit.assert_called_once()
    smtp.send_message.assert_not_called()
    smtp.sendmail.assert_not_called()
    assert "private-password" not in json.dumps(result)


@pytest.mark.parametrize(
    "error,part",
    [
        (ssl.SSLCertVerificationError("private-host: private-password"), "证书验证失败"),
        (smtplib.SMTPAuthenticationError(535, b"private-user private-password"), "身份验证失败"),
        (TimeoutError("private-user private-password"), "连接失败或超时"),
    ],
)
def test_smtp_failures_are_sanitized(monkeypatch, error, part):
    configure_smtp(monkeypatch)
    monkeypatch.setattr(integrations.smtplib, "SMTP", Mock(side_effect=error))
    result = check_integration("smtp")
    assert result["state"] == "failed" and part in result["checks"][0]["message"]
    assert "private-" not in json.dumps(result)


def test_wecom_check_does_not_sync_members_or_send_messages(db, team, monkeypatch):
    configure_wecom(monkeypatch)
    provider = Mock()
    provider.get.side_effect = [{"agentid": 1}, {"department": [{"id": 1, "name": "企业部门"}]}]
    monkeypatch.setattr(integration_checks, "WeCom", lambda: provider)
    count = db.scalar(select(func.count()).select_from(User))
    result = check_integration("wecom")
    assert result["state"] == "ready" and len(result["checks"]) == 2
    provider.send.assert_not_called()
    assert db.scalar(select(func.count()).select_from(User)) == count


def test_wecom_partial_failure_preserves_step_result_without_raw_error(monkeypatch):
    configure_wecom(monkeypatch)
    provider = Mock()
    provider.get.side_effect = [
        {"agentid": 1},
        integrations.ProviderRejected("private-secret leaked provider error"),
    ]
    monkeypatch.setattr(integration_checks, "WeCom", lambda: provider)
    result = check_integration("wecom")
    assert result["state"] == "failed"
    assert [c["state"] for c in result["checks"]] == ["ready", "failed"]
    assert "private-secret" not in json.dumps(result)


def test_wecom_zero_visible_departments_is_not_reported_ready(monkeypatch):
    configure_wecom(monkeypatch)
    provider = Mock()
    provider.get.side_effect = [{"agentid": 1}, {"department": []}]
    monkeypatch.setattr(integration_checks, "WeCom", lambda: provider)
    result = check_integration("wecom")
    assert result["state"] == "failed"
    assert [item["state"] for item in result["checks"]] == ["ready", "failed"]
    assert "可见范围" in result["checks"][1]["message"]


def test_wecom_direct_member_scope_is_limited_but_can_sync_and_expand(db, monkeypatch):
    configure_wecom(monkeypatch)
    direct = {
        "userid": "direct-member",
        "name": "当前授权成员",
        "status": 1,
        "department": [1],
        "main_department": 1,
    }
    provider = Mock()
    provider.get.side_effect = [
        {"agentid": 1, "allow_userinfos": {"user": [{"userid": "direct-member"}]}},
        {"department": []},
    ]
    monkeypatch.setattr(integration_checks, "WeCom", lambda: provider)
    result = check_integration("wecom")
    assert result["state"] == "limited"
    assert result["checks"][1]["state"] == "limited"

    provider = Mock()
    provider.get.side_effect = [
        {"allow_userinfos": {"user": [{"userid": "direct-member"}]}},
        {"department": []},
        direct,
    ]
    monkeypatch.setattr(integrations, "WeCom", lambda: provider)
    synced = integrations.sync_directory(db)
    assert synced == {
        "departments": 0,
        "users": 1,
        "direct_users": 1,
        "direct_assignment_leaders": 0,
        "directory_enriched_users": 0,
        "directory_mobile_users": 0,
    }
    member = db.scalar(select(User).where(User.wecom_id == "direct-member"))
    assert member and member.department_id is None
    member.email = "confirmed@example.test"
    db.flush()

    provider = Mock()
    provider.get.side_effect = [
        {"allow_userinfos": {"user": [{"userid": "direct-member"}]}},
        {"department": [{"id": 1, "parentid": 0, "name": "已开放部门"}]},
        {"userlist": [direct]},
    ]
    monkeypatch.setattr(integrations, "WeCom", lambda: provider)
    synced = integrations.sync_directory(db)
    assert synced == {
        "departments": 1,
        "users": 1,
        "direct_users": 1,
        "direct_assignment_leaders": 0,
        "directory_enriched_users": 0,
        "directory_mobile_users": 0,
    }
    assert db.scalar(select(func.count()).select_from(User).where(User.wecom_id == "direct-member")) == 1
    assert member.department_id == db.scalar(select(Department.id).where(Department.wecom_id == "1"))
    assert member.email == "confirmed@example.test"


def test_smtp_cleanup_failure_does_not_turn_success_into_retry(monkeypatch):
    configure_smtp(monkeypatch)
    smtp = Mock()
    smtp.quit.side_effect = OSError("connection closed")
    smtp.close.side_effect = OSError("already closed")
    monkeypatch.setattr(integrations.smtplib, "SMTP", Mock(return_value=smtp))
    with integrations.smtp_session() as connection:
        assert connection is smtp
    smtp.close.assert_called_once()
