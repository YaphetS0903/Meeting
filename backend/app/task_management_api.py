"""Task progress within the manager's position scope, with audited handover and withdrawal."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db, locked_get
from app.core.security import current_user
from app.models import (
    CompletionRequest,
    Department,
    Meeting,
    MeetingSeries,
    Membership,
    Task,
    TaskOwner,
    TaskVersion,
    User,
    now,
)
from app.services.access import (
    assignment_department_ids,
    can_manage_direct_task,
    can_oversee_tasks,
    department_subtree_ids,
    direct_assignees,
    managed_tasks,
    managed_users,
)
from app.services.business import (
    active_user,
    audit,
    enqueue,
    enqueue_native_todo,
    owner_ids,
    task_snapshot,
)
from app.services.role_permissions import permission_scope
from app.services.task_inbox import published_task_contexts, task_status_expression

router = APIRouter(prefix="/api")


def require_overview(db, actor):
    if not can_oversee_tasks(db, actor):
        raise HTTPException(403, "当前岗位没有任务总览权限")


def overview_users(db, actor):
    task_owners = select(TaskOwner.user_id).where(
        TaskOwner.task_id.in_(managed_tasks(db, actor).with_only_columns(Task.id))
    )
    return select(User).where(
        or_(
            User.id.in_(managed_users(db, actor).with_only_columns(User.id)),
            User.id.in_(task_owners),
        )
    )


def overview_department_ids(db, actor):
    tasks = managed_tasks(db, actor)
    people = overview_users(db, actor).with_only_columns(User.id)
    return (
        assignment_department_ids(db, actor)
        | set(db.scalars(tasks.with_only_columns(Task.department_id).where(Task.department_id.is_not(None))))
        | set(
            db.scalars(
                select(Meeting.department_id).where(
                    Meeting.id.in_(tasks.with_only_columns(Task.source_meeting_id)),
                    Meeting.department_id.is_not(None),
                )
            )
        )
        | set(
            db.scalars(
                select(User.department_id).where(
                    User.id.in_(people),
                    User.department_id.is_not(None),
                )
            )
        )
        | set(db.scalars(select(Membership.department_id).where(Membership.user_id.in_(people))))
    )


def task_series_expression():
    # Older tasks may only have the original meeting link.
    return func.coalesce(
        Task.series_id,
        select(Meeting.series_id).where(Meeting.id == Task.source_meeting_id).scalar_subquery(),
    )


def overview_series(db, actor):
    identifiers = managed_tasks(db, actor).with_only_columns(task_series_expression()).where(
        Task.source_meeting_id.is_not(None)
    )
    return select(MeetingSeries).where(MeetingSeries.id.in_(identifiers))


@router.get("/task-overview/options")
def overview_options(db: Session = Depends(get_db), actor: User = Depends(current_user)):
    require_overview(db, actor)
    all_staff = permission_scope(db, actor, "overview.view").all
    departments = select(Department).order_by(Department.name)
    if not all_staff:
        departments = departments.where(Department.id.in_(overview_department_ids(db, actor)))
    return {
        "scope": "all" if permission_scope(db, actor, "overview.view").all else "department",
        "users": [
            {"id": p.id, "name": p.name, "active": p.active, "department_id": p.department_id}
            for p in db.scalars(overview_users(db, actor).order_by(User.name, User.id))
        ],
        "departments": [{"id": d.id, "name": d.name} for d in db.scalars(departments)],
        "series": [
            {"id": series.id, "name": series.name}
            for series in db.scalars(overview_series(db, actor).order_by(MeetingSeries.name, MeetingSeries.id))
        ],
    }


@router.get("/task-overview")
def overview(
    q: str = "",
    person_id: str | None = None,
    department_id: str | None = None,
    source: Literal["meeting", "direct"] | None = None,
    series_id: str | None = None,
    status: Literal[
        "已完成", "未完成", "未到期", "不统计", "关闭", "待验证", "已撤回", "到期未验证", "未到期验证"
    ]
    | None = None,
    start: date | None = None,
    end: date | None = None,
    no_deadline: bool = False,
    issued_by_me: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: User = Depends(current_user),
):
    require_overview(db, actor)
    if start and end and start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if series_id and db.scalar(overview_series(db, actor).where(MeetingSeries.id == series_id)) is None:
        raise HTTPException(403, "会议系列不在当前任务总览范围")
    people_query = overview_users(db, actor)
    if person_id and db.scalar(people_query.where(User.id == person_id)) is None:
        raise HTTPException(403, "人员不在当前任务总览范围")
    if department_id:
        if not permission_scope(
            db, actor, "overview.view"
        ).all and department_id not in overview_department_ids(db, actor):
            raise HTTPException(403, "部门不在当前任务总览范围")
        selected_departments = department_subtree_ids(db, department_id)
        if not selected_departments:
            raise HTTPException(422, "所选部门不存在")
        people_query = people_query.where(
            or_(
                User.department_id.in_(selected_departments),
                User.id.in_(
                    select(Membership.user_id).where(Membership.department_id.in_(selected_departments))
                ),
            )
        )
    query = managed_tasks(db, actor)
    if series_id:
        query = query.where(Task.source_meeting_id.is_not(None), task_series_expression() == series_id)
    if issued_by_me:
        query = query.where(Task.issuer_id == actor.id)
    if department_id:
        query = query.where(
            or_(
                Task.department_id.in_(selected_departments),
                Task.source_meeting_id.in_(
                    select(Meeting.id).where(Meeting.department_id.in_(selected_departments))
                ),
                Task.id.in_(
                    select(TaskOwner.task_id).where(
                        TaskOwner.user_id.in_(people_query.with_only_columns(User.id))
                    )
                ),
            )
        )
    if person_id:
        query = query.where(Task.id.in_(select(TaskOwner.task_id).where(TaskOwner.user_id == person_id)))
    if q.strip():
        query = query.where(or_(Task.content.ilike(f"%{q.strip()}%"), Task.code.ilike(f"%{q.strip()}%")))
    if source:
        query = query.where(
            Task.source_meeting_id.is_(None) if source == "direct" else Task.source_meeting_id.is_not(None)
        )
    if no_deadline:
        query = query.where(Task.due_date.is_(None))
    else:
        if start:
            query = query.where(Task.due_date >= start)
        if end:
            query = query.where(Task.due_date <= end)
    pending = (
        select(CompletionRequest.id)
        .where(
            CompletionRequest.task_id == Task.id,
            CompletionRequest.state == "pending",
            CompletionRequest.task_version == Task.version,
            Task.closed_at.is_(None),
        )
        .exists()
    )
    contexts = published_task_contexts(db, actor, query)
    live_status = task_status_expression(contexts)
    if status == "待验证":
        query = query.where(pending)
    elif status == "已撤回":
        query = query.where(Task.withdrawn_at.is_not(None))
    elif status:
        query = query.where(live_status == status, Task.withdrawn_at.is_(None))
    summary_query = select(
        func.count(Task.id).label("total"),
        func.coalesce(func.sum(case((pending, 1), else_=0)), 0).label("pending"),
        func.coalesce(
            func.sum(case(((Task.closed_at.is_(None) & (live_status == "未完成")), 1), else_=0)), 0
        ).label("overdue"),
        func.coalesce(
            func.sum(case(((Task.closed_at.is_(None) & (live_status == "到期未验证")), 1), else_=0)), 0
        ).label("unverified_due"),
        func.coalesce(
            func.sum(case(((Task.status == "关闭") & Task.withdrawn_at.is_(None), 1), else_=0)), 0
        ).label("closed"),
        func.coalesce(func.sum(case((Task.withdrawn_at.is_not(None), 1), else_=0)), 0).label("withdrawn"),
    )
    if query.whereclause is not None:
        summary_query = summary_query.where(query.whereclause)
    summary = db.execute(summary_query).one()._mapping
    # Match department filtering: owners' primary and additional departments.
    # Aggregate before joining so shared tasks remain one row across page boundaries.
    staff_departments = (
        select(User.id.label("user_id"), User.department_id.label("department_id"))
        .where(User.department_id.is_not(None))
        .union(select(Membership.user_id, Membership.department_id))
        .subquery()
    )
    task_departments = (
        select(TaskOwner.task_id, Department.id, Department.name)
        .join(staff_departments, staff_departments.c.user_id == TaskOwner.user_id)
        .join(Department, Department.id == staff_departments.c.department_id)
        .distinct()
        .subquery()
    )
    department_order = (
        select(task_departments.c.task_id, func.min(task_departments.c.name).label("name"))
        .group_by(task_departments.c.task_id)
        .subquery()
    )
    person_order = (
        select(TaskOwner.task_id, func.min(User.name).label("name"))
        .join(User, User.id == TaskOwner.user_id)
        .group_by(TaskOwner.task_id)
        .subquery()
    )
    tasks = list(
        db.scalars(
            query.outerjoin(department_order, department_order.c.task_id == Task.id)
            .outerjoin(person_order, person_order.c.task_id == Task.id)
            .order_by(
                case((department_order.c.name.is_(None), 1), else_=0),
                department_order.c.name,
                case((Task.due_date.is_(None), 1), else_=0),
                Task.due_date,
                case((person_order.c.name.is_(None), 1), else_=0),
                person_order.c.name,
                Task.id,
            )
            .offset(offset)
            .limit(limit)
        )
    )
    identifiers = [t.id for t in tasks]
    departments: dict[str, list[str]] = {}
    if identifiers:
        for task_id, name in db.execute(
            select(task_departments.c.task_id, task_departments.c.name)
            .where(task_departments.c.task_id.in_(identifiers))
            .distinct()
            .order_by(task_departments.c.name)
        ):
            departments.setdefault(task_id, []).append(name)
    owners: dict[str, list[User]] = {}
    if identifiers:
        for task_id, person in db.execute(
            select(TaskOwner.task_id, User)
            .join(User, User.id == TaskOwner.user_id)
            .where(TaskOwner.task_id.in_(identifiers))
            .order_by(User.name, User.id)
        ):
            owners.setdefault(task_id, []).append(person)
    user_ids = {i for t in tasks for i in (t.issuer_id, t.verifier_id) if i}
    users = {p.id: p for p in db.scalars(select(User).where(User.id.in_(user_ids)))}
    meeting_ids = {t.source_meeting_id for t in tasks if t.source_meeting_id}
    meetings = {m.id: m for m in db.scalars(select(Meeting).where(Meeting.id.in_(meeting_ids)))}
    pending_ids = (
        set(
            db.scalars(
                select(CompletionRequest.task_id)
                .join(Task, Task.id == CompletionRequest.task_id)
                .where(
                    CompletionRequest.task_id.in_(identifiers),
                    CompletionRequest.state == "pending",
                    CompletionRequest.task_version == Task.version,
                )
            )
        )
        if identifiers
        else set()
    )
    status_labels = (
        dict(db.execute(select(Task.id, live_status).where(Task.id.in_(identifiers))).all())
        if identifiers
        else {}
    )
    return {
        "summary": dict(summary),
        "total": summary["total"],
        "items": [
            {
                "id": t.id,
                "code": t.code,
                "content": t.content,
                "due_date": t.due_date,
                "status": "已撤回" if t.withdrawn_at else status_labels[t.id],
                "pending": t.id in pending_ids and t.closed_at is None,
                "owner_names": [
                    p.name + ("（已停用）" if not p.active else "") for p in owners.get(t.id, [])
                ],
                "department_names": departments.get(t.id, []),
                "verifier_name": users[t.verifier_id].name,
                "issuer_name": users[t.issuer_id].name if t.issuer_id else None,
                "source_title": meetings[t.source_meeting_id].title
                if t.source_meeting_id in meetings
                else "直接派发",
                "source_meeting_date": meetings[t.source_meeting_id].meeting_date
                if t.source_meeting_id in meetings
                else None,
                "created_at": t.created_at,
            }
            for t in tasks
        ],
    }


class TaskTransfer(BaseModel):
    expected_version: int
    issuer_id: str
    reason: str = Field(min_length=1, max_length=1000)


def transfer_allowed(db, task, person):
    allowed = {p.id for p in direct_assignees(db, person)}
    if not allowed or (
        not task.closed_at
        and (not set(owner_ids(db, task)) <= allowed or task.verifier_id not in allowed | {person.id})
    ):
        return False
    return True


@router.get("/tasks/{identifier}/transfer-options")
def transfer_options(identifier: str, db: Session = Depends(get_db), actor: User = Depends(current_user)):
    task = db.get(Task, identifier)
    if not task or task.source_meeting_id is not None:
        raise HTTPException(404, "直接任务不存在")
    if not can_manage_direct_task(db, actor, task, "transfer"):
        raise HTTPException(403, "没有移交此派发任务的权限")
    return [
        {"id": p.id, "name": p.name, "position": p.position, "department_id": p.department_id}
        for p in db.scalars(select(User).where(User.active.is_(True)).order_by(User.name))
        if p.id != task.issuer_id and transfer_allowed(db, task, p)
    ]


def save_administrative_change(db, actor, task, before, reason, action):
    task.version += 1
    for request in db.scalars(
        select(CompletionRequest).where(
            CompletionRequest.task_id == task.id, CompletionRequest.state == "pending"
        )
    ):
        request.state, request.opinion = "invalidated", "任务已移交或撤回，原完成申请失效"
    snapshot = task_snapshot(db, task)
    db.add(
        TaskVersion(task_id=task.id, number=task.version, snapshot=snapshot, reason=reason, actor_id=actor.id)
    )
    audit(db, actor, action, task, before=before, after=snapshot, reason=reason)
    return snapshot


@router.post("/tasks/{identifier}/transfer")
def transfer(
    identifier: str, body: TaskTransfer, db: Session = Depends(get_db), actor: User = Depends(current_user)
):
    task = locked_get(db, Task, identifier)
    if not task or task.source_meeting_id is not None:
        raise HTTPException(404, "直接任务不存在")
    if not can_manage_direct_task(db, actor, task, "transfer"):
        raise HTTPException(403, "没有移交此派发任务的权限")
    if task.version != body.expected_version or task.closed_at:
        raise HTTPException(409, "任务已变化或结束，请刷新")
    if not body.reason.strip() or body.issuer_id == task.issuer_id:
        raise HTTPException(422, "请选择新的派发人并填写移交原因")
    person = active_user(db, body.issuer_id)
    if not transfer_allowed(db, task, person):
        raise HTTPException(422, "当前责任人或验证人不在新派发人的范围内，请先调整人员")
    before = task_snapshot(db, task)
    task.issuer_id = person.id
    snapshot = save_administrative_change(db, actor, task, before, body.reason.strip(), "task.transfer")
    enqueue(
        db,
        f"task:{task.id}:v{task.version}:transfer",
        "wecom",
        {
            "task_id": task.id,
            "task_version": task.version,
            "user_ids": [person.id],
            "title": "任务已移交给您跟进",
            "content": task.content,
        },
    )
    from app.services.app_native_todo import enqueue_issuer_confirmation

    enqueue_issuer_confirmation(db, task)
    return snapshot


class TaskWithdrawal(BaseModel):
    expected_version: int
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/tasks/{identifier}/withdraw")
def withdraw(
    identifier: str, body: TaskWithdrawal, db: Session = Depends(get_db), actor: User = Depends(current_user)
):
    task = locked_get(db, Task, identifier)
    if not task or task.source_meeting_id is not None:
        raise HTTPException(404, "直接任务不存在")
    if not can_manage_direct_task(db, actor, task, "withdraw"):
        raise HTTPException(403, "只有管理员或有权限的派发人可以撤回")
    if task.withdrawn_at:
        return task_snapshot(db, task)
    if task.version != body.expected_version or task.closed_at:
        raise HTTPException(409, "任务已变化或结束，请刷新")
    if not body.reason.strip():
        raise HTTPException(422, "请填写撤回原因")
    before = task_snapshot(db, task)
    task.closed_at = task.withdrawn_at = now()
    task.status, task.withdrawal_reason = "不统计", body.reason.strip()
    snapshot = save_administrative_change(db, actor, task, before, body.reason.strip(), "task.withdraw")
    enqueue(
        db,
        f"task:{task.id}:v{task.version}:withdraw",
        "wecom",
        {
            "task_id": task.id,
            "task_version": task.version,
            "user_ids": owner_ids(db, task),
            "title": "任务已撤回",
            "content": task.withdrawal_reason,
            "allow_closed": True,
        },
    )
    enqueue_native_todo(db, task, "finish", key_suffix="withdraw")
    return snapshot
