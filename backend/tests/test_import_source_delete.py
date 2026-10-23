from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import (
    Attachment,
    AuditLog,
    ImportBatch,
    ImportCandidate,
    ImportSource,
    NotificationLog,
    OutboxEvent,
    ValidationIssue,
)
from app.services.importing import ingest_file


def source_records(db, team, tmp_path, *, confirmed=False):
    storage = tmp_path / "storage"
    key = "originals/protected.xls"
    target = storage / key
    target.parent.mkdir(parents=True)
    target.write_bytes(b"same encrypted bytes")
    batch = ImportBatch(name="九月资料", creator_id=team["host"].id)
    attachment = Attachment(
        filename="会议.xls",
        storage_key=key,
        sha256="a" * 64,
        mime_type="application/octet-stream",
        size=20,
        uploader_id=team["host"].id,
        kind="original",
    )
    db.add_all([batch, attachment])
    db.flush()
    source = ImportSource(
        batch_id=batch.id,
        attachment_id=attachment.id,
        filename="会议.xls",
        sha256="a" * 64,
        diagnostics=[{"code": "sheet_parse_error", "message": "cannot parse"}],
    )
    db.add(source)
    db.flush()
    duplicate = ImportSource(
        batch_id=batch.id,
        attachment_id=attachment.id,
        filename="会议.xls",
        sha256="a" * 64,
        duplicate_of=source.id,
    )
    candidate = ImportCandidate(
        source_id=source.id,
        sheet_name="会议纪要",
        original={"title": "会议"},
        corrected={"title": "会议"},
        content_hash="b" * 64,
        state="confirmed" if confirmed else "out_of_scope",
    )
    db.add_all([duplicate, candidate])
    db.flush()
    issue = ValidationIssue(
        source_id=source.id,
        candidate_id=candidate.id,
        code="sheet_parse_error",
        message="无法读取",
    )
    event = OutboxEvent(
        event_key=f"import:{source.id}",
        channel="import",
        payload={"source_id": source.id, "actor_id": team["host"].id},
        state="sent",
    )
    db.add_all([issue, event])
    db.flush()
    log = NotificationLog(event_id=event.id, result="sent", detail={})
    db.add(log)
    db.flush()
    return batch, attachment, source, duplicate, candidate, target


def test_delete_unconfirmed_source_removes_duplicate_parse_data_and_allows_reimport(
    db, team, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    batch, attachment, source, duplicate, candidate, target = source_records(
        db, team, tmp_path
    )
    source_id, duplicate_id, attachment_id, candidate_id = (
        source.id,
        duplicate.id,
        attachment.id,
        candidate.id,
    )
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.request(
                "DELETE",
                f"/api/import-sources/{source_id}",
                json={"reason": "首次上传的文件未解密"},
                headers={"Authorization": "Bearer " + issue_token(team["host"])},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "deleted_sources": 2,
                "deleted_candidates": 1,
                "removed_original": True,
            }
    finally:
        app.dependency_overrides.clear()

    assert db.get(ImportSource, source_id) is None
    assert db.get(ImportSource, duplicate_id) is None
    assert db.get(ImportCandidate, candidate_id) is None
    assert db.get(Attachment, attachment_id) is None
    assert not target.exists()
    assert db.scalar(select(func.count()).select_from(ValidationIssue)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert db.scalar(select(func.count()).select_from(NotificationLog)) == 0
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "import.delete_source"))
    assert audit and audit.reason == "首次上传的文件未解密"

    upload = tmp_path / "会议.xls"
    upload.write_bytes(b"same encrypted bytes")
    replacement = ingest_file(db, team["host"], batch, upload, defer=True)
    assert replacement.duplicate_of is None


def test_confirmed_import_source_cannot_be_deleted(db, team, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    _, _, source, _, _, target = source_records(db, team, tmp_path, confirmed=True)
    source_id = source.id
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.request(
                "DELETE",
                f"/api/import-sources/{source_id}",
                json={"reason": "尝试删除已确认来源"},
                headers={"Authorization": "Bearer " + issue_token(team["host"])},
            )
            assert response.status_code == 409
            assert "确认入库" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()

    assert db.get(ImportSource, source_id) is not None
    assert target.exists()
