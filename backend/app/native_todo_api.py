"""Sign SDK requests and bind persisted business tasks to verified native todos."""

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, locked_get
from app.core.security import current_user
from app.models import Meeting, MeetingVersion, NativeTodoAttempt, NativeTodoLink, Task, User, now
from app.services.access import direct_assignees, task_access
from app.services.app_native_todo import (
    can_confirm_app_task,
    can_manage_app_task,
    enabled,
    native_state,
    task_payload,
    task_source,
)
from app.services.business import audit, enqueue_native_todo, owner_ids
from app.services.integrations import DeliveryUncertain, NotConfigured, ProviderRejected, WeCom
from app.services.role_permissions import meeting_require

router = APIRouter(prefix="/api")


def permitted_task(db, user, identifier, *, edit=False):
    task = locked_get(db, Task, identifier)
    if not task:
        raise HTTPException(404, "任务不存在")
    task_access(db, user, task)
    if not task.source_meeting_id and not task.issuer_id:
        raise HTTPException(409, "此任务没有可确认的派发来源")
    if edit and not can_manage_app_task(db, user, task):
        raise HTTPException(403, "只有派发人、会议发布者或管理员可关联或核实原生待办")
    return task


def provider_error(exc):
    if isinstance(exc, (NotConfigured, ProviderRejected, DeliveryUncertain)):
        return HTTPException(422, str(exc))
    return HTTPException(503, "企业微信服务暂时不可用，系统任务已保存，请稍后重试")


class SignatureRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    kind: Literal["corp", "agent"]


@router.get("/wecom/sdk")
def sdk_identity(user: User = Depends(current_user)):
    if not enabled() or not user.wecom_id:
        raise HTTPException(409, "请在企业微信中使用已绑定的员工账号打开任务派发页面")
    settings = get_settings()
    return {"corpId": settings.wecom_corp_id, "agentId": settings.wecom_agent_id}


@router.post("/wecom/sdk/signature")
def sdk_signature(body: SignatureRequest, user: User = Depends(current_user)):
    sdk_identity(user)
    try:
        return WeCom().signature(body.url, agent=body.kind == "agent")
    except Exception as exc:
        raise provider_error(exc) from None


@router.get("/tasks/{identifier}/native-todo")
def get_native_state(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    task = permitted_task(db, user, identifier)
    return native_state(db, user, task)


@router.get("/meetings/{identifier}/native-todos")
def meeting_native_todos(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    meeting = db.get(Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, user, meeting, "publish")
    available = enabled("meeting")
    reason = ""
    if meeting.historical:
        reason = "历史会议不派发原生待办"
    elif not meeting.published_version:
        reason = "请先发布会议，再确认派发本次安排的原生待办"
    elif not available:
        reason = "会议的自建应用原生待办尚未启用"
    elif not user.wecom_id:
        reason = "请使用已绑定企业微信的会议发布者账号确认派发"
    tasks = []
    excluded = []
    version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting.id,
            MeetingVersion.number == meeting.published_version,
        )
    )
    arrangements = [
        item
        for item in (version.snapshot.get("items", []) if version else [])
        if item.get("section") == "new"
    ]
    seen = set()
    for item in arrangements:
        identifier = item.get("task_id")
        if identifier and identifier in seen:
            continue
        seen.add(identifier)
        task = db.get(Task, identifier) if identifier else None
        exclusion = ""
        if meeting.historical:
            exclusion = "历史事项不派发原生待办"
        elif not task:
            exclusion = "此事项没有可派发的系统任务"
        elif task.closed_at or task.withdrawn_at:
            exclusion = "系统任务已关闭或撤回，无需派发"
        elif task.source_meeting_id != meeting.id and not can_manage_app_task(db, user, task):
            exclusion = "此任务属于其他会议或派发人，请由原任务发布者确认派发"
        if exclusion:
            # For inaccessible linked tasks show only the already-visible published snapshot.
            excluded.append(
                {
                    "id": identifier,
                    "content": item.get("content", ""),
                    "owner_names": item.get("owner_names", []),
                    "reason": exclusion,
                }
            )
            continue
        tasks.append(
            {
                "id": task.id,
                "code": task.code,
                "content": task.content,
                "version": task.version,
                "due_date": task.due_date,
                "owner_names": [db.get(User, value).name for value in owner_ids(db, task)],
                "native_todo": native_state(db, user, task),
            }
        )
    return {
        "meeting_id": meeting.id,
        "published_version": meeting.published_version,
        "enabled": available,
        "can_confirm": not reason,
        "reason": reason,
        "tasks": tasks,
        "excluded_tasks": excluded,
    }


class Prepare(BaseModel):
    expected_version: int = Field(ge=1)


@router.post("/tasks/{identifier}/native-todo/prepare")
def prepare_native(
    identifier: str, body: Prepare, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    task = permitted_task(db, user, identifier, edit=True)
    if not can_confirm_app_task(db, user, task):
        raise HTTPException(403, "原生待办须由派发人或本次会议发布者确认，责任人无需创建")
    if not enabled(task_source(task)) or not user.wecom_id:
        raise HTTPException(409, "自建应用原生待办尚未启用，或当前账号未绑定企业微信")
    if task.version != body.expected_version or task.closed_at or task.withdrawn_at:
        raise HTTPException(409, "任务已调整或关闭，请刷新后查看")
    if db.scalar(select(NativeTodoLink.id).where(NativeTodoLink.task_id == task.id)):
        raise HTTPException(409, "此任务已有关联待办，请查看已有待办")
    if not task.source_meeting_id:
        allowed = {p.id for p in direct_assignees(db, user)}
        if not set(owner_ids(db, task)) <= allowed:
            raise HTTPException(403, "责任人已超出当前岗位的派发范围，请先核对任务")
    attempt = db.scalar(select(NativeTodoAttempt).where(NativeTodoAttempt.task_id == task.id))
    if attempt and attempt.state == "pending":
        raise HTTPException(409, "上次原生待办创建结果尚未确认，请先恢复关联或核实是否已创建")
    try:
        payload = task_payload(db, task)
    except ProviderRejected as exc:
        raise provider_error(exc) from None
    # Replacing a cancelled attempt changes its ID, so late callbacks cannot bind the new attempt.
    if attempt:
        db.delete(attempt)
        db.flush()
    attempt = NativeTodoAttempt(
        task_id=task.id,
        actor_id=user.id,
        task_version=task.version,
        snapshot={
            **payload,
            "content_version": 2,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "owner_names": [db.get(User, identifier).name for identifier in owner_ids(db, task)],
        },
    )
    db.add(attempt)
    db.flush()
    audit(db, user, "native_todo.app.prepare", task, after={"attempt_id": attempt.id})
    return {"attempt_id": attempt.id, "payload": payload}


class Bind(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=36)
    todo_id: str = Field(min_length=1, max_length=200)


@router.post("/tasks/{identifier}/native-todo/record")
def record_native_callback(
    identifier: str, body: Bind, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    """Persist the callback before slow provider verification, without trusting its ID."""
    task = permitted_task(db, user, identifier, edit=True)
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    if link:
        if link.provider == "app" and link.todo_id == body.todo_id:
            return native_state(db, user, task)
        raise HTTPException(409, "此任务已关联其他原生待办，不能重复关联")
    attempt = db.scalar(select(NativeTodoAttempt).where(NativeTodoAttempt.task_id == task.id))
    if not attempt or attempt.id != body.attempt_id or attempt.state != "pending":
        raise HTTPException(409, "创建记录已失效，请先核实企业微信中的待办")
    if attempt.actor_id != user.id and not user.is_admin:
        raise HTTPException(403, "只能保存本人创建待办的回调")
    if db.scalar(select(NativeTodoLink.id).where(NativeTodoLink.todo_id == body.todo_id)):
        raise HTTPException(409, "此待办已关联其他系统任务")
    if attempt.snapshot.get("callback_todo_id") != body.todo_id:
        attempt.snapshot = {**attempt.snapshot, "callback_todo_id": body.todo_id, "verification_error": ""}
        audit(db, user, "native_todo.app.callback", task, after={"attempt_id": attempt.id})
    return native_state(db, user, task)


@router.post("/tasks/{identifier}/native-todo/bind")
def bind_native(
    identifier: str, body: Bind, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    task = permitted_task(db, user, identifier, edit=True)
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    if link:
        if link.provider == "app" and link.todo_id == body.todo_id:
            return native_state(db, user, task)
        raise HTTPException(409, "此任务已关联其他原生待办，不能重复关联")
    attempt = db.scalar(select(NativeTodoAttempt).where(NativeTodoAttempt.task_id == task.id))
    if not attempt or attempt.id != body.attempt_id or attempt.state != "pending":
        raise HTTPException(409, "创建记录已失效，请先核实企业微信中的待办")
    if attempt.actor_id != user.id and not user.is_admin:
        raise HTTPException(403, "只能恢复本人创建的待办")
    if not enabled(task_source(task)):
        raise HTTPException(409, "自建应用原生待办已关闭")
    if db.scalar(select(NativeTodoLink.id).where(NativeTodoLink.todo_id == body.todo_id)):
        raise HTTPException(409, "此待办已关联其他系统任务")
    creator = db.get(User, attempt.actor_id)
    # This is a recovery hint, never proof of association. Preserve it even when verification
    # fails so closing the browser does not lose the only ID of an already-created todo.
    attempt.snapshot = {**attempt.snapshot, "callback_todo_id": body.todo_id}
    try:
        provider = WeCom()
        details = provider.request("todo/get", body={"todo_id": body.todo_id})
        expected = attempt.snapshot
        created = (
            attempt.created_at.replace(tzinfo=timezone.utc)
            if attempt.created_at.tzinfo is None
            else attempt.created_at
        )
        remote_time = int(details.get("create_time", 0))
        mismatches = []
        if not creator or details.get("creator") != creator.wecom_id:
            mismatches.append("创建人")
        if details.get("content") != expected["content"]:
            mismatches.append("待办内容")
        if remote_time < int(created.timestamp()) - 60 or remote_time > int(now().timestamp()) + 60:
            mismatches.append("创建时间")
        if mismatches:
            raise ProviderRejected(
                "企微待办的"
                + "、".join(mismatches)
                + "与创建记录不一致，已保留待办编号，请核对后恢复关联，避免重复创建。"
            )
        # Native clients can omit prefilled attendees. Only after verifying identity and
        # task content do we reconcile them using the API restricted to this application.
        participants = sorted({p["userid"] for p in details.get("attendees", [])})
        desired = task_payload(db, task)["attendees"] if not task.closed_at else expected["attendees"]
        if not task.source_meeting_id:
            allowed = {p.wecom_id for p in direct_assignees(db, user)}
            if not task.closed_at and not set(desired) <= allowed:
                raise ProviderRejected("责任人已超出当前岗位的派发范围，请由管理员核对后恢复关联")
        update = {"todo_id": body.todo_id, "status": 0 if task.closed_at else 1}
        if not task.closed_at and (
            not set(desired) <= set(participants) or set(participants) - set(desired) - {creator.wecom_id}
        ):
            states = {p["userid"]: p.get("status", 1) for p in details.get("attendees", [])}
            update["attendees"] = [
                {"userid": identifier, "status": 0 if states.get(identifier) == 0 else 1}
                for identifier in desired
            ]
        provider.request("todo/update", body=update, write=True)
        details = provider.request("todo/get", body={"todo_id": body.todo_id})
        participants = sorted({p["userid"] for p in details.get("attendees", [])})
        if not task.closed_at and (
            not set(desired) <= set(participants) or set(participants) - set(desired) - {creator.wecom_id}
        ):
            raise ProviderRejected("企微待办责任人补齐后核验未通过，已保留编号，请稍后恢复关联")
        remote_end = int(details.get("end_time") or 0)
        remote_date = (
            datetime.fromtimestamp(remote_end, ZoneInfo("Asia/Shanghai")).date().isoformat()
            if remote_end > 0
            else None
        )
        # The business form selects a day; clients may normalize its time of day.
        if remote_date != expected.get("due_date"):
            deadline = expected.get("due_date") or "不设置截止日期"
            raise ProviderRejected(
                f"企微待办的截止日期与系统不一致（系统：{deadline}）。"
                "已保留创建记录，请派发人在企微补正截止日期后点击“恢复待办关联”，避免重复创建。"
            )
    except Exception as exc:
        error = provider_error(exc)
        attempt.snapshot = {**attempt.snapshot, "verification_error": error.detail}
        audit(
            db,
            user,
            "native_todo.app.bind_rejected",
            task,
            after={"attempt_id": attempt.id, "reason": error.detail},
        )
        # Returning a response lets get_db commit only the recovery record, while the todo
        # remains unlinked. Raising HTTPException here would roll back its callback ID.
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
    link = NativeTodoLink(
        task_id=task.id,
        todo_id=body.todo_id,
        provider="app",
        task_version=attempt.task_version,
        follower_ids=participants,
        snapshot={**expected, "verification_error": ""},
        state="finished" if task.closed_at else "active",
        last_synced_at=now(),
    )
    db.add(link)
    attempt.state = "bound"
    attempt.snapshot = {**attempt.snapshot, "verification_error": ""}
    db.flush()
    if not task.closed_at and task.version != attempt.task_version:
        enqueue_native_todo(db, task, "update", key_suffix=f"bind-v{task.version}")
    audit(db, user, "native_todo.app.bind", task, after={"todo_id": body.todo_id, "attempt_id": attempt.id})
    return native_state(db, user, task)


class Reset(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=36)
    reason: Literal["cancelled", "failed_before_create", "confirmed_not_created"]


@router.post("/tasks/{identifier}/native-todo/reset")
def reset_native(
    identifier: str, body: Reset, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    task = permitted_task(db, user, identifier, edit=True)
    attempt = db.scalar(select(NativeTodoAttempt).where(NativeTodoAttempt.task_id == task.id))
    if not attempt or attempt.id != body.attempt_id:
        raise HTTPException(409, "创建记录已变化，请刷新")
    if attempt.actor_id != user.id and not user.is_admin:
        raise HTTPException(403, "只能核实本人创建的待办")
    if attempt.state == "bound":
        raise HTTPException(409, "已关联的待办不能重新创建")
    attempt.state = "cancelled"
    audit(db, user, "native_todo.app.reset", task, reason=body.reason, after={"attempt_id": attempt.id})
    return native_state(db, user, task)
