"""Business roles pair each operation with its own scope; positions grant nothing."""

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import (
    BusinessRole,
    BusinessRoleAssignment,
    Department,
    MeetingAttendee,
    MeetingEditRequest,
    Task,
    TaskOwner,
)

MODULES = [
    ("series", "会议系列", [("view", "查看系列"), ("create", "新建系列"), ("rename", "修改系列名称")]),
    (
        "meetings",
        "会议任务单",
        [
            ("view", "查看会议及草稿"),
            ("create", "新建会议"),
            ("import", "导入历史任务单"),
            ("edit", "编辑任务单"),
            ("assess", "修改完成结果"),
            ("preview", "预览任务单"),
            ("publish", "发布会议"),
            ("revise", "创建修订"),
            ("cancel_revision", "取消修订"),
            ("continue", "持续新建"),
            ("rename", "修改会议名称"),
            ("move", "调整所属系列"),
            ("delete_draft", "删除未发布草稿"),
        ],
    ),
    (
        "files",
        "会议文件",
        [
            ("download", "下载会议文件"),
            ("upload", "上传附件"),
            ("delete", "删除附件"),
            ("copy", "复制预览图片"),
        ],
    ),
    ("overview", "任务总览", [("view", "查看总览和详情")]),
    (
        "dispatch",
        "直接派发任务",
        [
            ("create", "派发任务"),
            ("view", "查看本人派发记录"),
            ("edit", "修改本人派发任务"),
            ("transfer", "转交本人派发任务"),
            ("withdraw", "撤回本人派发任务"),
        ],
    ),
    ("reports", "常规统计报表", [("view", "查看常规统计"), ("export", "导出常规统计")]),
    ("senior_reports", "中高层会议统计", [("view", "查看中高层统计"), ("export", "导出中高层统计")]),
]
CATALOG = [
    {
        "key": key,
        "name": name,
        "actions": [{"key": a, "name": n} for a, n in actions],
        "scopes": ["self", "department", "selected", "all"],
    }
    for key, name, actions in MODULES
]
ACTION_KEYS = {key: {a for a, _ in actions} for key, _, actions in MODULES}
OWN_MEETING_ACTIONS = {
    "view",
    "edit",
    "assess",
    "preview",
    "publish",
    "revise",
    "cancel_revision",
    "continue",
    "delete_draft",
    "rename",
    "move",
}
APPROVED_MEETING_ACTIONS = {"view", "edit", "preview", "publish", "revise", "cancel_revision", "continue"}


def approved_meeting_filter(user):
    """A retained approval belongs to one host and permission generation only."""
    from app.models import Meeting

    return (
        select(MeetingEditRequest.id)
        .where(
            MeetingEditRequest.meeting_id == Meeting.id,
            MeetingEditRequest.applicant_id == user.id,
            MeetingEditRequest.host_id == Meeting.host_id,
            MeetingEditRequest.permission_generation == Meeting.permission_generation,
            MeetingEditRequest.state == "approved",
        )
        .exists()
    )


def has_meeting_approval(db, user, meeting):
    from app.models import Meeting

    if not user.active:
        return False
    cache = db.info.setdefault("business_approval_cache", {})
    key = (user.id, meeting.id, meeting.host_id, meeting.permission_generation)
    if key not in cache:
        cache[key] = bool(
            db.scalar(select(Meeting.id).where(Meeting.id == meeting.id, approved_meeting_filter(user)))
        )
    return cache[key]


@dataclass
class Scope:
    all: bool = False
    self_only: bool = False
    roots: set[str] = field(default_factory=set)
    departments: set[str] = field(default_factory=set)


def subtree(db, roots):
    children = {}
    existing = set()
    rows = db.info.get("business_department_cache")
    if rows is None:
        rows = list(db.execute(select(Department.id, Department.parent_id)))
        db.info["business_department_cache"] = rows
    for identifier, parent in rows:
        existing.add(identifier)
        children.setdefault(parent, set()).add(identifier)
    result, pending = set(), list(set(roots) & existing)
    while pending:
        identifier = pending.pop()
        if identifier not in result:
            result.add(identifier)
            pending.extend(children.get(identifier, ()))
    return result


def assigned_roles(db, user):
    if not user.active:
        return []
    cache = db.info.setdefault("business_role_cache", {})
    if user.id not in cache:
        cache[user.id] = list(
            db.scalars(
                select(BusinessRole)
                .join(BusinessRoleAssignment, BusinessRoleAssignment.role_id == BusinessRole.id)
                .where(BusinessRoleAssignment.user_id == user.id, BusinessRole.active.is_(True))
            )
        )
    return [r for r in cache[user.id] if r.active]


def permission_scope(db, user, permission):
    scope = Scope()
    if not user.active:
        return scope
    module, action = permission.split(".")
    if module not in ACTION_KEYS or (action not in ACTION_KEYS[module] and action != "view"):
        return scope
    if user.is_admin:
        scope.all = True
        scope.departments = all_department_ids(db)
        return scope
    for role in assigned_roles(db, user):
        for rule in role.rules:
            if rule["module"] != module:
                continue
            # Operations imply reading within precisely the same module scope.
            if action not in rule["actions"] and not (action == "view" and rule["actions"]):
                continue
            mode = rule["scope"]
            if mode == "all":
                scope.all = True
            elif mode == "self":
                scope.self_only = True
            elif mode == "department" and user.department_id:
                scope.roots.add(user.department_id)
            elif mode == "selected":
                scope.roots.update(rule["department_ids"])
    scope.departments = all_department_ids(db) if scope.all else subtree(db, scope.roots)
    return scope


def all_department_ids(db):
    rows = db.info.get("business_department_cache")
    if rows is None:
        rows = list(db.execute(select(Department.id, Department.parent_id)))
        db.info["business_department_cache"] = rows
    return {identifier for identifier, _ in rows}


def allows(db, user, permission, department_id=None, *, related=False):
    scope = permission_scope(db, user, permission)
    return (
        scope.all
        or (department_id is not None and department_id in scope.departments)
        or (related and scope.self_only)
    )


def require(db, user, permission, department_id=None, *, related=False):
    if not allows(db, user, permission, department_id, related=related):
        raise HTTPException(403, "当前角色没有此操作或数据范围的权限")


def meeting_allowed(db, user, meeting, action):
    if not user.active:
        return False
    if action == "delete_draft" and meeting.published_version:
        return bool(user.is_admin)
    own = user.id in {meeting.host_id, meeting.recorder_id}
    return bool(
        (own and action in OWN_MEETING_ACTIONS)
        or (action in APPROVED_MEETING_ACTIONS and has_meeting_approval(db, user, meeting))
        or allows(db, user, "meetings." + action, meeting.department_id, related=own)
    )


def meeting_require(db, user, meeting, action):
    if not meeting_allowed(db, user, meeting, action):
        raise HTTPException(403, "当前角色无权执行此会议操作")


def file_allowed(db, user, meeting, action):
    own = user.id in {meeting.host_id, meeting.recorder_id} or has_meeting_approval(db, user, meeting)
    related = own
    if action in {"download", "copy"} and not related:
        related = bool(
            db.scalar(
                select(MeetingAttendee.meeting_id).where(
                    MeetingAttendee.meeting_id == meeting.id, MeetingAttendee.user_id == user.id
                )
            )
            or db.scalar(
                select(Task.id)
                .where(
                    Task.source_meeting_id == meeting.id,
                    (Task.verifier_id == user.id)
                    | Task.id.in_(select(TaskOwner.task_id).where(TaskOwner.user_id == user.id)),
                )
                .limit(1)
            )
        )
    return bool(
        user.active
        and ((own or related) or allows(db, user, "files." + action, meeting.department_id, related=related))
    )


def capabilities(db, user):
    result = {}
    for module in ACTION_KEYS:
        result[module] = {}
        for action in ACTION_KEYS[module]:
            scope = permission_scope(db, user, module + "." + action)
            result[module][action] = {
                "all": scope.all,
                "self": scope.self_only,
                "department_ids": sorted(scope.departments),
            }
    return result


@event.listens_for(Session, "after_flush")
def clear_role_cache_after_flush(db, context):
    db.info.pop("business_role_cache", None)
    db.info.pop("business_approval_cache", None)
    db.info.pop("business_department_cache", None)


@event.listens_for(Session, "after_transaction_end")
def clear_role_cache_after_transaction(db, transaction):
    db.info.pop("business_role_cache", None)
    db.info.pop("business_approval_cache", None)
    db.info.pop("business_department_cache", None)
