"""Bounded deadline reminders, separate from legacy daily overdue digests."""
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import (
    Meeting,
    OutboxEvent,
    SystemSetting,
    Task,
    TaskCarryReplacement,
    TaskOwner,
    TaskVersion,
    User,
    now,
)

CHINA = ZoneInfo("Asia/Shanghai")


def enabled(db):
    entry = db.get(SystemSetting, "reminders")
    return bool(entry.value.get("deadline_enabled", True)) if entry else True


def local_date(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CHINA).date()


def reminder_dates(assigned_on: date, due_date: date):
    gap = (due_date - assigned_on).days
    if gap <= 0:
        return ()
    if gap == 1:
        return (due_date,)
    if gap == 2:
        return (due_date - timedelta(days=1), due_date)
    return (due_date - timedelta(days=2),)


def deadline_assigned_on(db, task):
    """Keep the original date through revisions; only a changed deadline resets it."""
    anchor = task.created_at
    matching = []
    for version in db.scalars(select(TaskVersion).where(TaskVersion.task_id == task.id)
                             .order_by(TaskVersion.number.desc())):
        if version.snapshot.get("due_date") != task.due_date.isoformat():
            break
        matching.append(version)
    if matching:
        anchor = matching[-1].created_at
    return local_date(anchor)


def eligible(db, task, day, *, recipient=None, due_date=None):
    from app.services.business import effective_terminal_review_task_ids, task_wecom_enabled

    if (not task or not task.due_date or task.closed_at or task.withdrawn_at
            or task.status in {"已完成", "关闭", "不统计"} or not task_wecom_enabled(db, task)):
        return False
    if due_date and task.due_date.isoformat() != due_date:
        return False
    if task.source_meeting_id:
        meeting = db.get(Meeting, task.source_meeting_id)
        if not meeting or meeting.historical or not meeting.published_version:
            return False
        if effective_terminal_review_task_ids(db, [task.id]):
            return False
    elif not task.issuer_id:
        return False
    if db.scalar(select(TaskCarryReplacement.id).where(TaskCarryReplacement.previous_task_id == task.id)):
        return False
    if recipient:
        person = db.get(User, recipient)
        if not person or not person.active or not person.wecom_id or not db.scalar(
            select(TaskOwner.user_id).where(TaskOwner.task_id == task.id, TaskOwner.user_id == recipient)
        ):
            return False
    return day in reminder_dates(deadline_assigned_on(db, task), task.due_date)


def enqueue_due_reminders(db, current):
    from app.core.db import locked_get
    from app.services.business import enqueue

    if not enabled(db):
        return 0
    local = current.astimezone(CHINA)
    if local.hour != 9:
        return 0
    day = local.date()
    groups = {}
    tasks = db.scalars(select(Task).where(
        Task.due_date >= day, Task.due_date <= day + timedelta(days=2),
        Task.closed_at.is_(None), Task.withdrawn_at.is_(None),
        Task.status.notin_(["已完成", "关闭", "不统计"]),
    ).order_by(Task.due_date, Task.created_at, Task.id))
    for task in tasks:
        if not eligible(db, task, day):
            continue
        for user_id in db.scalars(select(TaskOwner.user_id).where(TaskOwner.task_id == task.id)):
            if not eligible(db, task, day, recipient=user_id):
                continue
            groups.setdefault(user_id, []).append({"task_id": task.id, "due_date": task.due_date.isoformat()})
    count = 0
    for user_id, items in sorted(groups.items()):
        # Serialize repeated scheduler runs per recipient, without splitting a digest
        # through skip_locked task rows. The delivery path revalidates every task.
        locked_get(db, User, user_id)
        key = f"deadline-digest:{day}:{user_id}"
        if db.scalar(select(OutboxEvent.id).where(OutboxEvent.event_key == key)):
            continue
        legacy = list(db.scalars(select(OutboxEvent).where(
            OutboxEvent.channel == "wecom", OutboxEvent.event_key.startswith(f"deadline:{day}:"),
            OutboxEvent.event_key.endswith(f":{user_id}"),
        ).with_for_update()))
        legacy = [e for e in legacy if e.payload.get("user_ids") == [user_id]]
        handled = {e.payload.get("task_id") for e in legacy if e.state != "pending"}
        items = [item for item in items if item["task_id"] not in handled]
        if not items:
            continue
        enqueue(db, key, "wecom", {
            "deadline_reminder": True, "reminder_day": day.isoformat(),
            "deadline_items": items, "user_ids": [user_id], "title": "任务到期提醒",
        })
        included = {item["task_id"] for item in items}
        for event in legacy:
            if event.state == "pending" and event.payload.get("task_id") in included:
                event.state, event.sent_at = "skipped", now()
                event.last_error = "已合并到当日个人到期提醒"
        db.flush()
        count += 1
    return count


def digest_tasks(db, payload, recipient, *, lock=False):
    """Only return the recipient's still valid tasks from this fixed daily plan."""
    from app.core.db import locked_get

    if payload.get("user_ids") != [recipient]:
        return []
    try:
        day = date.fromisoformat(payload["reminder_day"])
    except (KeyError, ValueError, TypeError):
        return []
    items = payload.get("deadline_items")
    if items is None:  # Drain previously queued single-task reminders safely.
        items = [{"task_id": payload.get("task_id"), "due_date": payload.get("deadline_due_date")}]
    result, seen = [], set()
    for item in items:
        identifier, due = item.get("task_id"), item.get("due_date")
        if not identifier or not due or identifier in seen:
            continue
        seen.add(identifier)
        task = locked_get(db, Task, identifier) if lock else db.get(Task, identifier)
        if eligible(db, task, day, recipient=recipient, due_date=due):
            result.append(task)
    return sorted(result, key=lambda task: (task.due_date,
        (task.created_at.replace(tzinfo=timezone.utc) if task.created_at.tzinfo is None else task.created_at).timestamp(), task.id))
