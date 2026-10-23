import copy
import hashlib
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import locked_get
from app.importers.parser import STATUSES, digest, parse_file
from app.models import (
    Attachment,
    Department,
    ImportBatch,
    ImportCandidate,
    ImportSource,
    Meeting,
    NotificationLog,
    OutboxEvent,
    User,
    UserAlias,
    ValidationIssue,
    uid,
)
from app.schemas import HistoricalReference, MeetingDraft, MeetingItem, PublishRequest
from app.services.business import audit, create_meeting, enqueue, publish


def dedup_lock(db, fingerprint):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(fingerprint[:15], 16)})


def content_fingerprint(payload: dict):
    value = copy.deepcopy(payload)
    # Provenance and formatting do not make a second copy of a meeting a new meeting.
    for item in value["items"]:
        for key in ["id", "source_row", "source_sheet"]:
            item.pop(key, None)
    return digest(value)


def identity_fingerprint(payload: dict):
    if not payload.get("meeting_date"):
        return None
    if payload.get("kind") != "special" and not payload.get("department_name"):
        return None
    return digest(
        [
            payload.get("department_name") or "专项会议",
            payload["meeting_date"],
            payload["title"],
            payload["kind"],
        ]
    )


def resolve_name(db: Session, name: str, department_id: str | None = None) -> str | None:
    ids = set(db.scalars(select(User.id).where(User.name == name, User.active.is_(True))))
    ids.update(
        db.scalars(
            select(UserAlias.user_id)
            .join(User, User.id == UserAlias.user_id)
            .where(
                UserAlias.alias == name,
                User.active.is_(True),
                or_(UserAlias.department_id.is_(None), UserAlias.department_id == department_id),
            )
        )
    )
    if department_id and len(ids) > 1:
        department_ids = set(
            db.scalars(
                select(User.id).where(
                    User.id.in_(ids),
                    User.department_id == department_id,
                    User.active.is_(True),
                )
            )
        )
        if len(department_ids) == 1:
            return next(iter(department_ids))
    return next(iter(ids)) if len(ids) == 1 else None


def map_people(db: Session, payload: dict):
    value = copy.deepcopy(payload)
    issues = []
    departments = list(db.scalars(select(Department).where(Department.name == value["department_name"])))
    value["department_id"] = departments[0].id if len(departments) == 1 else None
    if not value["department_id"] and value.get("kind") != "special":
        issues.append(("department_unmatched", "department_id", "所属部门未与通讯录匹配"))
    value["host_id"] = resolve_name(db, value["host_name"], value["department_id"])
    value["recorder_id"] = resolve_name(db, value.get("recorder_name", ""), value["department_id"])
    value["reviewer_id"] = None
    if not value["host_id"]:
        issues.append(("host_unmatched", "host_id", "Excel 主持人未对应到企业微信成员"))
    if value.get("recorder_name") and not value["recorder_id"]:
        issues.append(("recorder_unmatched", "recorder_id", "Excel 编制人未对应到企业微信成员"))
    value["attendee_ids"] = [
        identifier
        for name in value["attendee_names"]
        if (identifier := resolve_name(db, name, value["department_id"]))
    ]
    if len(value["attendee_ids"]) < len(value["attendee_names"]):
        issues.append(("attendees_unmatched", "attendee_ids", "部分参会人未匹配，请确认参会人及原始名单"))
    for i, item in enumerate(value["items"]):
        ids = [
            identifier
            for name in item["owner_names"]
            if (identifier := resolve_name(db, name, value["department_id"]))
        ]
        item["owner_ids"] = list(dict.fromkeys(ids))
        if not ids or len(ids) != len(item["owner_names"]):
            issues.append(
                ("owners_unmatched", f"items.{i}.owner_ids", "Excel 责任人未全部对应到企业微信成员")
            )
        item["verifier_id"] = (
            resolve_name(db, item["verifier_name"], value["department_id"]) if item["verifier_name"] else None
        )
        if item["verifier_name"] and not item["verifier_id"]:
            issues.append(
                ("verifier_unmatched", f"items.{i}.verifier_id", "指定验证人无法匹配，不能自动改为主持人")
            )
    return value, issues


def save_original(db, actor, path: Path):
    key = f"originals/{uid()}{path.suffix.lower()}"
    target = get_settings().storage_dir / key
    target.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_bytes()
    target.write_bytes(content)
    attachment = Attachment(
        filename=path.name,
        storage_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/octet-stream",
        size=len(content),
        uploader_id=actor.id,
        kind="original",
    )
    db.add(attachment)
    db.flush()
    return attachment


def remove_import_source(db: Session, actor: User, identifier: str, reason: str):
    source = locked_get(db, ImportSource, identifier)
    if not source:
        raise HTTPException(404, "导入文件记录不存在")

    sources = [source]
    if not source.duplicate_of:
        sources.extend(
            db.scalars(select(ImportSource).where(ImportSource.duplicate_of == source.id).with_for_update())
        )
    source_ids = {item.id for item in sources}
    candidates = list(
        db.scalars(select(ImportCandidate).where(ImportCandidate.source_id.in_(source_ids)).with_for_update())
    )
    if any(item.meeting_id or item.state == "confirmed" for item in candidates):
        raise HTTPException(409, "该文件已有会议确认入库，为保留来源追溯不能删除")

    candidate_ids = {item.id for item in candidates}
    if candidate_ids:
        dependent = db.scalar(
            select(ImportCandidate.id).where(
                ImportCandidate.duplicate_of.in_(candidate_ids),
                ImportCandidate.source_id.notin_(source_ids),
            )
        )
        if dependent:
            raise HTTPException(409, "其他导入文件仍引用此记录，请先处理对应的重复会议记录")

    events = list(
        db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.channel == "import",
                OutboxEvent.event_key.in_([f"import:{item}" for item in source_ids]),
            )
            .with_for_update()
        )
    )
    if any(event.state == "sending" for event in events):
        raise HTTPException(409, "文件正在解析，请稍后再删除")

    attachment = db.get(Attachment, source.attachment_id) if not source.duplicate_of else None
    storage_key = None
    if attachment:
        other_reference = db.scalar(
            select(ImportSource.id).where(
                ImportSource.attachment_id == attachment.id,
                ImportSource.id.notin_(source_ids),
            )
        )
        if not other_reference and not attachment.meeting_id and not attachment.task_id:
            storage_key = attachment.storage_key

    audit(
        db,
        actor,
        "import.delete_source",
        source,
        before={
            "filename": source.filename,
            "sha256": source.sha256,
            "source_ids": sorted(source_ids),
            "candidate_count": len(candidates),
        },
        reason=reason,
    )

    event_ids = {event.id for event in events}
    if event_ids:
        for log in db.scalars(select(NotificationLog).where(NotificationLog.event_id.in_(event_ids))):
            db.delete(log)
        for event in events:
            db.delete(event)
        db.flush()

    for issue in db.scalars(select(ValidationIssue).where(ValidationIssue.source_id.in_(source_ids))):
        db.delete(issue)
    db.flush()

    child_candidates = [item for item in candidates if item.duplicate_of in candidate_ids]
    for item in child_candidates:
        db.delete(item)
    db.flush()
    for item in candidates:
        if item not in child_candidates:
            db.delete(item)
    db.flush()

    duplicate_sources = [item for item in sources if item.id != source.id]
    for item in duplicate_sources:
        db.delete(item)
    db.flush()
    db.delete(source)
    db.flush()
    if attachment and storage_key:
        db.delete(attachment)
        db.flush()

    return (
        {
            "deleted_sources": len(sources),
            "deleted_candidates": len(candidates),
            "removed_original": bool(storage_key),
        },
        storage_key,
    )


def ingest_file(db: Session, actor: User, batch: ImportBatch, path: Path, *, defer=False):
    if not actor.is_admin:
        raise HTTPException(403, "历史文件导入需要管理员")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    dedup_lock(db, sha)
    duplicate = db.scalar(
        select(ImportSource).where(ImportSource.sha256 == sha, ImportSource.duplicate_of.is_(None))
    )
    source = ImportSource(batch_id=batch.id, filename=path.name, sha256=sha)
    if duplicate:
        source.duplicate_of = duplicate.id
        source.attachment_id = duplicate.attachment_id
        db.add(source)
        db.flush()
        return source
    attachment = save_original(db, actor, path)
    source.attachment_id = attachment.id
    db.add(source)
    db.flush()
    if defer:
        source.diagnostics = [{"code": "queued", "message": "已上传，等待后台解析"}]
        enqueue(db, f"import:{source.id}", "import", {"source_id": source.id, "actor_id": actor.id})
        return source
    return parse_source(db, actor, batch, source, path)


def parse_source(db, actor, batch, source, path):
    if db.scalar(select(ImportCandidate.id).where(ImportCandidate.source_id == source.id).limit(1)):
        return source
    result = parse_file(path)
    source.diagnostics = result.diagnostics + [
        {"code": "attachment_sheet", "sheet": name, "message": "作为附件保留，不计任务"}
        for name in result.attachment_sheets
    ]
    for diagnostic in result.diagnostics:
        db.add(
            ValidationIssue(
                source_id=source.id,
                code=diagnostic["code"],
                message=diagnostic["message"],
                original=diagnostic,
            )
        )
    for parsed in result.candidates:
        original = parsed.payload
        fingerprint = content_fingerprint(original)
        dedup_lock(db, fingerprint)
        same = db.scalar(
            select(ImportCandidate).where(
                ImportCandidate.content_hash == fingerprint, ImportCandidate.duplicate_of.is_(None)
            )
        )
        corrected, mapping_issues = map_people(db, original)
        candidate = ImportCandidate(
            source_id=source.id,
            sheet_name=parsed.sheet,
            original=original,
            corrected=corrected,
            content_hash=fingerprint,
            identity_hash=identity_fingerprint(original),
        )
        if same:
            candidate.duplicate_of = same.id
            candidate.state = "duplicate"
        elif original["meeting_date"]:
            d = date.fromisoformat(original["meeting_date"])
            if (batch.scope_start and d < batch.scope_start) or (batch.scope_end and d > batch.scope_end):
                candidate.state = "out_of_scope"
        db.add(candidate)
        db.flush()
        if candidate.state in {"duplicate", "out_of_scope"}:
            continue
        for issue in parsed.issues:
            db.add(
                ValidationIssue(
                    candidate_id=candidate.id,
                    source_id=source.id,
                    code=issue.code,
                    field_path=issue.field_path,
                    message=issue.message,
                    original={"raw": issue.raw, "row": issue.row, "sheet": parsed.sheet},
                )
            )
        for code, field_path, message in mapping_issues:
            db.add(
                ValidationIssue(
                    candidate_id=candidate.id,
                    source_id=source.id,
                    code=code,
                    field_path=field_path,
                    message=message,
                    original={"sheet": parsed.sheet},
                )
            )
        if candidate.identity_hash:
            conflict = db.scalar(
                select(ImportCandidate.id).where(
                    ImportCandidate.identity_hash == candidate.identity_hash,
                    ImportCandidate.id != candidate.id,
                    ImportCandidate.content_hash != fingerprint,
                )
            )
            if conflict:
                db.add(
                    ValidationIssue(
                        candidate_id=candidate.id,
                        source_id=source.id,
                        code="meeting_conflict",
                        message="同一会议存在不同内容，请人工选择版本",
                        original={"other_candidate_id": conflict},
                    )
                )
    db.flush()
    return source


def rematch_directory(db, actor):
    count = 0
    for candidate in db.scalars(
        select(ImportCandidate)
        .where(ImportCandidate.state == "pending", ImportCandidate.version == 1)
        .with_for_update()
    ):
        corrected, problems = map_people(db, candidate.original)
        unresolved = {(code, path) for code, path, _ in problems}
        for issue in db.scalars(select(ValidationIssue).where(ValidationIssue.candidate_id == candidate.id)):
            if issue.code.endswith("_unmatched") and (issue.code, issue.field_path) not in unresolved:
                issue.resolved = True
                issue.resolver_id = actor.id
                issue.resolution = "已按通讯录或人员别名自动对应；人员信息已更新，会议仍需确认"
        if corrected == candidate.corrected:
            continue
        before = candidate.corrected
        candidate.corrected = corrected
        candidate.version += 1
        audit(db, actor, "import.rematch", candidate, before=before, after=corrected)
        count += 1
    return {"updated_candidates": count}


def remaining_problems(db, candidate: ImportCandidate) -> list[dict]:
    value = candidate.corrected
    problems = []
    confirmed = set(value.get("confirmed_issue_ids", []))
    for issue in db.scalars(select(ValidationIssue).where(ValidationIssue.candidate_id == candidate.id)):
        if issue.code in {"reviewer_unmatched", "invalid_due_date"}:
            continue
        if not issue.resolved and issue.id not in confirmed:
            problems.append({"field": issue.field_path, "message": "请逐条确认：" + issue.message})
    for key in ["title", "host_id", "meeting_date"]:
        if not value.get(key):
            problems.append({"field": key, "message": "必填信息未确认"})
    if value.get("kind") not in {"department", "special"}:
        problems.append({"field": "kind", "message": "会议类型未确认"})
    elif value.get("kind") == "department" and not value.get("department_id"):
        problems.append({"field": "department_id", "message": "部门会议必须选择所属部门"})
    for index, item in enumerate(value.get("items", [])):
        if item.get("section") == "attachment":
            continue
        path = f"items.{index}"
        if item.get("section") not in {"review", "new"}:
            problems.append({"field": path + ".section", "message": "统计分区未确认"})
        if not item.get("owner_ids"):
            problems.append({"field": path + ".owner_ids", "message": "责任人未确认"})
        if item.get("verifier_name") and not item.get("verifier_id"):
            problems.append(
                {"field": path + ".verifier_id", "message": "原文指定了验证人，请明确选择匹配人员"}
            )
        if item.get("section") == "review" and item.get("status") not in STATUSES:
            problems.append({"field": path + ".status", "message": "完成状态未确认"})
        if (
            item.get("status") == "未到期"
            and item.get("section") == "review"
            and value.get("meeting_date")
            and item.get("due_date")
        ):
            if value["meeting_date"] > item["due_date"]:
                problems.append(
                    {"field": path + ".status", "message": "会议日期晚于截止日期，不能标为未到期"}
                )
    return problems


class CorrectedItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    content: str
    section: str
    owner_ids: list[str] = Field(default_factory=list)
    verifier_id: str | None = None
    due_date: date | None = None
    status: str | None = None
    source_row: int
    source_sheet: str
    historical_reference: HistoricalReference | None = None


class CorrectedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str
    department_id: str | None = None
    host_id: str | None = None
    recorder_id: str | None = None
    meeting_date: date | None = None
    attendee_ids: list[str] = Field(default_factory=list)
    confirmed_issue_ids: list[str] = Field(default_factory=list)
    items: list[CorrectedItem]


def can_correct_candidate(db: Session, actor: User, candidate: ImportCandidate) -> bool:
    """Historical correction is available to administrators or the imported meeting host.

    A candidate with no resolved host remains administrator-only because there is no
    trustworthy meeting owner to authorize its correction.
    """
    return bool(
        actor.is_admin or (candidate.corrected.get("host_id") and candidate.corrected["host_id"] == actor.id)
    )


def candidate_access(db: Session, actor: User, candidate: ImportCandidate) -> None:
    if not can_correct_candidate(db, actor, candidate):
        raise HTTPException(403, "只有管理员或该会议主持人可以校验历史会议")


def correct_candidate(db, actor, identifier: str, payload: dict, expected: int, reason: str):
    candidate = locked_get(db, ImportCandidate, identifier)
    if not candidate:
        raise HTTPException(404, "导入记录不存在")
    candidate_access(db, actor, candidate)
    if candidate.state in {"confirmed", "duplicate"}:
        raise HTTPException(409, "该记录已确认或为重复副本")
    if candidate.version != expected:
        raise HTTPException(409, "校验数据已变化，请刷新")
    if not reason.strip():
        raise HTTPException(422, "请输入更正原因")
    try:
        payload = CorrectedPayload.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    originals = candidate.original.get("items", [])
    if len(payload["items"]) != len(originals) or any(
        (item["id"], item["source_row"], item["source_sheet"])
        != (original["id"], original["source_row"], original["source_sheet"])
        for item, original in zip(payload["items"], originals)
    ):
        raise HTTPException(422, "不可删除或重排来源行；非任务请选择附件分区并说明依据")
    from app.services.historical_links import resolve_link

    for item in payload["items"]:
        if item.get("historical_reference"):
            resolve_link(db, actor, payload, item)
    before = copy.deepcopy(candidate.corrected)
    candidate.corrected = payload
    candidate.version += 1
    audit(db, actor, "import.correct", candidate, before=before, after=payload, reason=reason)
    return candidate


def confirm_candidate(db, actor, identifier: str, expected: int, reason: str):
    candidate = locked_get(db, ImportCandidate, identifier)
    if not candidate:
        raise HTTPException(404, "导入记录不存在")
    candidate_access(db, actor, candidate)
    if candidate.state == "confirmed":
        return db.get(Meeting, candidate.meeting_id)
    if candidate.state in {"duplicate", "out_of_scope", "ignored"}:
        raise HTTPException(409, "先确认导入范围或选择原记录")
    if candidate.version != expected:
        raise HTTPException(409, "校验数据已变化")
    if not reason.strip():
        raise HTTPException(422, "请输入确认说明")
    unresolved_file_issue = db.scalar(
        select(ValidationIssue.id)
        .where(
            ValidationIssue.source_id == candidate.source_id,
            ValidationIssue.candidate_id.is_(None),
            ValidationIssue.resolved.is_(False),
        )
        .limit(1)
    )
    if unresolved_file_issue:
        raise HTTPException(
            422,
            "原文件仍有未处理的解析问题，请管理员在“导入历史会议 → 查看全部文件与问题说明”核对并记录处理结果后再入库",
        )
    problems = remaining_problems(db, candidate)
    if problems:
        raise HTTPException(422, {"message": "仍有待确认内容", "issues": problems})
    p = candidate.corrected
    identity = identity_fingerprint(p)
    if identity and db.scalar(select(Meeting.id).where(Meeting.source_identity == identity)):
        raise HTTPException(409, "同一会议已入库，请通过会议修订处理，不重复导入")
    items = []
    for item in p["items"]:
        if item["section"] == "attachment":
            continue
        values = dict(item)
        from app.services.historical_links import resolve_link

        values.update(resolve_link(db, actor, p, item))
        if values["section"] == "new":
            values["status"] = values.get("status") or "未到期"
        values["source"] = {
            "candidate_id": candidate.id,
            "source_id": candidate.source_id,
            "sheet": item["source_sheet"],
            "row": item["source_row"],
        }
        try:
            items.append(MeetingItem.model_validate(values))
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
    draft = MeetingDraft(
        title=p["title"],
        department_id=p["department_id"],
        meeting_date=p["meeting_date"],
        kind=p["kind"],
        host_id=p["host_id"],
        recorder_id=p["recorder_id"],
        reviewer_id=None,
        series_id=p.get("series_id"),
        attendee_ids=p.get("attendee_ids", []),
        meeting_time=p.get("meeting_time", ""),
        location=p.get("location", ""),
        absentees=p.get("absentees", ""),
        decisions=p.get("decisions", ""),
        items=items,
    )
    meeting = create_meeting(db, actor, draft, historical=True)
    meeting.source_identity = identity
    publish(
        db,
        actor,
        meeting.id,
        PublishRequest(expected_version=meeting.draft_version, notify=False, reason=reason),
    )
    candidate.state, candidate.meeting_id = "confirmed", meeting.id
    for issue in db.scalars(select(ValidationIssue).where(ValidationIssue.candidate_id == candidate.id)):
        issue.resolved, issue.resolution, issue.resolver_id = True, reason, actor.id
    audit(db, actor, "import.confirm", candidate, after={"meeting_id": meeting.id}, reason=reason)
    return meeting
