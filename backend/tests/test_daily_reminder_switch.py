from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.config import get_settings
from app.core.db import get_db
from app.main import app
from app.models import OutboxEvent, SystemSetting, Task
from app.schemas import ApplyCompletion, PublishRequest
from app.services import integrations
from app.services.business import apply_completion, create_meeting, enqueue, notification_enabled, publish
from tests.test_admin_people import headers


@pytest.fixture
def api_client(db):
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def configuration(daily_enabled=False):
    return {"enabled": True, "daily_enabled": daily_enabled, "hour": 9, "before_days": 1,
            "overdue_enabled": True, "verification_hours": 24}


@pytest.mark.parametrize("legacy", [False, True])
def test_new_and_existing_configuration_default_to_publication_only(api_client, db, team, legacy):
    if legacy:
        value = configuration()
        value.pop("daily_enabled")
        db.add(SystemSetting(key="reminders", value=value))
        db.flush()
    response = api_client.get("/api/admin/reminders", headers=headers(team["host"]))
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["daily_enabled"] is False
    assert notification_enabled(db, "wecom", {"meeting_publication": True})
    assert notification_enabled(db, "email", {"meeting_id": "publication"})
    assert not notification_enabled(db, "wecom", {"reminder_task_ids": ["legacy-test"]})
    assert not notification_enabled(db, "wecom", {"verification_request_ids": ["legacy-test"]})


@pytest.mark.parametrize("direct", [False, True])
def test_nine_am_does_not_generate_daily_or_verification_reminders(
    db, team, draft_factory, monkeypatch, direct,
):
    clock = datetime(2026, 10, 30, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(worker, "now", lambda: clock)
    monkeypatch.setattr(get_settings(), "wecom_direct_notifications_enabled", True)
    db.add(SystemSetting(key="reminders", value=configuration()))
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    if direct:
        task.source_meeting_id = None
        task.issuer_id = team["host"].id
    request = apply_completion(db, team["a"], task.id, ApplyCompletion(
        expected_version=1, idempotency_key="no-daily-reminder", explanation="测试提交材料",
    ))
    request.created_at = clock - timedelta(hours=25)
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
    for _ in range(2):
        assert worker.reminders() == 0
    assert not list(db.scalars(select(OutboxEvent).where(
        OutboxEvent.event_key.like("reminder:%") | OutboxEvent.event_key.like("verify-digest:%")
    )))


def test_disabling_daily_reminders_cancels_only_unsent_digests(api_client, db, team):
    db.add(SystemSetting(key="reminders", value=configuration(True)))
    pending = [OutboxEvent(event_key=f"reminder:old:{state}", channel="wecom",
                          payload={"reminder_task_ids": ["old-test"]}, state=state)
               for state in ["pending", "blocked", "failed"]]
    verification = OutboxEvent(event_key="verify-digest:old", channel="wecom",
                              payload={"verification_request_ids": ["old-request"]})
    preserved = [
        OutboxEvent(event_key="meeting:new:wecom", channel="wecom", payload={"meeting_publication": True}),
        OutboxEvent(event_key="meeting:new:mail", channel="email", payload={"meeting_id": "new"}),
        OutboxEvent(event_key="export:new", channel="export", payload={}),
        OutboxEvent(event_key="reminder:sent", channel="wecom",
                    payload={"reminder_task_ids": ["old-test"]}, state="sent"),
    ]
    db.add_all([*pending, verification, *preserved])
    db.flush()
    response = api_client.put("/api/admin/reminders", headers=headers(team["host"]), json=configuration())
    assert response.status_code == 200, response.text
    assert response.json()["cancelled_events"] == 4
    assert response.json()["enabled"] is True and response.json()["daily_enabled"] is False
    assert all(event.state == "skipped" and "不会补发" in event.last_error for event in [*pending, verification])
    assert [event.state for event in preserved] == ["pending", "pending", "pending", "sent"]
    api_client.put("/api/admin/reminders", headers=headers(team["host"]), json=configuration(True))
    assert all(event.state == "skipped" for event in [*pending, verification])


def test_stale_daily_reminders_are_blocked_at_enqueue_retry_and_delivery(
    api_client, db, team, monkeypatch,
):
    db.add(SystemSetting(key="reminders", value=configuration()))
    monkeypatch.setattr(get_settings(), "wecom_direct_notifications_enabled", True)
    enqueue(db, "reminder:new", "wecom", {"reminder_task_ids": ["old-test"]})
    blocked = OutboxEvent(event_key="reminder:retry", channel="wecom",
                         payload={"reminder_task_ids": ["old-test"]}, state="blocked")
    pending = OutboxEvent(event_key="verify-digest:late", channel="wecom",
                         payload={"verification_request_ids": ["old-test"]}, state="pending")
    db.add_all([blocked, pending])
    db.commit()
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == "reminder:new")).state == "skipped"
    response = api_client.post(f"/api/admin/notifications/{blocked.id}/retry",
                               headers=headers(team["host"]), json={"reason": "尝试旧提醒重试"})
    assert response.status_code == 409

    def forbidden(*_):
        pytest.fail("Disabled daily reminders must never call an external provider")

    monkeypatch.setattr(integrations, "WeCom", forbidden)
    assert integrations.send_wecom(db, pending).get("skipped")
    monkeypatch.setattr(worker, "send_wecom", forbidden)
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
    assert worker.dispatch() == 1
    assert worker.dispatch() == 0
    db.expire_all()
    assert db.get(OutboxEvent, pending.id).state == "skipped"
