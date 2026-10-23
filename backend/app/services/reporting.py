from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assessment,
    Department,
    ImportBatch,
    ImportCandidate,
    ImportSource,
    Meeting,
    User,
    ValidationIssue,
)
from app.services.access import department_ids, visible_meetings
from app.services.departments import department_paths


def report(
    db: Session,
    user: User,
    start: date,
    end: date,
    group_by="meeting_department",
    department_id=None,
    kind=None,
    person_id=None,
    status=None,
):
    query = (
        select(Assessment, Meeting)
        .join(Meeting, Meeting.id == Assessment.meeting_id)
        .where(
            Meeting.published_version == Assessment.meeting_version,
            Meeting.meeting_date >= start,
            Meeting.meeting_date <= end,
            Meeting.id.in_(visible_meetings(db, user).with_only_columns(Meeting.id)),
        )
    )
    if department_id:
        query = query.where(
            (Assessment.person_department_id if group_by == "person_department" else Meeting.department_id)
            == department_id
        )
    if kind:
        query = query.where(Meeting.kind == kind)
    if person_id:
        query = query.where(Assessment.user_id == person_id)
    if status:
        query = query.where(Assessment.status == status)
    entries = list(db.execute(query.order_by(Meeting.meeting_date, Meeting.id, Assessment.item_id)))
    departments = department_paths(db.scalars(select(Department)))
    groups = defaultdict(list)
    for assessment, meeting in entries:
        key = {
            "meeting_department": meeting.department_id,
            "person_department": assessment.person_department_id,
            "person": assessment.user_id,
            "kind": meeting.kind,
        }.get(group_by, meeting.department_id)
        if group_by == "person":
            label = assessment.user_name
        elif group_by == "kind":
            label = {"department": "部门周例会", "special": "专项会议"}.get(meeting.kind)
        elif group_by == "meeting_department" and meeting.kind == "special" and key is None:
            label = "专项会议（无所属部门）"
        else:
            label = departments.get(key, "未分配部门")
        groups[(key, label)].append((assessment, meeting))

    def summarize(records):
        counts = Counter(a.status for a, _ in records)
        denominator = counts["已完成"] + counts["未完成"]
        return {
            "items": len({(a.meeting_id, a.meeting_version, a.item_id) for a, _ in records}),
            "person_count": len(records),
            "counts": {k: counts[k] for k in ["已完成", "未完成", "未到期", "不统计", "关闭"]},
            "rate": counts["已完成"] / denominator if denominator else None,
        }

    pending, file_issues, unassigned = pending_quality(
        db, user, start, end, department_id, kind, group_by, person_id
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "group_by": group_by,
        "pending_meetings": pending,
        "pending_file_issues": file_issues,
        "quality": "待校验" if pending or file_issues or unassigned else "已确认数据",
        "quality_note": "存在归属尚未确认的导入资料，请联系管理员核实是否影响本报表。" if unassigned else "",
        "summary": summarize(entries),
        "groups": [
            {"id": key, "label": label, **summarize(records)} for (key, label), records in groups.items()
        ],
        "details": [
            {
                "id": a.id,
                "meeting_id": m.id,
                "meeting_title": m.title,
                "kind": m.kind,
                "meeting_date": m.meeting_date.isoformat(),
                "meeting_version": a.meeting_version,
                "item_id": a.item_id,
                "task_id": a.task_id,
                "user_id": a.user_id,
                "user_name": a.user_name,
                "department_name": (
                    "专项会议（无所属部门）"
                    if m.kind == "special" and m.department_id is None
                    else departments.get(m.department_id, "")
                ),
                "person_department_name": departments.get(a.person_department_id, ""),
                "content": a.content,
                "status": a.status,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "notes": a.notes,
            }
            for a, m in entries
        ],
    }


def pending_quality(db, user, start, end, department_id, kind, group_by, person_id):
    """Count only accessible staging; unknown scope is a warning, never disclosed records."""
    own_departments = set(department_ids(db, user))
    people = {p.id: p for p in db.scalars(select(User))}
    pending, unassigned = 0, False
    for candidate, batch in db.execute(
        select(ImportCandidate, ImportBatch)
        .join(ImportSource, ImportSource.id == ImportCandidate.source_id)
        .join(ImportBatch, ImportBatch.id == ImportSource.batch_id)
        .where(ImportCandidate.state == "pending")
    ):
        p = candidate.corrected
        try:
            meeting_date = date.fromisoformat(p.get("meeting_date") or "")
        except (ValueError, TypeError):
            meeting_date = None
        if meeting_date:
            if not start <= meeting_date <= end:
                continue
        elif (batch.scope_start and batch.scope_start > end) or (batch.scope_end and batch.scope_end < start):
            continue
        if kind and p.get("kind") and p["kind"] != kind:
            continue
        dep = p.get("department_id")
        related = {p.get("host_id"), p.get("recorder_id"), p.get("reviewer_id"), *p.get("attendee_ids", [])}
        owners = set()
        for item in p.get("items", []):
            related.update(item.get("owner_ids", []))
            related.add(item.get("verifier_id"))
            if item.get("section") == "review":
                owners.update(item.get("owner_ids", []))
        visible = user.is_admin or dep in own_departments or user.id in related
        if not visible:
            if not dep:
                unassigned = True
            continue
        if department_id:
            if group_by == "person_department":
                mapped = {people[i].department_id for i in owners if i in people}
                if mapped and department_id not in mapped:
                    continue
            elif dep != department_id:
                continue
        if person_id and owners and person_id not in owners:
            continue
        pending += 1
    file_issues = 0
    for issue, batch in db.execute(
        select(ValidationIssue, ImportBatch)
        .join(ImportSource, ImportSource.id == ValidationIssue.source_id)
        .join(ImportBatch, ImportBatch.id == ImportSource.batch_id)
        .where(ValidationIssue.candidate_id.is_(None), ValidationIssue.resolved.is_(False))
    ):
        if (batch.scope_start and batch.scope_start > end) or (batch.scope_end and batch.scope_end < start):
            continue
        # A failed worksheet has no confirmed department/date. Do not infer them from sibling sheets.
        if user.is_admin:
            file_issues += 1
        else:
            unassigned = True
    return pending, file_issues, unassigned
