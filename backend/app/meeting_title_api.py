"""Rename a meeting's display title without republishing its historical files."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db, locked_get
from app.core.security import current_user
from app.models import Meeting, MeetingSeries, User
from app.services.access import visible_series
from app.services.business import audit
from app.services.meeting_views import apply_series_key
from app.services.role_permissions import meeting_allowed, permission_scope

router = APIRouter(prefix="/api")


def renameable_series(db: Session, user: User):
    statement = visible_series(db, user)
    if not user.active:
        return statement.where(False)
    if user.is_admin:
        return statement
    scope = permission_scope(db, user, "series.rename")
    if scope.all:
        return statement
    return statement.where(
        or_(
            MeetingSeries.department_id.in_(scope.departments),
            MeetingSeries.id.in_(select(Meeting.series_id).where(Meeting.recorder_id == user.id)),
        )
    )


@router.get("/meeting-series-name-options")
def series_name_options(db: Session = Depends(get_db), user: User = Depends(current_user)):
    allowed = set(db.scalars(renameable_series(db, user).with_only_columns(MeetingSeries.id)))
    rows = (
        list(db.scalars(visible_series(db, user).order_by(MeetingSeries.name, MeetingSeries.id)))
        if user.active
        else []
    )
    return {
        "total": len(rows),
        "items": [
            {"id": series.id, "name": series.name, "can_rename": series.id in allowed} for series in rows
        ],
    }


class RenameSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=250)
    expected_name: str = Field(max_length=250)


@router.put("/meeting-series/{identifier}/name")
def rename_series(
    identifier: str,
    body: RenameSeries,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    series = locked_get(db, MeetingSeries, identifier)
    if series is None:
        raise HTTPException(404, "会议系列不存在")
    if db.scalar(renameable_series(db, user).where(MeetingSeries.id == identifier)) is None:
        raise HTTPException(403, "只有该系列会议的编制人或系统管理员可以修改会议系列名称")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请输入会议系列名称")
    if series.name != body.expected_name:
        raise HTTPException(409, "会议系列名称已被修改，请重新选择系列后再试")
    if name != series.name:
        duplicate = db.scalar(
            select(MeetingSeries.id).where(
                MeetingSeries.department_id == series.department_id,
                MeetingSeries.name == name,
                MeetingSeries.id != series.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(409, "该部门已有同名会议系列，请使用其他名称")
        before = {"name": series.name}
        try:
            with db.begin_nested():
                series.name = name
                audit(db, user, "meeting_series.name.rename", series, before=before, after={"name": name})
                db.flush()
        except IntegrityError as error:
            raise HTTPException(409, "该部门已有同名会议系列，请使用其他名称") from error
    return {"id": series.id, "name": series.name}


def renameable_meetings(user: User, db=None):
    statement = select(Meeting)
    if not user.active:
        return statement.where(False)
    if user.is_admin:
        return statement
    scope = permission_scope(db, user, "meetings.rename") if db is not None else None
    if scope and scope.all:
        return statement
    return statement.where(
        or_(
            Meeting.recorder_id == user.id,
            Meeting.host_id == user.id,
            Meeting.department_id.in_(scope.departments if scope else set()),
        )
    )


def can_rename_meetings(db: Session, user: User) -> bool:
    return bool(
        user.active
        and (
            user.is_admin
            or db.scalar(renameable_meetings(user, db).with_only_columns(Meeting.id).limit(1)) is not None
        )
    )


@router.get("/meeting-title-options")
def title_options(
    q: str = Query(default="", max_length=500),
    series_key: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    statement = apply_series_key(renameable_meetings(user, db), series_key)
    statement = statement.outerjoin(MeetingSeries, MeetingSeries.id == Meeting.series_id)
    if q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(or_(Meeting.title.ilike(term), MeetingSeries.name.ilike(term)))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(
        statement.add_columns(MeetingSeries.name)
        .order_by(
            Meeting.meeting_date.desc(),
            Meeting.created_at.desc(),
            Meeting.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return {
        "total": total,
        "items": [
            {
                "id": meeting.id,
                "title": meeting.title,
                "meeting_date": meeting.meeting_date,
                "series_id": meeting.series_id,
                "series_name": name or "未归入系列",
                "draft_version": meeting.draft_version,
            }
            for meeting, name in rows
        ],
    }


class RenameMeeting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    expected_title: str = Field(max_length=500)
    expected_draft_version: int = Field(ge=1)


@router.put("/meetings/{identifier}/title")
def rename_meeting(
    identifier: str,
    body: RenameMeeting,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    meeting = locked_get(db, Meeting, identifier)
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    if not meeting_allowed(db, user, meeting, "rename"):
        raise HTTPException(403, "只有该场会议的编制人或系统管理员可以修改会议名称")
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "请输入会议名称")
    if meeting.title != body.expected_title or meeting.draft_version != body.expected_draft_version:
        raise HTTPException(409, "会议已被修改，请重新选择会议后再试")
    if meeting.has_draft and meeting.draft.get("title", meeting.title) != meeting.title:
        raise HTTPException(409, "会议草稿中已有不同的名称，请先进入会议确认草稿名称")
    if title != meeting.title:
        before = {"title": meeting.title}
        meeting.title = title
        meeting.draft = {**meeting.draft, "title": title}
        # Invalidate an editor loaded before the rename, including a published meeting.
        meeting.draft_version += 1
        audit(db, user, "meeting.title.rename", meeting, before=before, after={"title": title})
        db.flush()
    return {"id": meeting.id, "title": meeting.title, "draft_version": meeting.draft_version}
