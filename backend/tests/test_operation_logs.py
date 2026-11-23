from contextlib import nullcontext
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import db as core_db
from app.core.security import issue_token, passwords
from app.main import app
from app.models import AuditLog, Meeting, NotificationLog, OutboxEvent, User
from app.schemas import PublishRequest
from app.services.business import audit, create_meeting, publish
from app.services.operation_logs import log_delivery, passive_read, present


@pytest.mark.parametrize("method,path,background,expected", [
    ("GET", "/api/reports/access", False, True),
    ("GET", "/api/reports/access", True, True),
    ("GET", "/api/meetings", True, True),
    ("GET", "/api/meetings", False, False),
    ("GET", "/api/tasks/123/transfer-options", False, True),
    ("GET", "/api/tasks/123/recurrence", False, True),
    ("GET", "/api/tasks/123", False, False),
    ("GET", "/api/reports/export", True, False),
    ("GET", "/api/meetings/123/export/pdf", True, False),
    ("GET", "/api/meetings/123/preview", True, False),
    ("GET", "/api/files/123", True, False),
    ("POST", "/api/reports/access", True, False),
    ("PUT", "/api/meetings/123", True, False),
])
def test_passive_read_only_classifies_auxiliary_reads(method, path, background, expected):
    assert passive_read({"method": method, "path": path, "background": background}) == expected


def headers(user):
    return {"Authorization": "Bearer " + issue_token(user)}


@pytest.fixture
def logs_client(db, team, monkeypatch):
    root = User(name="系统管理员", username="admin", is_admin=True, active=True,
                department_id=team["dep"].id, password_hash=passwords.hash("operation-test-password"))
    db.add(root)
    db.commit()
    monkeypatch.setattr(core_db, "SessionLocal", lambda: nullcontext(db))
    with TestClient(app) as client:
        yield client, root


def test_only_exact_active_admin_can_read_new_and_legacy_endpoints(logs_client, db, team):
    client, root = logs_client
    audit(db, root, "meeting.create", root, after={"title": "保密会议"})
    db.commit()
    entry = db.scalar(select(AuditLog))
    for actor in [team["host"], team["a"]]:
        for path in ["/api/admin/operation-logs", f"/api/admin/operation-logs/{entry.id}", "/api/admin/audit"]:
            assert client.get(path, headers=headers(actor)).status_code == 403
    assert client.get("/api/admin/operation-logs").status_code == 401
    assert client.get("/api/me", headers=headers(root)).json()["can_view_operation_logs"]
    assert not client.get("/api/me", headers=headers(team["host"])).json()["can_view_operation_logs"]
    root.active = False
    db.commit()
    assert client.get("/api/admin/operation-logs", headers=headers(root)).status_code == 401


def test_log_filters_pagination_dates_and_secret_redaction(logs_client, db, team):
    client, root = logs_client
    entries = [AuditLog(actor_id=team["host"].id, action="meeting.create", entity_type="meetings", entity_id="old-meeting",
                        created_at=datetime(2026, 11, 4, 16, 0, i, tzinfo=timezone.utc),
                        after={"title": f"采购会议{i}", "password": "never-display", "url": "x?access_token=abc"},
                        reason="外部说明 password=not-for-display",
                        context={"actor_name": "原姓名", "target_name": f"采购会议{i}", "category": "meeting", "result": "success"})
               for i in range(3)]
    db.add_all(entries)
    db.commit()
    params = dict(start="2026-11-05", end="2026-11-05", keyword="采购", category="meeting", page_size=2)
    result = client.get("/api/admin/operation-logs", headers=headers(root), params=params)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["total"] == 3
    assert [r["target"] for r in result.json()["items"]] == ["采购会议2", "采购会议1"]
    assert client.get("/api/admin/operation-logs", headers=headers(root), params={**params, "page": 2}).json()["items"][0]["target"] == "采购会议0"
    text = client.get(f"/api/admin/operation-logs/{entries[0].id}", headers=headers(root)).text
    assert "never-display" not in text and "access_token=abc" not in text and "not-for-display" not in text
    assert "已隐藏" in text and "原姓名" in text
    assert client.get("/api/admin/operation-logs", headers=headers(root), params={"start": "2026-11-06", "end": "2026-11-05"}).status_code == 422
    assert client.get("/api/admin/operation-logs", headers=headers(root), params={"page_size": 100000}).status_code == 422
    assert client.get("/api/admin/operation-logs", headers=headers(root), params={"keyword": "%"}).json()["total"] == 0


def test_reads_exports_rejections_and_mutations_are_recorded_once(logs_client, db, team):
    client, root = logs_client
    response = client.post("/api/series", headers=headers(root), json={"name": "采购例会"})
    assert response.status_code == 200
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "meeting_series.create"))
    assert entry.context["actor_username"] == "admin"
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.entity_id == entry.entity_id)))) == 1
    response = client.get("/api/meetings?keyword=采购", headers=headers(team["a"]))
    assert response.status_code == 200
    read = list(db.scalars(select(AuditLog).where(AuditLog.actor_id == team["a"].id)))[-1]
    assert read.context["query"] == {"keyword": "采购"}
    assert read.context["result"] == "success"
    response = client.get("/api/admin/config", headers=headers(team["a"]))
    assert response.status_code == 403
    rejected = list(db.scalars(select(AuditLog).where(AuditLog.actor_id == team["a"].id)))[-1]
    assert rejected.context["result"] == "failed" and rejected.context["http_status"] == 403
    assert client.get("/api/reports/export?start=2026-11-01&end=2026-11-05", headers=headers(root)).status_code == 200
    assert any(v.context.get("operation_label") == "导出统计报表" for v in db.scalars(select(AuditLog)))


def test_permission_option_loads_do_not_create_view_logs(logs_client, db):
    client, root = logs_client
    for path in ["/api/reports/access", "/api/direct-assignments/options", "/api/task-overview/options",
                 "/api/meeting-series-name-options", "/api/history-meeting-departments"]:
        assert client.get(path, headers=headers(root)).status_code == 200
    assert list(db.scalars(select(AuditLog))) == []
    assert client.get("/api/reports?start=2026-11-01&end=2026-11-05", headers=headers(root)).status_code == 200
    entries = list(db.scalars(select(AuditLog)))
    assert len(entries) == 1
    assert entries[0].context["operation_label"] == "查询统计报表"


def test_background_reads_are_omitted_and_each_foreground_view_is_retained(logs_client, db):
    client, root = logs_client
    path = "/api/meetings?view=current&scope=all&offset=0&limit=20"
    for _ in range(2):
        assert client.get(path, headers={**headers(root), "X-Operation-Context": "background"}).status_code == 200
    assert list(db.scalars(select(AuditLog))) == []
    for _ in range(2):
        assert client.get(path, headers=headers(root)).status_code == 200
    entries = list(db.scalars(select(AuditLog)))
    assert len(entries) == 2
    assert all(entry.context["operation_label"] == "查看会议" for entry in entries)
    assert all(entry.context["query"] == {"view": "current", "scope": "all", "offset": "0", "limit": "20"}
               for entry in entries)


def test_background_marker_cannot_hide_failures_writes_or_exports(logs_client, db, team):
    client, root = logs_client
    background = {**headers(root), "X-Operation-Context": "background"}
    for name in ["独立新建甲", "独立新建乙"]:
        assert client.post("/api/series", headers=background, json={"name": name}).status_code == 200
    created = list(db.scalars(select(AuditLog).where(AuditLog.action == "meeting_series.create")))
    assert len(created) == 2
    assert client.get("/api/reports/export?start=2026-11-01&end=2026-11-05", headers=background).status_code == 200
    assert any(entry.context.get("operation_label") == "导出统计报表" for entry in db.scalars(select(AuditLog)))
    response = client.get("/api/admin/config", headers={**headers(team["a"]), "X-Operation-Context": "background"})
    assert response.status_code == 403
    failures = list(db.scalars(select(AuditLog).where(AuditLog.actor_id == team["a"].id)))
    assert len(failures) == 1
    assert failures[0].context["result"] == "failed" and failures[0].context["background"]
    # Denied permission lookups are also kept even though successful lookups are auxiliary.
    assert client.get("/api/task-overview/options", headers=headers(team["a"])).status_code == 403
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.actor_id == team["a"].id)))) == 2


def test_publication_keeps_recipient_snapshot_and_delivery_results(logs_client, db, team, draft_factory, monkeypatch):
    client, root = logs_client
    from app.models import SystemSetting
    db.add(SystemSetting(key="reminders", value={"enabled": True}))
    team["a"].email = "a@example.com"
    team["b"].email = "b@example.com"
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    draft.cc_ids = [team["b"].id]
    draft.cc_emails = ["extra@example.com"]
    meeting = create_meeting(db, root, draft)
    publish(db, root, meeting.id, PublishRequest(expected_version=1, notify=True))
    db.commit()
    publication = db.scalar(select(AuditLog).where(AuditLog.action == "meeting.publish"))
    plan = publication.context["notification_plan"]
    mail = next(v for v in plan if v["channel"] == "email")
    assert mail["to"][0]["address"] == "a@example.com"
    assert [v["address"] for v in mail["cc"]] == ["b@example.com", "extra@example.com"]
    event = db.get(OutboxEvent, mail["event_id"])
    event.state, event.attempts = "sent", 1
    log_delivery(db, event, {"message_id": "receipt", "to": ["a@example.com"], "cc": ["b@example.com", "extra@example.com"]})
    team["a"].name, team["a"].email = "改名后员工", "changed@example.com"
    db.commit()
    detail = client.get(f"/api/admin/operation-logs/{publication.id}", headers=headers(root)).json()
    delivered = next(v for v in detail["deliveries"] if v["channel"] == "email")
    assert delivered["state"] == "sent"
    assert delivered["to"][0]["address"] == "a@example.com"
    assert delivered["to"][0]["name"] == "责任人甲"
    assert not client.get("/api/admin/operation-logs", headers=headers(team["host"])).is_success


def test_notification_attempts_and_identical_results_are_each_kept(logs_client, db, team):
    _, root = logs_client
    event = OutboxEvent(event_key="audit-attempt-test", channel="email",
                        payload={"actor_id": root.id, "recipient_ids": [team["a"].id]})
    db.add(event)
    db.flush()
    for attempt, state in [(1, "failed"), (2, "sent"), (3, "sent")]:
        event.attempts, event.state = attempt, state
        log_delivery(db, event, {"to": ["a@example.test"]})
    db.commit()
    entries = list(db.scalars(select(AuditLog).where(AuditLog.entity_id == event.id)))
    assert len(entries) == 3
    assert {entry.after["attempt"] for entry in entries} == {1, 2, 3}
    assert sorted(entry.context["result"] for entry in entries) == ["failed", "sent", "sent"]


@pytest.mark.parametrize("legacy", [False, True])
def test_old_mail_receipts_show_actual_addresses_times_and_creation_without_backfilling(
    logs_client, db, team, draft_factory, legacy
):
    _, root = logs_client
    from app.models import SystemSetting
    db.add(SystemSetting(key="reminders", value={"enabled": True}))
    team["a"].email, team["b"].email = "original@example.com", "copy@example.com"
    draft = draft_factory()
    draft.recipient_ids, draft.cc_ids = [team["a"].id], [team["b"].id]
    meeting = create_meeting(db, root, draft)
    publish(db, root, meeting.id, PublishRequest(expected_version=1, notify=True))
    publication = db.scalar(select(AuditLog).where(AuditLog.action == "meeting.publish"))
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "email"))
    sent_at = datetime(2026, 11, 8, 5, 37, 47, tzinfo=timezone.utc)
    event.state, event.attempts, event.sent_at = "sent", 2, sent_at
    db.add_all([
        NotificationLog(event_id=event.id, result="failed", detail={},
                        created_at=datetime(2026, 11, 8, 5, 36, tzinfo=timezone.utc)),
        NotificationLog(event_id=event.id, result="sent", detail={
            "to": ["original@example.com"], "cc": ["copy@example.com"], "message_id": "receipt"},
                        created_at=sent_at),
    ])
    if legacy:
        publication.context = {k: v for k, v in publication.context.items() if k != "notification_plan"}
    team["a"].email = "changed@example.com"
    db.flush()
    audit_ids = list(db.scalars(select(AuditLog.id)))
    before = dict(meeting.draft)
    detail = present(db, publication, detail=True)
    delivery = next(item for item in detail["deliveries"] if item["channel"] == "email")
    assert delivery["actual_recorded"] and not delivery["historical"]
    assert delivery["to"][0]["address"] == "original@example.com"
    assert delivery["cc"][0]["address"] == "copy@example.com"
    assert delivery["to"][0]["name"] == team["a"].name
    assert delivery["sent_at"] == sent_at
    assert [item["state"] for item in delivery["history"]] == ["failed", "sent"]
    assert "收件 1 人，抄送 1 人" in detail["summary"]
    assert detail["meeting_record"]["created_by"] == root.name
    assert detail["meeting_record"]["created_at"] == meeting.created_at
    assert detail["meeting_record"]["meeting_date"] == "2026-08-10"
    assert list(db.scalars(select(AuditLog.id))) == audit_ids
    assert meeting.draft == before


def test_failed_delivery_attempt_never_uses_later_success_time(logs_client, db, team):
    _, root = logs_client
    event = OutboxEvent(event_key="retry-history", channel="email", state="failed",
                        payload={"actor_id": root.id, "recipient_ids": [team["a"].id]})
    db.add(event)
    db.flush()
    log_delivery(db, event, {})
    db.flush()
    failed = db.scalar(select(AuditLog).where(AuditLog.entity_id == event.id))
    event.state, event.sent_at = "sent", datetime(2026, 11, 8, tzinfo=timezone.utc)
    db.flush()
    detail = present(db, failed, detail=True)
    assert detail["deliveries"][0]["state"] == "failed"
    assert detail["deliveries"][0]["sent_at"] is None
    assert not detail["deliveries"][0]["actual_recorded"]


def test_login_logout_failures_and_sensitive_requests(logs_client, db, team):
    client, root = logs_client
    response = client.post("/api/auth/login", json={"username": "admin", "password": "operation-test-password"})
    assert response.status_code == 200
    assert client.post("/api/auth/logout", headers=headers(root)).status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"}).status_code == 401
    entries = list(db.scalars(select(AuditLog)))
    assert {"auth.login", "auth.logout", "auth.login_failed"}.issubset({e.action for e in entries})
    failed = next(e for e in entries if e.action == "auth.login_failed")
    assert failed.context["actor_name"] == "未登录访客"
    assert "operation-test-password" not in str([e.after for e in entries])
    assert "wrong-password" not in str([e.context for e in entries])


def test_failed_business_write_rolls_back_but_rejection_persists(logs_client, db, team, draft_factory):
    client, root = logs_client
    meeting = create_meeting(db, root, draft_factory())
    db.commit()
    original = meeting.draft
    response = client.put(f"/api/meetings/{meeting.id}", headers=headers(team["outsider"]),
                          json={"draft": {**original, "title": "越权改名"}, "expected_version": 1})
    assert response.status_code == 403
    db.expire_all()
    assert db.get(Meeting, meeting.id).title == original["title"]
    assert db.scalar(select(AuditLog).where(AuditLog.actor_id == team["outsider"].id)).context["result"] == "failed"


def test_additive_migration_preserves_existing_records_and_blocks_destructive_downgrade():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261105_operation_logs.py"
    spec = importlib.util.spec_from_file_location("operation_logs_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with create_engine("sqlite://").begin() as connection:
        connection.execute(text("CREATE TABLE audit_logs (id TEXT PRIMARY KEY, actor_id TEXT, created_at DATETIME)"))
        connection.execute(text("INSERT INTO audit_logs (id,actor_id) VALUES ('existing','user')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(text("SELECT id,context FROM audit_logs")).one() == ("existing", "{}")
        assert {i["name"] for i in inspect(connection).get_indexes("audit_logs")} == {
            "ix_audit_log_created_id", "ix_audit_log_actor_created"}
        connection.execute(text("UPDATE audit_logs SET context = :value"), {"value": '{"actor_name":"saved"}'})
        with pytest.raises(RuntimeError, match="不能删除"):
            migration.downgrade()
        assert connection.scalar(text("SELECT count(*) FROM audit_logs")) == 1
