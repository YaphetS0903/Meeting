"""Durable, idempotent PDF/photo recognition; never publishes business data."""

import hashlib
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import locked_get
from app.importers.parser import digest
from app.models import Attachment, MeetingSheetImport, OutboxEvent, User, now
from app.services.ai_recognition import AIRecognitionFailed, AIRecognitionUnavailable, configured
from app.services.business import audit, enqueue
from app.services.document_sheet_import import DocumentInvalid, parse_documents, validate_documents
from app.services.importing import dedup_lock, save_original
from app.services.meeting_sheet_import import prepare_candidate, series_access, source_access

INTERNAL_CODES = {"source_files", "recognition_error"}


def metadata(source):
    return next((d for d in source.diagnostics if d.get("code") == "source_files"), {})


def originals(source):
    return metadata(source).get("attachment_ids", [source.attachment_id])


def job_for(db, source):
    key = metadata(source).get("job_key")
    return db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key)) if key else None


def status(db, source):
    if source.candidates:
        return "ready", None
    event = job_for(db, source)
    if event and event.state in {"pending", "sending"}:
        return ("processing" if event.state == "sending" else "pending"), None
    error = next((d["message"] for d in source.diagnostics if d.get("code") == "recognition_error"), None)
    return "failed", error or "识别未完成，请重新识别"


def queue_documents(db, actor, series_id, paths: list[Path]):
    series_access(db, actor, series_id)
    try:
        validate_documents(paths)
    except DocumentInvalid as exc:
        raise HTTPException(422, str(exc)) from exc
    if not configured():
        raise HTTPException(422, "PDF／照片识别尚未配置，请联系系统管理员；Excel 导入仍可使用")
    key = "meeting-sheet:" + digest(
        [series_id, actor.id, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]]
    )
    dedup_lock(db, key.split(":", 1)[1])
    existing = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
    if existing:
        return source_access(db, actor, existing.payload["source_id"])
    attachments = [save_original(db, actor, path) for path in paths]
    for attachment, path in zip(attachments, paths):
        attachment.kind = "meeting_sheet"
        attachment.mime_type = {".pdf": "application/pdf", ".png": "image/png"}.get(
            path.suffix.lower(), "image/jpeg"
        )
    source = MeetingSheetImport(
        series_id=series_id,
        creator_id=actor.id,
        attachment_id=attachments[0].id,
        candidates=[],
        diagnostics=[
            dict(
                code="source_files",
                attachment_ids=[a.id for a in attachments],
                filenames=[a.filename for a in attachments],
                job_key=key,
            )
        ],
    )
    db.add(source)
    db.flush()
    enqueue(db, key, "import", {"kind": "meeting_sheet", "source_id": source.id, "actor_id": actor.id})
    audit(
        db,
        actor,
        "meeting_sheet.upload",
        source,
        after={"filenames": [p.name for p in paths], "series_id": series_id},
    )
    return source


def run_job(db, event):
    source = locked_get(db, MeetingSheetImport, event.payload["source_id"])
    actor = db.get(User, event.payload["actor_id"])
    if source.candidates:
        return {}
    try:
        if not actor or not actor.active or not source.series_id:
            raise HTTPException(403, "账号或会议系列不可用，请联系系统管理员")
        series_access(db, actor, source.series_id)
        attachments = [db.get(Attachment, identifier) for identifier in originals(source)]
        if not all(attachments):
            raise DocumentInvalid("原文件不存在，请重新上传")
        parsed = parse_documents(
            [get_settings().storage_dir / a.storage_key for a in attachments],
            filenames=[a.filename for a in attachments],
        )
        candidates = [prepare_candidate(db, c, actor) for c in parsed.candidates]
        # Assign only after every page has succeeded; failed recognition exposes no partial rows.
        source.candidates = candidates
        source.diagnostics = [metadata(source), *parsed.diagnostics]
        audit(
            db,
            actor,
            "meeting_sheet.recognize",
            source,
            after={
                "review_count": sum(c["review_count"] for c in candidates),
                "new_count": sum(c["new_count"] for c in candidates),
            },
        )
        return {}
    except (DocumentInvalid, AIRecognitionFailed, AIRecognitionUnavailable, HTTPException) as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    except Exception:
        message = "文件识别失败，未保存部分任务，请重新识别或上传更清晰的文件"
    source.diagnostics = [metadata(source), {"code": "recognition_error", "message": message}]
    if actor:
        audit(
            db,
            actor,
            "meeting_sheet.recognition_failed",
            source,
            after={"error": message},
            context={"result": "failed"},
        )
    return {"failed": True, "error": message}


def retry(db, actor, identifier):
    source = source_access(db, actor, identifier)
    series_access(db, actor, source.series_id)
    event = job_for(db, source)
    if not event or source.candidates:
        return source
    event = locked_get(db, OutboxEvent, event.id)
    if event.state in {"pending", "sending"}:
        return source
    if not configured():
        raise HTTPException(422, "识别服务尚未配置，请联系系统管理员")
    event.state, event.attempts, event.last_error = "pending", 0, None
    event.available_at, event.claimed_at, event.sent_at = now(), None, None
    source.diagnostics = [metadata(source)]
    audit(db, actor, "meeting_sheet.retry", source)
    return source
