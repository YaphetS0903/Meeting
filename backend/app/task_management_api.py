"""Task progress within the manager's position scope, with audited handover and withdrawal."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db, locked_get
from app.core.security import admin_user, current_user
from app.models import (
    CompletionRequest,
    Department,
    Meeting,
    RecurringRule,
    Task,
    TaskOwner,
    TaskVersion,
    User,
    now,
)
from app.services.access import (
    assignment_department_ids,
    can_assign_companywide,
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
    current_status,
    enqueue,
    enqueue_native_todo,
    owner_ids,
    task_snapshot,
    today,
)
from app.services.recurrence import can_manage_rule

router = APIRouter(prefix="/api")


def require_overview(db, actor):
    if not can_oversee_tasks(db, actor):
        raise HTTPException(403, "当前岗位没有任务总览权限")


@router.get("/task-overview/options")
def overview_options(db: Session = Depends(get_db), actor: User = Depends(current_user)):
    require_overview(db, actor)
    all_staff = can_assign_companywide(actor)
    departments = select(Department).order_by(Department.name)
    if not all_staff:
        departments = departments.where(Department.id.in_(assignment_department_ids(db, actor)))
    return {
        "scope": "all" if all_staff else "department",
        "users": [
            {"id": p.id, "name": p.name, "active": p.active, "department_id": p.department_id}
            for p in db.scalars(managed_users(db, actor).order_by(User.name, User.id))
        ],
        "departments": [{"id": d.id, "name": d.name} for d in db.scalars(departments)],
    }


@router.get("/task-overview")
def overview(
    q: str = "",
    person_id: str | None = None,
    department_id: str | None = None,
    source: Literal["meeting", "direct"] | None = None,
    status: Literal["已完成", "未完成", "未到期", "不统计", "关闭", "待验证", "已撤回"] | None = None,
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
    people_query = managed_users(db, actor)
    if person_id and db.scalar(people_query.where(User.id == person_id)) is None:
        raise HTTPException(403, "人员不在当前任务总览范围")
    if department_id:
        if not can_assign_companywide(actor) and department_id not in assignment_department_ids(db, actor):
            raise HTTPException(403, "部门不在当前任务总览范围")
        from app.models import Membership

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
    if person_id:
        people_query = people_query.where(User.id == person_id)
    query = managed_tasks(db, actor)
    if issued_by_me:
        query = query.where(Task.issuer_id == actor.id)
    if person_id or department_id:
        query = query.where(
            Task.id.in_(
                select(TaskOwner.task_id).where(
                    TaskOwner.user_id.in_(people_query.with_only_columns(User.id)),
                )
            )
        )
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
    live_status = case(
        (Task.status.in_(["已完成", "不统计", "关闭"]), Task.status),
        (Task.due_date < today(), "未完成"),
        else_="未到期",
    )
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
            func.sum(case(((Task.status == "关闭") & Task.withdrawn_at.is_(None), 1), else_=0)), 0
        ).label("closed"),
        func.coalesce(func.sum(case((Task.withdrawn_at.is_not(None), 1), else_=0)), 0).label("withdrawn"),
    )
    if query.whereclause is not None:
        summary_query = summary_query.where(query.whereclause)
    summary = db.execute(summary_query).one()._mapping
    tasks = list(db.scalars(query.order_by(Task.created_at.desc(), Task.id).offset(offset).limit(limit)))
    identifiers = [t.id for t in tasks]
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
    meetings = {m.id: m.title for m in db.scalars(select(Meeting).where(Meeting.id.in_(meeting_ids)))}
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
    return {
        "summary": dict(summary),
        "total": summary["total"],
        "items": [
            {
                "id": t.id,
                "code": t.code,
                "content": t.content,
                "due_date": t.due_date,
                "status": "已撤回" if t.withdrawn_at else current_status(t),
                "pending": t.id in pending_ids and t.closed_at is None,
                "owner_names": [
                    p.name + ("（已停用）" if not p.active else "") for p in owners.get(t.id, [])
                ],
                "verifier_name": users[t.verifier_id].name,
                "issuer_name": users[t.issuer_id].name if t.issuer_id else None,
                "source_title": meetings.get(t.source_meeting_id, "直接派发"),
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
    rule = db.get(RecurringRule, task.recurrence_key) if task.recurrence_key else None
    return not rule or (
        set(rule.template["owner_ids"]) <= allowed and rule.template["verifier_id"] in allowed | {person.id}
    )


@router.get("/tasks/{identifier}/transfer-options")
def transfer_options(identifier: str, db: Session = Depends(get_db), actor: User = Depends(admin_user)):
    task = db.get(Task, identifier)
    if not task or task.source_meeting_id is not None:
        raise HTTPException(404, "直接任务不存在")
    rule = db.get(RecurringRule, task.recurrence_key) if task.recurrence_key else None
    return [
        {"id": p.id, "name": p.name, "position": p.position, "department_id": p.department_id}
        for p in db.scalars(select(User).where(User.active.is_(True)).order_by(User.name))
        if (p.id != task.issuer_id or (rule and p.id != rule.creator_id)) and transfer_allowed(db, task, p)
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
    identifier: str, body: TaskTransfer, db: Session = Depends(get_db), actor: User = Depends(admin_user)
):
    task = locked_get(db, Task, identifier)
    if not task or task.source_meeting_id is not None:
        raise HTTPException(404, "直接任务不存在")
    if task.version != body.expected_version or (task.closed_at and not task.recurrence_key):
        raise HTTPException(409, "任务已变化或结束，请刷新")
    rule = locked_get(db, RecurringRule, task.recurrence_key) if task.recurrence_key else None
    if not body.reason.strip() or (
        body.issuer_id == task.issuer_id and (not rule or body.issuer_id == rule.creator_id)
    ):
        raise HTTPException(422, "请选择新的派发人并填写移交原因")
    person = active_user(db, body.issuer_id)
    if not transfer_allowed(db, task, person):
        raise HTTPException(422, "当前责任人、验证人或后续周期人员不在新派发人的范围内，请先调整人员")
    before = task_snapshot(db, task)
    task.issuer_id = person.id
    if rule:
        rule_before = {"creator_id": rule.creator_id, "version": rule.version}
        rule.creator_id, rule.version = person.id, rule.version + 1
        audit(
            db,
            actor,
            "recurrence.transfer",
            rule,
            before=rule_before,
            after={"creator_id": person.id, "version": rule.version},
            reason=body.reason,
        )
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
    stop_recurrence: bool = False


@router.post("/tasks/{identifier}/withdraw")
def withdraw(
    identifier: str, body: TaskWithdrawal, db: Session = Depends(get_db), actor: User = Depends(current_user)
):
    task = locked_get(db, Task, identifier)
    if not task or task.source_meeting_id is not None:
        raise HTTPException(404, "直接任务不存在")
    if not can_manage_direct_task(db, actor, task):
        raise HTTPException(403, "只有管理员或有权限的派发人可以撤回")
    if task.withdrawn_at:
        return task_snapshot(db, task)
    if task.version != body.expected_version or task.closed_at:
        raise HTTPException(409, "任务已变化或结束，请刷新")
    if not body.reason.strip():
        raise HTTPException(422, "请填写撤回原因")
    if body.stop_recurrence and task.recurrence_key:
        rule = locked_get(db, RecurringRule, task.recurrence_key)
        if not can_manage_rule(db, actor, rule):
            raise HTTPException(403, "没有停止后续周期任务的权限")
        rule.active, rule.version = False, rule.version + 1
        audit(db, actor, "recurrence.stop_on_withdrawal", rule, reason=body.reason)
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
