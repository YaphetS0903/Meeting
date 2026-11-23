from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AuditLog, MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services import business, integrations
from app.services.access import meeting_access


@pytest.fixture(autouse=True)
def isolated_contacts(team, monkeypatch):
    for name in ("host", "a", "b", "colleague", "outsider"):
        team[name].email = name + "@example.test"
        team[name].wecom_id = "wecom-" + name
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from", "meetings@example.test")

    class NoExternalProvider:
        def send(self, *args, **kwargs):
            pytest.fail("Shared-recipient tests may not send real notifications")

    monkeypatch.setattr(integrations, "WeCom", NoExternalProvider)
    monkeypatch.setattr(integrations, "smtp_session", lambda: pytest.fail("SMTP must be mocked"))


def shared_publish(db, team, meeting, **kwargs):
    return business.publish(
        db, team["host"], meeting.id,
        PublishRequest(expected_version=meeting.draft_version, shared_recipients=True, **kwargs),
    )


def events(db, meeting, channel):
    return list(db.scalars(select(OutboxEvent).where(
        OutboxEvent.channel == channel,
        OutboxEvent.event_key.like(f"meeting:{meeting.id}:v%"),
    )))


def test_shared_primary_and_mail_only_cc_are_frozen_without_changing_task_owners(
    db, team, draft_factory, monkeypatch,
):
    draft = draft_factory()
    # A same-department colleague can receive the meeting without being a task owner.
    draft.recipient_ids = [team["colleague"].id, team["colleague"].id]
    draft.cc_ids = [team["a"].id, team["colleague"].id, team["a"].id]
    team["a"].wecom_id = None  # Mail-only CC needs no WeCom binding.
    meeting = business.create_meeting(db, team["host"], draft)
    native = []
    monkeypatch.setattr(business, "enqueue_native_todo", lambda db, task, action: native.append((task.id, action)))
    shared_publish(db, team, meeting)

    wecom, = events(db, meeting, "wecom")
    mail, = events(db, meeting, "email")
    assert wecom.payload["user_ids"] == mail.payload["recipient_ids"] == [team["colleague"].id]
    assert mail.payload["cc_ids"] == [team["a"].id]
    assert wecom.payload["shared_recipients"] is mail.payload["shared_recipients"] is True
    assert len(list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "wecom")))) == 1
    task = db.scalar(select(Task))
    assert set(business.owner_ids(db, task)) == {team["a"].id, team["b"].id}
    assert native == [(task.id, "create")]
    version = db.scalar(select(MeetingVersion))
    assert version.snapshot["notification_recipient_ids"] == [team["colleague"].id]
    assert version.snapshot["notification_cc_ids"] == [team["a"].id]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "meeting.publish"))
    assert audit.after["recipient_ids"] == [team["colleague"].id]
    assert audit.after["cc_ids"] == [team["a"].id]
    assert audit.after["shared_recipients"] is True


def test_multiple_recipients_keep_the_selected_order_through_both_transports(db, team, draft_factory, monkeypatch):
    team['colleague'].is_admin = True  # An authorized extra receiver outside the attendee list.
    draft = draft_factory()
    draft.attendee_ids = [team['b'].id, team['a'].id]
    draft.recipient_ids = [team['host'].id, team['b'].id, team['a'].id, team['colleague'].id]
    meeting = business.create_meeting(db, team['host'], draft)
    shared_publish(db, team, meeting)
    mail, = events(db, meeting, 'email')
    wecom, = events(db, meeting, 'wecom')
    assert mail.payload['recipient_ids'] == wecom.payload['user_ids'] == draft.recipient_ids
    sent_users = []

    class Provider:
        def send(self, users, content, **kwargs):
            sent_users.extend(users)
            return {'msgid': 'isolated-order'}

    smtp = Mock()
    smtp.send_message.return_value = {}

    @contextmanager
    def local_smtp():
        yield smtp

    monkeypatch.setattr(integrations, 'WeCom', Provider)
    monkeypatch.setattr(integrations, 'smtp_session', local_smtp)
    monkeypatch.setattr(integrations, 'generate_mail_exports', lambda *args: [])
    integrations.send_wecom(db, wecom)
    result = integrations.send_mail(db, mail)
    assert sent_users == ['wecom-host', 'wecom-b', 'wecom-a', 'wecom-colleague']
    assert result['to'] == ['host@example.test', 'b@example.test', 'a@example.test', 'colleague@example.test']
    assert smtp.send_message.call_args.kwargs['to_addrs'] == result['to']


def test_shared_revision_does_not_send_extra_changed_messages_to_unselected_owners(
    db, team, draft_factory, monkeypatch,
):
    draft = draft_factory()
    draft.recipient_ids = [team["colleague"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    shared_publish(db, team, meeting)
    business.revision(db, team["host"], meeting.id, "调整要求")
    updated = MeetingDraft.model_validate(meeting.draft)
    updated.items[0].content = "修订后的任务要求"
    business.save_meeting(db, team["host"], meeting.id, updated, meeting.draft_version)
    native = []
    monkeypatch.setattr(business, "enqueue_native_todo", lambda db, task, action: native.append((task.id, action)))
    shared_publish(db, team, meeting, reason="调整要求")
    task = db.scalar(select(Task))
    assert task.content == "修订后的任务要求" and task.version == 2
    assert native == [(task.id, "update")]
    assert all(event.payload.get("meeting_publication") for event in db.scalars(
        select(OutboxEvent).where(OutboxEvent.channel == "wecom")
    ))


@pytest.mark.parametrize("problem,match", [
    ("empty", "请选择至少一位通知接收人"),
    ("email", "责任人甲 尚未配置邮箱"),
    ("wecom", "责任人甲 尚未配置企业微信绑定"),
    ("access", "外部门员工 无权查看该会议"),
])
def test_shared_validation_precedes_task_and_outbox_mutations(db, team, draft_factory, problem, match):
    draft = draft_factory()
    draft.recipient_ids = [] if problem == "empty" else [team["a"].id]
    if problem == "email":
        team["a"].email = None
    elif problem == "wecom":
        team["a"].wecom_id = None
    elif problem == "access":
        draft.recipient_ids = [team["outsider"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    with pytest.raises(HTTPException, match=match) as error:
        shared_publish(db, team, meeting)
    assert error.value.status_code == (403 if problem == "access" else 422)
    assert meeting.has_draft and meeting.published_version == 0
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.parametrize("empty", [True, False])
def test_no_notification_does_not_require_contact_configuration(db, team, draft_factory, empty):
    draft = draft_factory()
    draft.recipient_ids = [] if empty else [team["a"].id]
    team["a"].email = team["a"].wecom_id = None
    meeting = business.create_meeting(db, team["host"], draft)
    shared_publish(db, team, meeting, notify=False)
    assert meeting.published_version == 1
    assert events(db, meeting, "email") == events(db, meeting, "wecom") == []
    assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_legacy_callers_keep_owner_notifications_and_selected_mail(db, team, draft_factory):
    draft = draft_factory()
    draft.recipient_ids = [team["colleague"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    business.publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    wecom, = events(db, meeting, "wecom")
    mail, = events(db, meeting, "email")
    assert set(wecom.payload["user_ids"]) == {team["a"].id, team["b"].id}
    assert mail.payload["recipient_ids"] == [team["colleague"].id]
    assert "shared_recipients" not in wecom.payload
    assert len(list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "wecom")))) == 2


def test_delivery_uses_frozen_selection_even_if_later_draft_recipients_change(
    db, team, draft_factory, monkeypatch,
):
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    draft.cc_ids = [team["colleague"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    shared_publish(db, team, meeting)
    wecom, = events(db, meeting, "wecom")
    mail, = events(db, meeting, "email")
    meeting.draft = {**meeting.draft, "recipient_ids": [team["b"].id], "cc_ids": []}
    wecom_calls, mail_calls = [], []

    class Provider:
        def send(self, users, content, *, card=None):
            wecom_calls.append(users)
            return {"msgid": "isolated-shared"}

    smtp = Mock()
    smtp.send_message = lambda message, **kwargs: mail_calls.append((message, kwargs)) or {}

    @contextmanager
    def local_smtp():
        yield smtp

    monkeypatch.setattr(integrations, "WeCom", Provider)
    monkeypatch.setattr(integrations, "smtp_session", local_smtp)
    monkeypatch.setattr(integrations, "generate_mail_exports", lambda *args: [])
    assert integrations.send_wecom(db, wecom)["msgid"] == "isolated-shared"
    assert integrations.send_mail(db, mail)["message_id"]
    assert wecom_calls == [["wecom-a"]]
    assert mail_calls[0][0]["To"] == "a@example.test"
    assert mail_calls[0][0]["Cc"] == "colleague@example.test"
    assert set(mail_calls[0][1]["to_addrs"]) == {"a@example.test", "colleague@example.test"}


@pytest.mark.parametrize("shared", [True, False])
def test_cross_department_company_staff_can_receive_mail_only_cc_without_web_access(
    db, team, draft_factory, monkeypatch, shared,
):
    team["host"].is_admin = False
    team["outsider"].wecom_id = None
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    draft.cc_ids = [team["outsider"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    business.publish(db, team["host"], meeting.id, PublishRequest(
        expected_version=meeting.draft_version, shared_recipients=shared,
    ))
    mail, = events(db, meeting, "email")
    assert mail.payload["cc_ids"] == [team["outsider"].id]
    assert all(team["outsider"].id not in event.payload["user_ids"]
               for event in events(db, meeting, "wecom"))
    assert set(business.owner_ids(db, db.scalar(select(Task)))) == {team["a"].id, team["b"].id}
    with pytest.raises(HTTPException) as denied:
        meeting_access(db, team["outsider"], meeting)
    assert denied.value.status_code == 403
    calls = []
    smtp = Mock()
    smtp.send_message = lambda message, **kwargs: calls.append((message, kwargs)) or {}

    @contextmanager
    def local_smtp():
        yield smtp

    monkeypatch.setattr(integrations, "smtp_session", local_smtp)
    monkeypatch.setattr(integrations, "generate_mail_exports", lambda *args: [])
    assert integrations.send_mail(db, mail)["message_id"]
    assert calls[0][0]["Cc"] == "outsider@example.test"
    assert set(calls[0][1]["to_addrs"]) == {"a@example.test", "outsider@example.test"}


@pytest.mark.parametrize("problem", ["inactive", "missing-email"])
def test_cross_department_cc_requires_active_staff_with_email(db, team, draft_factory, problem):
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    draft.cc_ids = [team["outsider"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    if problem == "inactive":
        team["outsider"].active = False
    else:
        team["outsider"].email = None
    with pytest.raises(HTTPException) as error:
        shared_publish(db, team, meeting)
    assert error.value.status_code == 422
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_legacy_cc_retry_cannot_change_published_staff_selection(db, team, draft_factory):
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    draft.cc_ids = [team["outsider"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    business.publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    mail, = events(db, meeting, "email")
    mail.payload = {**mail.payload, "cc_ids": [team["colleague"].id]}
    with pytest.raises(integrations.NotConfigured, match="邮件抄送人员与已发布版本不一致"):
        integrations.send_mail(db, mail)


@pytest.mark.parametrize("channel", ["wecom", "email"])
def test_tampered_retry_list_is_rejected_against_published_snapshot(db, team, draft_factory, channel):
    draft = draft_factory()
    draft.recipient_ids = [team["a"].id]
    meeting = business.create_meeting(db, team["host"], draft)
    shared_publish(db, team, meeting)
    event, = events(db, meeting, channel)
    key = "user_ids" if channel == "wecom" else "recipient_ids"
    event.payload = {**event.payload, key: [team["b"].id]}
    sender = integrations.send_wecom if channel == "wecom" else integrations.send_mail
    with pytest.raises(integrations.NotConfigured, match="通知名单与已发布版本不一致"):
        sender(db, event)
