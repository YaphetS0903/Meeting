"""Recognize a task sheet and create one editable online or historical meeting."""

import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select

from app.core.db import locked_get
from app.importers.parser import digest, parse_date, parse_file
from app.models import Attachment, Department, Meeting, MeetingSeries, MeetingSheetImport, User, UserAlias
from app.schemas import MeetingDraft, MeetingItem
from app.services.access import meeting_access, visible_series
from app.services.business import audit, create_meeting
from app.services.importing import dedup_lock, save_original
from app.services.role_permissions import require as require_permission


class ImportHeaders(BaseModel):
    candidate_index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    meeting_date: date
    department_id: str | None = None
    kind: Literal["department", "special"] = "department"
    host_id: str
    recorder_id: str | None = None
    history_backfill: bool = False
    backfill_all_new: bool = False

    @model_validator(mode="after")
    def historical_sections_only(self):
        if self.backfill_all_new and not self.history_backfill:
            raise ValueError("只有补录历史会议可以将任务统一放入本次新安排")
        return self


def series_access(db, actor, series_id):
    series = db.scalar(visible_series(db, actor).where(MeetingSeries.id == series_id))
    if not series:
        raise HTTPException(403, "无权在该会议系列导入任务单")
    own = (
        series.department_id == actor.department_id
        or db.scalar(
            select(Meeting.id)
            .where(
                Meeting.series_id == series.id,
                or_(Meeting.host_id == actor.id, Meeting.recorder_id == actor.id),
            )
            .limit(1)
        )
        is not None
    )
    require_permission(db, actor, "meetings.import", series.department_id, related=own)
    return series


def match_person(db, name, department_id):
    ids = set(db.scalars(select(User.id).where(User.name == name, User.active.is_(True))))
    ids.update(
        db.scalars(
            select(UserAlias.user_id)
            .join(User)
            .where(
                UserAlias.alias == name,
                User.active.is_(True),
                or_(UserAlias.department_id.is_(None), UserAlias.department_id == department_id),
            )
        )
    )
    return next(iter(ids)) if len(ids) == 1 else None


def dated_history(raw, year):
    """Only consume a cell made entirely of a sequence of explicit dates."""
    parts = [p.strip() for p in re.split(r"[\n\r、,，;；]+", raw) if p.strip()]
    if len(parts) < 2:
        return []
    parsed = [parse_date(part, year) for part in parts]
    return [value for value, _ in parsed] if all(value and not error for value, error in parsed) else []


def prepare_candidate(db, candidate, actor):
    payload = deepcopy(candidate.payload)
    departments = list(db.scalars(select(Department).where(Department.name == payload["department_name"])))
    payload["department_id"] = departments[0].id if len(departments) == 1 else None
    department = payload["department_id"]
    issues = []

    def issue(code, field, message, raw="", item_id=None, row=None, review_item_id=None):
        value = dict(code=code, field=field, message=message, raw=raw, item_id=item_id, row=row)
        if review_item_id:
            value["review_item_id"] = review_item_id
        value["id"] = digest(value)[:32]
        if not any(i["id"] == value["id"] for i in issues):
            issues.append(value)

    for role in ("host", "recorder"):
        name = payload.get(role + "_name", "")
        payload[role + "_id"] = match_person(db, name, department) if name else None
        if name and not payload[role + "_id"]:
            issue("name_unmatched", role + "_id", "姓名未唯一匹配，请核对人员", name)
    # The person importing is the initial recorder unless the admin preserves an explicit recorder.
    payload["recorder_id"] = payload["recorder_id"] if actor.is_admin else actor.id
    payload["recorder_id"] = payload["recorder_id"] or actor.id
    payload["attendee_ids"] = []
    for name in payload.get("attendee_names", []):
        identifier = match_person(db, name, department)
        if identifier:
            payload["attendee_ids"].append(identifier)
        else:
            issue("attendee_unmatched", "attendee_ids", "参会人未唯一匹配，请补充或确认原始名单", name)
    year = int(payload["meeting_date"][:4]) if payload.get("meeting_date") else None
    for item in payload["items"]:
        item["owner_ids"] = []
        for name in item.get("owner_names", []):
            identifier = match_person(db, name, department)
            if identifier:
                item["owner_ids"].append(identifier)
            else:
                issue(
                    "owner_unmatched",
                    "owner_ids",
                    "责任人未唯一匹配，请手动选择后确认",
                    name,
                    item["id"],
                    item["source_row"],
                )
        item["verifier_id"] = match_person(db, item.get("verifier_name", ""), department)
        if item.get("verifier_name") and not item["verifier_id"]:
            issue(
                "verifier_unmatched",
                "verifier_id",
                "原表验证人未匹配，请手动选择",
                item["verifier_name"],
                item["id"],
                item["source_row"],
            )
        history = dated_history(item.get("raw_due_date", ""), year)
        if history:
            item["due_date"] = history[-1]
        item["date_history"] = history[:-1]
        if "延期" in item.get("notes", ""):
            note_dates = re.findall(r"(?:20\d{2}[年/.-])?\d{1,2}[月/.-]\d{1,2}日?", item["notes"])
            if any(parse_date(value, year)[0] not in {None, item.get("due_date")} for value in note_dates):
                issue(
                    "date_note_conflict",
                    "due_date",
                    "计划完成日期与备注中的延期日期不一致，请手动核对；系统保留日期栏原值",
                    item["raw_due_date"] + "；备注：" + item["notes"],
                    item["id"],
                    item["source_row"],
                )
    for original in candidate.issues:
        match = re.match(r"items\.(\d+)\.(.*)", original.field_path)
        item = payload["items"][int(match[1])] if match else None
        if item and original.code == "invalid_due_date" and item.get("date_history"):
            continue
        issue(
            original.code,
            match[2] if match else original.field_path,
            original.message,
            original.raw,
            item["id"] if item else None,
            original.row,
        )
    for item in payload["items"]:
        if item["section"] != "new":
            continue
        matches = [
            prior
            for prior in payload["items"]
            if prior["section"] == "review"
            and prior.get("status") in {"未完成", "未到期"}
            and prior["content"] == item["content"]
            and set(prior["owner_names"]) == set(item["owner_names"])
        ]
        if len(matches) == 1:
            issue(
                "continuation_check",
                "continuation",
                "本次任务与一条上期未结束任务同名，请确认是延续还是独立新任务，避免下次重复带入",
                item["content"],
                item["id"],
                item["source_row"],
                matches[0]["id"],
            )
    usable = all(i["section"] in {"review", "new"} for i in payload["items"])
    return dict(
        sheet=candidate.sheet,
        payload=payload,
        issues=issues,
        usable=usable,
        content_hash=digest(candidate.payload),
        review_count=sum(i["section"] == "review" for i in payload["items"]),
        new_count=sum(i["section"] == "new" for i in payload["items"]),
    )


def recognize(db, actor, series_id, path: Path):
    series_access(db, actor, series_id)
    if path.suffix.lower() not in {".xls", ".xlsx"}:
        raise HTTPException(422, "请选择 .xls 或 .xlsx 格式的 Excel 任务单")
    parsed = parse_file(path)
    if not parsed.candidates:
        raise HTTPException(422, "未识别到会议任务单，请检查会议信息和上期、本次任务分区标题")
    attachment = save_original(db, actor, path)
    attachment.kind = "meeting_sheet"
    source = MeetingSheetImport(
        series_id=series_id,
        creator_id=actor.id,
        attachment_id=attachment.id,
        candidates=[prepare_candidate(db, c, actor) for c in parsed.candidates],
        diagnostics=[
            *parsed.diagnostics,
            *[
                dict(code="attachment_sheet", sheet=name, message="作为原文件附件保留，不并入会议任务")
                for name in parsed.attachment_sheets
            ],
        ],
    )
    db.add(source)
    db.flush()
    audit(db, actor, "meeting_sheet.recognize", source, after={"filename": path.name, "series_id": series_id})
    return source


def source_access(db, actor, identifier, *, lock=False):
    source = (
        locked_get(db, MeetingSheetImport, identifier) if lock else db.get(MeetingSheetImport, identifier)
    )
    if not source:
        raise HTTPException(404, "任务单识别记录不存在")
    if source.meeting_id:
        meeting_access(db, actor, db.get(Meeting, source.meeting_id), edit=True)
    elif not actor.is_admin and source.creator_id != actor.id:
        raise HTTPException(403, "无权使用其他人的任务单识别记录")
    return source


def existing_import(meeting, headers):
    if bool(meeting.draft.get("history_backfill")) != headers.history_backfill:
        raise HTTPException(409, "该任务单已有会议，请打开已有会议核对；重复导入不能切换历史补录模式")
    if headers.backfill_all_new and any(row["section"] != "new" for row in meeting.draft.get("items", [])):
        raise HTTPException(409, "该任务单已有会议且分区不同，请打开已有会议核对，避免重复导入")
    return meeting, True


def create_from_source(db, actor, identifier, headers: ImportHeaders):
    source = source_access(db, actor, identifier, lock=True)
    series_access(db, actor, source.series_id)
    if source.meeting_id:
        return existing_import(db.get(Meeting, source.meeting_id), headers)
    if not source.candidates:
        raise HTTPException(409, "任务单尚未识别完成，请等待识别成功后生成草稿")
    if headers.candidate_index >= len(source.candidates):
        raise HTTPException(422, "请选择已识别的会议工作表")
    candidate = source.candidates[headers.candidate_index]
    if not candidate["usable"] and not headers.backfill_all_new:
        raise HTTPException(422, "存在无法确定分区的任务，请明确上期、本次任务标题后重新上传")
    fingerprint = digest([source.series_id, candidate["content_hash"]])
    dedup_lock(db, fingerprint)
    duplicate = db.scalar(select(MeetingSheetImport).where(MeetingSheetImport.fingerprint == fingerprint))
    if duplicate and duplicate.meeting_id:
        meeting = db.get(Meeting, duplicate.meeting_id)
        meeting_access(db, actor, meeting, edit=True)
        return existing_import(meeting, headers)
    rows = []
    for original in candidate["payload"]["items"]:
        values = {
            key: original[key]
            for key in (
                "id",
                "section",
                "category",
                "content",
                "owner_ids",
                "due_date",
                "verifier_id",
                "deliverable",
                "notes",
            )
        }
        if headers.backfill_all_new:
            values["section"] = "new"
        values["status"] = (original.get("status") or "") if values["section"] == "review" else "未到期"
        values["source"] = dict(
            sheet_import_id=source.id,
            sheet_import_item_id=original["id"],
            sheet=original["source_sheet"],
            row=original["source_row"],
            date_history=original.get("date_history", []),
            raw_due_date=original.get("raw_due_date", ""),
            raw_owners=original.get("raw_owners", ""),
            raw_status=original.get("raw_status", ""),
        )
        if headers.backfill_all_new:
            values["source"]["original_section"] = original["section"]
        if values["section"] == "review":
            values["historical_work_id"] = f"sheet:{source.id}:{original['id']}"
        rows.append(MeetingItem.model_validate(values))
    p = candidate["payload"]
    draft = MeetingDraft(
        **headers.model_dump(exclude={"candidate_index", "backfill_all_new"}),
        series_id=source.series_id,
        meeting_time=p.get("meeting_time", ""),
        location=p.get("location", ""),
        attendee_ids=list(dict.fromkeys(p.get("attendee_ids", []))),
        absentees=p.get("absentees", ""),
        decisions=p.get("decisions", ""),
        items=rows,
        sheet_import_id=source.id,
    )
    meeting = create_meeting(db, actor, draft, sheet_import=source)
    source.meeting_id, source.selected_index, source.fingerprint = (
        meeting.id,
        headers.candidate_index,
        fingerprint,
    )
    from app.services.sheet_import_jobs import originals

    for attachment_id in originals(source):
        db.get(Attachment, attachment_id).meeting_id = meeting.id
    audit(
        db,
        actor,
        "meeting_sheet.create_draft",
        source,
        after={
            "meeting_id": meeting.id,
            "review_count": sum(item.section == "review" for item in draft.items),
            "new_count": sum(item.section == "new" for item in draft.items),
        },
    )
    db.flush()
    return meeting, False


def review_source(db, actor, draft, item):
    """Authenticate imported review provenance without creating a fake historical meeting."""
    identifier = item.source.get("sheet_import_id")
    source = db.get(MeetingSheetImport, identifier) if identifier else None
    if not source or not source.meeting_id or source.selected_index is None:
        raise HTTPException(422, "导入上期任务的来源不存在，请重新导入")
    origin = db.get(Meeting, source.meeting_id)
    meeting_access(db, actor, origin)
    if not origin.published_version and draft.sheet_import_id != source.id:
        raise HTTPException(422, "上期任务的导入来源尚未发布")
    if origin.meeting_date > draft.meeting_date or origin.series_id != draft.series_id:
        raise HTTPException(422, "导入来源必须属于同一会议系列，且不晚于本次会议")
    original = next(
        (
            r
            for r in source.candidates[source.selected_index]["payload"]["items"]
            if r["id"] == item.source.get("sheet_import_item_id") and r["section"] == "review"
        ),
        None,
    )
    if not original or item.historical_work_id != f"sheet:{source.id}:{original['id']}":
        raise HTTPException(422, "导入上期任务的追溯编号不一致")
    return original


def validate_confirmations(db, actor, draft):
    if not draft.sheet_import_id:
        return
    source = source_access(db, actor, draft.sheet_import_id)
    if source.selected_index is None:
        raise HTTPException(422, "任务单尚未生成会议草稿")
    existing = {item.source.get("sheet_import_item_id") for item in draft.items}
    unresolved = [
        issue
        for issue in source.candidates[source.selected_index]["issues"]
        if issue["id"] not in draft.import_confirmed_issues
        and (not issue["item_id"] or issue["item_id"] in existing)
    ]
    if unresolved:
        raise HTTPException(422, f"还有 {len(unresolved)} 项导入内容待核对，请在会议信息或对应任务行核对确认")
