"""Partition visible meetings into the current workbench and department history."""

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, or_, select

from app.models import Department, Meeting
from app.schemas import MeetingDraft
from app.services.access import visible_meetings
from app.services.departments import department_paths


def revision_in_progress(meeting, snapshot):
    """An empty working copy does not change the published meeting's status."""
    if not meeting.published_version or not meeting.has_draft:
        return False
    if snapshot is None:
        return True
    # A direct display-title correction is already effective; it does not create
    # a pending content revision or rewrite the immutable published snapshot.
    published_snapshot = snapshot
    if meeting.draft.get("title") == meeting.title:
        published_snapshot = {**snapshot, "title": meeting.title}
    try:
        # Published snapshots include display names. Saving an unchanged editor
        # strips those names and fills defaults; neither is a substantive edit.
        draft = MeetingDraft.model_validate(meeting.draft).model_dump(mode="json")
        published = MeetingDraft.model_validate(published_snapshot).model_dump(mode="json")
    except ValidationError:
        # Preserve a warning for legacy content that cannot be normalized.
        return meeting.draft != published_snapshot
    return draft != published


def apply_meeting_view(query, db, user, view):
    if view == "all":
        return query
    # Rank the complete authorized set before applying date, search, status or
    # department filters. A filter must never promote an old meeting to latest.
    published = visible_meetings(db, user).where(Meeting.published_version > 0)
    ranked = published.with_only_columns(
        Meeting.id,
        Meeting.series_id,
        func.row_number().over(
            partition_by=Meeting.series_id,
            order_by=(Meeting.meeting_date.desc(), Meeting.created_at.desc(), Meeting.id.desc()),
        ).label("position"),
    ).subquery()
    current_ids = select(ranked.c.id).where(
        or_(ranked.c.series_id.is_(None), ranked.c.position == 1)
    )
    if view == "current":
        # Unpublished work stays accessible without replacing the latest
        # published minutes. Creating or editing a draft cannot archive it.
        return query.where(or_(
            (Meeting.historical.is_(False) & (Meeting.published_version == 0)),
            Meeting.id.in_(current_ids),
        ))
    return query.where(or_(
        Meeting.historical.is_(True) & (Meeting.published_version == 0),
        (Meeting.published_version > 0) & Meeting.series_id.is_not(None) & Meeting.id.not_in(current_ids),
    ))


def apply_series_key(query, series_key):
    if not series_key:
        return query
    if series_key.startswith("series:") and len(series_key) > len("series:"):
        return query.where(Meeting.series_id == series_key[len("series:"):])
    if series_key == "unassigned":
        return query.where(Meeting.series_id.is_(None))
    if series_key.startswith("unassigned:") and len(series_key) > len("unassigned:"):
        department_id = series_key[len("unassigned:"):]
        return query.where(Meeting.series_id.is_(None)).where(
            Meeting.department_id.is_(None) if department_id == "none" else Meeting.department_id == department_id
        )
    raise HTTPException(422, "会议系列参数无效")


def history_departments(db, user, series_key=None):
    history = apply_meeting_view(visible_meetings(db, user), db, user, "history")
    history = apply_series_key(history, series_key)
    counts = db.execute(
        history.with_only_columns(Meeting.department_id, func.count())
        .group_by(Meeting.department_id)
    ).all()
    departments = list(db.scalars(select(Department)))
    by_id = {department.id: department for department in departments}
    paths = department_paths(departments)
    items = [
        {
            "key": identifier or "none",
            "id": identifier,
            "name": by_id[identifier].name if identifier in by_id else "专项会议",
            "path": paths.get(identifier, "专项会议"),
            "meeting_count": count,
        }
        for identifier, count in counts
    ]
    items.sort(key=lambda item: (item["id"] is None, item["path"]))
    return {"items": items, "total": len(items), "meeting_total": sum(item["meeting_count"] for item in items)}
