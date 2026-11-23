"""The current meeting inbox is a projection of published minutes, never drafts."""

from sqlalchemy import case, null, or_, select

from app.models import Meeting, MeetingVersion, SystemSetting, Task
from app.services.access import visible_meetings
from app.services.business import current_status, today


def unreviewed_meeting_status(task):
    if task.status in {"关闭", "已完成", "不统计"}:
        return task.status
    return "到期未验证" if task.due_date and task.due_date < today() else "未到期验证"


def published_task_contexts(db, user, task_query, *, latest_only=False):
    """Read publication metadata once; tasks, owners and requests are paged separately.

    Rank publications before filters. New arrangements in the same publication
    supersede its review; drafts never affect this read-only projection.
    """
    sources = task_query.with_only_columns(Task.source_meeting_id).order_by(None)
    series = task_query.with_only_columns(Task.series_id).order_by(None).union(
        select(Meeting.series_id).where(Meeting.id.in_(sources)))
    publications = db.execute(
        select(Meeting, MeetingVersion.snapshot).join(MeetingVersion,
            (MeetingVersion.meeting_id == Meeting.id) &
            (MeetingVersion.number == Meeting.published_version))
        .where(Meeting.published_version > 0, Meeting.historical.is_(False),
               or_(Meeting.series_id.in_(series), Meeting.id.in_(sources)))
        .order_by(Meeting.meeting_date.desc(), Meeting.created_at.desc(), Meeting.id.desc())
    ).all()
    visible_ids = set(db.scalars(visible_meetings(db, user)
        .where(Meeting.id.in_([m.id for m, _ in publications]))
        .with_only_columns(Meeting.id))) if publications else set()
    # Legacy snapshots may lack task_id on their original new row.
    legacy = any(not row.get("task_id") and row.get("section") == "new"
                 for _, snapshot in publications for row in snapshot.get("items", []))
    by_source = {(source, item): identifier for identifier, source, item in db.execute(
        task_query.with_only_columns(Task.id, Task.source_meeting_id, Task.source_item_id).order_by(None)
    )} if legacy else {}
    contexts, seen = {}, set()
    for meeting, snapshot in publications:
        key = meeting.series_id or meeting.id
        is_current = key not in seen
        seen.add(key)
        if latest_only and not is_current:
            continue
        for item in sorted(snapshot.get("items", []), key=lambda row: row.get("section") != "new"):
            if item.get("section") not in {"new", "review"}:
                continue
            identifier = item.get("task_id") or by_source.get((meeting.id, item.get("id")))
            if not identifier or identifier in contexts:
                continue
            result = item.get("status") if item.get("section") == "review" else None
            contexts[identifier] = {
                "review_status": result if result in {"关闭", "已完成", "未完成", "不统计"} else None,
                "is_current": is_current,
                "inbox_meeting_id": meeting.id,
                "inbox_meeting_date": meeting.meeting_date if meeting.id in visible_ids else None,
                "inbox_meeting_title": meeting.title if meeting.id in visible_ids else None,
            }
    return contexts


def task_status_expression(contexts):
    """Shared SQL display/filter/count rule, without rewriting assessments."""
    reviewed = {identifier: ctx["review_status"] for identifier, ctx in contexts.items()
                if ctx.get("review_status")}
    reviewed_status = case(reviewed, value=Task.id, else_=None) if reviewed else null()
    return case(
        (Task.status.in_(["关闭", "已完成", "不统计"]), Task.status),
        (Task.source_meeting_id.is_not(None), case(
            (reviewed_status.is_not(None), reviewed_status),
            (Task.due_date < today(), "到期未验证"), else_="未到期验证")),
        (Task.due_date < today(), "未完成"), else_="未到期")


def inbox_contexts(db, user, tasks):
    contexts = published_task_contexts(db, user, select(Task).where(Task.id.in_([t.id for t in tasks])))
    for task in tasks:
        if task.id in contexts:
            context = contexts[task.id]
            status = unreviewed_meeting_status(task)
            context["todo_status"] = (context.get("review_status") or status) if status in {
                "到期未验证", "未到期验证"} else status
    return contexts


def inbox_status(task, context):
    if task.source_meeting_id:
        return context.get("todo_status", unreviewed_meeting_status(task))
    return current_status(task)


def hidden_inbox_task_ids(db):
    setting = db.get(SystemSetting, "task_inbox")
    return set(setting.value.get("hidden_task_ids", [])) if setting else set()
