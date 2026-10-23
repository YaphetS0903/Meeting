import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.db import get_db
from app.main import app
from app.models import AuditLog, OutboxEvent
from app.services.business import enqueue
from tests.test_admin_people import headers


@pytest.fixture
def api_client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def reminder_payload(enabled):
    return {
        "enabled": enabled,
        "hour": 9,
        "before_days": 1,
        "overdue_enabled": True,
        "verification_hours": 24,
    }


def test_admin_can_pause_notifications_and_cancel_unsent_backlog(api_client, db, team):
    events = [
        OutboxEvent(event_key="pending-wecom", channel="wecom", payload={}, state="pending"),
        OutboxEvent(event_key="failed-email", channel="email", payload={}, state="failed"),
        OutboxEvent(event_key="blocked-wecom", channel="wecom", payload={}, state="blocked"),
        OutboxEvent(event_key="uncertain-wecom", channel="wecom", payload={}, state="uncertain"),
        OutboxEvent(event_key="pending-export", channel="export", payload={}, state="pending"),
    ]
    db.add_all(events)
    db.flush()

    assert (
        api_client.put(
            "/api/admin/reminders", headers=headers(team["a"]), json=reminder_payload(False)
        ).status_code
        == 403
    )
    response = api_client.put(
        "/api/admin/reminders", headers=headers(team["host"]), json=reminder_payload(False)
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["cancelled_events"] == 3
    assert api_client.get("/api/admin/reminders", headers=headers(team["host"])).json()["enabled"] is False

    assert [event.state for event in events[:3]] == ["skipped", "skipped", "skipped"]
    assert all(event.sent_at for event in events[:3])
    assert all("不会补发" in event.last_error for event in events[:3])
    assert events[3].state == "uncertain"
    assert events[4].state == "pending"
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "settings.reminders").order_by(AuditLog.created_at.desc())
    )
    assert audit.after["enabled"] is False and audit.after["cancelled_events"] == 3


def test_paused_period_is_skipped_without_later_replay(api_client, db, team):
    api_client.put("/api/admin/reminders", headers=headers(team["host"]), json=reminder_payload(False))
    enqueue(db, "paused-wecom", "wecom", {"title": "测试期任务"})
    enqueue(db, "paused-email", "email", {"title": "测试期纪要"})
    enqueue(db, "still-export", "export", {})
    db.flush()

    paused = list(
        db.scalars(
            select(OutboxEvent).where(OutboxEvent.event_key.like("paused-%")).order_by(OutboxEvent.event_key)
        )
    )
    assert [event.state for event in paused] == ["skipped", "skipped"]
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == "still-export")).state == "pending"

    api_client.put("/api/admin/reminders", headers=headers(team["host"]), json=reminder_payload(True))
    enqueue(db, "enabled-wecom", "wecom", {"title": "试运行后任务"})
    db.flush()
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == "enabled-wecom")).state == "pending"
    assert all(event.state == "skipped" for event in paused)


def test_dispatch_rechecks_switch_and_retry_is_blocked(api_client, db, team, monkeypatch):
    api_client.put("/api/admin/reminders", headers=headers(team["host"]), json=reminder_payload(False))
    pending = OutboxEvent(event_key="late-pending", channel="wecom", payload={}, state="pending")
    blocked = OutboxEvent(event_key="manual-retry", channel="email", payload={}, state="blocked")
    db.add_all([pending, blocked])
    db.commit()

    assert (
        api_client.post(
            f"/api/admin/notifications/{blocked.id}/retry",
            headers=headers(team["host"]),
            json={"reason": "测试重试"},
        ).status_code
        == 409
    )
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
    monkeypatch.setattr(
        worker,
        "send_wecom",
        lambda *_: (_ for _ in ()).throw(AssertionError("paused notifications must not send")),
    )
    assert worker.dispatch() == 1
    db.expire_all()
    assert db.get(OutboxEvent, pending.id).state == "skipped"
    assert db.get(OutboxEvent, pending.id).attempts == 1
    assert db.get(OutboxEvent, blocked.id).state == "blocked"
    assert worker.reminders() == 0
