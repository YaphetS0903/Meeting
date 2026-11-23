from io import BytesIO

import openpyxl
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Attachment, MeetingVersion, OutboxEvent, Task
from app.schemas import PublishRequest
from app.services.business import create_meeting, publish


def test_unpublished_attachments_hidden_from_department_readers(
    db, team, draft_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    meeting = create_meeting(db, team["host"], draft_factory())
    (tmp_path / "draft.txt").write_text("未发布的材料")
    attachment = Attachment(
        filename="修订附件.txt",
        storage_key="draft.txt",
        sha256="a" * 64,
        mime_type="text/plain",
        size=21,
        uploader_id=team["host"].id,
        meeting_id=meeting.id,
    )
    db.add(attachment)
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["a"])}
            assert client.get(f"/api/meetings/{meeting.id}", headers=headers).status_code == 403
            assert client.get(f"/api/files/{attachment.id}", headers=headers).status_code == 403
            meeting.draft = {**meeting.draft, "attachment_ids": [attachment.id]}
            publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
            assert client.get(f"/api/files/{attachment.id}", headers=headers).status_code == 200
            assert len(client.get(f"/api/meetings/{meeting.id}", headers=headers).json()["attachments"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_preview_is_authenticated_versioned_and_has_no_publication_side_effects(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            path = f"/api/meetings/{meeting.id}/preview"
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            assert client.post(path, json={"expected_version": 1}).status_code == 401
            assert (
                client.post(
                    path,
                    headers={"Authorization": "Bearer " + issue_token(team["a"])},
                    json={"expected_version": 1},
                ).status_code
                == 403
            )
            assert client.post(path, headers=headers, json={"expected_version": 2}).status_code == 409
            pdf = client.post(path, headers=headers, json={"expected_version": 1})
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
            assert pdf.headers["cache-control"] == "no-store"
            excel = client.post(path, headers=headers, json={"expected_version": 1, "format": "tasks"})
            workbook = openpyxl.load_workbook(BytesIO(excel.content))
            assert workbook.sheetnames == ["本次工作部署"]
            assert workbook.active["A1"].value.startswith("【草稿预览】")
            assert team["a"].name in workbook.active["D5"].value
            assert meeting.published_version == 0
            for model in (Task, MeetingVersion, OutboxEvent, Attachment):
                assert db.scalar(select(func.count()).select_from(model)) == 0
    finally:
        app.dependency_overrides.clear()


def test_reviewer_can_read_published_meeting_but_not_delivery_records(db, team, draft_factory):
    draft = draft_factory()
    draft.reviewer_id = team["outsider"].id
    team["outsider"].email = "reviewer@example.test"
    draft.recipient_ids = [team["outsider"].id]
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            path = f"/api/meetings/{meeting.id}"
            viewer = client.get(path, headers={"Authorization": "Bearer " + issue_token(team["outsider"])})
            assert viewer.status_code == 200
            assert viewer.json()["notifications"] == []
            host = client.get(path, headers={"Authorization": "Bearer " + issue_token(team["host"])})
            assert host.json()["notifications"][0]["recipient_ids"] == [team["outsider"].id]
            assert host.json()["notifications"][0]["state"] == "pending"
    finally:
        app.dependency_overrides.clear()
