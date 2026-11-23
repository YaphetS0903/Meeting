import asyncio
import base64
import json
from io import BytesIO

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Assessment, Attachment, Meeting, OutboxEvent, Task
from app.services import document_sheet_import as document
from app.services import sheet_import_jobs as jobs
from app.services.meeting_sheet_import import ImportHeaders, create_from_source


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(document, "configured", lambda: True)
    monkeypatch.setattr(jobs, "configured", lambda: True)
    paths = [tmp_path / "第一页.PNG", tmp_path / "第二页.jpeg"]
    for path, color in zip(paths, ["white", "gray"]):
        Image.new("RGB", (200, 100), color).save(path)
    return paths


def page(items, **kwargs):
    return dict(
        complete=True,
        title="文档导入测试",
        department_name="技术部",
        raw_meeting_date="2026-10-15",
        kind="department",
        host_name="主持人",
        items=items,
        **kwargs,
    )


def response(value, finish_reason="stop"):
    return {
        "choices": [
            {"finish_reason": finish_reason, "message": {"content": json.dumps(value, ensure_ascii=False)}}
        ]
    }


def parse(files, values):
    iterator = iter(values)
    return document.parse_documents(files, sender=lambda body: response(next(iterator)))


def test_photos_preserve_raw_fields_and_sections_without_inferred_results(files, db, team):
    parsed = parse(
        files,
        [
            page(
                [
                    dict(
                        section="review",
                        content="原日期保持",
                        owners="责任人甲",
                        due="7/31\n8/12\n10/20",
                        status="暂停",
                        notes="延期到10/25",
                    )
                ]
            ),
            page(
                [dict(section="new", content="本次新增", owners="责任人乙", due="10/30", deliverable="报告")]
            ),
        ],
    )
    c = jobs.prepare_candidate(db, parsed.candidates[0], team["host"])
    assert c["review_count"] == c["new_count"] == 1
    rows = c["payload"]["items"]
    assert rows[0]["status"] is None and rows[0]["raw_status"] == "暂停"
    assert rows[0]["due_date"] == "2026-10-20"
    assert rows[0]["date_history"] == ["2026-07-31", "2026-08-12"]
    assert rows[0]["notes"] == "延期到10/25"
    assert rows[0]["owner_ids"] == [team["a"].id]
    assert rows[1]["status"] is None and rows[1]["deliverable"] == "报告"
    assert {"invalid_status", "date_note_conflict", "document_review"} <= {i["code"] for i in c["issues"]}
    assert not any(i["code"] == "document_uncertain" for i in c["issues"])


def test_actual_pdf_renders_each_page_and_strips_image_metadata(tmp_path, files):
    path = tmp_path / "two.pdf"
    pdf = canvas.Canvas(str(path))
    for label in ["First", "Second"]:
        pdf.drawString(50, 800, label)
        pdf.showPage()
    pdf.save()
    rendered = list(document.pages([path]))
    assert [n for _, n, _ in rendered] == [1, 2]
    for _, _, url in rendered:
        with Image.open(BytesIO(base64.b64decode(url.split(",")[1]))) as img:
            assert img.format == "JPEG" and max(img.size) <= 2400
    assert rendered[0][2] != rendered[1][2]
    parsed = parse(
        [path],
        [
            page([dict(section="review", number="1", content="跨页内容", owners="责任人甲")]),
            page([dict(section="review", number="1", content="后半段", continues_previous=True)]),
        ],
    )
    assert len(parsed.candidates[0].payload["items"]) == 1
    assert "跨页内容\n后半段" == parsed.candidates[0].payload["items"][0]["content"]
    assert any(i.code == "document_uncertain" for i in parsed.candidates[0].issues)


@pytest.mark.parametrize(
    "bad",
    [
        {"complete": False, "items": []},
        {"complete": True, "items": [{"section": "new", "content": None}]},
    ],
)
def test_incomplete_or_invalid_recognition_is_atomic(files, bad):
    with pytest.raises(document.AIRecognitionFailed):
        parse(files, [page([dict(section="new", content="不能仅保存这条")]), bad])


def test_truncated_response_and_mixed_meetings_rejected(files):
    with pytest.raises(document.AIRecognitionFailed):
        document.parse_documents(files[:1], sender=lambda _: response(page([]), "length"))
    with pytest.raises(document.AIRecognitionFailed, match="不同会议"):
        parse(files, [page([]), {**page([]), "raw_meeting_date": "2026-10-16"}])


def test_bad_files_and_limits_rejected(files, tmp_path):
    for name in ["broken.pdf", "broken.png"]:
        path = tmp_path / name
        path.write_bytes(b"not the declared format")
        with pytest.raises(document.DocumentInvalid):
            document.validate_documents([path])
    with pytest.raises(document.DocumentInvalid):
        document.validate_documents(files * 11)
    with pytest.raises(document.DocumentInvalid):
        document.validate_documents([files[0], tmp_path / "other.pdf"])
    path = tmp_path / "long.pdf"
    pdf = canvas.Canvas(str(path))
    for _ in range(21):
        pdf.drawString(10, 10, "page")
        pdf.showPage()
    pdf.save()
    with pytest.raises(document.DocumentInvalid, match="20"):
        document.validate_documents([path])
    from reportlab.lib.pdfencrypt import StandardEncryption

    encrypted = tmp_path / "password.pdf"
    pdf = canvas.Canvas(str(encrypted), encrypt=StandardEncryption("secret"))
    pdf.drawString(10, 10, "locked")
    pdf.save()
    with pytest.raises(document.DocumentInvalid, match="加密"):
        document.validate_documents([encrypted])


def headers(source, team):
    return ImportHeaders(
        candidate_index=0,
        title="识别草稿",
        meeting_date="2026-10-15",
        department_id=team["dep"].id,
        host_id=team["host"].id,
        recorder_id=team["host"].id,
    )


def test_durable_queue_deduplicates_worker_and_preserves_all_originals(files, db, team, monkeypatch):
    source = jobs.queue_documents(db, team["host"], team["series"].id, files)
    assert jobs.status(db, source) == ("pending", None)
    assert jobs.queue_documents(db, team["host"], team["series"].id, files).id == source.id
    with pytest.raises(HTTPException) as exc:
        create_from_source(db, team["host"], source.id, headers(source, team))
    assert exc.value.status_code == 409
    event = jobs.job_for(db, source)
    monkeypatch.setattr(
        jobs,
        "parse_documents",
        lambda paths, **_: parse(
            paths,
            [
                page(
                    [dict(section="review", content="回顾", owners="责任人甲", due="10/10", status="已完成")]
                ),
                page([dict(section="new", content="新增", owners="责任人乙", due="10/20")]),
            ],
        ),
    )
    assert jobs.run_job(db, event) == {}
    assert jobs.status(db, source) == ("ready", None)
    for model in [Meeting, Task, Assessment]:
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1  # recognition only
    first_candidates = source.candidates
    assert jobs.run_job(db, event) == {} and source.candidates is first_candidates
    meeting, duplicate = create_from_source(db, team["host"], source.id, headers(source, team))
    assert not duplicate and meeting.published_version == 0
    assert [r["section"] for r in meeting.draft["items"]] == ["review", "new"]
    assert len(jobs.originals(source)) == 2
    assert all(db.get(Attachment, i).meeting_id == meeting.id for i in jobs.originals(source))
    assert create_from_source(db, team["host"], source.id, headers(source, team))[1]


def test_failed_job_retry_and_permissions(files, db, team, monkeypatch):
    source = jobs.queue_documents(db, team["host"], team["series"].id, files[:1])
    event = jobs.job_for(db, source)
    monkeypatch.setattr(
        jobs,
        "parse_documents",
        lambda *a, **kw: (_ for _ in ()).throw(document.AIRecognitionFailed("图片模糊，请重试")),
    )
    result = jobs.run_job(db, event)
    event.state = "failed"
    assert result["failed"] and not source.candidates
    assert jobs.status(db, source) == ("failed", "图片模糊，请重试")
    with pytest.raises(HTTPException) as exc:
        jobs.retry(db, team["outsider"], source.id)
    assert exc.value.status_code == 403
    jobs.retry(db, team["host"], source.id)
    assert event.state == "pending" and jobs.status(db, source)[0] == "pending"
    team["host"].active = False
    assert jobs.run_job(db, event)["failed"]
    assert not source.candidates


def test_dispatch_processes_recognition_without_delivery(files, db, team, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app import worker

    source = jobs.queue_documents(db, team["host"], team["series"].id, files[:1])
    identifier = source.id
    event_id = jobs.job_for(db, source).id
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.bind))
    monkeypatch.setattr(
        jobs,
        "parse_documents",
        lambda paths, **_: parse(
            paths, [page([dict(section="new", content="仅识别草稿", owners="责任人甲", due="10/30")])]
        ),
    )
    monkeypatch.setattr(worker, "send_mail", lambda *a: pytest.fail("recognition must not send mail"))
    monkeypatch.setattr(worker, "send_wecom", lambda *a: pytest.fail("recognition must not send notices"))
    assert worker.dispatch(limit=1) == 1
    db.expire_all()
    from app.models import MeetingSheetImport

    assert db.get(OutboxEvent, event_id).state == "sent"
    assert jobs.status(db, db.get(MeetingSheetImport, identifier)) == ("ready", None)
    assert db.scalar(select(func.count()).select_from(Meeting)) == 0


def test_api_multifile_validation_status_retry_and_privacy(files, db, team):
    from app.core.db import get_db
    from app.core.security import current_user
    from app.main import app

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: team["host"]

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            endpoint = f"/api/series/{team['series'].id}/task-sheet-imports"
            mixed = [("file", (p.name, p.read_bytes())) for p in files]
            result = await client.post(endpoint, files=mixed)
            assert result.status_code == 200, result.text
            body = result.json()
            assert body["status"] == "pending" and body["candidates"] == []
            assert body["diagnostics"] == [] and len(body["filenames"]) == 2
            assert "job_key" not in result.text and "attachment_ids" not in result.text
            assert (await client.post(endpoint, files=mixed)).json()["id"] == body["id"]
            assert (await client.get(f"/api/meeting-sheet-imports/{body['id']}")).json()[
                "status"
            ] == "pending"
            assert (
                await client.post(endpoint, files=[mixed[0], ("file", ("x.pdf", b"x"))])
            ).status_code == 422
            assert (await client.post(endpoint, files={"file": ("x.pdf", b"x")})).status_code == 422
            app.dependency_overrides[current_user] = lambda: team["outsider"]
            assert (await client.post(f"/api/meeting-sheet-imports/{body['id']}/retry")).status_code == 403

    try:
        asyncio.run(requests())
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
