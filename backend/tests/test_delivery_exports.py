import re
from io import BytesIO
from unittest.mock import Mock
from urllib.parse import unquote

import openpyxl
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Attachment, CompletionRequest, OutboxEvent, Task
from app.schemas import ApplyCompletion, MeetingItem, PublishRequest
from app.services.business import apply_completion, create_meeting, publish
from app.services.exports import generate_exports, pdf_minutes, xlsx_minutes
from app.services.integrations import DeliveryUncertain, NotConfigured, WeCom, send_wecom


def test_incomplete_rows_save_but_do_not_publish(db, team, draft_factory):
    draft = draft_factory()
    draft.items = [MeetingItem(section="new")]
    meeting = create_meeting(db, team["host"], draft)
    with pytest.raises(HTTPException, match="请补全"):
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert meeting.published_version == 0


def test_exports_immutable_and_literal_cells(tmp_path, db, team, draft_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    draft = draft_factory()
    draft.items[0].content = '=HYPERLINK("https://invalid", "literal")'
    draft.items[0].notes = "长文本说明" * 200
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    files = generate_exports(db, meeting.id, 1)
    assert {x.kind for x in files} == {"minutes_xlsx", "tasks_xlsx", "minutes_pdf"}
    assert all("-V1-" not in x.filename for x in files)
    assert [x.id for x in generate_exports(db, meeting.id, 1)] == [x.id for x in files]
    xlsx = next(x for x in files if x.kind == "tasks_xlsx")
    w = openpyxl.load_workbook(tmp_path / xlsx.storage_key)
    assert w.active["C5"].data_type == "s"
    assert w.active["C5"].value.startswith("=HYPERLINK")
    assert w.active.print_title_rows == "$1:$4"
    pdf = next(x for x in files if x.kind == "minutes_pdf")
    assert len(PdfReader(tmp_path / pdf.storage_key).pages) >= 1


def test_pdf_long_chinese_text_not_truncated():
    text = "核对责任范围、验收结果、整改要求与相关记录。" * 650 + "最终验收标记"
    snapshot = {
        "title": "长文本验收纪要",
        "meeting_date": "2026-08-18",
        "items": [
            {
                "section": "new",
                "content": text,
                "owner_names": ["责任人甲", "责任人乙"],
                "due_date": "2026-08-31",
            }
        ],
    }
    pdf = PdfReader(BytesIO(pdf_minutes(snapshot)))
    assert len(pdf.pages) > 2
    output = "".join("".join(p.extract_text().split()) for p in pdf.pages)
    # The final marker may span pages; strip running furniture before checking continuity.
    output = re.sub(r"第\d+页", "", output)
    output = output.replace("序号具体工作任务内容（写清标准、要求）责任人计划完成时间任务闭环完成结果备注", "")
    assert "最终验收标记" in output
    w = openpyxl.load_workbook(BytesIO(xlsx_minutes(snapshot, True)))
    assert "最终验收标记" in "".join(str(row[2].value or "") for row in w.active)


@pytest.mark.parametrize("legacy", [False, True])
def test_download_names_hide_only_generated_revision_suffixes(
    db, team, draft_factory, tmp_path, monkeypatch, legacy
):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    draft = draft_factory()
    draft.title = "技术规范-V1"
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    files = generate_exports(db, meeting.id, 1)
    expected_names = {a.id: a.filename for a in files}
    assert set(expected_names.values()) == {
        "技术规范-V1-纪要.xlsx", "技术规范-V1-任务单.xlsx", "技术规范-V1-纪要.pdf",
    }
    if legacy:
        for attachment in files:
            attachment.filename = attachment.filename.replace("-V1-", "-V1-V1-")
        db.flush()
    stored_names = {a.id: a.filename for a in files}
    headers = {"Authorization": "Bearer " + issue_token(team["host"])}
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            for attachment in files:
                fmt = {"minutes_xlsx": "xlsx", "tasks_xlsx": "tasks", "minutes_pdf": "pdf"}[attachment.kind]
                for url in (
                    "/api/files/" + attachment.id,
                    f"/api/meetings/{meeting.id}/export/{fmt}",
                ):
                    response = client.get(url, headers=headers)
                    assert response.status_code == 200, response.text
                    assert expected_names[attachment.id] in unquote(response.headers["content-disposition"])
            from app.services.exports import attachment_filename

            # An uploaded filename containing V1 is user content, not a system label.
            upload = Attachment(filename="技术规范-V1-V1-纪要.pdf", kind="attachment", meeting_version=1)
            assert attachment_filename(upload, db) == upload.filename
        assert {a.id: a.filename for a in files} == stored_names
    finally:
        app.dependency_overrides.clear()


def test_legacy_acceptance_requirement_is_not_shown_in_new_exports():
    snapshot = {
        "title": "周工作计划",
        "meeting_date": "2026-09-21",
        "items": [
            {
                "section": "new",
                "content": "完成本周计划",
                "requirements": "旧版隐藏验收字段",
                "owner_names": ["责任人甲"],
                "due_date": "2026-09-25",
            }
        ],
    }

    workbook = openpyxl.load_workbook(BytesIO(xlsx_minutes(snapshot, True)))
    excel_text = " ".join(str(cell.value or "") for row in workbook.active for cell in row)
    pdf_text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf_minutes(snapshot))).pages)

    assert "完成本周计划" in excel_text and "完成本周计划" in pdf_text
    assert "旧版隐藏验收字段" not in excel_text
    assert "旧版隐藏验收字段" not in pdf_text


def test_reviewer_is_not_shown_in_new_exports():
    snapshot = {
        "title": "周工作会议",
        "meeting_date": "2026-09-22",
        "recorder_name": "编制人员",
        "reviewer_name": "旧版审核人员",
        "items": [],
    }
    workbook = openpyxl.load_workbook(BytesIO(xlsx_minutes(snapshot, False)))
    excel_text = " ".join(str(cell.value or "") for row in workbook.active for cell in row)
    pdf_text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf_minutes(snapshot))).pages)

    assert "编制人员" in excel_text and "编制人员" in pdf_text
    assert "审核" not in excel_text and "旧版审核人员" not in excel_text
    assert "审核" not in pdf_text and "旧版审核人员" not in pdf_text


def test_stale_notification_and_closed_digest_are_skipped(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task))
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "wecom"))
    task.version += 1
    assert "skipped" in send_wecom(db, event)
    task.closed_at = __import__("app.models", fromlist=["now"]).now()
    digest = OutboxEvent(payload={"reminder_task_ids": [task.id], "user_ids": [team["a"].id]})
    assert "skipped" in send_wecom(db, digest)


def test_missing_wecom_binding_blocks_instead_of_false_success(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "wecom"))
    with pytest.raises(NotConfigured):
        send_wecom(db, event)


def test_wecom_transport_timeout_is_uncertain(monkeypatch):
    import httpx

    settings = get_settings()
    for field, value in [("wecom_corp_id", "test"), ("wecom_agent_id", "1"), ("wecom_secret", "test")]:
        monkeypatch.setattr(settings, field, value)
    provider = WeCom()
    monkeypatch.setattr(provider, "token", lambda: "secret-test")
    transport = Mock(side_effect=httpx.ReadTimeout("timeout"))
    monkeypatch.setattr(httpx, "request", transport)
    with pytest.raises(DeliveryUncertain):
        provider.send(["u1"], "test")
    assert transport.call_count == 1
    assert transport.call_args.args[0] == "POST"


def test_file_upload_export_permission_and_evidence(db, team, draft_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    app.dependency_overrides[get_db] = lambda: db

    def headers(who):
        return {"Authorization": "Bearer " + issue_token(team[who])}

    try:
        with TestClient(app) as client:
            meeting = create_meeting(db, team["host"], draft_factory())
            publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
            task = db.scalar(select(Task))
            response = client.post(
                "/api/files?task_id=" + task.id,
                files={"file": ("proof.txt", b"proof", "text/plain")},
                headers=headers("a"),
            )
            assert response.status_code == 200, response.text
            file_id = response.json()["id"]
            assert client.get("/api/files/" + file_id, headers=headers("outsider")).status_code == 403
            assert client.get("/api/files/" + file_id, headers=headers("host")).status_code == 200
            assert (
                client.post(
                    "/api/files?task_id=" + task.id,
                    files={"file": ("proof.txt", b"x")},
                    headers=headers("colleague"),
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/meetings/" + meeting.id + "/export/pdf", headers=headers("outsider")
                ).status_code
                == 403
            )
            assert (
                client.get("/api/meetings/" + meeting.id + "/export/pdf", headers=headers("host")).status_code
                == 200
            )
            apply_completion(
                db,
                team["a"],
                task.id,
                ApplyCompletion(
                    expected_version=1,
                    idempotency_key="evidence-test-key",
                    explanation="done",
                    evidence_ids=[file_id],
                ),
            )
            assert db.scalar(select(CompletionRequest)).evidence_ids == [file_id]
    finally:
        app.dependency_overrides.clear()


def test_original_workbook_not_exposed_by_single_meeting(db, team, draft_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    attachment = Attachment(
        filename="原工作簿.xlsx",
        storage_key="x.xlsx",
        sha256="a" * 64,
        mime_type="application/octet-stream",
        size=1,
        uploader_id=team["host"].id,
        meeting_id=meeting.id,
        kind="original",
    )
    db.add(attachment)
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert (
                client.get(
                    "/api/files/" + attachment.id,
                    headers={"Authorization": "Bearer " + issue_token(team["a"])},
                ).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()
