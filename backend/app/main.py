import hashlib
import json
import tempfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, locked_get
from app.core.login_throttle import LoginThrottle
from app.core.security import admin_user, current_user, issue_token, passwords
from app.extra_api import router as extra_router
from app.models import (
    Assessment,
    Attachment,
    AuditLog,
    CompletionRequest,
    Department,
    DirectAssignmentRequest,
    ImportBatch,
    ImportCandidate,
    ImportSource,
    Meeting,
    MeetingAttendee,
    MeetingEditRequest,
    MeetingSeries,
    MeetingVersion,
    NativeTodoAttempt,
    NativeTodoLink,
    OutboxEvent,
    RecurringRule,
    Task,
    TaskOwner,
    TaskVersion,
    User,
    UserAlias,
    ValidationIssue,
    now,
    uid,
)
from app.native_todo_api import router as native_todo_router
from app.schemas import (
    ApplyCompletion,
    LoginRequest,
    MeetingDraft,
    PublishRequest,
    SaveDraft,
    TaskChange,
    VerifyCompletion,
)
from app.services.access import (
    can_assign_companywide,
    can_edit_meeting,
    can_manage_direct_task,
    can_oversee_tasks,
    department_ids,
    direct_assignees,
    meeting_access,
    meeting_attachment_visible,
    related_meetings,
    task_access,
    visible_directory,
    visible_meeting_attachment_ids,
    visible_meetings,
    visible_tasks,
)
from app.services.app_native_todo import enqueue_issuer_confirmation
from app.services.business import (
    active_user,
    apply_completion,
    audit,
    carry_forward,
    change_task,
    continue_meeting,
    create_meeting,
    current_status,
    enqueue,
    enqueue_native_todo,
    move_meeting_series,
    owner_ids,
    publish,
    revision,
    save_meeting,
    task_snapshot,
    today,
    verify_completion,
)
from app.services.importing import (
    candidate_access,
    confirm_candidate,
    correct_candidate,
    ingest_file,
    remaining_problems,
    remove_import_source,
)
from app.services.meeting_context import import_context, meeting_context
from app.services.recurrence import can_manage_rule
from app.services.reporting import pending_quality, report
from app.task_management_api import router as task_management_router


@asynccontextmanager
async def lifespan(app):
    get_settings().storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="会议纪要任务单系统", version="0.1.0", lifespan=lifespan)

app.include_router(extra_router)
app.include_router(task_management_router)
app.include_router(native_todo_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)


def record(obj, omit=()):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name not in omit}


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": app.version}


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Production password attempts share counters across workers; enterprise SSO uses a separate flow.
    throttle = (
        LoginThrottle(body.username, request.client.host if request.client else "unknown")
        if get_settings().app_env == "production"
        else None
    )
    if throttle:
        throttle.check()
    user = db.scalar(select(User).where(User.username == body.username))
    if (
        not user
        or not user.active
        or not user.password_hash
        or not passwords.verify(body.password, user.password_hash)
    ):
        if throttle:
            throttle.failed()
        raise HTTPException(401, "账号或密码错误")
    if throttle:
        throttle.succeeded()
    return {"access_token": issue_token(user), "token_type": "bearer"}


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)


@app.put("/api/me/password")
def change_password(
    body: ChangePassword, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    account = locked_get(db, User, user.id)
    if not account.password_hash:
        raise HTTPException(403, "此账号使用企业微信登录，没有可修改的本地密码")
    if not passwords.verify(body.current_password, account.password_hash):
        raise HTTPException(422, "当前密码不正确")
    if body.current_password == body.new_password:
        raise HTTPException(422, "新密码不能与当前密码相同")
    account.password_hash = passwords.hash(body.new_password)
    account.auth_version = (account.auth_version or 0) + 1
    audit(db, account, "user.password.change", account, after={"auth_version": account.auth_version})
    db.flush()
    return {"access_token": issue_token(account), "token_type": "bearer"}


@app.get("/api/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    value = record(user, ("password_hash", "auth_version", "leader_department_ids", "manual_leader_department_ids"))
    value["can_assign_tasks"] = bool(direct_assignees(db, user))
    value["can_oversee_tasks"] = can_oversee_tasks(db, user)
    value["has_password"] = bool(user.password_hash)
    value["can_correct_history"] = (
        user.is_admin
        or db.scalar(
            select(ImportCandidate.id)
            .where(ImportCandidate.corrected["host_id"].as_string() == user.id)
            .limit(1)
        )
        is not None
    )
    return value


@app.get("/api/directory")
def directory(db: Session = Depends(get_db), user: User = Depends(current_user)):
    visible = visible_directory(db, user)
    return {
        "departments": [record(d) for d in sorted(visible["departments"], key=lambda item: item.name)],
        "users": [
            record(u, ("password_hash", "auth_version", "username", "leader_department_ids", "manual_leader_department_ids", *([] if user.is_admin else ["mobile"])))
            for u in sorted(visible["users"], key=lambda item: (item.name, item.id))
        ],
        "series": [record(s) for s in sorted(visible["series"], key=lambda item: item.name)],
    }


class NewSeries(BaseModel):
    name: str = Field(min_length=1, max_length=250)


@app.post("/api/series")
def add_series(body: NewSeries, db: Session = Depends(get_db), user: User = Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请输入会议系列名称")
    old = db.scalar(select(MeetingSeries).where(MeetingSeries.name == name))
    if old:
        return record(old)
    series = MeetingSeries(name=name, department_id=None, kind="general")
    db.add(series)
    db.flush()
    return record(series)


def archive_query(
    db, user, start, end, department_id, kind, publication, q, scope, *, include_series_name=False
):
    query = related_meetings(db, user) if scope == "related" else visible_meetings(db, user)
    if start:
        query = query.where(Meeting.meeting_date >= start)
    if end:
        query = query.where(Meeting.meeting_date <= end)
    if department_id:
        query = query.where(Meeting.department_id == department_id)
    if kind:
        query = query.where(Meeting.kind == kind)
    if publication == "draft":
        query = query.where(Meeting.published_version == 0)
    elif publication == "published":
        query = query.where(Meeting.published_version > 0, Meeting.has_draft.is_(False))
    elif publication == "revising":
        query = query.where(Meeting.published_version > 0, Meeting.has_draft.is_(True))
    if q:
        if include_series_name:
            query = query.outerjoin(MeetingSeries, MeetingSeries.id == Meeting.series_id).where(
                or_(Meeting.title.ilike(f"%{q}%"), MeetingSeries.name.ilike(f"%{q}%"))
            )
        else:
            query = query.where(Meeting.title.ilike(f"%{q}%"))
    return query


def archive_status_matches(db, meeting, status):
    version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == meeting.published_version
        )
    )
    payload = version.snapshot if version else meeting.draft
    return any(
        item.get("section") == "review" and item.get("status") == status for item in payload.get("items", [])
    )


def pending_edit_counts(db: Session, user: User, meeting_ids: list[str]) -> dict[str, int]:
    if not meeting_ids:
        return {}
    statement = (
        select(MeetingEditRequest.meeting_id, func.count())
        .join(Meeting, Meeting.id == MeetingEditRequest.meeting_id)
        .where(
            Meeting.id.in_(meeting_ids),
            MeetingEditRequest.state == "pending",
            MeetingEditRequest.host_id == Meeting.host_id,
            MeetingEditRequest.permission_generation == Meeting.permission_generation,
        )
        .group_by(MeetingEditRequest.meeting_id)
    )
    if not user.is_admin:
        statement = statement.where(Meeting.host_id == user.id)
    return {meeting_id: count for meeting_id, count in db.execute(statement)}


@app.get("/api/meeting-series")
def meeting_series_archive(
    start: date | None = None,
    end: date | None = None,
    department_id: str | None = None,
    kind: str | None = None,
    publication: Literal["draft", "published", "revising"] | None = None,
    status: Literal["已完成", "未完成", "未到期", "不统计", "关闭"] | None = None,
    q: str = "",
    scope: Literal["all", "related"] = "all",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    archive = archive_query(
        db, user, start, end, department_id, kind, publication, q, scope, include_series_name=True
    )
    groups = {}
    archived_meetings = list(db.scalars(archive))
    pending = pending_edit_counts(db, user, [meeting.id for meeting in archived_meetings])
    for meeting in archived_meetings:
        if status and not archive_status_matches(db, meeting, status):
            continue
        key = f"series:{meeting.series_id}" if meeting.series_id else "unassigned"
        if key not in groups:
            series = db.get(MeetingSeries, meeting.series_id) if meeting.series_id else None
            groups[key] = {
                "key": key,
                "name": series.name if series else "未归入系列",
                "meeting_count": 0,
                "pending_edit_count": 0,
                "latest_date": None,
            }
        group = groups[key]
        group["meeting_count"] += 1
        group["pending_edit_count"] += pending.get(meeting.id, 0)
        meeting_date = meeting.meeting_date.isoformat()
        if group["latest_date"] is None or meeting_date > group["latest_date"]:
            group["latest_date"] = meeting_date
    # A series is useful before its first meeting exists. Keep empty series visible
    # in the unfiltered archive (and when searching for its name).
    if not any((start, end, department_id, kind, publication, status)) and scope == "all":
        for series in db.scalars(select(MeetingSeries)):
            key = f"series:{series.id}"
            if key not in groups and (not q or q.casefold() in series.name.casefold()):
                groups[key] = {"key": key, "name": series.name, "meeting_count": 0, "pending_edit_count": 0, "latest_date": None}
    items = sorted(
        groups.values(),
        key=lambda group: (
            group["latest_date"] is None,
            -(int(group["latest_date"].replace("-", "")) if group["latest_date"] else 0),
            group["name"],
        ),
    )
    return {
        "total": len(items),
        "meeting_total": sum(group["meeting_count"] for group in items),
        "items": items,
    }


@app.get("/api/meetings")
def meetings(
    start: date | None = None,
    end: date | None = None,
    department_id: str | None = None,
    kind: str | None = None,
    publication: Literal["draft", "published", "revising"] | None = None,
    status: Literal["已完成", "未完成", "未到期", "不统计", "关闭"] | None = None,
    q: str = "",
    scope: Literal["all", "related"] = "all",
    series_key: str | None = None,
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    query = archive_query(db, user, start, end, department_id, kind, publication, q, scope)
    if series_key:
        if series_key.startswith("series:") and len(series_key) > len("series:"):
            query = query.where(Meeting.series_id == series_key[len("series:") :])
        elif series_key == "unassigned":
            query = query.where(Meeting.series_id.is_(None))
        elif series_key.startswith("unassigned:") and len(series_key) > len("unassigned:"):
            series_department = series_key[len("unassigned:") :]
            query = query.where(Meeting.series_id.is_(None))
            query = query.where(
                Meeting.department_id.is_(None)
                if series_department == "none"
                else Meeting.department_id == series_department
            )
        else:
            raise HTTPException(422, "会议系列参数无效")
    ordered = query.order_by(Meeting.meeting_date.desc(), Meeting.created_at.desc())
    # Review status is stored in the published snapshot rather than a meeting column.
    # Apply this filter before pagination so totals and pages remain accurate across SQLite/PostgreSQL.
    if status:
        candidates = list(db.scalars(ordered))
        filtered = [meeting for meeting in candidates if archive_status_matches(db, meeting, status)]
        total = len(filtered)
        values = filtered[max(offset, 0) : max(offset, 0) + min(max(limit, 1), 100)]
    else:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        values = list(db.scalars(ordered.offset(max(offset, 0)).limit(min(max(limit, 1), 100))))
    result = []
    pending = pending_edit_counts(db, user, [meeting.id for meeting in values])
    for meeting in values:
        view = record(meeting, ("draft",))
        version = db.scalar(
            select(MeetingVersion).where(
                MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == meeting.published_version
            )
        )
        payload = version.snapshot if version else meeting.draft
        view["item_count"] = len(payload.get("items", []))
        from app.services.departments import department_path

        view["department_name"] = department_path(db, meeting.department_id) or "专项会议"
        view["host_name"] = db.get(User, meeting.host_id).name
        counts = {"已完成": 0, "未完成": 0}
        for item in payload.get("items", []):
            if item["section"] == "review" and item.get("status") in counts:
                counts[item["status"]] += len(item.get("owner_ids", []))
        view["rate"] = counts["已完成"] / sum(counts.values()) if sum(counts.values()) else None
        view["pending_issue_count"] = import_context(db, user, meeting)["pending_issue_count"]
        view["can_continue"] = bool(meeting.published_version and meeting.series_id) and can_edit_meeting(
            db, user, meeting
        )
        view["can_request_edit"] = not can_edit_meeting(db, user, meeting)
        view["can_delete"] = user.is_admin or user.id == meeting.host_id
        view["pending_edit_count"] = pending.get(meeting.id, 0)
        result.append(view)
    pending, file_issues, unassigned = pending_quality(
        db, user, start or date.min, end or date.max, department_id, kind, "meeting_department", None
    )
    return {
        "total": total,
        "items": result,
        "pending_meetings": pending,
        "pending_file_issues": file_issues,
        "unknown_import_scope": unassigned,
    }


@app.post("/api/meetings")
def add_meeting(body: MeetingDraft, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return record(create_meeting(db, user, body))


@app.post("/api/meetings/{identifier}/continue")
def add_followup_meeting(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return record(continue_meeting(db, user, identifier))


@app.get("/api/meetings/{identifier}")
def get_meeting(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    meeting = db.get(Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, user, meeting)
    can_edit = can_edit_meeting(db, user, meeting)
    my_request = db.scalar(
        select(MeetingEditRequest)
        .where(
            MeetingEditRequest.meeting_id == meeting.id,
            MeetingEditRequest.applicant_id == user.id,
            MeetingEditRequest.host_id == meeting.host_id,
            MeetingEditRequest.permission_generation == meeting.permission_generation,
        )
        .order_by(MeetingEditRequest.updated_at.desc())
    )
    pending_requests = (
        [
            {**record(request), "applicant_name": db.get(User, request.applicant_id).name}
            for request in db.scalars(
                select(MeetingEditRequest)
                .where(
                    MeetingEditRequest.meeting_id == meeting.id,
                    MeetingEditRequest.host_id == meeting.host_id,
                    MeetingEditRequest.permission_generation == meeting.permission_generation,
                    MeetingEditRequest.state == "pending",
                )
                .order_by(MeetingEditRequest.created_at)
            )
        ]
        if user.is_admin or user.id == meeting.host_id
        else []
    )
    versions = [
        record(v)
        for v in db.scalars(
            select(MeetingVersion)
            .where(MeetingVersion.meeting_id == identifier)
            .order_by(MeetingVersion.number.desc())
        )
    ]
    view = record(meeting, () if can_edit or not meeting.published_version else ("draft",))
    published_files = visible_meeting_attachment_ids(db, user, meeting)
    notifications = []
    if can_edit:
        for event in db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_key.like(f"meeting:{meeting.id}:v%:mail"))
            .order_by(OutboxEvent.created_at.desc())
        ):
            notifications.append(
                {
                    "id": event.id,
                    "version": event.payload.get("version"),
                    "state": event.state,
                    "created_at": event.created_at,
                    "sent_at": event.sent_at,
                    "attempts": event.attempts,
                    "recipient_ids": event.payload.get("recipient_ids", []),
                    "cc_ids": event.payload.get("cc_ids", []),
                    "last_error": event.last_error,
                }
            )
    view.update(
        {
            "versions": versions,
            "can_edit": can_edit,
            "can_delete": user.is_admin or user.id == meeting.host_id,
            "can_continue": bool(meeting.published_version and meeting.series_id) and can_edit,
            "my_edit_request": record(my_request) if my_request else None,
            "pending_edit_requests": pending_requests,
            "notifications": notifications,
            "attachments": [
                record(a, ("storage_key",))
                for a in db.scalars(select(Attachment).where(Attachment.meeting_id == identifier))
                if meeting_attachment_visible(user, meeting, a, published_files)
            ],
        }
    )
    view.update(meeting_context(db, user, meeting, versions, can_edit))
    return view


@app.post("/api/meetings/{identifier}/edit-requests")
def request_meeting_edit(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    meeting = locked_get(db, Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, user, meeting)
    if can_edit_meeting(db, user, meeting):
        raise HTTPException(409, "您已有此会议的编辑权限")
    request = db.scalar(
        select(MeetingEditRequest).where(
            MeetingEditRequest.meeting_id == meeting.id,
            MeetingEditRequest.applicant_id == user.id,
            MeetingEditRequest.permission_generation == meeting.permission_generation,
        ).with_for_update()
    )
    if request and request.state == "pending":
        return record(request)
    if request:
        request.state = "pending"
        request.reviewer_id = None
        request.reviewed_at = None
        request.updated_at = now()
    else:
        request = MeetingEditRequest(
            meeting_id=meeting.id,
            applicant_id=user.id,
            host_id=meeting.host_id,
            permission_generation=meeting.permission_generation,
            state="pending",
        )
        db.add(request)
    db.flush()
    host = db.get(User, meeting.host_id)
    reviewers = [meeting.host_id] if host and host.active else list(
        db.scalars(select(User.id).where(User.is_admin.is_(True), User.active.is_(True)))
    )
    enqueue(
        db,
        f"meeting-edit:{request.id}:apply:{request.updated_at.isoformat()}",
        "wecom",
        {
            "meeting_id": meeting.id,
            "meeting_edit_request_id": request.id,
            "meeting_edit_expected_state": "pending",
            "user_ids": reviewers,
            "title": "会议编辑权限待审批",
            "content": f"{user.name}申请编辑并接续新建《{meeting.title}》，请进入会议详情审批。",
        },
    )
    audit(db, user, "meeting.edit_request", meeting, after={"request_id": request.id})
    return record(request)


class MeetingEditDecision(BaseModel):
    decision: Literal["approved", "rejected"]


@app.post("/api/meetings/{identifier}/edit-requests/{request_id}/decision")
def decide_meeting_edit(
    identifier: str,
    request_id: str,
    body: MeetingEditDecision,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    meeting = locked_get(db, Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    if not (user.is_admin or user.id == meeting.host_id):
        raise HTTPException(403, "只有会议主持人或管理员可以审批")
    request = db.scalar(
        select(MeetingEditRequest)
        .where(MeetingEditRequest.id == request_id, MeetingEditRequest.meeting_id == meeting.id)
        .with_for_update()
    )
    if not request:
        raise HTTPException(404, "审批申请不存在")
    if request.host_id != meeting.host_id or request.permission_generation != meeting.permission_generation:
        raise HTTPException(409, "主持人已变更，请申请人重新提交")
    if request.state != "pending":
        raise HTTPException(409, "此申请已处理，请刷新页面")
    request.state = body.decision
    request.reviewer_id = user.id
    request.reviewed_at = now()
    request.updated_at = now()
    enqueue(
        db,
        f"meeting-edit:{request.id}:result:{request.reviewed_at.isoformat()}",
        "wecom",
        {
            "meeting_id": meeting.id,
            "meeting_edit_request_id": request.id,
            "meeting_edit_expected_state": body.decision,
            "user_ids": [request.applicant_id],
            "title": "会议编辑权限申请结果",
            "content": f"《{meeting.title}》编辑及接续新建权限申请已{'通过' if body.decision == 'approved' else '拒绝'}。",
        },
    )
    audit(db, user, "meeting.edit_decision", meeting, after={"request_id": request.id, "decision": body.decision})
    return record(request)


def deletion_scope(db: Session, meeting: Meeting, *, lock=False):
    query = select(Task).where(Task.source_meeting_id == meeting.id).order_by(Task.id)
    tasks = list(db.scalars(query.with_for_update() if lock else query))
    task_ids = [task.id for task in tasks]
    linked = list(db.scalars(select(NativeTodoLink).where(NativeTodoLink.task_id.in_(task_ids))))
    candidates = list(db.scalars(select(ImportCandidate).where(ImportCandidate.meeting_id == meeting.id)))
    return tasks, task_ids, linked, candidates


def meeting_deletion_dependencies(db: Session, meeting: Meeting, task_ids: list[str]):
    identifiers = set(task_ids)

    def references_source(document):
        return any(
            row.get("task_id") in identifiers
            or (row.get("historical_reference") or {}).get("meeting_id") == meeting.id
            for row in document.get("items", [])
        )

    other_meetings = {
        other.id: other for other in db.scalars(select(Meeting).where(Meeting.id != meeting.id))
    }
    dependencies = {}

    def include(identifier, version=None):
        other = other_meetings[identifier]
        value = dependencies.setdefault(identifier, {
            "id": identifier, "title": other.title, "draft": False, "published_versions": [],
        })
        if version is None:
            value["draft"] = True
        else:
            value["published_versions"].append(version)

    for other in other_meetings.values():
        if other.has_draft and references_source(other.draft):
            include(other.id)
    for version in db.scalars(
        select(MeetingVersion)
        .where(MeetingVersion.meeting_id != meeting.id)
        .order_by(MeetingVersion.number)
    ):
        if references_source(version.snapshot):
            include(version.meeting_id, version.number)
    return list(dependencies.values())


@app.get("/api/meetings/{identifier}/deletion-impact")
def deletion_impact(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    meeting = db.get(Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    if not (user.is_admin or user.id == meeting.host_id):
        raise HTTPException(403, "只有管理员或会议主持人可以删除")
    tasks, task_ids, linked, candidates = deletion_scope(db, meeting)
    dependencies = meeting_deletion_dependencies(db, meeting, task_ids)
    visible_ids = set(db.scalars(visible_meetings(db, user).with_only_columns(Meeting.id)))
    visible_dependencies = [item for item in dependencies if item["id"] in visible_ids]
    return {
        "title": meeting.title,
        "draft_version": meeting.draft_version,
        "published": bool(meeting.published_version),
        "task_count": len(tasks),
        "import_count": len(candidates),
        "active_native_todos": sum(link.state != "finished" for link in linked),
        "blocking_meeting_count": len(dependencies),
        "blocking_meetings": visible_dependencies,
        "hidden_blocking_meeting_count": len(dependencies) - len(visible_dependencies),
        "can_delete": not dependencies and all(link.state == "finished" for link in linked),
    }


class DeleteMeetingRequest(BaseModel):
    expected_draft_version: int


@app.delete("/api/meetings/{identifier}")
def delete_meeting(
    identifier: str,
    background_tasks: BackgroundTasks,
    body: DeleteMeetingRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    meeting = locked_get(db, Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    if not (user.is_admin or user.id == meeting.host_id):
        raise HTTPException(403, "只有管理员或会议主持人可以删除")
    if body is None and (meeting.historical or meeting.published_version):
        raise HTTPException(422, "删除已归档会议需要确认当前版本")
    if body and meeting.draft_version != body.expected_draft_version:
        raise HTTPException(409, "会议已被修改，请刷新后重试删除")
    tasks, task_ids, linked, candidates = deletion_scope(db, meeting, lock=True)
    dependencies = meeting_deletion_dependencies(db, meeting, task_ids)
    if dependencies:
        raise HTTPException(409, "其他会议的草稿或已发布纪要仍引用本会议事项，请先处理关联会议后再删除")
    if any(link.state != "finished" for link in linked):
        raise HTTPException(409, "该会议还有未结束的企业微信原生待办，请先处理后再删除")
    completion_request_ids = set(
        db.scalars(select(CompletionRequest.id).where(CompletionRequest.task_id.in_(task_ids)))
    )

    event_prefixes = [f"meeting:{identifier}:", *(f"task:{task_id}:" for task_id in task_ids)]
    events = list(
        db.scalars(
            select(OutboxEvent)
            .where(
                or_(
                    *(OutboxEvent.event_key.startswith(prefix) for prefix in event_prefixes),
                    OutboxEvent.payload["meeting_id"].as_string() == identifier,
                    OutboxEvent.payload["task_id"].as_string().in_(task_ids),
                )
            )
            .with_for_update()
        )
    )
    if any(event.state == "sending" for event in events):
        raise HTTPException(409, "会议通知或导出正在处理，请稍后重试")
    for event in events:
        if event.state not in {"sent", "skipped"}:
            event.state = "skipped"
            event.last_error = "所属会议已由管理员删除"

    # Daily digests can contain tasks from several meetings. Keep the other entries.
    for event in db.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.channel == "wecom",
            OutboxEvent.state.in_(["pending", "sending", "blocked", "uncertain"]),
        )
        .with_for_update()
    ):
        payload = event.payload
        reminder_ids = payload.get("reminder_task_ids", [])
        verification_ids = payload.get("verification_request_ids", [])
        if not set(reminder_ids).intersection(task_ids) and not set(verification_ids).intersection(
            completion_request_ids
        ):
            continue
        if event.state == "sending":
            raise HTTPException(409, "任务汇总提醒正在发送，请稍后重试")
        remaining_reminders = [item for item in reminder_ids if item not in task_ids]
        remaining_verifications = [item for item in verification_ids if item not in completion_request_ids]
        if remaining_reminders or remaining_verifications:
            event.payload = {
                **payload,
                **({"reminder_task_ids": remaining_reminders} if reminder_ids else {}),
                **({"verification_request_ids": remaining_verifications} if verification_ids else {}),
            }
        else:
            event.state = "skipped"
            event.last_error = "关联任务已由管理员删除"

    source_attachment_ids = set(
        db.scalars(select(ImportSource.attachment_id).where(ImportSource.attachment_id.is_not(None)))
    )
    attachments = list(
        db.scalars(
            select(Attachment).where(
                or_(Attachment.meeting_id == identifier, Attachment.task_id.in_(task_ids))
            )
        )
    )
    storage_keys = [a.storage_key for a in attachments if a.id not in source_attachment_ids]
    for attachment in attachments:
        if attachment.id in source_attachment_ids:
            attachment.meeting_id = None
            attachment.task_id = None
        else:
            db.delete(attachment)
    db.flush()

    for candidate in candidates:
        candidate.meeting_id = None
        candidate.state = "ignored"
        candidate.version += 1
    db.flush()

    db.execute(delete(Assessment).where(Assessment.meeting_id == identifier))
    for assessment in db.scalars(select(Assessment).where(Assessment.task_id.in_(task_ids))):
        assessment.task_id = None
    for version in db.scalars(
        select(TaskVersion).where(
            TaskVersion.source_meeting_id == identifier, TaskVersion.task_id.notin_(task_ids)
        )
    ):
        version.source_meeting_id = None
    db.flush()

    db.execute(delete(CompletionRequest).where(CompletionRequest.task_id.in_(task_ids)))
    db.execute(delete(RecurringRule).where(RecurringRule.source_task_id.in_(task_ids)))
    db.execute(delete(NativeTodoLink).where(NativeTodoLink.task_id.in_(task_ids)))
    db.execute(delete(NativeTodoAttempt).where(NativeTodoAttempt.task_id.in_(task_ids)))
    db.execute(delete(TaskOwner).where(TaskOwner.task_id.in_(task_ids)))
    db.execute(delete(TaskVersion).where(TaskVersion.task_id.in_(task_ids)))
    db.execute(delete(Task).where(Task.id.in_(task_ids)))
    db.execute(delete(MeetingEditRequest).where(MeetingEditRequest.meeting_id == identifier))
    db.execute(delete(MeetingAttendee).where(MeetingAttendee.meeting_id == identifier))
    db.execute(delete(MeetingVersion).where(MeetingVersion.meeting_id == identifier))
    audit(
        db,
        user,
        "meeting.delete",
        meeting,
        before={
            "title": meeting.title,
            "historical": meeting.historical,
            "published_version": meeting.published_version,
        },
        after={"deleted_tasks": len(tasks), "retained_import_sources": len(candidates)},
        reason="管理员删除会议" if user.is_admin else "会议主持人删除会议",
    )
    db.delete(meeting)
    for key in storage_keys:
        background_tasks.add_task((get_settings().storage_dir / key).unlink, missing_ok=True)
    return {"deleted": True, "deleted_tasks": len(tasks)}


@app.put("/api/meetings/{identifier}")
def edit_meeting(
    identifier: str, body: SaveDraft, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return record(save_meeting(db, user, identifier, body.draft, body.expected_version))


class ArchiveSeriesChange(BaseModel):
    series_id: str
    expected_draft_version: int


@app.put("/api/meetings/{identifier}/archive-series")
def change_archive_series(
    identifier: str,
    body: ArchiveSeriesChange,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    meeting = move_meeting_series(db, user, identifier, body.series_id, body.expected_draft_version)
    return {"id": meeting.id, "series_id": meeting.series_id, "draft_version": meeting.draft_version}


@app.post("/api/meetings/{identifier}/publish")
def publish_meeting(
    identifier: str, body: PublishRequest, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return record(publish(db, user, identifier, body))


class Reason(BaseModel):
    reason: str = Field(min_length=1)


@app.post("/api/meetings/{identifier}/revision")
def revise(identifier: str, body: Reason, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return record(revision(db, user, identifier, body.reason))


@app.get("/api/series/{identifier}/carry-forward")
def carry(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return carry_forward(db, user, identifier)


@app.get("/api/tasks")
def tasks(
    mine: bool = False,
    q: str = "",
    state: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    values = []
    for task in db.scalars(visible_tasks(db, user).order_by(Task.due_date, Task.created_at)):
        ids = owner_ids(db, task)
        if mine and user.id not in ids:
            continue
        if state and current_status(task) != state:
            continue
        if q and q not in task.content and q not in task.code:
            continue
        view = task_snapshot(db, task)
        view.update(
            {
                "status": current_status(task),
                "owner_names": [db.get(User, x).name for x in ids],
                "verifier_name": db.get(User, task.verifier_id).name,
                "source_meeting_id": task.source_meeting_id,
                "issuer_name": db.get(User, task.issuer_id).name if task.issuer_id else None,
                "department_id": task.department_id,
            }
        )
        request = db.scalar(
            select(CompletionRequest)
            .where(CompletionRequest.task_id == task.id)
            .order_by(CompletionRequest.created_at.desc())
            .limit(1)
        )
        view["latest_request"] = record(request) if request else None
        values.append(view)
    return values


class DirectAssignmentCreate(BaseModel):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100)
    content: str = Field(min_length=1, max_length=20000)
    requirements: str = Field(default="", max_length=20000)
    deliverable: str = Field(default="", max_length=20000)
    due_date: date
    owner_ids: list[str] = Field(min_length=1, max_length=50)
    verifier_id: str


@app.get("/api/direct-assignments/options")
def direct_assignment_options(db: Session = Depends(get_db), user: User = Depends(current_user)):
    allowed = direct_assignees(db, user)
    return {
        "can_assign": bool(allowed),
        "scope": "all" if can_assign_companywide(user) else "department",
        "native_todo_auto_confirm": get_settings().native_todo_app_enabled and get_settings().native_todo_source_enabled("direct"),
        "wecom_bound": bool(user.wecom_id),
        "users": [
            {"id": person.id, "name": person.name, "department_id": person.department_id}
            for person in allowed
        ],
    }


@app.get("/api/direct-assignments")
def direct_assignments(db: Session = Depends(get_db), user: User = Depends(current_user)):
    values = list(db.scalars(select(Task).where(Task.issuer_id == user.id).order_by(Task.created_at.desc())))
    return [
        {
            **task_snapshot(db, task),
            "owner_names": [db.get(User, identifier).name for identifier in owner_ids(db, task)],
            "status": current_status(task),
            "created_at": task.created_at,
        }
        for task in values
    ]


@app.post("/api/direct-assignments", status_code=201)
def create_direct_assignment(
    body: DirectAssignmentCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if body.idempotency_key is None:
        raise HTTPException(409, "派发页面已更新，请刷新页面后重新确认派发")
    # Serialize this actor's submissions before looking up the request key.
    # The database constraint also protects against duplicate request records.
    locked_get(db, User, user.id)
    db.refresh(user)
    payload = body.model_dump(mode="json", exclude={"idempotency_key"})
    for field in ("content", "requirements", "deliverable"):
        payload[field] = payload[field].strip()
    payload["owner_ids"] = sorted(payload["owner_ids"])
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    previous = db.scalar(select(DirectAssignmentRequest).where(
        DirectAssignmentRequest.actor_id == user.id,
        DirectAssignmentRequest.request_key == body.idempotency_key,
    ))
    if previous:
        if previous.payload_hash != payload_hash:
            raise HTTPException(409, "此次派发内容已变化，请重新确认后派发")
        existing = db.get(Task, previous.task_id)
        task_access(db, user, existing)
        return {**task_snapshot(db, existing), "created_at": existing.created_at}
    allowed = {person.id for person in direct_assignees(db, user)}
    if not allowed:
        raise HTTPException(403, "只有管理员或指定岗位可以派发任务")
    owners = set(body.owner_ids)
    if not owners <= allowed or body.verifier_id not in allowed | {user.id}:
        raise HTTPException(403, "责任人或验证人不在可派发范围")
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "请填写任务内容")
    if len(owners) != len(body.owner_ids):
        raise HTTPException(422, "责任人不能重复")
    if body.verifier_id == user.id:
        active_user(db, user.id)
    departments = {db.get(User, identifier).department_id for identifier in owners}
    task = Task(
        code="RW-" + uid().replace("-", "")[:12].upper(),
        content=content,
        requirements=body.requirements.strip(),
        deliverable=body.deliverable.strip(),
        department_id=departments.pop() if len(departments) == 1 else user.department_id,
        source_meeting_id=None,
        source_item_id=uid(),
        issuer_id=user.id,
        due_date=body.due_date,
        verifier_id=body.verifier_id,
        status="未完成" if body.due_date and body.due_date < today() else "未到期",
    )
    db.add(task)
    db.flush()
    for identifier in owners:
        db.add(TaskOwner(task_id=task.id, user_id=identifier))
    db.flush()
    snapshot = task_snapshot(db, task)
    db.add(DirectAssignmentRequest(
        actor_id=user.id, request_key=body.idempotency_key, payload_hash=payload_hash, task_id=task.id,
    ))
    db.add(
        TaskVersion(
            task_id=task.id,
            number=1,
            snapshot=snapshot,
            reason="直接派发",
            actor_id=user.id,
        )
    )
    audit(db, user, "task.direct_assign", task, after=snapshot)
    enqueue(
        db,
        f"task:{task.id}:assigned",
        "wecom",
        {
            "task_id": task.id,
            "task_version": 1,
            "user_ids": sorted(owners),
            "title": "新的直接派发任务",
            "content": task.content,
        },
    )
    enqueue_native_todo(db, task, "create")
    enqueue_issuer_confirmation(db, task, user)
    return {**snapshot, "created_at": task.created_at}


@app.get("/api/tasks/{identifier}")
def get_task(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    task = db.get(Task, identifier)
    if not task:
        raise HTTPException(404, "任务不存在")
    task_access(db, user, task)
    source_meeting = db.get(Meeting, task.source_meeting_id) if task.source_meeting_id else None
    rule = db.get(RecurringRule, task.recurrence_key) if task.recurrence_key else None
    return {
        **record(task),
        "can_edit": (
            can_edit_meeting(db, user, source_meeting)
            if source_meeting
            else can_manage_direct_task(db, user, task)
        ),
        "issuer_name": db.get(User, task.issuer_id).name if task.issuer_id else None,
        "can_transfer": user.is_admin and source_meeting is None and (task.closed_at is None or rule is not None),
        "can_view_source": source_meeting is not None and db.scalar(
            visible_meetings(db, user).where(Meeting.id == source_meeting.id)
        ) is not None,
        "can_manage_recurrence": can_manage_rule(db, user, rule) if rule else (
            can_edit_meeting(db, user, source_meeting) if source_meeting else can_manage_direct_task(db, user, task)
        ),
        "allowed_owner_ids": (
            [person.id for person in direct_assignees(db, user)] if task.issuer_id else None
        ),
        "owner_ids": owner_ids(db, task),
        "status": current_status(task),
        "versions": [
            record(v)
            for v in db.scalars(
                select(TaskVersion)
                .where(TaskVersion.task_id == identifier)
                .order_by(TaskVersion.number.desc())
            )
        ],
        "requests": [
            record(r)
            for r in db.scalars(
                select(CompletionRequest)
                .where(CompletionRequest.task_id == identifier)
                .order_by(CompletionRequest.created_at.desc())
            )
        ],
    }


@app.put("/api/tasks/{identifier}")
def update_task(
    identifier: str, body: TaskChange, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    task = locked_get(db, Task, identifier)
    if not task:
        raise HTTPException(404, "任务不存在")
    return record(change_task(db, user, task, body))


@app.post("/api/tasks/{identifier}/completion")
def complete(
    identifier: str, body: ApplyCompletion, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return record(apply_completion(db, user, identifier, body))


@app.get("/api/verifications")
def verifications(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = (
        select(CompletionRequest, Task)
        .join(Task, Task.id == CompletionRequest.task_id)
        .where(Task.verifier_id == user.id)
    )
    result = []
    for request, task in db.execute(query.order_by(CompletionRequest.created_at.desc())):
        version = db.scalar(
            select(TaskVersion).where(
                TaskVersion.task_id == task.id,
                TaskVersion.number == request.task_version,
            )
        )
        snapshot = version.snapshot if version else None
        if snapshot is None and request.task_version == task.version:
            snapshot = task_snapshot(db, task)
        result.append(
            {
                **record(request),
                "task": snapshot,
                "task_code": task.code,
                "source_meeting_id": task.source_meeting_id,
                "source_meeting_title": (
                    db.get(Meeting, task.source_meeting_id).title if task.source_meeting_id else "直接派发任务"
                ),
                "can_verify": request.state == "pending"
                and request.task_version == task.version
                and not task.closed_at,
                "applicant_name": db.get(User, request.applicant_id).name,
            }
        )
    return result


@app.post("/api/verifications/{identifier}")
def verify(
    identifier: str, body: VerifyCompletion, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return record(verify_completion(db, user, identifier, body))


class BatchCreate(BaseModel):
    name: str = Field(min_length=1)
    scope_start: date | None = None
    scope_end: date | None = None


@app.get("/api/imports")
def batches(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(ImportBatch).order_by(ImportBatch.created_at.desc())
    batches = list(db.scalars(query))
    if user.is_admin:
        return [record(batch) for batch in batches]
    visible_ids = set(
        db.scalars(
            select(ImportSource.batch_id)
            .join(ImportCandidate, ImportCandidate.source_id == ImportSource.id)
            .where(ImportCandidate.corrected["host_id"].as_string() == user.id)
        )
    )
    return [record(batch) for batch in batches if batch.id in visible_ids]


@app.post("/api/imports")
def new_batch(body: BatchCreate, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    batch = ImportBatch(**body.model_dump(), creator_id=user.id)
    db.add(batch)
    db.flush()
    return record(batch)


@app.post("/api/imports/{identifier}/files")
def import_file(
    identifier: str, file: UploadFile, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    batch = db.get(ImportBatch, identifier)
    if not batch:
        raise HTTPException(404, "导入批次不存在")
    name = Path(file.filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".xls", ".xlsx", ".docx"}:
        raise HTTPException(422, "只支持 XLS、XLSX、DOCX")
    content = file.file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "文件过大")
    with tempfile.TemporaryDirectory(prefix="meeting-import-") as folder:
        path = Path(folder) / name
        path.write_bytes(content)
        source = ingest_file(db, user, batch, path, defer=True)
    return record(source)


def import_source_record(db: Session, source: ImportSource, batch_name: str):
    candidates = list(db.scalars(select(ImportCandidate).where(ImportCandidate.source_id == source.id)))
    confirmed = any(candidate.meeting_id or candidate.state == "confirmed" for candidate in candidates)
    original = db.get(ImportSource, source.duplicate_of) if source.duplicate_of else None
    return {
        **record(source),
        "batch_name": batch_name,
        "original_batch_id": original.batch_id if original else None,
        "original_filename": original.filename if original else None,
        "candidate_count": len(candidates),
        "state_counts": dict(Counter(candidate.state for candidate in candidates)),
        "can_delete": not confirmed,
        "delete_block_reason": "该文件已有会议确认入库" if confirmed else "",
        "issues": [
            record(issue)
            for issue in db.scalars(
                select(ValidationIssue).where(
                    ValidationIssue.source_id == source.id,
                    ValidationIssue.candidate_id.is_(None),
                )
            )
        ],
    }


@app.get("/api/import-sources")
def all_import_sources(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return [
        import_source_record(db, source, batch.name)
        for source, batch in db.execute(
            select(ImportSource, ImportBatch)
            .join(ImportBatch, ImportBatch.id == ImportSource.batch_id)
            .order_by(ImportSource.created_at.desc())
        )
    ]


@app.get("/api/imports/{identifier}/sources")
def import_sources(identifier: str, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    batch = db.get(ImportBatch, identifier)
    if not batch:
        raise HTTPException(404, "导入批次不存在")
    return [
        import_source_record(db, source, batch.name)
        for source in db.scalars(
            select(ImportSource)
            .where(ImportSource.batch_id == identifier)
            .order_by(ImportSource.created_at.desc())
        )
    ]


@app.delete("/api/import-sources/{identifier}")
def delete_import_source(
    identifier: str,
    body: Reason,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    result, storage_key = remove_import_source(db, user, identifier, body.reason)
    db.commit()
    if storage_key:
        try:
            (get_settings().storage_dir / storage_key).unlink(missing_ok=True)
        except OSError:
            result["removed_original"] = False
    return result


class ScopeChange(BaseModel):
    expected_version: int
    state: Literal["pending", "ignored"]
    reason: str = Field(min_length=1)


@app.post("/api/validation/{identifier}/scope")
def candidate_scope(
    identifier: str, body: ScopeChange, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    value = locked_get(db, ImportCandidate, identifier)
    if not value:
        raise HTTPException(404, "导入记录不存在")
    candidate_access(db, user, value)
    if value.state in {"confirmed", "duplicate"}:
        raise HTTPException(409, "已确认或重复记录不能直接改变范围")
    if value.version != body.expected_version:
        raise HTTPException(409, "记录已变化")
    before = {"state": value.state}
    value.state = body.state
    value.version += 1
    audit(db, user, "import.scope", value, before=before, after={"state": value.state}, reason=body.reason)
    return record(value)


@app.post("/api/import-issues/{identifier}/acknowledge")
def acknowledge_source_issue(
    identifier: str, body: Reason, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    issue = db.get(ValidationIssue, identifier)
    if not issue or issue.candidate_id:
        raise HTTPException(404, "文件级问题不存在，条目问题请在校验页面修正")
    issue.resolved, issue.resolver_id, issue.resolution = True, user.id, body.reason
    audit(db, user, "import.acknowledge_file_issue", issue, after={"resolved": True}, reason=body.reason)
    return record(issue)


@app.get("/api/validation/{identifier}/items/{item_id}/suggestions")
def historical_suggestions(
    identifier: str, item_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    from app.services.historical_links import suggestions

    candidate = db.get(ImportCandidate, identifier)
    if not candidate:
        raise HTTPException(404, "导入记录不存在")
    candidate_access(db, user, candidate)
    return suggestions(db, user, candidate.corrected, item_id)


@app.get("/api/validation")
def validation(
    batch_id: str | None = None,
    state: str = "pending",
    q: str = "",
    start: date | None = None,
    end: date | None = None,
    department_id: str | None = None,
    kind: str | None = None,
    scope: Literal["all", "related"] = "all",
    offset: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conditions = []
    if batch_id:
        conditions.append(ImportSource.batch_id == batch_id)
    base_query = (
        select(ImportCandidate, ImportSource)
        .join(ImportSource, ImportSource.id == ImportCandidate.source_id)
        .where(*conditions)
    )
    pairs = list(db.execute(base_query))
    if not user.is_admin:
        pairs = [
            (candidate, source)
            for candidate, source in pairs
            if candidate.corrected.get("host_id") == user.id
        ]
        if (
            not pairs
            and db.scalar(
                select(ImportCandidate.id)
                .where(ImportCandidate.corrected["host_id"].as_string() == user.id)
                .limit(1)
            )
            is None
        ):
            raise HTTPException(403, "历史导入校验需要管理员或对应会议主持人")
    elif scope == "related":
        own_departments = set(department_ids(db, user))

        def related(candidate):
            payload = candidate.corrected
            people = {
                payload.get("host_id"),
                payload.get("recorder_id"),
                *(payload.get("attendee_ids") or []),
            }
            for item in payload.get("items", []):
                people.update(item.get("owner_ids") or [])
                people.add(item.get("verifier_id"))
            return payload.get("department_id") in own_departments or user.id in people

        pairs = [(candidate, source) for candidate, source in pairs if related(candidate)]
    term = q.strip().casefold()
    if term:
        pairs = [
            (candidate, source)
            for candidate, source in pairs
            if term in str(candidate.corrected.get("title") or "").casefold()
            or term in source.filename.casefold()
            or term in candidate.sheet_name.casefold()
        ]
    if start or end:

        def in_dates(candidate):
            try:
                meeting_date = date.fromisoformat(candidate.corrected.get("meeting_date") or "")
            except ValueError:
                return False
            return (not start or meeting_date >= start) and (not end or meeting_date <= end)

        pairs = [(candidate, source) for candidate, source in pairs if in_dates(candidate)]
    if department_id:
        pairs = [
            (candidate, source)
            for candidate, source in pairs
            if candidate.corrected.get("department_id") == department_id
        ]
    if kind:
        pairs = [
            (candidate, source) for candidate, source in pairs if candidate.corrected.get("kind") == kind
        ]
    state_counts = dict(Counter(candidate.state for candidate, _ in pairs))
    if state == "actionable":
        pairs = [
            (candidate, source)
            for candidate, source in pairs
            if candidate.state in {"pending", "out_of_scope"}
        ]
    elif state != "all":
        pairs = [(candidate, source) for candidate, source in pairs if candidate.state == state]
    total = len(pairs)
    summary_candidates = [candidate for candidate, _ in pairs]
    summary_ids = [candidate.id for candidate in summary_candidates]
    issue_counts = Counter()
    if summary_ids:
        issue_counts.update(
            dict(
                db.execute(
                    select(ValidationIssue.code, func.count())
                    .where(
                        ValidationIssue.candidate_id.in_(summary_ids),
                        ValidationIssue.resolved.is_(False),
                    )
                    .group_by(ValidationIssue.code)
                ).all()
            )
        )
    section_counts = Counter()
    for candidate in summary_candidates:
        for item in candidate.corrected.get("items", []):
            section_counts[item.get("section") or "unknown"] += 1
    pairs.sort(
        key=lambda pair: (
            str(pair[0].corrected.get("meeting_date") or ""),
            pair[1].created_at,
            pair[0].sheet_name,
        ),
        reverse=True,
    )
    start = max(offset, 0)
    rows = pairs[start : start + min(max(limit, 1), 100)]
    row_ids = [candidate.id for candidate, _ in rows]
    row_issue_counts = {}
    if row_ids:
        row_issue_counts = dict(
            db.execute(
                select(ValidationIssue.candidate_id, func.count())
                .where(
                    ValidationIssue.candidate_id.in_(row_ids),
                    ValidationIssue.resolved.is_(False),
                )
                .group_by(ValidationIssue.candidate_id)
            ).all()
        )
    return {
        "total": total,
        "state_counts": state_counts,
        "summary": {
            "item_count": sum(section_counts.values()),
            "section_counts": dict(section_counts),
            "issue_counts": dict(issue_counts),
        },
        "items": [
            {
                "id": c.id,
                "version": c.version,
                "state": c.state,
                "filename": s.filename,
                "sheet": c.sheet_name,
                "title": c.corrected.get("title"),
                "department_name": c.corrected.get("department_name"),
                "meeting_date": c.corrected.get("meeting_date"),
                "item_count": len(c.corrected.get("items", [])),
                "meeting_id": c.meeting_id,
                "duplicate_of": c.duplicate_of,
                "issue_count": row_issue_counts.get(c.id, 0),
            }
            for c, s in rows
        ],
    }


@app.get("/api/validation/config")
def validation_config(user: User = Depends(current_user)):
    settings = get_settings()
    return {
        "ai_configured": bool(settings.ai_base_url and settings.ai_api_key and settings.ai_model),
        "ai_model": settings.ai_model
        if settings.ai_base_url and settings.ai_api_key and settings.ai_model
        else "",
    }


@app.get("/api/validation/{identifier}")
def candidate(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    value = db.get(ImportCandidate, identifier)
    if not value:
        raise HTTPException(404, "导入记录不存在")
    candidate_access(db, user, value)
    return {
        **record(value),
        "source": record(db.get(ImportSource, value.source_id)),
        "issues": [
            record(x)
            for x in db.scalars(select(ValidationIssue).where(ValidationIssue.candidate_id == identifier))
        ],
        "source_issues": [
            record(x)
            for x in db.scalars(
                select(ValidationIssue).where(
                    ValidationIssue.source_id == value.source_id,
                    ValidationIssue.candidate_id.is_(None),
                    ValidationIssue.resolved.is_(False),
                )
            )
        ],
        "remaining": remaining_problems(db, value),
    }


class Correction(BaseModel):
    expected_version: int
    reason: str = Field(min_length=1)
    payload: dict


class Confirmation(BaseModel):
    expected_version: int
    reason: str = Field(min_length=1)


class AISuggestionRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=20)


@app.post("/api/validation/{identifier}/ai-suggestions")
def ai_suggestions(
    identifier: str,
    body: AISuggestionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    from app.services.ai_recognition import (
        AIRecognitionFailed,
        AIRecognitionUnavailable,
        suggest,
    )

    value = db.get(ImportCandidate, identifier)
    if not value:
        raise HTTPException(404, "导入记录不存在")
    candidate_access(db, user, value)
    if value.state != "pending":
        raise HTTPException(409, "仅对待确认历史记录生成 AI 建议")
    try:
        result = suggest(db, value, body.item_ids)
    except AIRecognitionUnavailable as exc:
        raise HTTPException(409, str(exc)) from None
    except AIRecognitionFailed as exc:
        raise HTTPException(502, str(exc)) from None
    audit(
        db,
        user,
        "import.ai_suggest",
        value,
        after={"model": result["model"], "item_count": len(body.item_ids)},
        reason="管理员请求 AI 识别建议，未自动修改数据",
    )
    return result


@app.put("/api/validation/{identifier}")
def correct(
    identifier: str, body: Correction, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return record(correct_candidate(db, user, identifier, body.payload, body.expected_version, body.reason))


@app.post("/api/validation/{identifier}/confirm")
def confirm(
    identifier: str, body: Confirmation, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return record(confirm_candidate(db, user, identifier, body.expected_version, body.reason))


@app.get("/api/reports")
def statistics(
    start: date,
    end: date,
    group_by: Literal["meeting_department", "person_department", "person", "kind"] = "meeting_department",
    department_id: str | None = None,
    kind: str | None = None,
    person_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    return report(db, user, start, end, group_by, department_id, kind, person_id, status)


@app.get("/api/files/{identifier}")
def download(identifier: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    attachment = db.get(Attachment, identifier)
    if not attachment:
        raise HTTPException(404, "附件不存在")
    if attachment.kind == "original" and not user.is_admin:
        raise HTTPException(403, "原工作簿可能包含多个部门资料，仅管理员可下载")
    if not user.is_admin and attachment.uploader_id != user.id:
        if attachment.meeting_id:
            meeting = db.get(Meeting, attachment.meeting_id)
            meeting_access(db, user, meeting)
            if not meeting_attachment_visible(
                user, meeting, attachment, visible_meeting_attachment_ids(db, user, meeting)
            ):
                raise HTTPException(403, "该附件尚未随会议发布")
        elif attachment.task_id:
            task_access(db, user, db.get(Task, attachment.task_id))
        else:
            raise HTTPException(403, "无权下载此文件")
    path = (get_settings().storage_dir / attachment.storage_key).resolve()
    if not path.is_relative_to(get_settings().storage_dir.resolve()) or not path.is_file():
        raise HTTPException(404, "文件不可用")
    return FileResponse(path, filename=attachment.filename, media_type=attachment.mime_type)


@app.get("/api/admin/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return [
        record(e) for e in db.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(200))
    ]


@app.get("/api/admin/audit")
def audits(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return [record(a) for a in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200))]


class AliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=120)
    user_id: str
    department_id: str | None = None


@app.post("/api/admin/aliases")
def alias(body: AliasCreate, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    if not locked_get(db, User, body.user_id):
        raise HTTPException(422, "成员不存在")
    body.alias = body.alias.strip()
    if not body.alias:
        raise HTTPException(422, "别名不能为空")
    if body.department_id and not db.get(Department, body.department_id):
        raise HTTPException(422, "部门不存在")
    existing = db.scalar(
        select(UserAlias).where(
            UserAlias.alias == body.alias,
            UserAlias.user_id == body.user_id,
            UserAlias.department_id == body.department_id,
        )
    )
    if existing:
        return record(existing)
    entry = UserAlias(**body.model_dump())
    db.add(entry)
    db.flush()
    audit(db, user, "directory.alias", entry, after=body.model_dump())
    return record(entry)


@app.get("/api/admin/aliases")
def list_aliases(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return [record(entry) for entry in db.scalars(select(UserAlias).order_by(UserAlias.alias, UserAlias.id))]


class AliasRemoval(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


@app.delete("/api/admin/aliases/{identifier}")
def remove_alias(
    identifier: str, body: AliasRemoval, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    if len(body.reason.strip()) < 2:
        raise HTTPException(422, "请填写删除原因")
    entry = locked_get(db, UserAlias, identifier)
    if not entry:
        raise HTTPException(404, "别名已不存在，请刷新")
    audit(
        db,
        user,
        "directory.alias.remove",
        entry,
        before={"alias": entry.alias, "user_id": entry.user_id, "department_id": entry.department_id},
        reason=body.reason.strip(),
    )
    db.delete(entry)
    return {"removed": True}
