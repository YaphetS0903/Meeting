import hashlib
import mimetypes
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, locked_get
from app.core.security import admin_user, current_user, issue_token
from app.models import (
    Attachment,
    AuditLog,
    Meeting,
    MeetingVersion,
    NativeTodoLink,
    OutboxEvent,
    Task,
    TaskOwner,
    TaskVersion,
    User,
    now,
    uid,
)
from app.schemas import MeetingDraft
from app.services.access import meeting_access, task_access
from app.services.business import (
    active_user,
    audit,
    enqueue,
    enqueue_native_todo,
    notification_enabled,
    task_snapshot,
    today,
    validate_draft,
)
from app.services.exports import (
    attachment_filename,
    export_report,
    generate_exports,
    pdf_minutes,
    xlsx_minutes,
)
from app.services.integrations import (
    DeliveryUncertain,
    NotConfigured,
    ProviderRejected,
    oauth_cookie_name,
    oauth_url,
    oauth_user,
    sync_directory,
)
from app.services.reporting import report
from app.services.role_permissions import file_allowed, meeting_require

router = APIRouter(prefix="/api")


class IntegrationCheck(BaseModel):
    target: Literal["wecom", "smtp"]


@router.post("/admin/integration-checks")
def check_connection(body: IntegrationCheck, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    from app.services.integration_checks import check_integration

    result = check_integration(body.target)
    audit(db, user, "integration.check", user, after=result, reason="检查企业连接，不发送业务消息或邮件")
    return result


@router.get("/admin/integration-checks")
def connection_results(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    result = {}
    for target in ("wecom", "smtp"):
        entry = db.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "integration.check",
                AuditLog.after["target"].as_string() == target,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        result[target] = entry.after if entry else None
    return result


@router.get("/admin/operations")
def operations(user: User = Depends(admin_user)):
    from app.services.monitoring import public_monitor_state

    return public_monitor_state()


class PreviewRequest(BaseModel):
    expected_version: int
    format: Literal["pdf", "xlsx", "tasks"] = "pdf"


@router.post("/meetings/{identifier}/preview")
def preview_minutes(
    identifier: str, body: PreviewRequest, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    meeting = db.get(Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, user, meeting, "preview")
    if body.format != "pdf" and not file_allowed(db, user, meeting, "download"):
        raise HTTPException(403, "没有下载会议文件的权限")
    if not meeting.has_draft or meeting.draft_version != body.expected_version:
        raise HTTPException(409, "草稿已变化，请重新保存并预览")
    draft = MeetingDraft.model_validate(meeting.draft)
    previous = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == identifier, MeetingVersion.number == meeting.published_version
        )
    )
    validate_draft(db, draft, user, meeting.historical, previous.snapshot if previous else None, notify=False)
    snapshot = draft.model_dump(mode="json")
    snapshot["title"] = "【草稿预览】" + snapshot["title"]
    from app.services.departments import department_path

    snapshot["department_name"] = department_path(db, draft.department_id)
    for role in ("host", "recorder", "reviewer"):
        identifier = snapshot.get(role + "_id")
        snapshot[role + "_name"] = db.get(User, identifier).name if identifier else ""
    snapshot["attendee_names"] = [db.get(User, i).name for i in draft.attendee_ids]
    snapshot["online_attendee_names"] = [db.get(User, i).name for i in draft.online_attendee_ids]
    for item in snapshot["items"]:
        item["owner_names"] = [db.get(User, i).name for i in item["owner_ids"]]
        item["verifier_name"] = db.get(User, item.get("verifier_id") or draft.host_id).name
    is_pdf = body.format == "pdf"
    content = pdf_minutes(snapshot) if is_pdf else xlsx_minutes(snapshot, body.format == "tasks")
    return Response(
        content,
        media_type="application/pdf"
        if is_pdf
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline; filename=preview." + ("pdf" if is_pdf else "xlsx"),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/admin/reassignment")
def reassignment(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    inactive = {p.id: p for p in db.scalars(select(User).where(User.active.is_(False)))}
    if not inactive:
        return []
    identifiers = set(inactive)
    owner_tasks = select(TaskOwner.task_id).where(TaskOwner.user_id.in_(identifiers))
    tasks = db.scalars(
        select(Task)
        .where(
            Task.closed_at.is_(None),
            Task.status != "关闭",
            (Task.verifier_id.in_(identifiers)) | (Task.id.in_(owner_tasks)),
        )
        .order_by(Task.due_date, Task.id)
    )
    result = []
    for task in tasks:
        snapshot = task_snapshot(db, task)
        result.append(
            {
                **snapshot,
                "inactive_owners": [inactive[i].name for i in snapshot["owner_ids"] if i in inactive],
                "inactive_verifier": inactive[task.verifier_id].name
                if task.verifier_id in inactive
                else None,
            }
        )
    return result


class RoleChange(BaseModel):
    is_admin: bool
    expected_is_admin: bool
    reason: str = Field(min_length=1, max_length=1000)


@router.put("/admin/users/{identifier}/role")
def change_role(
    identifier: str, body: RoleChange, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    # Lock the directory in stable order so concurrent demotions cannot remove every admin.
    people = list(
        db.scalars(select(User).order_by(User.id).with_for_update().execution_options(populate_existing=True))
    )
    actor = next((p for p in people if p.id == user.id), None)
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化，请刷新")
    target = next((p for p in people if p.id == identifier), None)
    if not target:
        raise HTTPException(404, "人员不存在")
    if target.is_admin != body.expected_is_admin:
        raise HTTPException(409, "人员角色已变化，请刷新")
    if body.is_admin and not target.active:
        raise HTTPException(422, "已停用人员不能设为管理员")
    if (
        not body.is_admin
        and target.is_admin
        and not any(p.active and p.is_admin and p.id != identifier for p in people)
    ):
        raise HTTPException(422, "至少保留一名在用管理员")
    if not body.reason.strip():
        raise HTTPException(422, "请填写角色变更原因")
    before = {"is_admin": target.is_admin}
    target.is_admin = body.is_admin
    audit(
        db, user, "user.role", target, before=before, after={"is_admin": target.is_admin}, reason=body.reason
    )
    return {"id": target.id, "is_admin": target.is_admin}


@router.post("/admin/import-rematch")
def rematch(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    from app.services.importing import rematch_directory

    return rematch_directory(db, user)


@router.get("/task-suggestions")
def task_suggestions(
    q: str = "",
    series_id: str | None = None,
    owner_ids: list[str] = Query(default=[]),
    due_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    from difflib import SequenceMatcher

    from app.services.access import visible_tasks
    from app.services.business import owner_ids as task_owner_ids

    rows = []
    for task in db.scalars(visible_tasks(db, user).where(Task.closed_at.is_(None)).limit(1000)):
        score = SequenceMatcher(None, q, task.content).ratio() if q else 0.1
        matched = []
        if series_id and task.series_id == series_id:
            score += 0.25
            matched.append("同一会议系列")
        owners = set(task_owner_ids(db, task))
        overlap = owners.intersection(owner_ids)
        if overlap:
            score += 0.25 * len(overlap) / len(owners.union(owner_ids))
            matched.append(f"{len(overlap)} 位共同责任人相同")
        if due_date and task.due_date:
            distance = abs((task.due_date - due_date).days)
            if distance <= 30:
                score += 0.15 * (1 - distance / 31)
                matched.append("截止日期相同" if distance == 0 else f"截止日期相差 {distance} 天")
        if q and score < 0.12:
            continue
        rows.append(
            {
                **task_snapshot(db, task),
                "score": round(score, 3),
                "owner_names": [db.get(User, i).name for i in sorted(owners)],
                "match_reasons": matched,
            }
        )
    return sorted(rows, key=lambda x: x["score"], reverse=True)[:30]


class ReminderConfig(BaseModel):
    enabled: bool = True
    deadline_enabled: bool = True
    daily_enabled: bool = False
    hour: int = Field(default=9, ge=0, le=23)
    before_days: int = Field(default=1, ge=0, le=30)
    overdue_enabled: bool = True
    verification_hours: int = Field(default=24, ge=1, le=720)


@router.get("/admin/reminders")
def reminder_config(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    from app.models import SystemSetting

    entry = db.get(SystemSetting, "reminders")
    return ReminderConfig.model_validate(entry.value if entry else {}).model_dump()


@router.put("/admin/reminders")
def save_reminder_config(
    body: ReminderConfig, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    from app.models import SystemSetting

    entry = db.get(SystemSetting, "reminders")
    before = entry.value if entry else None
    value = body.model_dump()
    if entry:
        entry.value = value
    else:
        db.add(SystemSetting(key="reminders", value=value))
    db.flush()
    cancelled = 0
    if not body.enabled or not body.daily_enabled or not body.deadline_enabled:
        for event in db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.channel.in_(["wecom", "email"]),
                OutboxEvent.state.in_(["pending", "failed", "blocked"]),
            )
        ):
            if notification_enabled(db, event.channel, event.payload):
                continue
            event.state = "skipped"
            event.sent_at = now()
            event.last_error = "该来源的外部提醒已关闭，未发送且不会补发"
            cancelled += 1
    audit(
        db,
        user,
        "settings.reminders",
        user,
        before=before,
        after={**value, "cancelled_events": cancelled},
    )
    return {**value, "cancelled_events": cancelled}


class DeliveryResolution(BaseModel):
    delivered: bool
    reason: str = Field(min_length=5)


@router.post("/admin/notifications/{identifier}/resolve")
def resolve_delivery(
    identifier: str, body: DeliveryResolution, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    event = locked_get(db, OutboxEvent, identifier)
    if not event or event.state != "uncertain":
        raise HTTPException(409, "仅处理发送结果不明确的通知")
    event.state = "sent" if body.delivered else "failed"
    event.last_error = body.reason
    audit(db, user, "notification.resolve", event, after={"delivered": body.delivered}, reason=body.reason)
    return {"state": event.state}


@router.post("/files")
def upload(
    file: UploadFile,
    meeting_id: str | None = None,
    task_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if bool(meeting_id) == bool(task_id):
        raise HTTPException(422, "请选择一个会议或任务作为附件归属")
    if meeting_id:
        meeting = db.get(Meeting, meeting_id)
        if not meeting:
            raise HTTPException(404, "会议不存在")
        meeting_access(db, user, meeting, edit=True)
        if not file_allowed(db, user, meeting, "upload"):
            raise HTTPException(403, "没有上传会议附件的权限")
        if not meeting.has_draft:
            raise HTTPException(409, "已发布会议请先创建修订版本")
    if task_id:
        task = db.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        task_access(db, user, task)
        if not db.scalar(select(TaskOwner).where(TaskOwner.task_id == task_id, TaskOwner.user_id == user.id)):
            raise HTTPException(403, "只有责任人可以上传完成证据")
    name = Path(file.filename or "附件").name[:200]
    suffix = Path(name).suffix.lower()
    if suffix not in {
        ".pdf",
        ".xlsx",
        ".xls",
        ".docx",
        ".doc",
        ".png",
        ".jpg",
        ".jpeg",
        ".txt",
        ".csv",
        ".zip",
    }:
        raise HTTPException(422, "不支持此附件格式")
    content = file.file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "文件过大")
    key = f"attachments/{uid()}{suffix}"
    target = get_settings().storage_dir / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    entry = Attachment(
        filename=name,
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
        size=len(content),
        uploader_id=user.id,
        meeting_id=meeting_id,
        task_id=task_id,
        kind="evidence" if task_id else "attachment",
    )
    db.add(entry)
    db.flush()
    audit(db, user, "file.upload", entry, after={"filename": name, "size": len(content)})
    return {"id": entry.id, "filename": name, "size": len(content)}


@router.get("/meetings/{identifier}/export/{format}")
def export_meeting(
    identifier: str,
    format: Literal["xlsx", "pdf", "tasks"],
    version: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    meeting = locked_get(db, Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, user, meeting)
    if not file_allowed(db, user, meeting, "download"):
        raise HTTPException(403, "没有下载会议文件的权限")
    version = version or meeting.published_version
    if not version or not db.scalar(
        select(MeetingVersion.id).where(
            MeetingVersion.meeting_id == identifier, MeetingVersion.number == version
        )
    ):
        raise HTTPException(404, "所选发布版本不存在")
    attachments = generate_exports(db, identifier, version)
    wanted = {"xlsx": "minutes_xlsx", "pdf": "minutes_pdf", "tasks": "tasks_xlsx"}[format]
    file = next(a for a in attachments if a.kind == wanted)
    return FileResponse(
        get_settings().storage_dir / file.storage_key,
        filename=attachment_filename(file, db),
        media_type=file.mime_type,
    )


@router.get("/reports/new-arrangements")
def report_new_arrangements(
    start: date,
    end: date,
    department_id: str | None = None,
    person_id: str | None = None,
    series_key: str | None = None,
    kind: str | None = None,
    leader_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    from app.services.senior_arrangements import senior_arrangements

    return senior_arrangements(
        db, user, start, end, department_id, person_id, series_key, kind, offset=offset, limit=limit,
        leader_id=leader_id
    )


@router.get("/reports/export")
def report_export(
    start: date,
    end: date,
    group_by: Literal[
        "meeting_department", "person_department", "person", "kind", "department_people"
    ] = "meeting_department",
    department_id: str | None = None,
    kind: str | None = None,
    person_id: str | None = None,
    status: str | None = None,
    series_key: str | None = None,
    senior_only: bool = False,
    leader_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    from app.services.report_access import report_export_access

    report_export_access(db, user, department_id, person_id, senior_only=senior_only)
    data = report(
        db, user, start, end, group_by, department_id, kind, person_id, status, series_key, senior_only,
        leader_id=leader_id
    )
    return Response(
        export_report(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote("中高层会议统计报表.xlsx" if senior_only else "会议考核报表.xlsx")
        },
    )


@router.get("/admin/config")
def config(user: User = Depends(admin_user)):
    s = get_settings()
    return {
        "wecom_configured": bool(s.wecom_corp_id and s.wecom_secret and s.wecom_agent_id),
        "smtp_configured": bool(s.smtp_host and s.smtp_from),
        "directory_sync_interval_minutes": 60,
        "ai_configured": bool(s.ai_base_url and s.ai_api_key and s.ai_model),
        "ai_model": s.ai_model if s.ai_base_url and s.ai_api_key and s.ai_model else "",
        "native_todo_enabled": s.native_todo_enabled,
        "native_todo_app_enabled": s.native_todo_app_enabled,
        "wecom_direct_notifications_enabled": s.wecom_direct_notifications_enabled,
        "native_todo_direct_enabled": s.native_todo_source_enabled("direct"),
        "native_todo_meeting_enabled": s.native_todo_source_enabled("meeting"),
        "public_url": s.public_url,
    }


@router.get("/admin/native-todo")
def native_todo_status(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    from sqlalchemy import func

    from app.services.native_todo import status

    settings = get_settings()
    allowed = settings.native_todo_allowlist()
    query = select(User).where(User.active.is_(True), User.wecom_id.is_not(None))
    if not settings.native_todo_all_active_users:
        query = query.where(User.wecom_id.in_(allowed))
    users = list(db.scalars(query.order_by(User.name)))
    return {
        **status(),
        "enabled": settings.native_todo_enabled,
        "app_enabled": settings.native_todo_app_enabled,
        "direct_messages_enabled": settings.wecom_direct_notifications_enabled,
        "direct_enabled": settings.native_todo_source_enabled("direct"),
        "meeting_enabled": settings.native_todo_source_enabled("meeting"),
        "all_active_users": settings.native_todo_all_active_users,
        "mapped_users": sum(bool(member.native_todo_userid) for member in users),
        "allowlist_configured": bool(allowed),
        "allowed_users": [
            {
                "id": member.id,
                "name": member.name,
                "email": member.email,
                "wecom_id": member.wecom_id,
                "native_todo_userid": member.native_todo_userid,
            }
            for member in users
        ],
        "active_links": db.scalar(
            select(func.count()).select_from(NativeTodoLink).where(NativeTodoLink.state == "active")
        ),
        "legacy_links": db.scalar(
            select(func.count())
            .select_from(NativeTodoLink)
            .where(
                NativeTodoLink.provider == "cli",
                NativeTodoLink.state != "finished",
            )
        ),
        "app_links": db.scalar(
            select(func.count()).select_from(NativeTodoLink).where(NativeTodoLink.provider == "app")
        ),
    }


@router.post("/admin/native-todo/auth/start")
def native_todo_auth_start(user: User = Depends(admin_user)):
    from app.services.native_todo import start_authorization

    try:
        return start_authorization()
    except (NotConfigured, ProviderRejected, DeliveryUncertain) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/admin/native-todo/auth/qrcode")
def native_todo_auth_qrcode(user: User = Depends(admin_user)):
    from app.services.native_todo import authorization_qr_path

    try:
        path = authorization_qr_path()
    except NotConfigured as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


class NativeTodoSearch(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=3)


@router.post("/admin/native-todo/search")
def native_todo_search(body: NativeTodoSearch, user: User = Depends(admin_user)):
    from app.services.native_todo import search_users

    keywords = [value.strip() for value in body.keywords if value.strip()]
    if not keywords:
        raise HTTPException(422, "请输入要查找的成员姓名或邮箱")
    try:
        return search_users(keywords)
    except (NotConfigured, ProviderRejected, DeliveryUncertain) as exc:
        raise HTTPException(422, str(exc)) from exc


class NativeTodoBinding(BaseModel):
    native_todo_userid: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=2, max_length=500)


@router.post("/admin/native-todo/users/{identifier}/bind")
def bind_native_todo_user(
    identifier: str,
    body: NativeTodoBinding,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    from app.services.native_todo import match_member, search_users

    member = locked_get(db, User, identifier)
    if not member or not get_settings().native_todo_member_allowed(member.wecom_id, member.active):
        raise HTTPException(422, "只能绑定同步范围内的在用企业微信成员")
    duplicate = db.scalar(
        select(User).where(
            User.native_todo_userid == body.native_todo_userid,
            User.id != member.id,
        )
    )
    if duplicate:
        raise HTTPException(409, "该原生待办成员标识已绑定其他成员")
    try:
        candidates = search_users([member.name] + ([member.email] if member.email else []))
        candidate = match_member(member, candidates)
    except (NotConfigured, ProviderRejected, DeliveryUncertain) as exc:
        raise HTTPException(422, str(exc)) from exc
    if candidate["userid"] != body.native_todo_userid:
        raise HTTPException(422, "该成员标识与本次唯一匹配结果不一致")
    before = {"native_todo_userid": member.native_todo_userid}
    member.native_todo_userid = body.native_todo_userid
    audit(
        db,
        user,
        "native_todo.user.bind",
        member,
        before=before,
        after={"native_todo_userid": member.native_todo_userid},
        reason=body.reason,
    )
    return {"id": member.id, "name": member.name, "native_todo_userid": member.native_todo_userid}


@router.post("/admin/native-todo/tasks/{identifier}/sync")
def sync_native_todo_task(identifier: str, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    task = locked_get(db, Task, identifier)
    if not task:
        raise HTTPException(404, "任务不存在")
    source = "meeting" if task.source_meeting_id else "direct"
    if not get_settings().native_todo_source_enabled(source):
        raise HTTPException(409, "该任务来源的原生待办同步尚未开启")
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    if get_settings().native_todo_app_enabled and not link:
        raise HTTPException(409, "请由派发人或会议发布者在企业微信中确认创建原生待办")
    action = "finish" if task.closed_at and link else ("update" if link else "create")
    before = db.scalar(
        select(OutboxEvent.id).where(
            OutboxEvent.event_key == f"native-todo:{task.id}:{action}:manual-v{task.version}"
        )
    )
    enqueue_native_todo(db, task, action, key_suffix=f"manual-v{task.version}")
    db.flush()
    queued = bool(
        db.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.event_key == f"native-todo:{task.id}:{action}:manual-v{task.version}"
            )
        )
    )
    audit(
        db,
        user,
        "native_todo.task.sync",
        task,
        after={"action": action, "queued": queued and not before},
        reason="管理员手动同步原生待办",
    )
    if not queued:
        raise HTTPException(409, "任务未设置截止日期，或责任人不在原生待办同步范围")
    return {"queued": True, "action": action}


@router.post("/admin/native-todo/resume")
def resume_native_todo(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    """Resume configuration-blocked events only after real provider permission checks."""
    from app.services.native_todo import status

    current = status()
    if not current["authorized"]:
        raise HTTPException(409, "企业微信通讯录或待办权限尚未恢复，请完成授权后再刷新")
    settings = get_settings()
    count = 0
    events = db.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.channel == "native_todo",
            OutboxEvent.state == "blocked",
        )
        .order_by(OutboxEvent.created_at)
        .with_for_update()
    )
    for item in events:
        task = db.get(Task, item.payload.get("task_id"))
        source = "meeting" if task and task.source_meeting_id else "direct"
        if task and settings.native_todo_source_enabled(source):
            item.state = "pending"
            item.available_at = now()
            item.last_error = None
            count += 1
    audit(db, user, "native_todo.resume", user, after={"queued": count}, reason="恢复权限后重试阻塞待办")
    return {"queued": count}


@router.post("/admin/wecom/sync")
def sync(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    from app.services.directory_sync import synchronize_directory

    result = synchronize_directory(db, user, synchronizer=sync_directory)
    if result["state"] == "busy":
        raise HTTPException(409, result["error"])
    if result["state"] == "failed":
        db.commit()  # Preserve the diagnostic when get_db rolls back the HTTP error.
        raise HTTPException(result["status_code"], result["error"])
    return result["result"]


@router.get("/admin/directory-sync-history")
def sync_history(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return [
        {
            "id": entry.id,
            "created_at": entry.created_at,
            "state": "failed" if entry.action.endswith("failed") else "succeeded",
            "actor_name": "系统自动同步"
            if (entry.after or {}).get("mode") == "automatic"
            else db.get(User, entry.actor_id).name,
            "result": entry.after,
        }
        for entry in db.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(["directory.sync", "directory.sync.failed"]))
            .order_by(AuditLog.created_at.desc())
            .limit(100)
        )
    ]


class Retry(BaseModel):
    reason: str = Field(min_length=1)


@router.post("/admin/notifications/{identifier}/cancel")
def cancel_notification(
    identifier: str, body: Retry, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    event = locked_get(db, OutboxEvent, identifier)
    if not event:
        raise HTTPException(404, "通知不存在")
    if event.state not in {"pending", "blocked", "failed"}:
        raise HTTPException(409, "仅可取消待发送、配置阻塞或明确失败的通知；发送中或结果不明须先核实")
    before = {"state": event.state, "last_error": event.last_error, "attempts": event.attempts}
    event.state, event.claimed_at = "skipped", None
    event.last_error = "管理员取消：" + body.reason
    from app.models import NotificationLog

    db.add(
        NotificationLog(
            event_id=event.id, result="skipped", detail={"reason": body.reason, "actor_id": user.id}
        )
    )
    audit(
        db,
        user,
        "notification.cancel",
        event,
        before=before,
        after={"state": event.state, "last_error": event.last_error},
        reason=body.reason,
    )
    return {"state": event.state}


@router.post("/admin/notifications/{identifier}/retry")
def retry(identifier: str, body: Retry, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    event = locked_get(db, OutboxEvent, identifier)
    if not event:
        raise HTTPException(404, "通知不存在")
    if event.state not in {"failed", "blocked"}:
        raise HTTPException(409, "只有明确失败或配置阻塞的通知可直接重试；结果不明须先核实")
    if not notification_enabled(db, event.channel, event.payload):
        raise HTTPException(409, "该来源的外部提醒已关闭，不能重试通知")
    from app.models import now

    event.state = "pending"
    event.available_at = now()
    event.last_error = None
    audit(db, user, "notification.retry", event, reason=body.reason)
    return {"state": event.state}


@router.get("/auth/options")
def login_options():
    s = get_settings()
    enabled = bool(s.wecom_corp_id and s.wecom_secret and s.wecom_agent_id)
    return {"wecom": enabled, "wecom_qr": enabled}


@router.get("/auth/wecom/url")
def wecom_url(
    request: Request, response: Response, return_to: str = "/tasks", mode: Literal["oauth", "qr"] = "oauth"
):
    try:
        url, state = oauth_url(return_to, mode)
    except (NotConfigured, ProviderRejected) as exc:
        raise HTTPException(422, str(exc)) from exc
    # Each attempt owns its cookie so another tab cannot replace its CSRF binding.
    # Bound the number of cookies; the browser sends older cookies first.
    previous = [name for name in request.cookies if name.startswith("wecom_oauth_")]
    for name in previous[:-7]:
        response.delete_cookie(name, path="/api/auth/wecom")
    response.set_cookie(
        oauth_cookie_name(state),
        state,
        max_age=600,
        httponly=True,
        secure=get_settings().public_url.startswith("https://"),
        samesite="lax",
        path="/api/auth/wecom",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"url": url}


class OAuthExchange(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=1, max_length=128)


@router.post("/auth/wecom/exchange")
def wecom_exchange(
    body: OAuthExchange,
    request: Request,
    response: Response,
    wecom_oauth: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    try:
        cookie_name = oauth_cookie_name(body.state)
        # Existing links issued before this release retain their original cookie.
        cookie = request.cookies.get(cookie_name, wecom_oauth)
        user, return_to = oauth_user(db, body.code, body.state, cookie)
    except (NotConfigured, ProviderRejected) as exc:
        raise HTTPException(422, str(exc)) from exc
    response.delete_cookie(cookie_name, path="/api/auth/wecom")
    if cookie_name not in request.cookies:
        response.delete_cookie("wecom_oauth", path="/api/auth/wecom")
    response.headers["Cache-Control"] = "no-store"
    audit(db, user, "auth.login", user, after={"method": "企业微信"})
    return {"access_token": issue_token(user), "token_type": "bearer", "return_to": return_to}


class Activate(BaseModel):
    item_id: str
    due_date: date
    owner_ids: list[str] = Field(min_length=1)
    verifier_id: str | None = None
    reason: str = Field(min_length=1)
    expected_version: int


@router.post("/meetings/{identifier}/activate")
def activate(
    identifier: str, body: Activate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    meeting = locked_get(db, Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, user, meeting, edit=True)
    if not meeting.historical or meeting.published_version != body.expected_version:
        raise HTTPException(409, "仅可激活当前已确认历史版本中的事项")
    old = db.scalar(
        select(Task).where(Task.source_meeting_id == identifier, Task.source_item_id == body.item_id)
    )
    if old:
        return {"id": old.id, "code": old.code}
    snapshot = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == identifier, MeetingVersion.number == meeting.published_version
        )
    ).snapshot
    item = next((i for i in snapshot["items"] if i["id"] == body.item_id), None)
    if not item:
        raise HTTPException(422, "事项不存在")
    if item.get("backfill_reference"):
        raise HTTPException(422, "该历史事项已关联后续任务，请在后续会议中继续跟进，无需重复激活")
    from app.services.historical_links import work_identity
    from app.services.importing import dedup_lock

    history_id = work_identity(meeting.id, item)
    dedup_lock(db, hashlib.sha256(history_id.encode()).hexdigest())
    linked_task = db.scalar(select(Task).where(Task.historical_work_id == history_id))
    if linked_task:
        task_access(db, user, linked_task)
        return {"id": linked_task.id, "code": linked_task.code}
    if item.get("status") in {"关闭", "已完成", "不统计"}:
        raise HTTPException(422, "该事项无待跟进工作，请新建后续安排")
    for member in [*body.owner_ids, body.verifier_id or meeting.host_id]:
        active_user(db, member)
    task = Task(
        code="RW-" + uid().replace("-", "")[:12].upper(),
        content=item["content"],
        category=item.get("category", ""),
        requirements=item.get("requirements", ""),
        deliverable=item.get("deliverable", ""),
        department_id=meeting.department_id,
        source_meeting_id=meeting.id,
        source_item_id=body.item_id,
        series_id=meeting.series_id,
        verifier_id=body.verifier_id or meeting.host_id,
        due_date=body.due_date,
        status="未完成" if body.due_date and body.due_date < today() else "未到期",
        historical_work_id=history_id,
    )
    db.add(task)
    db.flush()
    for identifier in set(body.owner_ids):
        db.add(TaskOwner(task_id=task.id, user_id=identifier))
    db.flush()
    db.add(
        TaskVersion(
            task_id=task.id,
            number=1,
            snapshot=task_snapshot(db, task),
            reason=body.reason,
            actor_id=user.id,
            source_meeting_id=meeting.id,
        )
    )
    enqueue(
        db,
        f"task:{task.id}:activate",
        "wecom",
        {
            "task_id": task.id,
            "task_version": 1,
            "user_ids": list(set(body.owner_ids)),
            "title": "历史会议事项已激活跟进",
            "content": task.content,
        },
    )
    enqueue_native_todo(db, task, "create", key_suffix="activate")
    audit(db, user, "task.activate_historical", task, after=task_snapshot(db, task), reason=body.reason)
    return {"id": task.id, "code": task.code}
