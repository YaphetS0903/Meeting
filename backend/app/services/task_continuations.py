"""Explicit continuity for legacy copies whose text or owners have changed."""

from fastapi import HTTPException
from sqlalchemy import select

from app.core.db import locked_get
from app.models import Meeting, MeetingVersion, Task, TaskCarryReplacement
from app.services.access import meeting_access


def confirm_carry_replacement(db, actor, meeting_id, previous_id, next_id, *, expected_version, reason):
    """Record a reviewed pair with published evidence; never infer fuzzy identity."""
    from app.services.business import audit

    source = locked_get(db, Meeting, meeting_id)
    if not source:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, actor, source, edit=True)
    if source.historical or not source.series_id or source.published_version != expected_version:
        raise HTTPException(409, "来源会议发布版本已变化，请重新核对")
    if not reason.strip() or previous_id == next_id:
        raise HTTPException(422, "请说明延续关系并选择不同任务")
    previous, following = (locked_get(db, Task, identifier) for identifier in sorted({previous_id, next_id}))
    # Lock in a stable order, then recover the requested direction.
    tasks = {task.id: task for task in (previous, following) if task}
    old, new = tasks.get(previous_id), tasks.get(next_id)
    version = db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == expected_version,
    ))
    rows = version.snapshot.get("items", []) if version else []
    old_rows = [row for row in rows if row["section"] == "review" and row.get("task_id") == previous_id]
    new_rows = [row for row in rows if row["section"] == "new" and row.get("task_id") == next_id]
    if not (old and new and len(old_rows) == len(new_rows) == 1
            and old.source_meeting_id and old.source_meeting_id != source.id
            and new.source_meeting_id == source.id and old.series_id == new.series_id == source.series_id
            and old.version == old_rows[0].get("task_version")
            and not any((old.recurrence_key, new.recurrence_key, old.recurrence_period, new.recurrence_period))
            and not old.withdrawn_at and not new.withdrawn_at):
        raise HTTPException(422, "任务不符合已发布会议中的前后延续关系，请重新核对")
    existing = db.scalar(select(TaskCarryReplacement).where(TaskCarryReplacement.previous_task_id == old.id))
    if existing:
        if existing.next_task_id != new.id or existing.meeting_id != source.id:
            raise HTTPException(409, "旧任务已关联其他延续任务")
        return existing
    cursor, seen = new.id, {old.id}
    while cursor:
        if cursor in seen:
            raise HTTPException(409, "延续关系不能形成循环")
        seen.add(cursor)
        cursor = db.scalar(select(TaskCarryReplacement.next_task_id).where(
            TaskCarryReplacement.previous_task_id == cursor,
        ))
    link = TaskCarryReplacement(
        previous_task_id=old.id, next_task_id=new.id, meeting_id=source.id,
        meeting_version=expected_version, previous_task_version=old.version,
        actor_id=actor.id, reason=reason.strip(),
    )
    db.add(link)
    db.flush()
    audit(db, actor, "task.carry_replacement", link, after={
        "previous_task_id": old.id, "next_task_id": new.id,
        "meeting_id": source.id, "meeting_version": expected_version,
    }, reason=reason.strip())
    return link


def confirmed_superseded_task_ids(db, source, snapshot):
    """Use only confirmations anchored to this visible published meeting."""
    current_arrangements = {row.get("task_id") for row in snapshot.get("items", []) if row["section"] == "new"}
    result = set()
    for link in db.scalars(select(TaskCarryReplacement).where(
        TaskCarryReplacement.meeting_id == source.id,
        TaskCarryReplacement.meeting_version <= source.published_version,
    )):
        old, new = db.get(Task, link.previous_task_id), db.get(Task, link.next_task_id)
        if (old and new and new.id in current_arrangements and not new.withdrawn_at
                and old.version == link.previous_task_version
                and old.series_id == new.series_id == source.series_id
                and old.source_meeting_id and old.source_meeting_id != source.id
                and new.source_meeting_id == source.id
                and not any((old.recurrence_key, new.recurrence_key, old.recurrence_period, new.recurrence_period))):
            result.add(old.id)
    return result
