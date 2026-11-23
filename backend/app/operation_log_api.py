"""Only the built-in admin account can read operation history."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user, operation_log_user
from app.models import AuditLog, User
from app.services.business import audit
from app.services.operation_logs import CATEGORIES, present

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
