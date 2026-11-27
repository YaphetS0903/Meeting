"""Only the built-in admin account can read operation history."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user, operation_log_user
from app.models import AuditLog, User
from app.services.business import audit
from app.services.operation_logs import CATEGORIES, category_for, present

router = APIRouter(prefix="/api")


@router.post("/auth/logout")
def logout(db: Session = Depends(get_db), user: User = Depends(current_user)):
    audit(db, user, "auth.logout", user)
    return {"ok": True}


@router.get("/admin/operation-logs")
def operation_logs(
    response: Response,
    start: date | None = None, end: date | None = None,
    keyword: str = Query(default="", max_length=120), category: str = "", result: str = "",
    view: Literal["operations", "errors"] = "operations",
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(operation_log_user),
):
    response.headers["Cache-Control"] = "no-store"
    if start and end and start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if category and category not in CATEGORIES:
        raise HTTPException(422, "操作分类无效")
    if result not in {"", "success", "failed", "pending"}:
        raise HTTPException(422, "操作结果无效")
    statement = select(AuditLog).outerjoin(User, AuditLog.actor_id == User.id)
    if start:
        statement = statement.where(AuditLog.created_at >= datetime.combine(start, time.min, ZoneInfo("Asia/Singapore")).astimezone(timezone.utc))
    if end:
        if end == date.max:
            raise HTTPException(422, "结束日期超出支持范围")
        statement = statement.where(AuditLog.created_at < datetime.combine(end + timedelta(days=1), time.min, ZoneInfo("Asia/Singapore")).astimezone(timezone.utc))
    # Legacy audit entries predate the category context; keep them in their natural group.
    from app.services.operation_logs import ACTION_LABELS
    if category:
        action = AuditLog.action
        legacy = case(
            (or_(action.like("auth.%"), action.like("user.password%")), "auth"),
            (action.like("notification.%"), "notification"), (action.like("user.%"), "permission"),
            (or_(action.like("meeting_sheet.%"), action.like("import.%"), action.like("file.%")), "import"),
            (or_(action.like("meeting.%"), action.like("meeting_series.%")), "meeting"),
            (or_(action.like("task.%"), action.like("recurrence.%"), action.like("native_todo.%")), "task"),
            else_="system")
        statement = statement.where(func.coalesce(AuditLog.context["category"].as_string(), legacy) == category)
    if view == "errors":
        result = "failed"
    if result:
        states = {"success": ["success", "sent", "skipped"], "failed": ["failed", "blocked", "uncertain"],
                  "pending": ["pending", "sending", "queued"]}[result]
        actual = func.coalesce(AuditLog.context["result"].as_string(),
                               func.nullif(AuditLog.after["state"].as_string(), ""),
                               case((AuditLog.action.endswith(".failed"), "failed"), else_="success"))
        statement = statement.where(actual.in_(states))
    keyword = keyword.strip()
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        columns = [AuditLog.action, AuditLog.reason, AuditLog.context["target_name"].as_string(),
                   AuditLog.context["actor_name"].as_string(), AuditLog.context["actor_username"].as_string(),
                   AuditLog.context["operation_label"].as_string(),
                   cast(AuditLog.before, String), cast(AuditLog.after, String), User.name, User.username]
        for values in (AuditLog.before, AuditLog.after):
            columns.extend(values[key].as_string() for key in ("title", "content", "name", "filename"))
        matching_actions = [key for key, label in ACTION_LABELS.items() if keyword.casefold() in label.casefold()]
        statement = statement.where(or_(*(column.ilike(pattern, escape="\\") for column in columns),
                                        AuditLog.action.in_(matching_actions)))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(db.scalars(statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                           .offset((page - 1) * page_size).limit(page_size)))
    return {"items": [present(db, entry) for entry in rows], "total": total,
            "page": page, "page_size": page_size, "categories": CATEGORIES}


@router.get("/admin/operation-logs/{identifier}")
def operation_log_detail(identifier: str, response: Response, db: Session = Depends(get_db),
                         user: User = Depends(operation_log_user)):
    response.headers["Cache-Control"] = "no-store"
    entry = db.get(AuditLog, identifier)
    if not entry:
        raise HTTPException(404, "该操作记录不存在")
    return present(db, entry, detail=True)


CLIENT_ERRORS = {
    "network": "网络连接中断，请稍后重试",
    "network_write": "网络连接中断，操作结果尚未确认，请先核对结果再重试",
    "response": "服务器返回的数据不完整，请稍后重新加载",
    "response_write": "服务器未返回完整数据，操作结果尚未确认，请先核对结果再重试",
    "title_required": "请填写会议名称后再保存草稿",
    "setup_title": "请填写会议名称",
    "setup_host": "请选择主持人",
    "setup_date": "请选择会议日期",
    "setup_department": "请选择所属部门",
    "gateway": "服务暂时不可用，请稍后重试",
    "browser": "页面处理失败，请重试；如仍有问题，请联系管理员",
    "route": "页面加载失败，请重新打开该功能",
}
PAGE_LABELS = {
    "meetings": "会议纪要与任务单", "tasks": "我的待办", "task-overview": "任务总览",
    "assignments": "直接派发任务", "my-assignments": "我派发的任务",
    "verifications": "待我验证", "reports": "统计报表", "settings": "系统管理",
    "operation-logs": "操作日志", "history-meetings": "历史会议", "validation": "导入校验",
    "": "工作台",
}


class ClientError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=32)
    page: str = Field(pattern=r"^/[a-zA-Z0-9/_-]{0,180}$")
    path: str = Field(default="", pattern=r"^(?:/[a-zA-Z0-9/_-]{0,180})?$")
    method: Literal["", "GET", "POST", "PUT", "PATCH", "DELETE"] = ""
    status: int | None = Field(default=None, ge=400, le=599)


@router.post("/client-errors", status_code=204)
def client_error(body: ClientError, db: Session = Depends(get_db), user: User = Depends(current_user)):
    message = CLIENT_ERRORS.get(body.code)
    page_key = body.page.split("/")[1]
    if not message or page_key not in PAGE_LABELS:
        return Response(status_code=204)
    page = PAGE_LABELS[page_key]
    if body.code.startswith("setup_"):
        operation = "新建会议"
    elif body.code == "title_required" or (
        body.method in {"POST", "PUT"} and body.path.startswith("/meetings")
        and body.path.count("/") <= 2
    ):
        operation = "保存会议草稿"
    else:
        operation = page + ("操作" if body.method != "GET" else "读取")
    db.add(AuditLog(
        actor_id=user.id, action="client.error", entity_type="system", entity_id=user.id,
        reason=message,
        context={"actor_name": user.name, "actor_username": user.username,
                 "category": category_for("", body.path or body.page), "result": "failed",
                 "target_name": page, "operation_label": operation, "error_source": "浏览器",
                 "page": body.page, "path": "/api" + body.path if body.path else "",
                 "method": body.method, "http_status": body.status},
    ))
    return Response(status_code=204)
