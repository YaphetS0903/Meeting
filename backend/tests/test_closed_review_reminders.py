"""Published review decisions must also govern queued deadline notifications."""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.models import OutboxEvent, SystemSetting
from app.schemas import MeetingDraft, PublishRequest
from app.services import integrations
from app.services.business import create_meeting, publish, revision, save_meeting, task_snapshot
from app.services.deadline_reminders import digest_tasks, enqueue_due_reminders
from tests.test_deadline_reminders import assigned

CLOCK = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)


def review_task(db, team, draft_factory, task, status, *, published=True):
    draft = draft_factory(
        section="review", status=status, when=date(2026, 11, 4),
        task_id=task.id, task_version=task.version,
    )
    draft.items[0].due_date = task.due_date
    draft.items[0].owner_ids = [team["a"].id]
    meeting = create_meeting(db, team["host"], draft)
    if published:
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def daily_config(db):
    db.add(SystemSetting(key="reminders", value={
        "enabled": True, "daily_enabled": True, "hour": 9, "before_days": 1,
        "overdue_enabled": True, "verification_hours": 24,
    }))
    db.flush()


@pytest.mark.parametrize("status", ["关闭", "不统计"])
@pytest.mark.parametrize("channel", ["deadline", "daily_due", "daily_overdue"])
def test_published_terminal_review_prevents_scheduler_queue(
    db, team, draft_factory, monkeypatch, status, channel,
):
    task = assigned(db, team, draft_factory, 6 if channel != "daily_overdue" else 4)
    before = task_snapshot(db, task)
    review_task(db, team, draft_factory, task, status)
    assert task.closed_at is None and task.status == before["status"]
    if channel == "deadline":
        assert enqueue_due_reminders(db, CLOCK) == 0
    else:
        daily_config(db)
        db.commit()
        monkeypatch.setattr(worker, "now", lambda: CLOCK)
        monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
        worker.reminders()
    assert not list(db.scalars(select(OutboxEvent).where(
        OutboxEvent.event_key.like("deadline-digest:%") | OutboxEvent.event_key.like("reminder:%")
    )))
    assert task_snapshot(db, task) == before


@pytest.mark.parametrize("status", ["关闭", "不统计"])
@pytest.mark.parametrize("channel", ["deadline", "daily_due", "daily_overdue"])
def test_queued_notification_rechecks_newly_published_terminal_review(
    db, team, draft_factory, monkeypatch, status, channel,
):
    task = assigned(db, team, draft_factory, 6 if channel != "daily_overdue" else 4)
    if channel == "deadline":
        assert enqueue_due_reminders(db, CLOCK) == 1
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))
    else:
        daily_config(db)
        event = OutboxEvent(event_key="reminder:queued", channel="wecom", payload={
            "reminder_task_ids": [task.id], "user_ids": [team["a"].id], "title": "任务到期 / 逾期汇总",
        })
        db.add(event)
    review_task(db, team, draft_factory, task, status)
    monkeypatch.setattr("app.models.now", lambda: CLOCK)
    monkeypatch.setattr("app.services.business.today", lambda: CLOCK.date())
    monkeypatch.setattr(integrations, "WeCom", lambda: pytest.fail("Closed work must not contact WeCom"))
    assert integrations.send_wecom(db, event).get("skipped")
    if channel == "deadline":
        assert digest_tasks(db, event.payload, team["a"].id) == []


@pytest.mark.parametrize("channel", ["deadline", "daily"])
def test_mixed_digest_delivers_only_open_work(db, team, draft_factory, monkeypatch, channel):
    closed = assigned(db, team, draft_factory, 6)
    active = assigned(db, team, draft_factory, 6)
    if channel == "deadline":
        assert enqueue_due_reminders(db, CLOCK) == 1
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))
    else:
        daily_config(db)
        event = OutboxEvent(channel="wecom", payload={
            "reminder_task_ids": [closed.id, active.id], "user_ids": [team["a"].id],
            "title": "任务到期 / 逾期汇总",
        })
    review_task(db, team, draft_factory, closed, "关闭")
    calls = []

    class Provider:
        def send(self, recipients, content, **kwargs):
            calls.append((recipients, content))
            return {"msgid": "local-test"}

    monkeypatch.setattr("app.models.now", lambda: CLOCK)
    monkeypatch.setattr("app.services.business.today", lambda: CLOCK.date())
    monkeypatch.setattr(integrations, "WeCom", Provider)
    assert integrations.send_wecom(db, event)["msgid"] == "local-test"
    assert len(calls) == 1 and calls[0][0] == ["test-owner"]
    if channel == "deadline":
        assert [t.id for t in digest_tasks(db, event.payload, team["a"].id)] == [active.id]
    else:
        assert active.code in calls[0][1] and closed.code not in calls[0][1]


def test_draft_closure_and_draft_reopening_do_not_change_reminders(db, team, draft_factory):
    task = assigned(db, team, draft_factory, 6)
    meeting = review_task(db, team, draft_factory, task, "关闭", published=False)
    assert enqueue_due_reminders(db, CLOCK) == 1
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))
    assert [t.id for t in digest_tasks(db, event.payload, team["a"].id)] == [task.id]
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    assert digest_tasks(db, event.payload, team["a"].id) == []
    revision(db, team["host"], meeting.id, "更正关闭结果")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].status = "未到期"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    assert digest_tasks(db, event.payload, team["a"].id) == []
    publish(db, team["host"], meeting.id, PublishRequest(
        expected_version=meeting.draft_version, reason="恢复跟进", notify=False,
    ))
    assert [t.id for t in digest_tasks(db, event.payload, team["a"].id)] == [task.id]


def test_direct_assignments_keep_their_existing_reminder_rules(db, team, draft_factory):
    task = assigned(db, team, draft_factory, 6, direct=True)
    assert enqueue_due_reminders(db, CLOCK) == 1
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))
    assert [t.id for t in digest_tasks(db, event.payload, team["a"].id)] == [task.id]
