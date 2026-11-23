"""Meeting-specific assessment, delivery and source evidence, under the caller's data permissions."""

from collections import Counter

from sqlalchemy import or_, select

from app.models import (
    AuditLog,
    Assessment,
    CompletionRequest,
    Department,
    ImportCandidate,
    ImportSource,
    Meeting,
    MeetingVersion,
    OutboxEvent,
    Task,
    ValidationIssue,
)
from app.services.access import visible_tasks
from app.services.business import current_status
from app.services.departments import department_paths
from app.services.historical_links import work_identity

STATES = ["已完成", "未完成", "未到期", "不统计", "关闭"]


def continuation_origins(db, meeting):
    """Recover ordering evidence for existing drafts without rewriting history."""
    from app.services.business import continuation_key, continuation_source_rows
    from app.services.meeting_items import continuation_origin

    link = db.scalar(select(AuditLog).where(
        AuditLog.entity_id == meeting.id, AuditLog.action == "meeting.continue",
    ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1))
    if not link or not link.after:
        return {}
    source = db.get(Meeting, link.after.get("source_meeting_id"))
    if not source:
        return {}
    syncs = db.scalars(select(AuditLog).where(
        AuditLog.entity_id == meeting.id, AuditLog.action == "meeting.continuation.sync",
        AuditLog.after["source_meeting_id"].as_string() == source.id,
    ))
    number = max([link.after.get("source_version", 0)] + [log.after.get("source_version", 0) for log in syncs])
    version = db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == number,
    ))
    if not version:
        return {}
    rows = version.snapshot.get("items", [])
    by_key = continuation_source_rows(source, version.snapshot)
    return {
        row["id"]: continuation_origin(by_key[key], rows)
        for row in meeting.draft.get("items", [])
        if row.get("section") == "review" and (key := continuation_key(row)) in by_key
    }


def import_context(db, user, meeting):
    filters = [ImportCandidate.meeting_id == meeting.id]
    if meeting.source_identity:
        filters.append(ImportCandidate.identity_hash == meeting.source_identity)
    candidates = list(
        db.scalars(
            select(ImportCandidate).where(
                or_(*filters), ImportCandidate.state.notin_(["duplicate", "out_of_scope", "ignored"])
            )
        )
    )
    ids = [c.id for c in candidates]
    source_ids = {c.source_id for c in candidates}
    issues = (
        list(
            db.scalars(
                select(ValidationIssue)
                .where(
                    ValidationIssue.resolved.is_(False),
                    or_(
                        ValidationIssue.candidate_id.in_(ids),
                        (ValidationIssue.candidate_id.is_(None)) & ValidationIssue.source_id.in_(source_ids),
                    ),
                )
                .order_by(ValidationIssue.created_at)
            )
        )
        if candidates
        else []
    )
    return {
        "pending_issue_count": len(issues),
        "source_issue_details": [
            {
                "id": issue.id,
                "candidate_id": issue.candidate_id,
                "source_id": issue.source_id,
                "field_path": issue.field_path,
                "code": issue.code,
                "message": issue.message,
                "original": issue.original,
            }
            for issue in issues
        ]
        if user.is_admin
        else [],
        "import_sources": [
            {
                "id": source.id,
                "filename": source.filename,
                "attachment_id": source.attachment_id,
                "candidates": [
                    {"id": c.id, "sheet": c.sheet_name, "state": c.state}
                    for c in candidates
                    if c.source_id == source.id
                ],
            }
            for source in db.scalars(select(ImportSource).where(ImportSource.id.in_(source_ids)))
        ]
        if user.is_admin
        else [],
    }


def meeting_context(db, user, meeting, versions, can_edit):
    assessments = list(
        db.scalars(
            select(Assessment)
            .where(Assessment.meeting_id == meeting.id)
            .order_by(Assessment.meeting_version, Assessment.item_id, Assessment.user_id)
        )
    )
    departments = department_paths(db.scalars(select(Department)))
    stats = {}
    for version in versions:
        rows = [a for a in assessments if a.meeting_version == version["number"]]
        counts = Counter(a.status for a in rows)
        denominator = counts["已完成"] + counts["未完成"]
        stats[str(version["number"])] = {
            "effective": version["number"] == meeting.published_version,
            "items": len({a.item_id for a in rows}),
            "person_count": len(rows),
            "counts": {state: counts[state] for state in STATES},
            "rate": counts["已完成"] / denominator if denominator else None,
            "details": [
                {
                    "id": a.id,
                    "item_id": a.item_id,
                    "task_id": a.task_id,
                    "user_name": a.user_name,
                    "person_department": departments.get(a.person_department_id, "未分配"),
                    "content": a.content,
                    "status": a.status,
                    "due_date": a.due_date,
                    "notes": a.notes,
                }
                for a in rows
            ],
        }
    linked = {
        item["task_id"] for v in versions for item in v["snapshot"].get("items", []) if item.get("task_id")
    }
    linked.update(db.scalars(select(Task.id).where(Task.source_meeting_id == meeting.id)))
    history_ids = (
        {work_identity(meeting.id, item) for v in versions for item in v["snapshot"].get("items", [])}
        if meeting.historical
        else set()
    )
    linked.update(db.scalars(select(Task.id).where(Task.historical_work_id.in_(history_ids))))
    accessible = {task.id: task for task in db.scalars(visible_tasks(db, user).where(Task.id.in_(linked)))}
    by_history = {task.historical_work_id: task for task in accessible.values() if task.historical_work_id}
    links = []
    for v in versions:
        for item in v["snapshot"].get("items", []):
            task = accessible.get(item.get("task_id")) or by_history.get(work_identity(meeting.id, item))
            if task:
                links.append(
                    {
                        "task_id": task.id,
                        "code": task.code,
                        "content": item["content"],
                        "meeting_version": v["number"],
                        "item_id": item["id"],
                        "section": item["section"],
                        "task_version": item.get("task_version"),
                        "current_version": task.version,
                        "current_status": current_status(task),
                        "current_due_date": task.due_date,
                    }
                )
    # A historical activation is linked by source item; it does not rewrite the published minutes.
    for task in accessible.values():
        if task.source_meeting_id == meeting.id and not any(row["task_id"] == task.id for row in links):
            links.append(
                {
                    "task_id": task.id,
                    "code": task.code,
                    "content": task.content,
                    "meeting_version": meeting.published_version,
                    "item_id": task.source_item_id,
                    "section": "recurring" if task.recurrence_key else "activated",
                    "task_version": 1,
                    "current_version": task.version,
                    "current_status": current_status(task),
                    "current_due_date": task.due_date,
                }
            )
    notifications = []
    if can_edit and accessible:
        request_tasks = dict(db.execute(select(CompletionRequest.id, CompletionRequest.task_id)).all())
        for event in db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.channel == "wecom")
            .order_by(OutboxEvent.created_at.desc())
            .execution_options(yield_per=100)
        ):
            targets = {
                event.payload.get("task_id"),
                *event.payload.get("reminder_task_ids", []),
                *(item["task_id"] for item in event.payload.get("deadline_items", [])),
                *(request_tasks.get(r) for r in event.payload.get("verification_request_ids", [])),
            }.intersection(accessible)
            if not targets:
                continue
            notifications.append(
                {
                    "id": event.id,
                    "title": event.payload.get("title", "任务通知"),
                    "task_codes": [accessible[t].code for t in sorted(targets)],
                    "state": event.state,
                    "created_at": event.created_at,
                    "sent_at": event.sent_at,
                    "user_ids": event.payload.get("user_ids", []),
                    "attempts": event.attempts,
                    "last_error": event.last_error,
                }
            )
            if len(notifications) == 200:
                break
    return {
        "assessments": stats,
        "continuation_origins": continuation_origins(db, meeting) if can_edit else {},
        "task_links": links,
        "task_notifications": notifications,
        **import_context(db, user, meeting),
    }
