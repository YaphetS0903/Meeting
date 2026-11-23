import json
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import OutboxEvent
from app.services.monitoring import (
    collect,
    heartbeat,
    load_state,
    public_monitor_state,
    send_alert,
    update_alert,
)

MOMENT = datetime(2026, 9, 21, 3, tzinfo=timezone.utc)


@pytest.fixture
def config(tmp_path):
    config = Settings(
        _env_file=None,
        storage_dir=tmp_path / "files",
        backup_dir=tmp_path / "backups",
        monitor_state_dir=tmp_path / "monitor",
        alert_webhook_url="https://monitor.example.test/events",
    )
    config.storage_dir.mkdir()
    backup = config.backup_dir / "sample"
    backup.mkdir(parents=True)
    for name in ("database.dump", "storage.tar.gz"):
        (backup / name).write_bytes(b"test-backup")
    manifest = backup / "manifest.json"
    manifest.write_text(json.dumps({"database.dump": "hash", "storage.tar.gz": "hash"}))
    os.utime(manifest, (MOMENT.timestamp(), MOMENT.timestamp()))
    return config


def snapshot(issues=True):
    return {
        "checked_at": MOMENT.isoformat(),
        "healthy": not issues,
        "issues": [{"code": "delivery_failed", "label": "通知失败", "healthy": False, "detail": "2 条"}]
        if issues
        else [],
        "checks": [],
        "queue_counts": {"failed": 2} if issues else {},
    }


def test_checks_detect_queue_stalls_failed_delivery_and_missing_heartbeat(db, config):
    db.add_all(
        [
            OutboxEvent(
                event_key="delayed",
                channel="export",
                payload={},
                state="pending",
                available_at=MOMENT - timedelta(minutes=20),
            ),
            OutboxEvent(event_key="blocked", channel="wecom", payload={}, state="blocked"),
            OutboxEvent(event_key="uncertain", channel="email", payload={}, state="uncertain"),
            OutboxEvent(
                event_key="stuck",
                channel="export",
                payload={},
                state="sending",
                claimed_at=MOMENT - timedelta(minutes=11),
            ),
        ]
    )
    db.commit()
    factory = sessionmaker(db.bind)
    r = collect(factory, config, MOMENT, ping=lambda: None)
    assert {i["code"] for i in r["issues"]} == {
        "scheduler_worker",
        "delivery_blocked",
        "delivery_uncertain",
        "queue_overdue",
        "queue_stuck",
    }
    with factory.begin() as session:
        heartbeat(session, MOMENT)
    r = collect(factory, config, MOMENT, ping=lambda: None)
    assert "scheduler_worker" not in {i["code"] for i in r["issues"]}
    assert r["latest_backup_at"] == MOMENT.isoformat()


def test_database_and_redis_outage_still_produce_sanitized_alert(config):
    def fail(*_):
        raise RuntimeError("postgresql://private-user:secret@db")

    r = collect(fail, config, MOMENT, ping=fail)
    assert {i["code"] for i in r["issues"]} == {"database", "redis"}
    sent = []
    update_alert(r, config, MOMENT, sender=lambda c, p: sent.append(p))
    assert len(sent) == 1 and "secret" not in json.dumps(sent)


def test_failed_import_keeps_history_without_repeating_system_alerts(db, config):
    event = OutboxEvent(
        event_key="meeting-sheet:recognition-failed", channel="import", payload={},
        state="failed", attempts=1, last_error="本页未完整识别，请上传更清晰的文件后重试",
    )
    db.add(event)
    heartbeat(db, MOMENT)
    db.commit()
    factory = sessionmaker(db.bind)
    for elapsed in (0, 61, 121):
        current = MOMENT + timedelta(minutes=elapsed)
        with factory.begin() as session:
            heartbeat(session, current)
        result = collect(factory, config, current, ping=lambda: None)
        assert result["healthy"]
        assert result["queue_counts"]["failed"] == 1
        update_alert(result, config, current, sender=lambda *_: pytest.fail("must not send import alert"))
    db.refresh(event)
    assert event.state == "failed" and event.attempts == 1
    assert event.last_error == "本页未完整识别，请上传更清晰的文件后重试"


@pytest.mark.parametrize("channel", ["email", "wecom", "export", "unknown"])
def test_failed_import_does_not_hide_other_failed_work(db, config, channel):
    db.add_all([
        OutboxEvent(event_key="import-failure", channel="import", payload={}, state="failed"),
        OutboxEvent(event_key="operational-failure", channel=channel, payload={}, state="failed"),
    ])
    heartbeat(db, MOMENT)
    db.commit()
    result = collect(sessionmaker(db.bind), config, MOMENT, ping=lambda: None)
    assert result["issues"] == [{
        "code": "delivery_failed", "label": "明确失败", "healthy": False, "detail": "1 条待处理",
    }]
    sent = []
    update_alert(result, config, MOMENT, sender=lambda c, p: sent.append(p))
    assert len(sent) == 1 and sent[0]["type"] == "incident"


@pytest.mark.parametrize("state,code", [("pending", "queue_overdue"), ("sending", "queue_stuck")])
def test_import_queue_stalls_are_still_operational_failures(db, config, state, code):
    db.add(OutboxEvent(
        event_key="stalled-import", channel="import", payload={}, state=state,
        available_at=MOMENT - timedelta(minutes=20), claimed_at=MOMENT - timedelta(minutes=20),
    ))
    heartbeat(db, MOMENT)
    db.commit()
    result = collect(sessionmaker(db.bind), config, MOMENT, ping=lambda: None)
    assert {issue["code"] for issue in result["issues"]} == {code}


def test_stale_backup_and_storage_failure_detected_independently(db, config):
    with sessionmaker(db.bind).begin() as session:
        heartbeat(session, MOMENT)
    config.storage_dir.rmdir()
    r = collect(sessionmaker(db.bind), config, MOMENT + timedelta(days=3), ping=lambda: None)
    codes = {i["code"] for i in r["issues"]}
    assert {"backup", "storage_write", "scheduler_worker"} <= codes
    # State writes use a separate directory, so attachment permissions do not prevent alert delivery.
    sent = []
    update_alert(r, config, MOMENT, sender=lambda c, p: sent.append(p))
    assert len(sent) == 1


def test_alert_changes_reminders_and_recovery_persist_across_restarts(config):
    sent = []

    def sender(c, p):
        sent.append(p)

    update_alert(snapshot(), config, MOMENT, sender)
    update_alert(snapshot(), config, MOMENT + timedelta(minutes=1), sender)
    assert len(sent) == 1
    update_alert(snapshot(), config, MOMENT + timedelta(minutes=61), sender)
    assert len(sent) == 2 and sent[0]["event_id"] != sent[1]["event_id"]
    update_alert(snapshot(False), config, MOMENT + timedelta(minutes=62), sender)
    assert sent[-1]["type"] == "recovery"
    update_alert(snapshot(False), config, MOMENT + timedelta(minutes=63), sender)
    assert len(sent) == 3
    assert load_state(config)["acknowledged_fingerprint"] == ""


def test_failed_alert_retries_same_id_without_exposing_credentials(config):
    sent = []

    def fail(c, p):
        sent.append(p)
        raise httpx.ReadTimeout("https://secret@example.test/token?key=secret")

    update_alert(snapshot(), config, MOMENT, fail)
    assert public_monitor_state(config, MOMENT)["delivery"] == "failed"
    update_alert(snapshot(), config, MOMENT + timedelta(seconds=30), fail)
    assert len(sent) == 1
    update_alert(snapshot(), config, MOMENT + timedelta(seconds=60), fail)
    assert len(sent) == 2 and sent[0]["event_id"] == sent[1]["event_id"]
    assert "secret" not in json.dumps(load_state(config))
    update_alert(snapshot(False), config, MOMENT + timedelta(seconds=120), lambda c, p: sent.append(p))
    assert sent[-1]["type"] == "recovery"


def test_unconfigured_alert_and_expired_monitor_are_not_shown_as_healthy(config):
    config.alert_webhook_url = ""
    update_alert(snapshot(), config, MOMENT, sender=lambda *_: pytest.fail("must not send"))
    status = public_monitor_state(config, MOMENT)
    assert status["running"] and status["delivery"] == "not_configured"
    assert not status["webhook_configured"]
    assert not status["alert_configured"]
    assert not public_monitor_state(config, MOMENT + timedelta(minutes=4))["running"]


def test_email_alert_uses_stable_event_id_and_configured_recipient(config, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, host, port, **kwargs):
            assert (host, port, kwargs["timeout"]) == ("smtp.example.test", 25, 10)

        def ehlo(self):
            pass

        def login(self, username, password):
            assert (username, password) == ("sender", "secret")

        def send_message(self, message, to_addrs):
            sent.append((message, to_addrs))
            return {}

        def quit(self):
            pass

    monkeypatch.setattr("app.services.monitoring.smtplib.SMTP", SMTP)
    config.alert_webhook_url = ""
    config.alert_email_to = "operator@example.test"
    config.smtp_host = "smtp.example.test"
    config.smtp_port = 25
    config.smtp_from = "sender@example.test"
    config.smtp_username = "sender"
    config.smtp_password = "secret"
    config.smtp_starttls = False
    payload = {
        "event_id": "stable-event",
        "type": "test",
        "checked_at": MOMENT.isoformat(),
        "issues": [{"label": "数据库", "detail": "连接失败"}],
        "management_url": "https://meeting.example.test/settings",
    }

    send_alert(config, payload)

    message, recipients = sent[0]
    assert recipients == ["operator@example.test"]
    assert message["To"] == "operator@example.test"
    assert "告警通道测试" in message["Subject"]
    assert "stable-event" in message["Message-ID"]
    assert "数据库" in message.get_content()
    status = public_monitor_state(config, MOMENT)
    assert status["alert_configured"] and status["alert_channel"] == "email"


def test_crash_after_delivery_keeps_attempt_and_allows_recovery(config):
    delivered = []

    def crash(c, payload):
        delivered.append(payload)
        raise SystemExit("simulate process exit after receiver accepted")

    with pytest.raises(SystemExit):
        update_alert(snapshot(), config, MOMENT, crash)
    pending = load_state(config)["pending"]
    assert pending["attempted_at"] == MOMENT.isoformat()
    assert pending["event_id"] == delivered[0]["event_id"]
    update_alert(snapshot(False), config, MOMENT + timedelta(minutes=1), lambda c, p: delivered.append(p))
    assert delivered[-1]["type"] == "recovery"


def test_webhook_receives_event_with_auth_and_stable_id(config):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config.alert_webhook_url = f"http://127.0.0.1:{server.server_port}/events"
        config.alert_webhook_token = "test-only-token"
        update_alert(snapshot(), config, MOMENT)
        assert len(received) == 1
        headers, event = received[0]
        assert headers["Authorization"] == "Bearer test-only-token"
        assert headers["Idempotency-Key"] == event["event_id"]
        assert event["type"] == "incident" and event["queue_counts"]["failed"] == 2
        assert load_state(config)["delivery"] == "sent"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize(
    "url", ["http://monitor.example.test/events", "https://user:secret@example.test/", "file:///tmp/out"]
)
def test_unsafe_webhook_configuration_rejected(config, url):
    config.alert_webhook_url = url
    with pytest.raises(ValueError):
        send_alert(config, {"event_id": "test"})


def test_operational_details_are_admin_only(db, team, config, monkeypatch):
    update_alert(snapshot(), config, MOMENT, lambda *_: None)
    monkeypatch.setattr(get_settings(), "monitor_state_dir", config.monitor_state_dir)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            for name, code in [("a", 403), ("host", 200)]:
                r = client.get(
                    "/api/admin/operations", headers={"Authorization": "Bearer " + issue_token(team[name])}
                )
                assert r.status_code == code
            assert "snapshot" in r.json()
            assert "alert_webhook_token" not in r.text
    finally:
        app.dependency_overrides.clear()
