from datetime import date

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Assessment,
    Department,
    ImportBatch,
    ImportCandidate,
    ImportSource,
    Meeting,
    Task,
    User,
    ValidationIssue,
)
from app.services.access import department_ids, department_subtree_ids, visible_meetings
from app.services.departments import department_paths
from app.services.meeting_views import apply_series_key
from app.services.report_access import report_filters


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
    series_key=None,
    senior_only=False,
    *, include_details=True, details_only=False, offset=0, limit=20, leader_id=None,
):
    # Report authorization applies before all filters, groupings and Excel exports.
    access, person_id = report_filters(db, user, department_id, person_id, senior_only=senior_only)
    from app.services.senior_meetings import SENIOR_PARTICIPANTS, validate_senior_leader
    validate_senior_leader(leader_id, senior_only=senior_only)
    person_view = group_by == "department_people"
    selected_departments = department_subtree_ids(db, department_id) if person_view and department_id else set()
    people = (
        {p.id: p for p in db.scalars(
            access.people().where(User.id == user.id) if access.mode == "self" else access.people()
        )}
        if person_view else {}
    )
    query = (
        select(Assessment, Meeting)
        .join(Meeting, Meeting.id == Assessment.meeting_id)
        .where(
            Meeting.published_version == Assessment.meeting_version,
            Assessment.user_id.not_in(access.excluded_users),
        )
    )
    if access.mode == "self":
        query = query.where(Meeting.id.in_(visible_meetings(db, user).with_only_columns(Meeting.id)))
    elif access.mode == "departments":
        # A report grant covers these employees across meetings, without granting access
        # to those meetings, their other participants, attachments or draft content.
        query = query.where(Assessment.user_id.in_(access.people().with_only_columns(User.id)))
    if department_id:
        if person_view:
            query = query.where(Assessment.user_id.in_(
                select(User.id).where(User.department_id.in_(selected_departments))
            ))
        else:
            query = query.where(
                (Assessment.person_department_id if group_by == "person_department" else Meeting.department_id)
                == department_id
            )
    if kind:
        query = query.where(Meeting.kind == kind)
    if senior_only:
        from app.services.senior_meetings import senior_meeting_ids
        query = query.where(Meeting.id.in_(senior_meeting_ids(db, leader_id=leader_id)))
    query = apply_series_key(query, series_key)
    if person_id:
        query = query.where(Assessment.user_id == person_id)
    if status:
        query = query.where(Assessment.status == status)
    # Rank the published history across periods before applying the selected dates.
    linked_history = (
        select(
            Assessment.id.label("assessment_id"),
            func.row_number().over(
                partition_by=(Assessment.task_id, Assessment.user_id),
                order_by=(Meeting.meeting_date.desc(), Assessment.created_at.desc(), Assessment.id.desc()),
            ).label("position"),
        )
        .join(Meeting, Meeting.id == Assessment.meeting_id)
        .where(Assessment.task_id.is_not(None), Meeting.published_version == Assessment.meeting_version)
    )
    if person_id:
        linked_history = linked_history.where(Assessment.user_id == person_id)
    ranked = linked_history.subquery()
    pending = Assessment.status == "未到期"
    query = query.outerjoin(Task, Task.id == Assessment.task_id).where(or_(
        ~pending,
        Assessment.task_id.is_(None),
        Assessment.id.in_(select(ranked.c.assessment_id).where(ranked.c.position == 1)),
    ))
    due_date = case((pending, func.coalesce(Task.due_date, Assessment.due_date)), else_=Assessment.due_date)
    period_date = case((pending, func.coalesce(due_date, Meeting.meeting_date)), else_=Meeting.meeting_date)
    query = query.where(period_date.between(start, end))
    departments = department_paths(db.scalars(select(Department)))
    def summary_for(filtered, *, per_person=False):
        counts = dict(db.execute(filtered.with_only_columns(Assessment.status, func.count())
                                .group_by(Assessment.status)).all())
        identities = filtered.with_only_columns(Assessment.meeting_id, Assessment.meeting_version,
                                                Assessment.item_id).distinct().subquery()
        denominator = counts.get("已完成", 0) + counts.get("未完成", 0)
        return {"items": sum(counts.values()) if per_person else db.scalar(select(func.count()).select_from(identities)),
                "person_count": sum(counts.values()),
                "counts": {k: counts.get(k, 0) for k in ["已完成", "未完成", "未到期", "不统计", "关闭"]},
                "rate": counts.get("已完成", 0) / denominator if denominator else None}

    groups = []
    if not details_only:
        group_column = {"meeting_department": Meeting.department_id,
                        "person_department": Assessment.person_department_id,
                        "person": Assessment.user_id, "department_people": Assessment.user_id,
                        "kind": Meeting.kind}.get(group_by, Meeting.department_id)
        label_column = (Assessment.user_name if group_by == "person" else
                        Meeting.kind if group_by == "meeting_department" else literal(""))
        grouped = {}
        for key, raw_label, result, count in db.execute(query.with_only_columns(
                group_column, label_column.label("group_label"), Assessment.status, func.count())
                .group_by(group_column, label_column, Assessment.status)):
            label = (people[key].name if person_view else raw_label if group_by == "person" else
                     {"department": "部门周例会", "special": "专项会议"}.get(key) if group_by == "kind" else
                     "专项会议（无所属部门）" if key is None and raw_label == "special" else
                     departments.get(key, "未分配部门"))
            group = grouped.setdefault((key, label), {"id": key, "label": label, "items": 0,
                "person_count": 0, "counts": {k: 0 for k in ["已完成", "未完成", "未到期", "不统计", "关闭"]},
                **({"active": people[key].active} if person_view else {})})
            group["counts"][result] = group["counts"].get(result, 0) + count
            group["person_count"] += count
        identities = query.with_only_columns(group_column.label("group_id"), label_column.label("group_label"),
            Assessment.meeting_id, Assessment.meeting_version, Assessment.item_id).distinct().subquery()
        for key, raw_label, count in db.execute(select(identities.c.group_id, identities.c.group_label, func.count())
                                               .group_by(identities.c.group_id, identities.c.group_label)):
            label = (people[key].name if person_view else raw_label if group_by == "person" else
                     {"department": "部门周例会", "special": "专项会议"}.get(key) if group_by == "kind" else
                     "专项会议（无所属部门）" if key is None and raw_label == "special" else departments.get(key, "未分配部门"))
            grouped[(key, label)]["items"] += count
        if person_view and not senior_only:
            for person in people.values():
                if (person.active and (not department_id or person.department_id in selected_departments)
                        and (not person_id or person.id == person_id)):
                    grouped.setdefault((person.id, person.name), {"id": person.id, "label": person.name,
                        "active": person.active, "items": 0, "person_count": 0,
                        "counts": {k: 0 for k in ["已完成", "未完成", "未到期", "不统计", "关闭"]}})
        groups = list(grouped.values())
        if person_view:
            # Use the same authorized, filtered assessments as the person totals.
            # Fetch distinct meetings separately so item counts never duplicate names
            # and summary requests need not load every task detail.
            person_meetings = {}
            for identifier, meeting_id, title, meeting_date in db.execute(
                query.with_only_columns(Assessment.user_id, Meeting.id, Meeting.title, Meeting.meeting_date)
                .distinct().order_by(Meeting.meeting_date, Meeting.id)
            ):
                person_meetings.setdefault(identifier, []).append({
                    "id": meeting_id, "title": title, "meeting_date": meeting_date.isoformat(),
                })
            for group in groups:
                group["meetings"] = person_meetings.get(group["id"], [])
        for group in groups:
            denominator = group["counts"]["已完成"] + group["counts"]["未完成"]
            group["rate"] = group["counts"]["已完成"] / denominator if denominator else None
        if person_view:
            groups.sort(key=lambda row: (row["label"], row["id"]))
    detail_query = query.order_by(Meeting.meeting_date, Meeting.id, Assessment.item_id, Assessment.id)
    if details_only:
        detail_query = detail_query.offset(offset).limit(limit)
    entries = list(db.execute(detail_query.add_columns(due_date))) if include_details or details_only else []
    details_total = db.scalar(select(func.count()).select_from(query.subquery())) if details_only else None
    pending, file_issues, unassigned = (0, 0, False) if senior_only or details_only else pending_quality(
        db, user, start, end, department_id, kind, group_by, person_id, series_key
    )
    arrangements = None
    if senior_only and not details_only:
        from app.services.senior_arrangements import senior_arrangements
        arrangements = senior_arrangements(db, user, start, end, department_id, person_id,
                                           series_key, kind, include_details=include_details, leader_id=leader_id)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "group_by": group_by,
        "series_key": series_key,
        **({"leader_id": leader_id, "leader_name": SENIOR_PARTICIPANTS.get(leader_id, "全部参会领导")} if senior_only else {}),
        **({"senior_only": True, "senior_meeting_count": db.scalar(select(func.count()).select_from(query.with_only_columns(Meeting.id).distinct().subquery()))} if senior_only else {}),
        **({"new_arrangements_count": arrangements["total"],
            "senior_published_meeting_count": arrangements["meeting_count"],
            "senior_meetings": arrangements["meetings"],
            "new_arrangements": arrangements["details"]} if arrangements is not None else {}),
        "pending_meetings": pending,
        "pending_file_issues": file_issues,
        "quality": "待校验" if pending or file_issues or unassigned else "已确认数据",
        "quality_note": "存在归属尚未确认的导入资料，请联系管理员核实是否影响本报表。" if unassigned else "",
        "summary": summary_for(query, per_person=person_view) if not details_only else None,
        "groups": groups,
        **({"details_paginated": True} if not include_details else {}),
        **({"details_total": details_total, "offset": offset, "limit": limit} if details_only else {}),
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
                "user_name": people[a.user_id].name if person_view else a.user_name,
                "department_name": (
                    "专项会议（无所属部门）"
                    if m.kind == "special" and m.department_id is None
                    else departments.get(m.department_id, "")
                ),
                "person_department_name": departments.get(a.person_department_id, ""),
                "content": a.content,
                "status": a.status,
                "due_date": deadline.isoformat() if deadline else None,
                "notes": a.notes,
            }
            for a, m, deadline in entries
        ],
    }


def pending_quality(db, user, start, end, department_id, kind, group_by, person_id, series_key=None):
    """Count only accessible staging; unknown scope is a warning, never disclosed records."""
    own_departments = set(department_ids(db, user))
    selected_departments = (
        department_subtree_ids(db, department_id)
        if department_id and group_by == "department_people" else {department_id}
    )
    people = {p.id: p for p in db.scalars(select(User))}
    access, person_id = report_filters(db, user, department_id, person_id)
    scoped_people = {
        p.id for p in people.values()
        if p.department_id in access.departments and p.id not in access.excluded_users
    }
    pending, unassigned = 0, False
    for candidate, batch in db.execute(
        select(ImportCandidate, ImportBatch)
        .join(ImportSource, ImportSource.id == ImportCandidate.source_id)
        .join(ImportBatch, ImportBatch.id == ImportSource.batch_id)
        .where(ImportCandidate.state == "pending")
    ):
        p = candidate.corrected
        if series_key:
            # Unattributed staging cannot be counted against a named series.
            if series_key.startswith("series:") and p.get("series_id") != series_key[7:]:
                continue
            if series_key == "unassigned" or series_key.startswith("unassigned:"):
                if p.get("series_id"):
                    continue
                if series_key.startswith("unassigned:"):
                    selected = series_key[len("unassigned:"):]
                    if p.get("department_id") != (None if selected == "none" else selected):
                        continue
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
        if (owners and owners <= access.excluded_users) or (not owners and dep in access.excluded_departments):
            continue
        owners.difference_update(access.excluded_users)
        if access.mode == "self" and user.id not in owners:
            continue
        if access.mode == "departments":
            if owners and not owners.intersection(scoped_people):
                continue
            if not owners and dep not in access.departments:
                continue
        visible = access.mode != "self" or dep in own_departments or user.id in related
        if not visible:
            if not dep:
                unassigned = True
            continue
        if department_id:
            if group_by in {"person_department", "department_people"}:
                mapped = {people[i].department_id for i in owners if i in people}
                if mapped and not mapped.intersection(selected_departments):
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
        if user.is_admin and not series_key:
            file_issues += 1
    return pending, file_issues, unassigned
