from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.models import MeetingVersion, OutboxEvent
from app.schemas import MeetingDraft, PublishRequest
from app.services import business, integrations


def test_cc_addresses_are_optional_validated_and_case_insensitively_unique(draft_factory):
    data = draft_factory().model_dump(mode="json")
    assert MeetingDraft.model_validate(data).cc_emails == []
    data["cc_emails"] = ["Auditor@EXAMPLE.com", "auditor@example.com", "other@example.com"]
    assert MeetingDraft.model_validate(data).cc_emails == ["Auditor@example.com", "other@example.com"]
    for value in ("not-an-email", "ok@example.com\r\nBcc: hidden@example.com", "a@example.com,b@example.com"):
        with pytest.raises(ValidationError):
            MeetingDraft.model_validate({**data, "cc_emails": [value]})


def test_copied_mailbox_formats_normalize_without_accepting_control_characters(draft_factory):
    data = draft_factory().model_dump(mode="json")
    for value in ["张三 <Person@EXAMPLE.com>", "Ｐｅｒｓｏｎ＠ＥＸＡＭＰＬＥ．ｃｏｍ", "mailto:Person@example.com"]:
        assert MeetingDraft.model_validate({**data, "cc_emails": [value]}).cc_emails == ["Person@example.com"]
    for value in ["x@example.com\r\nBcc: hidden@example.com", "张三 <x@example.com>\n"]:
        with pytest.raises(ValidationError):
            MeetingDraft.model_validate({**data, "cc_emails": [value]})


@pytest.mark.parametrize("notify", [True, False])
def test_cc_email_is_frozen_mail_only_and_deduplicated_at_smtp(
    db, team, draft_factory, monkeypatch, notify,
):
    for name in ("a", "b", "colleague"):
        team[name].email = name + "@example.com"
        team[name].wecom_id = "wecom-" + name
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    draft.cc_ids = [team["colleague"].id]
    draft.cc_emails = ["A@example.com", "colleague@example.com", "External@example.com"]
    meeting = business.create_meeting(db, team["host"], draft)
    business.publish(db, team["host"], meeting.id, PublishRequest(
        expected_version=1, shared_recipients=True, notify=notify,
    ))
    events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel.in_(["email", "wecom"]))))
    if not notify:
        assert events == []
        return
    mail, = [e for e in events if e.channel == "email"]
    wecom, = [e for e in events if e.channel == "wecom"]
    assert wecom.payload["user_ids"] == [team["a"].id]
    assert "cc_emails" not in wecom.payload
    assert mail.payload["cc_emails"] == draft.cc_emails
    assert db.scalar(select(MeetingVersion)).snapshot["notification_cc_emails"] == draft.cc_emails
    meeting.draft = {**meeting.draft, "cc_emails": ["changed@example.com"]}
    config = get_settings()
    monkeypatch.setattr(config, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(config, "smtp_from", "meetings@example.com")
    smtp = Mock()
    smtp.send_message.return_value = {}

    @contextmanager
    def connection():
        yield smtp

    monkeypatch.setattr(integrations, "smtp_session", connection)
    monkeypatch.setattr(integrations, "generate_mail_exports", lambda *args: [])
    integrations.send_mail(db, mail)
    message = smtp.send_message.call_args.args[0]
    assert str(message["To"]) == "a@example.com"
    assert str(message["Cc"]) == "colleague@example.com, External@example.com"
    assert smtp.send_message.call_args.kwargs["to_addrs"] == ["a@example.com", "colleague@example.com", "External@example.com"]
    mail.payload = {**mail.payload, "cc_emails": ["tampered@example.com"]}
    with pytest.raises(integrations.NotConfigured, match="通知名单与已发布版本不一致"):
        integrations.send_mail(db, mail)
    assert smtp.send_message.call_count == 1
