from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.models import Meeting, OutboxEvent, SystemSetting, Task, TaskVersion
from app.schemas import PublishRequest
from app.services import integrations
from app.services.business import create_meeting, notification_enabled, publish
from app.services.deadline_reminders import deadline_assigned_on, enqueue_due_reminders, reminder_dates

ASSIGNED = datetime(2026, 11, 4, 6, tzinfo=timezone.utc)


def assigned(db, team, draft_factory, due, *, direct=False):
    draft = draft_factory(when=date(2026, 11, 4))
    draft.items[0].due_date = date(2026, 11, due)
    draft.items[0].owner_ids = [team["a"].id]
    team["a"].wecom_id = "test-owner"
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    task.created_at = ASSIGNED
    db.scalar(select(TaskVersion).where(TaskVersion.task_id == task.id)).created_at = ASSIGNED
    if direct:
        task.source_meeting_id = None
        task.issuer_id = team["host"].id
    db.flush()
    return task


@pytest.mark.parametrize("due,expected", [(4, []), (5, [5]), (6, [5, 6]), (7, [5]), (11, [9])])
def test_requested_date_examples(due, expected):
    assert [day.day for day in reminder_dates(date(2026, 11, 4), date(2026, 11, due))] == expected


@pytest.mark.parametrize("direct", [False, True])
def test_scheduler_sends_only_selected_days_once_for_both_sources(db, team, draft_factory, direct):
    tasks = {due: assigned(db, team, draft_factory, due, direct=direct) for due in (5, 6, 11)}
    for day in range(4, 13):
        clock = datetime(2026, 11, day, 1, tzinfo=timezone.utc)
        enqueue_due_reminders(db, clock)
        enqueue_due_reminders(db, clock)
    db.flush()
    events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%"))))
    assert len(events) == 3
    actual = {(item["task_id"], e.payload["reminder_day"]) for e in events for item in e.payload["deadline_items"]}
    assert actual == {(tasks[5].id, "2026-11-05"), (tasks[6].id, "2026-11-05"),
                      (tasks[6].id, "2026-11-06"), (tasks[11].id, "2026-11-09")}
    assert all(e.state == "pending" for e in events)


@pytest.mark.parametrize("change", ["已完成", "关闭", "不统计", "withdrawn", "inactive", "unbound", "historical"])
def test_ineligible_tasks_never_queue(db, team, draft_factory, change):
    task = assigned(db, team, draft_factory, 5)
    if change == "withdrawn":
        task.withdrawn_at = ASSIGNED
    elif change == "inactive":
        team["a"].active = False
    elif change == "unbound":
        team["a"].wecom_id = None
    elif change == "historical":
        db.get(Meeting, task.source_meeting_id).historical = True
    else:
        task.status = change
    assert enqueue_due_reminders(db, ASSIGNED + timedelta(days=1, hours=-5)) == 0
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%"))))


def test_deadline_revision_reschedules_but_unchanged_versions_do_not(db, team, draft_factory):
    task = assigned(db, team, draft_factory, 5)
    task.due_date = date(2026, 11, 11)
    task.version = 2
    db.add(TaskVersion(task_id=task.id, number=2, snapshot={"due_date": "2026-11-11"},
                       reason="延期", actor_id=team["host"].id, created_at=ASSIGNED))
    db.add(TaskVersion(task_id=task.id, number=3, snapshot={"due_date": "2026-11-11"},
                       reason="修改备注", actor_id=team["host"].id, created_at=ASSIGNED + timedelta(days=5)))
    db.flush()
    assert deadline_assigned_on(db, task) == date(2026, 11, 4)
    assert enqueue_due_reminders(db, datetime(2026, 11, 5, 1, tzinfo=timezone.utc)) == 0
    assert enqueue_due_reminders(db, datetime(2026, 11, 9, 1, tzinfo=timezone.utc)) == 1


@pytest.mark.parametrize("change", ["completed", "due", "owner", "next_day", "disabled"])
def test_queued_event_revalidated_without_calling_provider(db, team, draft_factory, monkeypatch, change):
    task = assigned(db, team, draft_factory, 5)
    clock = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)
    enqueue_due_reminders(db, clock)
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))
    if change == "completed":
        task.status = "已完成"
    elif change == "due":
        task.due_date = date(2026, 11, 11)
    elif change == "owner":
        event.payload = {**event.payload, "user_ids": [team["b"].id]}
    elif change == "next_day":
        clock += timedelta(days=1)
    else:
        db.add(SystemSetting(key="reminders", value={"enabled": True, "deadline_enabled": False}))
    monkeypatch.setattr("app.models.now", lambda: clock)
    monkeypatch.setattr(integrations, "WeCom", lambda: pytest.fail("Must not call real provider"))
    assert integrations.send_wecom(db, event).get("skipped")


def test_delivery_is_to_owner_with_date_and_existing_switches(db, team, draft_factory, monkeypatch):
    task = assigned(db, team, draft_factory, 5)
    clock = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)
    enqueue_due_reminders(db, clock)
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))
    calls = []

    class FakeWeCom:
        def send(self, recipients, content, **kwargs):
            calls.append((recipients, content, kwargs))
            return {"msgid": "fake"}

    monkeypatch.setattr("app.models.now", lambda: clock)
    monkeypatch.setattr(integrations, "WeCom", FakeWeCom)
    assert integrations.send_wecom(db, event)["msgid"] == "fake"
    assert calls[0][0] == ["test-owner"] and "2026-11-05" in str(calls[0][1])
    assert notification_enabled(db, "wecom", event.payload)
    assert not notification_enabled(db, "wecom", {"reminder_task_ids": [task.id]})
    db.add(SystemSetting(key="reminders", value={"enabled": False}))
    db.flush()
    assert not notification_enabled(db, "wecom", event.payload)


def test_clock_boundary_and_worker_registration(db, team, draft_factory, monkeypatch):
    assigned(db, team, draft_factory, 5)
    assert enqueue_due_reminders(db, datetime(2026, 11, 5, 0, tzinfo=timezone.utc)) == 0
    db.commit()
    monkeypatch.setattr(worker, "now", lambda: datetime(2026, 11, 5, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
    assert worker.deadline_reminders() == 1
    schedule = worker.celery.conf.beat_schedule["deadline-reminders"]
    assert schedule["schedule"].hour == {9} and schedule["schedule"].minute == {0}
