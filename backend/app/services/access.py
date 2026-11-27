from fastapi import HTTPException
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Assessment,
    AuditLog,
    Department,
    Meeting,
    MeetingAttendee,
    MeetingSeries,
    MeetingVersion,
    Membership,
    Task,
    TaskOwner,
    User,
)
from app.services.role_permissions import allows, approved_meeting_filter, meeting_allowed, permission_scope


def department_ids(db: Session, user: User) -> list[str]:
    ids = list(db.scalars(select(Membership.department_id).where(Membership.user_id == user.id)))
    if user.department_id:
        ids.append(user.department_id)
    return list(set(ids))


EXECUTIVE_POSITIONS = {"执行总经理", "总经理", "董事长"}
DEPARTMENT_ASSIGNER_SUFFIXES = ("副部长", "副厂长", "部长", "厂长", "主任", "总监")
NON_ASSIGNER_SUFFIXES = (
    "副主任",
    "副总监",
    "助理部长",
    "助理厂长",
    "助理主任",
    "助理总监",
)


def can_assign_companywide(user: User) -> bool:
    return bool(user.active and user.is_admin)


def can_dispatch_companywide(db, user):
    return permission_scope(db, user, "dispatch.create").all


def has_department_assignment_role(user):
    return False  # Job titles never grant business permissions.


def assignment_department_ids(db, user):
    return permission_scope(db, user, "dispatch.create").departments


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


def meeting_department_roots(db, user):
    return permission_scope(db, user, "meetings.view").roots


def meeting_department_scope(db, user):
    return permission_scope(db, user, "meetings.view").departments


def direct_assignees(db: Session, actor: User, action="create") -> list[User]:
    """Assign to the company, or to staff in the actor's department subtree."""
    permission = permission_scope(db, actor, "dispatch." + action)
    if permission.all:
        return list(db.scalars(select(User).where(User.active.is_(True)).order_by(User.name, User.id)))
    scope = permission.departments
    if not scope and not permission.self_only:
        return []
    member_ids = select(Membership.user_id).where(Membership.department_id.in_(scope))
    return list(
        db.scalars(
            select(User)
            .where(
                User.active.is_(True),
                or_(
                    User.department_id.in_(scope),
                    User.id.in_(member_ids),
                    User.id == actor.id if permission.self_only else false(),
                ),
            )
            .order_by(User.name, User.id)
        )
    )


def can_oversee_tasks(db, actor):
    if not actor.active:
        return False
    scope = permission_scope(db, actor, "overview.view")
    return bool(
        scope.all
        or scope.self_only
        or scope.departments
        or db.scalar(
            select(Meeting.id)
            .where(or_(Meeting.host_id == actor.id, Meeting.recorder_id == actor.id))
            .limit(1)
        )
    )


def managed_users(db, actor):
    scope = permission_scope(db, actor, "overview.view")
    if scope.all:
        return select(User)
    members = select(Membership.user_id).where(Membership.department_id.in_(scope.departments))
    return select(User).where(
        or_(User.department_id.in_(scope.departments), User.id.in_(members), User.id == actor.id)
    )


def managed_tasks(db, actor):
    scope = permission_scope(db, actor, "overview.view")
    if scope.all:
        return select(Task)
    people = managed_users(db, actor).with_only_columns(User.id)
    owners = select(TaskOwner.task_id).where(TaskOwner.user_id.in_(people))
    meetings = select(Meeting.id).where(
        or_(
            Meeting.department_id.in_(scope.departments),
            Meeting.host_id == actor.id,
            Meeting.recorder_id == actor.id,
        )
    )
    return select(Task).where(
        or_(
            Task.id.in_(owners),
            Task.source_meeting_id.in_(meetings),
            Task.issuer_id == actor.id,
            Task.verifier_id == actor.id,
        )
    )


def can_manage_direct_task(db, actor, task, action="edit"):
    if not actor.active or task.source_meeting_id is not None:
        return False
    if actor.is_admin:
        return True
    if task.issuer_id != actor.id:
        return False
    scope = permission_scope(db, actor, "dispatch." + action)
    if scope.all or scope.self_only:
        return True
    owners = set(db.scalars(select(TaskOwner.user_id).where(TaskOwner.task_id == task.id)))
    allowed = {p.id for p in direct_assignees(db, actor, action)}
    return bool(owners and owners <= allowed)


def related_meetings(db: Session, user: User):
    departments = set() if user.is_admin else meeting_department_scope(db, user)
    all_meetings = False if user.is_admin else permission_scope(db, user, "meetings.view").all
    return select(Meeting).where(
        or_(
            all_meetings,
            Meeting.department_id.in_(departments),
            approved_meeting_filter(user),
            Meeting.host_id == user.id,
            Meeting.recorder_id == user.id,
            Meeting.id.in_(select(MeetingAttendee.meeting_id).where(MeetingAttendee.user_id == user.id)),
            Meeting.id.in_(select(Assessment.meeting_id).where(Assessment.user_id == user.id)),
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


def visible_series(db, user):
    query = select(MeetingSeries)
    scope = permission_scope(db, user, "series.view")
    if user.is_admin or scope.all:
        return query
    departments = set(department_ids(db, user)) | meeting_department_scope(db, user) | scope.departments
    related = visible_meetings(db, user).with_only_columns(Meeting.series_id)
    created = select(AuditLog.entity_id).where(
        AuditLog.action == "meeting_series.create", AuditLog.actor_id == user.id
    )
    return query.where(or_(
        MeetingSeries.department_id.in_(departments), MeetingSeries.id.in_(related), MeetingSeries.id.in_(created),
    ))


def startable_series_ids(db, user, series_rows):
    """Only the creator can start an empty series, except for legacy series without a creator."""
    if not user.active:
        return set()
    identifiers = {series.id for series in series_rows}
    if user.is_admin or not identifiers:
        return identifiers
    used = set(db.scalars(select(Meeting.series_id).where(Meeting.series_id.in_(identifiers)).distinct()))
    creators = dict(db.execute(select(AuditLog.entity_id, AuditLog.actor_id).where(
        AuditLog.entity_id.in_(identifiers - used), AuditLog.action == "meeting_series.create",
    )).all())
    departments = set(department_ids(db, user))
    return {
        series.id for series in series_rows if series.id not in used and (
            creators.get(series.id) == user.id if series.id in creators else (
                series.department_id in departments or allows(db, user, "meetings.create", series.department_id)
            )
        )
    }


def visible_tasks(db, user):
    if user.is_admin:
        return select(Task)
    # A recorded review is an explicit relationship, without exposing the source
    # meeting. New links are checked before saving so this cannot bootstrap access.
    meetings = list(db.scalars(visible_meetings(db, user)))
    linked = set()
    for meeting in meetings:
        if can_edit_meeting(db, user, meeting) or meeting_allowed(db, user, meeting, "view"):
            linked.update(i["task_id"] for i in meeting.draft.get("items", []) if i.get("task_id"))
    for version in db.scalars(
        select(MeetingVersion).where(MeetingVersion.meeting_id.in_([m.id for m in meetings]))
    ):
        linked.update(i["task_id"] for i in version.snapshot.get("items", []) if i.get("task_id"))
    return select(Task).where(
        or_(
            Task.source_meeting_id.in_(visible_meetings(db, user).with_only_columns(Meeting.id)),
            Task.id.in_(linked),
            Task.id.in_(managed_tasks(db, user).with_only_columns(Task.id))
            if can_oversee_tasks(db, user)
            else false(),
            Task.verifier_id == user.id,
            Task.issuer_id == user.id,
            Task.id.in_(select(TaskOwner.task_id).where(TaskOwner.user_id == user.id)),
        )
    )


def meeting_editor_department_ids(db, user):
    return permission_scope(db, user, "meetings.edit").departments


def can_confirm_meeting_results(db, user, meeting):
    return meeting_allowed(db, user, meeting, "assess")


def can_delete_meeting(db, user, meeting):
    return bool(
        user.active
        and (
            user.is_admin
            or (
                not meeting.published_version
                and meeting.has_draft
                and meeting_allowed(db, user, meeting, "delete_draft")
            )
        )
    )


def can_edit_meeting(db: Session, user: User, meeting: Meeting) -> bool:
    return meeting_allowed(db, user, meeting, "edit")


def meeting_access(db: Session, user: User, meeting: Meeting, *, edit=False, correction=False):
    if correction:
        allowed = can_confirm_meeting_results(db, user, meeting)
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


def meeting_attachment_visible(user, meeting, attachment, published_ids, can_view_draft=False):
    if attachment.kind == "original":
        return user.is_admin
    return (
        user.is_admin
        or can_view_draft
        or user.id in {meeting.host_id, meeting.recorder_id, attachment.uploader_id}
        or attachment.id in published_ids
        or (
            attachment.kind in {"minutes_pdf", "minutes_xlsx", "tasks_xlsx", "minutes_png"}
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

    allowed_department_ids = (
        set(department_ids(db, user))
        | meeting_department_scope(db, user)
        | permission_scope(db, user, "series.view").departments
    )
    allowed_user_ids = {user.id}
    allowed_series_ids: set[str] = set()

    meetings = list(db.scalars(visible_meetings(db, user)))
    for meeting in meetings:
        if can_edit_meeting(db, user, meeting):
            # Editors need names for cross-department invitees in saved drafts.
            # Reading the published meeting alone does not expose draft invitees.
            allowed_user_ids.update(meeting.draft.get("attendee_ids", []))
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
        allowed_department_ids.update(permission_scope(db, user, "dispatch.edit").departments)
        if can_dispatch_companywide(db, user):
            allowed_user_ids.update(db.scalars(select(User.id).where(User.active.is_(True))))

    # Include members of departments the user can manage/read. Memberships are
    # considered in addition to a user's primary department because WeCom users
    # may belong to multiple departments.
    allowed_user_ids.update(
        db.scalars(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.department_id.in_(allowed_department_ids), User.active.is_(True))
        )
    )
    allowed_user_ids.update(
        db.scalars(
            select(User.id).where(User.department_id.in_(allowed_department_ids), User.active.is_(True))
        )
    )

    # A full path requires every ancestor of a visible department.
    visible_department_ids = set(allowed_department_ids)
    if can_dispatch_companywide(db, user):
        visible_department_ids.update(all_by_id)
    for identifier in list(allowed_department_ids):
        current = all_by_id.get(identifier)
        visited: set[str] = set()
        while current and current.parent_id and current.parent_id not in visited:
            visited.add(current.parent_id)
            visible_department_ids.add(current.parent_id)
            current = all_by_id.get(current.parent_id)

    departments = [item for item in all_departments if item.id in visible_department_ids]
    users = list(db.scalars(select(User).where(User.id.in_(allowed_user_ids))))
    series = list(db.scalars(visible_series(db, user)))
    return {"departments": departments, "users": users, "series": series}
