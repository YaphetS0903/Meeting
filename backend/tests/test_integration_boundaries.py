from datetime import date
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.models import ImportBatch, ImportCandidate, OutboxEvent
from app.schemas import MeetingDraft, PublishRequest, TaskChange
from app.services import integrations
from app.services.business import change_task, publish, revision, save_meeting
from app.services.importing import ingest_file, parse_source
from tests.test_business import get_task, published


def test_oauth_state_short_cookie_bound_and_one_use(db, team, monkeypatch):
    settings = get_settings()
    for key, value in [("wecom_corp_id", "corp"), ("wecom_agent_id", "1"), ("wecom_secret", "test")]:
        monkeypatch.setattr(settings, key, value)
    cache = {}
    fake = Mock()
    fake.setex = lambda key, ttl, value: cache.update({key: value})
    fake.getdel = lambda key: cache.pop(key, None)
    monkeypatch.setattr(integrations, "redis_client", lambda: fake)
    team["a"].wecom_id = "wecom-a"
    db.flush()
    monkeypatch.setattr(integrations.WeCom, "get", lambda *args, **kwargs: {"userid": "wecom-a"})
    url, state = integrations.oauth_url()
    assert len(state) <= 128 and parse_qs(urlparse(url).query)["state"] == [state]
    with pytest.raises(HTTPException):
        integrations.oauth_user(db, "code", state, "different")
    user, return_to = integrations.oauth_user(db, "code", state, state)
    assert user.id == team["a"].id and return_to == "/tasks"
    with pytest.raises(HTTPException):
        integrations.oauth_user(db, "code", state, state)


def test_smtp_attaches_published_pdf_and_task_sheet(db, team, draft_factory, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(settings, "smtp_host", "smtp.invalid")
    monkeypatch.setattr(settings, "smtp_from", "meetings@example.com")
    team["a"].email = "owner@example.com"
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    published(db, team, draft)
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "email"))
    sent = []
    smtp = Mock()
    smtp.send_message = lambda msg, **kwargs: sent.append(msg) or {}
    monkeypatch.setattr(integrations.smtplib, "SMTP", lambda *args, **kwargs: smtp)
    result = integrations.send_mail(db, event)
    assert result["message_id"] and sent[0]["To"] == "owner@example.com"
    assert {a.get_content_type() for a in sent[0].iter_attachments()} == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    smtp.starttls.assert_called_once()


def test_async_upload_preserves_source_and_does_not_parse_twice(db, team, tmp_path, monkeypatch):
    import openpyxl

    from tests.test_parser import grid

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    path = tmp_path / "会议.xlsx"
    w = openpyxl.Workbook()
    for row in grid("顺延"):
        w.active.append(row)
    w.save(path)
    batch = ImportBatch(name="异步导入", creator_id=team["host"].id)
    db.add(batch)
    db.flush()
    source = ingest_file(db, team["host"], batch, path, defer=True)
    assert db.scalar(select(ImportCandidate)) is None
    assert db.scalar(select(OutboxEvent)).channel == "import"
    parse_source(db, team["host"], batch, source, path)
    count = len(list(db.scalars(select(ImportCandidate))))
    parse_source(db, team["host"], batch, source, path)
    assert len(list(db.scalars(select(ImportCandidate)))) == count == 1


def test_revision_after_deadline_change_preserves_old_department_and_deadline(db, team, draft_factory):
    from app.models import Assessment

    published(db, team, draft_factory())
    task = get_task(db)
    draft = draft_factory(section="review", status="未完成", task_id=task.id, task_version=1)
    meeting = published(db, team, draft)
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=1,
            content=task.content,
            due_date=date(2026, 10, 1),
            owner_ids=[team["a"].id, team["b"].id],
            verifier_id=team["host"].id,
            reason="增加验收内容",
        ),
    )
    team["a"].department_id = team["other"].id
    revision(db, team["host"], meeting.id, "补充备注")
    updated = MeetingDraft.model_validate(meeting.draft)
    updated.items[0].notes = "补充说明，不改写原期限和人员归属"
    save_meeting(db, team["host"], meeting.id, updated, 2)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="补充备注"))
    record = db.scalar(
        select(Assessment).where(
            Assessment.meeting_id == meeting.id,
            Assessment.meeting_version == 2,
            Assessment.user_id == team["a"].id,
        )
    )
    assert record.person_department_id == team["dep"].id
    assert record.due_date == date(2026, 8, 17)
