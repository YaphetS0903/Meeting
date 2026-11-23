import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import current_user
from app.models import Attachment, Meeting, MeetingSheetImport, User
from app.services import sheet_import_jobs as jobs
from app.services.access import meeting_access
from app.services.meeting_sheet_import import (
    ImportHeaders,
    create_from_source,
    recognize,
    series_access,
    source_access,
)

router = APIRouter(prefix="/api")


def describe(db, source):
    candidates = [source.candidates[source.selected_index]] if source.meeting_id else source.candidates
    return dict(
        id=source.id,
        series_id=source.series_id,
        candidates=candidates,
        diagnostics=[d for d in source.diagnostics if d.get("code") not in jobs.INTERNAL_CODES],
        selected_index=0 if source.meeting_id else None,
        filename=db.get(Attachment, source.attachment_id).filename,
        filenames=jobs.metadata(source).get("filenames", [db.get(Attachment, source.attachment_id).filename]),
        status=jobs.status(db, source)[0],
        error=jobs.status(db, source)[1],
    )


@router.post("/series/{identifier}/task-sheet-imports")
def upload_sheet(
    identifier: str,
    file: list[UploadFile],
    db: Session = Depends(get_db),
    actor: User = Depends(current_user),
):
    series_access(db, actor, identifier)
    if not 1 <= len(file) <= 20:
        raise HTTPException(422, "一次选择一个 Excel／PDF，或最多 20 张同一会议的照片")
    names = [Path((f.filename or "").replace("\\", "/")).name for f in file]
    suffixes = [Path(n).suffix.lower() for n in names]
    if any(s not in {".xls", ".xlsx", ".pdf", ".png", ".jpg", ".jpeg"} for s in suffixes):
        raise HTTPException(422, "请选择 Excel、PDF、PNG 或 JPG 文件")
    if len(file) > 1 and any(s not in {".png", ".jpg", ".jpeg"} for s in suffixes):
        raise HTTPException(422, "一次选择一个 Excel／PDF；同一场会议的多张照片可以一起选择")
    limit = get_settings().max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory() as folder:
        paths, total = [], 0
        for i, (upload, name) in enumerate(zip(file, names)):
            content = upload.file.read(limit - total + 1)
            total += len(content)
            if not content or total > limit:
                raise HTTPException(422, f"文件为空或合计超过 {get_settings().max_upload_mb} MB")
            path = Path(folder) / str(i) / name
            path.parent.mkdir()
            path.write_bytes(content)
            paths.append(path)
        source = (
            recognize(db, actor, identifier, paths[0])
            if suffixes[0] in {".xls", ".xlsx"}
            else jobs.queue_documents(db, actor, identifier, paths)
        )
    # A follow-up request may start before dependency cleanup commits.
    db.commit()
    return describe(db, source)


@router.post("/meeting-sheet-imports/{identifier}/retry")
def retry_source(identifier: str, db: Session = Depends(get_db), actor: User = Depends(current_user)):
    source = jobs.retry(db, actor, identifier)
    db.commit()
    return describe(db, source)


@router.post("/meeting-sheet-imports/{identifier}/draft")
def create_draft(
    identifier: str, body: ImportHeaders, db: Session = Depends(get_db), actor: User = Depends(current_user)
):
    try:
        meeting, duplicate = create_from_source(db, actor, identifier, body)
    except ValidationError as exc:
        raise HTTPException(422, "识别内容不符合任务单要求，请核对会议基本信息、分区和字段长度") from exc
    db.commit()
    return dict(id=meeting.id, duplicate=duplicate)


@router.get("/meeting-sheet-imports/{identifier}")
def read_source(identifier: str, db: Session = Depends(get_db), actor: User = Depends(current_user)):
    return describe(db, source_access(db, actor, identifier))


@router.get("/meetings/{identifier}/task-sheet-source")
def meeting_source(identifier: str, db: Session = Depends(get_db), actor: User = Depends(current_user)):
    meeting = db.get(Meeting, identifier)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, actor, meeting, edit=True)
    source = (
        db.get(MeetingSheetImport, meeting.draft.get("sheet_import_id"))
        if meeting.draft.get("sheet_import_id")
        else None
    )
    return describe(db, source) if source else None
