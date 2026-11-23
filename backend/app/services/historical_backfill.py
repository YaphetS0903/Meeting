"""Supplement older minutes without changing later published tasks or results."""

from datetime import date

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Meeting, MeetingVersion, Task
from app.services.access import meeting_access, task_access, visible_meetings


def validate_mode(db, actor, draft, existing=None):
    if (
        existing
        and existing.published_version
        and draft.history_backfill != bool(existing.draft.get("history_backfill"))
    ):
        raise HTTPException(422, "已发布会议不能切换历史补录模式")
    if not draft.history_backfill:
        if any(item.backfill_reference for item in draft.items):
            raise HTTPException(422, "请先取消任务关联，再关闭历史补录")
        return
    if not draft.series_id:
        raise HTTPException(422, "历史补录必须选择会议系列")
    seen = set()
    for item in draft.items:
        if item.task_id or item.postponed_due_date:
            raise HTTPException(422, "历史补录不修改当前任务，请使用“关联已发布任务”记录对应关系")
        if item.backfill_reference:
            validate_reference(db, actor, draft, item, existing)
            key = (item.section, item.backfill_reference.task_id)
            if key in seen:
                raise HTTPException(422, "同一分区内不能重复关联同一任务")
            seen.add(key)


def validate_reference(db, actor, draft, item, existing=None):
    ref = item.backfill_reference
    if not ref:
        return
    if not draft.history_backfill:
        raise HTTPException(422, "只有历史补录会议可以关联后续已发布任务")
    target = db.get(Meeting, ref.meeting_id)
    if not target or target.historical or not target.published_version:
        raise HTTPException(422, "请选择后续已发布的在线会议任务")
    meeting_access(db, actor, target)
    if not draft.series_id or target.series_id != draft.series_id:
        raise HTTPException(422, "只能关联同一会议系列的后续任务")
    previous = next(
        (r for r in (existing.draft if existing else {}).get("items", []) if r["id"] == item.id), {}
    )
    preserved = bool(
        existing and existing.published_version and previous.get("backfill_reference") == ref.model_dump()
    )
    if (not preserved and target.published_version != ref.version) or target.published_version < ref.version:
        raise HTTPException(409, "后续会议发布版本已变化，请重新选择关联任务")
    version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == target.id,
            MeetingVersion.number == ref.version,
        )
    )
    row = (
        next((r for r in version.snapshot.get("items", []) if r["id"] == ref.item_id), None)
        if version
        else None
    )
    if not row or row.get("task_id") != ref.task_id:
        raise HTTPException(422, "关联任务与已发布会议条目不一致")
    target_date = date.fromisoformat(version.snapshot["meeting_date"])
    if target_date <= draft.meeting_date:
        raise HTTPException(422, "补录会议日期必须早于所关联的已发布会议")
    task = db.get(Task, ref.task_id)
    if not task or task.series_id != target.series_id:
        raise HTTPException(422, "关联任务不存在或已移出该会议系列")
    task_access(db, actor, task)
    ref.meeting_title = version.snapshot["title"]
    ref.meeting_date = target_date.isoformat()


def task_options(db, actor, series_id, meeting_date, q=""):
    if not series_id:
        raise HTTPException(422, "请先选择会议系列")
    query = (
        visible_meetings(db, actor)
        .where(
            Meeting.series_id == series_id,
            Meeting.historical.is_(False),
            Meeting.published_version > 0,
            Meeting.meeting_date > meeting_date,
        )
        .add_columns(MeetingVersion)
        .join(
            MeetingVersion,
            (
                (MeetingVersion.meeting_id == Meeting.id)
                & (MeetingVersion.number == Meeting.published_version)
            ),
        )
        .order_by(Meeting.meeting_date, Meeting.created_at, Meeting.id)
    )
    result = []
    for meeting, version in db.execute(query):
        for row in version.snapshot.get("items", []):
            if not row.get("task_id"):
                continue
            text = " ".join([row.get("content", ""), version.snapshot["title"], *row.get("owner_names", [])])
            if q.strip().casefold() not in text.casefold():
                continue
            task = db.get(Task, row["task_id"])
            if not task or task.series_id != series_id:
                continue
            try:
                task_access(db, actor, task)
            except HTTPException as error:
                if error.status_code == 403:
                    continue
                raise
            result.append(
                {
                    "meeting_id": meeting.id,
                    "version": version.number,
                    "item_id": row["id"],
                    "task_id": task.id,
                    "meeting_title": version.snapshot["title"],
                    "meeting_date": version.snapshot["meeting_date"],
                    "content": row["content"],
                    "owner_names": row.get("owner_names", []),
                    "due_date": row.get("due_date"),
                    "status": row.get("status", ""),
                    "section": row["section"],
                }
            )
            if len(result) >= 100:
                return result
    return result


def history_for_meeting(db, actor, target):
    if not target.published_version or target.historical or not target.series_id:
        return []
    result = []
    query = (
        visible_meetings(db, actor)
        .where(
            Meeting.historical.is_(True),
            Meeting.series_id == target.series_id,
            Meeting.published_version > 0,
            Meeting.meeting_date < target.meeting_date,
        )
        .add_columns(MeetingVersion)
        .join(
            MeetingVersion,
            (
                (MeetingVersion.meeting_id == Meeting.id)
                & (MeetingVersion.number == Meeting.published_version)
            ),
        )
        .order_by(Meeting.meeting_date.desc(), Meeting.id)
    )
    for history, version in db.execute(query):
        linked = [
            r
            for r in version.snapshot.get("items", [])
            if (r.get("backfill_reference") or {}).get("meeting_id") == target.id
        ]
        if linked:
            result.append(
                {
                    "id": history.id,
                    "title": history.title,
                    "meeting_date": str(history.meeting_date),
                    "task_count": len(linked),
                }
            )
    return result
