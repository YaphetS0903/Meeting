from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.models import OutboxEvent, Task
from app.schemas import ApplyCompletion, PublishRequest, VerifyCompletion
from app.services import integrations
from app.services.business import apply_completion, create_meeting, publish, verify_completion


@pytest.mark.parametrize("content", ["验收要求需要逐项确认。" * 300, "中文🚀é" * 1000, "A" * 9000])
def test_long_messages_fit_provider_bytes_and_keep_deadline_source_and_full_link(
    db, team, draft_factory, monkeypatch, content
):
    draft = draft_factory()
    draft.title = "很长的会议名称" * 50
    draft.items[0].content = content
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task))
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "wecom"))
    for who in ["a", "b", "host"]:
        team[who].wecom_id = "wecom-" + who
    sent = []

    class Provider:
        def send(self, users, text):
            assert len(text.encode("utf-8")) <= 2048
            sent.append(text)
            return {"msgid": "test"}

    monkeypatch.setattr(integrations, "WeCom", Provider)
    integrations.send_wecom(db, event)
    assert "截止日期：2026-08-17" in sent[0] and "会议来源：很长的会议名称" in sent[0]
    assert (
        sent[0].splitlines()[-1]
        == integrations.get_settings().public_url.rstrip("/") + "/tasks?id=" + task.id
    )
    assert "�" not in sent[0] and task.content == content


def test_reapplication_has_distinct_actionable_link_instead_of_provider_duplicate(
    db, team, draft_factory, monkeypatch
):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task))
    team["host"].wecom_id = "verifier"
    sent = []

    class Provider:
        def send(self, users, text):
            sent.append(text)
            return {"msgid": "test"}

    monkeypatch.setattr(integrations, "WeCom", Provider)
    first = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="content-first", explanation="第一次提交"),
    )
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f"request:{first.id}:verify"))
    integrations.send_wecom(db, event)
    verify_completion(db, team["host"], first.id, VerifyCompletion(decision="rejected", opinion="请补齐证据"))
    second = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="content-second", explanation="已补齐证据"),
    )
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f"request:{second.id}:verify"))
    integrations.send_wecom(db, event)
    assert sent[0] != sent[1]
    assert parse_qs(urlparse(sent[0].splitlines()[-1]).query)["request"] == [first.id]
    assert parse_qs(urlparse(sent[1].splitlines()[-1]).query)["request"] == [second.id]
