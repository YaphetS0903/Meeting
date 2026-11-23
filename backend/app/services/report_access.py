"""Report-only authorization; it never grants meeting editing or system administration."""

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import Assessment, Department, Meeting, MeetingSeries, User
from app.services.access import visible_meetings
from app.services.employee_accounts import is_employee_account
from app.services.role_permissions import permission_scope


@dataclass
class ReportAccess:
    mode: str
    roots: set[str]
    departments: set[str]
    excluded_departments: set[str] = field(default_factory=set)
    excluded_users: set[str] = field(default_factory=set)
    include_user_id: str | None = None

    def people(self):
        query = select(User).where(User.id.not_in(self.excluded_users))
        if self.mode == "departments":
            query = query.where(
                or_(User.department_id.in_(self.departments), User.id == self.include_user_id)
            )
        return query


def report_exclusions(db: Session) -> tuple[set[str], set[str]]:
    """HR's service accounts and non-working branches are not report subjects.

    Resolve the organization tree each time so directory sync covers new accounts
    and descendants without deleting users or changing their operational access.
    Only primary departments determine report membership, as in report filters.
    """
    departments = list(db.scalars(select(Department)))
    children: dict[str, set[str]] = {}
    for department in departments:
        if department.parent_id:
            children.setdefault(department.parent_id, set()).add(department.id)

    def descendants(roots):
        scope, pending = set(), list(roots)
        while pending:
            identifier = pending.pop()
            if identifier in scope:
                continue
            scope.add(identifier)
            pending.extend(children.get(identifier, ()))
        return scope

    hr = descendants(d.id for d in departments if d.name.strip() == "人力资源部")
    excluded = descendants(
        d.id
        for d in departments
        if d.id in hr
        and d.name.strip().replace("(", "（").replace(")", "）")
        in {"固定非在岗", "非在岗（待离职）", "信息化"}
    )
    users = set(
        db.scalars(
            select(User.id).where(
                or_(
                    User.department_id.in_(excluded),
                    and_(User.department_id.in_(hr), func.trim(User.name) == "董事长信箱"),
                )
            )
        )
    )
    users.update(
        identifier for identifier, name, position in db.execute(select(User.id, User.name, User.position))
        if not is_employee_account(name, position)
    )
    return excluded, users


def report_access(db: Session, user: User) -> ReportAccess:
    if not user.active:
        raise HTTPException(403, "账号已停用")
    excluded_departments, excluded_users = report_exclusions(db)
    if user.is_admin:
        return ReportAccess(
            "all", set(), set(db.scalars(select(Department.id))), excluded_departments, excluded_users
        )
    scope = permission_scope(db, user, "reports.view")
    return ReportAccess(
        "all" if scope.all else "departments" if scope.departments else "self",
        scope.roots,
        scope.departments,
        excluded_departments,
        excluded_users,
        user.id,
    )


def can_view_senior_reports(db, user):
    scope = permission_scope(db, user, "senior_reports.view")
    return bool(scope.all or scope.self_only or scope.departments)


def senior_report_access(db, user):
    if not can_view_senior_reports(db, user):
        raise HTTPException(403, "没有中高层会议统计权限")
    scope = permission_scope(db, user, "senior_reports.view")
    excluded_departments, excluded_users = report_exclusions(db)
    return ReportAccess(
        "all" if scope.all else "departments" if scope.departments else "self",
        scope.roots,
        scope.departments,
        excluded_departments,
        excluded_users,
        user.id if scope.self_only else None,
    )


def report_export_access(db, user, department_id=None, person_id=None, *, senior_only=False):
    from app.models import User

    view = senior_report_access(db, user) if senior_only else report_access(db, user)
    report_filters(db, user, department_id, person_id, senior_only=senior_only)
    scope = permission_scope(db, user, ("senior_reports" if senior_only else "reports") + ".export")
    if not senior_only and person_id == user.id and not department_id:
        return
    if (
        view.mode == "self"
        and (not senior_only or scope.self_only)
        and (not person_id or person_id == user.id)
        and not department_id
    ):
        return
    if scope.all:
        return
    if department_id:
        if department_id not in scope.departments:
            raise HTTPException(403, "没有导出此部门报表的权限")
        if not person_id:
            return
    if person_id:
        person = db.get(User, person_id)
        if person and person.department_id in scope.departments:
            return
        raise HTTPException(403, "没有导出此人员报表的权限")
    if not view.departments or not view.departments <= scope.departments:
        raise HTTPException(403, "当前统计范围包含无权导出的部门，请缩小筛选范围")
    return


def report_filters(db, user, department_id=None, person_id=None, *, senior_only=False):
    access = senior_report_access(db, user) if senior_only else report_access(db, user)
    if access.mode == "self":
        if person_id and person_id != user.id:
            raise HTTPException(403, "只能查看本人的任务统计")
        return access, user.id
    if access.mode == "departments":
        if department_id and department_id not in access.departments:
            raise HTTPException(403, "无权查看此部门的报表")
        if person_id and not db.scalar(access.people().where(User.id == person_id)):
            raise HTTPException(403, "无权查看此人员的报表")
    return access, person_id


def report_directory(db, user, *, senior_only=False, leader_id=None):
    access = senior_report_access(db, user) if senior_only else report_access(db, user)
    from app.services.senior_meetings import SENIOR_PARTICIPANTS, validate_senior_leader
    validate_senior_leader(leader_id, senior_only=senior_only)
    departments = list(db.scalars(select(Department)))
    if access.mode == "self":
        people = list(db.scalars(access.people().where(User.id == user.id)))
        visible = set()
    else:
        people = list(db.scalars(access.people().order_by(User.name, User.id)))
        visible = access.departments - access.excluded_departments
    by_id = {d.id: d for d in departments}
    # Ancestors supply company labels, but are never selectable without authorization.
    for identifier in list(visible):
        current = by_id.get(identifier)
        visited = set()
        while current and current.parent_id and current.parent_id not in visited:
            visited.add(current.parent_id)
            visible.add(current.parent_id)
            current = by_id.get(current.parent_id)
    return {
        "mode": access.mode,
        "can_view_senior_meetings": can_view_senior_reports(db, user),
        "can_export": user.is_admin
        or permission_scope(
            db, user, ("senior_reports" if senior_only else "reports") + ".export"
        ).all
        or (
            access.mode == "self"
            and (not senior_only or permission_scope(db, user, "senior_reports.export").self_only)
        )
        or bool(
            permission_scope(
                db, user, ("senior_reports" if senior_only else "reports") + ".export"
            ).departments
        ),
        "department_ids": sorted(access.departments - access.excluded_departments),
        "root_department_ids": sorted(access.roots - access.excluded_departments),
        "departments": [
            {"id": d.id, "name": d.name, "parent_id": d.parent_id} for d in departments if d.id in visible
        ],
        "users": [
            {"id": p.id, "name": p.name, "department_id": p.department_id, "active": p.active} for p in people
        ],
        "topics": report_topics(db, user, access, senior_only=senior_only, leader_id=leader_id),
        **({"senior_leaders": [{"id": identifier, "name": name} for identifier, name in SENIOR_PARTICIPANTS.items()]} if senior_only else {}),
    }


def report_topics(db, user, access, *, senior_only=False, leader_id=None):
    """Series available in authorized meetings or the authorized employees' reports.

    Department filters count those employees across meetings. Include series from
    their published assessments even when they do not grant access to the meeting.
    Keep options independent of the selected month so zero-result topics are usable.
    """
    topics = {}
    if senior_only:
        from app.services.senior_meetings import senior_meeting_ids

        senior_ids = senior_meeting_ids(db, leader_id=leader_id)

    def include(series_id, name, department_id):
        key = f"series:{series_id}" if series_id else "unassigned"
        item = topics.setdefault(key, {"key": key, "name": name or "未归入系列", "department_ids": set()})
        if department_id:
            item["department_ids"].add(department_id)

    if access.mode != "self":
        meetings = visible_meetings(db, user).where(
            or_(Meeting.department_id.is_(None), Meeting.department_id.not_in(access.excluded_departments))
        )
        if access.mode == "departments":
            meetings = meetings.where(Meeting.department_id.in_(access.departments))
        if senior_only:
            meetings = meetings.where(Meeting.id.in_(senior_ids))
        for series_id, name, department_id in db.execute(
            meetings.outerjoin(MeetingSeries, MeetingSeries.id == Meeting.series_id)
            .with_only_columns(Meeting.series_id, MeetingSeries.name, Meeting.department_id)
            .distinct()
        ):
            include(series_id, name, department_id)

    assessments = (
        select(Meeting.series_id, MeetingSeries.name, User.department_id)
        .select_from(Assessment)
        .join(Meeting, Meeting.id == Assessment.meeting_id)
        .join(User, User.id == Assessment.user_id)
        .outerjoin(MeetingSeries, MeetingSeries.id == Meeting.series_id)
        .where(Meeting.published_version == Assessment.meeting_version, User.id.not_in(access.excluded_users))
    )
    if access.mode == "self":
        assessments = assessments.where(
            Assessment.user_id == user.id,
            Meeting.id.in_(visible_meetings(db, user).with_only_columns(Meeting.id)),
        )
    elif access.mode == "departments":
        assessments = assessments.where(User.department_id.in_(access.departments))
    if senior_only:
        assessments = assessments.where(Meeting.id.in_(senior_ids))
    for series_id, name, department_id in db.execute(assessments.distinct()):
        include(series_id, name, department_id)

    return [
        {**item, "department_ids": sorted(item["department_ids"])}
        for item in sorted(topics.values(), key=lambda item: (item["name"], item["key"]))
    ]
