from fastapi import HTTPException
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Department,
    Meeting,
    MeetingAttendee,
    MeetingEditRequest,
    MeetingVersion,
    Membership,
    Task,
    TaskOwner,
    User,
)


def department_ids(db: Session, user: User) -> list[str]:
    ids = list(db.scalars(select(Membership.department_id).where(Membership.user_id == user.id)))
    if user.department_id:
        ids.append(user.department_id)
    return list(set(ids))


EXECUTIVE_POSITIONS = {"执行总经理", "总经理", "董事长"}
DEPARTMENT_ASSIGNER_SUFFIXES = ("副部长", "副厂长", "部长", "厂长", "主任", "总监")
NON_ASSIGNER_SUFFIXES = (
    "副主任", "副总监", "助理部长", "助理厂长", "助理主任", "助理总监",
)


def can_assign_companywide(user: User) -> bool:
    return user.active and (user.is_admin or user.position.strip() in EXECUTIVE_POSITIONS)


def has_department_assignment_role(user: User) -> bool:
    position = user.position.strip()
    return (
        user.active
        and bool(position)
        and position.endswith(DEPARTMENT_ASSIGNER_SUFFIXES)
        and not position.endswith(NON_ASSIGNER_SUFFIXES)
    )


def assignment_department_ids(db: Session, user: User) -> set[str]:
    """Use the WeCom primary department and every descendant, not other memberships."""
    if not has_department_assignment_role(user) or not user.department_id:
        return set()
    return department_subtree_ids(db, user.department_id)


def department_subtree_ids(db: Session, root_id: str) -> set[str]:
    children: dict[str, set[str]] = {}
    all_ids: set[str] = set()
    for identifier, parent_id in db.execute(select(Department.id, Department.parent_id)):
        all_ids.add(identifier)
        if parent_id:
            children.setdefault(parent_id, set()).add(identifier)
    if root_id not in all_ids:
        return set()
    scope = {root_id}
    pending = [root_id]
    while pending:
        for identifier in children.get(pending.pop(), ()):
            if identifier not in scope:
                scope.add(identifier)
                pending.append(identifier)
    return scope


def direct_assignees(db: Session, actor: User) -> list[User]:
    """Assign to the company, or to staff in the actor's department subtree."""
    if can_assign_companywide(actor):
        return list(db.scalars(select(User).where(User.active.is_(True)).order_by(User.name, User.id)))
    scope = assignment_department_ids(db, actor)
    if not scope:
        return []
    member_ids = select(Membership.user_id).where(Membership.department_id.in_(scope))
    return list(
        db.scalars(
            select(User)
            .where(User.active.is_(True), or_(User.department_id.in_(scope), User.id.in_(member_ids)))
            .order_by(User.name, User.id)
        )
    )


def can_oversee_tasks(db: Session, actor: User) -> bool:
    return can_assign_companywide(actor) or bool(assignment_department_ids(db, actor))


def managed_users(db: Session, actor: User):
    """Include inactive members for task handover, within the current organization scope."""
    if can_assign_companywide(actor):
        return select(User)
    scope = assignment_department_ids(db, actor)
    if not scope:
        return select(User).where(false())
    members = select(Membership.user_id).where(Membership.department_id.in_(scope))
    return select(User).where(or_(User.department_id.in_(scope), User.id.in_(members)))


def managed_tasks(db: Session, actor: User):
    if can_assign_companywide(actor):
        return select(Task)
    owners = select(TaskOwner.task_id).where(
        TaskOwner.user_id.in_(managed_users(db, actor).with_only_columns(User.id))
    )
    return select(Task).where(Task.id.in_(owners))


def can_manage_direct_task(db: Session, actor: User, task: Task) -> bool:
    return actor.active and task.source_meeting_id is None and (
        actor.is_admin or (task.issuer_id == actor.id and bool(direct_assignees(db, actor)))
    )


def related_meetings(db: Session, user: User):
    return select(Meeting).where(
        or_(
            Meeting.department_id.in_(department_ids(db, user)),
            Meeting.host_id == user.id,
            Meeting.recorder_id == user.id,
            Meeting.id.in_(select(MeetingAttendee.meeting_id).where(MeetingAttendee.user_id == user.id)),
            Meeting.id.in_(
                select(Task.source_meeting_id).where(
                    or_(
                        Task.verifier_id == user.id,
                        Task.id.in_(select(TaskOwner.task_id).where(TaskOwner.user_id == user.id)),
                    )
                )
            ),
        )
    )


def visible_meetings(db: Session, user: User):
    return select(Meeting) if user.is_admin else related_meetings(db, user)


def visible_tasks(db: Session, user: User):
    query = select(Task)
    if can_assign_companywide(user):
        return query
    return query.where(
        or_(
            (Task.source_meeting_id.is_not(None) & Task.department_id.in_(department_ids(db, user))),
            Task.verifier_id == user.id,
            Task.issuer_id == user.id,
            Task.id.in_(managed_tasks(db, user).with_only_columns(Task.id)),
            Task.id.in_(select(TaskOwner.task_id).where(TaskOwner.user_id == user.id)),
            Task.source_meeting_id.in_(
                select(Meeting.id).where(or_(Meeting.host_id == user.id, Meeting.recorder_id == user.id))
            ),
            Task.source_meeting_id.in_(
                select(Meeting.id)
                .join(MeetingEditRequest, MeetingEditRequest.meeting_id == Meeting.id)
                .where(
                    MeetingEditRequest.applicant_id == user.id,
                    MeetingEditRequest.state == "approved",
                    MeetingEditRequest.host_id == Meeting.host_id,
                    MeetingEditRequest.permission_generation == Meeting.permission_generation,
                )
            ),
        )
    )


def can_edit_meeting(db: Session, user: User, meeting: Meeting) -> bool:
    if user.is_admin or user.id in {meeting.host_id, meeting.recorder_id}:
        return True
    return db.scalar(
        select(MeetingEditRequest.id).where(
            MeetingEditRequest.meeting_id == meeting.id,
            MeetingEditRequest.applicant_id == user.id,
            MeetingEditRequest.host_id == meeting.host_id,
            MeetingEditRequest.permission_generation == meeting.permission_generation,
            MeetingEditRequest.state == "approved",
        )
    ) is not None


def meeting_access(db: Session, user: User, meeting: Meeting, *, edit=False, correction=False):
    if correction:
        allowed = user.is_admin or user.id == meeting.host_id
    elif edit:
        allowed = can_edit_meeting(db, user, meeting)
    else:
        allowed = db.scalar(visible_meetings(db, user).where(Meeting.id == meeting.id)) is not None
    if not allowed:
        raise HTTPException(403, "无权访问此会议")


def task_access(db: Session, user: User, task: Task):
    if db.scalar(visible_tasks(db, user).where(Task.id == task.id)) is None:
        raise HTTPException(403, "无权访问此任务")


def visible_meeting_attachment_ids(db, user, meeting):
    """Readers can access files included in published minutes, not unreleased revision uploads."""
    identifiers = set()
    for version in db.scalars(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id)):
        identifiers.update(version.snapshot.get("attachment_ids", []))
    return identifiers


def meeting_attachment_visible(user, meeting, attachment, published_ids):
    if attachment.kind == "original":
        return user.is_admin
    return (
        user.is_admin
        or user.id in {meeting.host_id, meeting.recorder_id, attachment.uploader_id}
        or attachment.id in published_ids
        or (
            attachment.kind in {"minutes_pdf", "minutes_xlsx", "tasks_xlsx"}
            and attachment.meeting_version is not None
            and 0 < attachment.meeting_version <= meeting.published_version
        )
    )


def visible_directory(db: Session, user: User):
    """Return only directory entries needed by a member's authorized work.

    Administrators need the full directory for matching, role management and
    historical correction. Ordinary members must not receive unrelated
    company users or departments merely because the global selector is loaded.
    Related inactive users remain visible so historical records and existing
    task ownership can still be displayed.
    """
    from app.models import MeetingSeries

    all_departments = list(db.scalars(select(Department)))
    all_by_id = {department.id: department for department in all_departments}
    if user.is_admin:
        return {
            "departments": all_departments,
            "users": list(db.scalars(select(User))),
            "series": list(db.scalars(select(MeetingSeries))),
        }

    allowed_department_ids = set(department_ids(db, user))
    allowed_user_ids = {user.id}
    allowed_series_ids: set[str] = set()

    meetings = list(db.scalars(visible_meetings(db, user)))
    for meeting in meetings:
        allowed_user_ids.update(
            identifier for identifier in (meeting.host_id, meeting.recorder_id) if identifier
        )
        allowed_department_ids.update(identifier for identifier in (meeting.department_id,) if identifier)
        allowed_series_ids.update(identifier for identifier in (meeting.series_id,) if identifier)
        allowed_user_ids.update(
            db.scalars(select(MeetingAttendee.user_id).where(MeetingAttendee.meeting_id == meeting.id))
        )

    tasks = list(db.scalars(visible_tasks(db, user)))
    for task in tasks:
        allowed_user_ids.add(task.verifier_id)
        if task.issuer_id:
            allowed_user_ids.add(task.issuer_id)
        allowed_department_ids.update(identifier for identifier in (task.department_id,) if identifier)
        allowed_series_ids.update(identifier for identifier in (task.series_id,) if identifier)
        allowed_user_ids.update(db.scalars(select(TaskOwner.user_id).where(TaskOwner.task_id == task.id)))

    # Direct dispatch requires the full assignable directory, including staff whose
    # department has no meeting yet. Preserve the meeting-series visibility boundary.
    if can_assign_companywide(user):
        allowed_department_ids.update(all_by_id)
        allowed_user_ids.update(db.scalars(select(User.id).where(User.active.is_(True))))
    else:
        allowed_department_ids.update(assignment_department_ids(db, user))

    # Include members of departments the user can manage/read. Memberships are
    # considered in addition to a user's primary department because WeCom users
    # may belong to multiple departments.
    allowed_user_ids.update(
        db.scalars(select(Membership.user_id).where(Membership.department_id.in_(allowed_department_ids)))
    )
    allowed_user_ids.update(db.scalars(select(User.id).where(User.department_id.in_(allowed_department_ids))))

    # A full path requires every ancestor of a visible department.
    visible_department_ids = set(allowed_department_ids)
    for identifier in list(allowed_department_ids):
        current = all_by_id.get(identifier)
        visited: set[str] = set()
        while current and current.parent_id and current.parent_id not in visited:
            visited.add(current.parent_id)
            visible_department_ids.add(current.parent_id)
            current = all_by_id.get(current.parent_id)

    departments = [item for item in all_departments if item.id in visible_department_ids]
    users = list(db.scalars(select(User).where(User.id.in_(allowed_user_ids))))
    # Series names are shared catalog labels; meeting contents retain their own access rules.
    series = list(db.scalars(select(MeetingSeries)))
    return {"departments": departments, "users": users, "series": series}
