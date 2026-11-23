from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import AuditLog, MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish, revision, save_meeting


def test_continuation_keeps_latest_published_notes_and_ignores_unpublished_changes(db, team, draft_factory):
    source = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    revision(db, team["host"], source.id, "补充工作备注")
    revised = MeetingDraft.model_validate(source.draft)
    revised.items[0].notes = "与侯总达成一致\n保留最新修订备注"
    save_meeting(db, team["host"], source.id, revised, source.draft_version)
    publish(db, team["host"], source.id,
            PublishRequest(expected_version=source.draft_version, notify=False, reason="补充工作备注"))
    snapshot = deepcopy(source.draft)
    revision(db, team["host"], source.id, "尚未发布的修订")
    unpublished = MeetingDraft.model_validate(source.draft)
    unpublished.items[0].notes = "不应提前带入的备注"
    save_meeting(db, team["host"], source.id, unpublished, source.draft_version)
    events_before = list(db.scalars(select(OutboxEvent.id)))

    followup = continue_meeting(db, team["host"], source.id)
    assert followup.draft["items"][0]["notes"] == snapshot["items"][0]["notes"]
    assert followup.draft["items"][0]["section"] == "review"
    assert followup.draft["items"][0]["task_id"] == snapshot["items"][0]["task_id"]
    assert db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == 2,
    )).snapshot == snapshot
    assert list(db.scalars(select(OutboxEvent.id))) == events_before
    assert len(list(db.scalars(select(Task)))) == 1
    assert db.scalar(select(AuditLog).where(
        AuditLog.entity_id == followup.id, AuditLog.action == "meeting.continue",
    )).after == {"source_meeting_id": source.id, "source_version": 2}


@pytest.mark.parametrize("notify", [False, True])
def test_revision_email_requires_opt_in_and_audits_notification_choice(db, team, draft_factory, notify):
    team["a"].email = "owner@example.test"
    source = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    revision(db, team["host"], source.id, "工作备注修订")
    revised = MeetingDraft.model_validate(source.draft)
    revised.items[0].notes = "最新备注"
    revised.recipient_ids = [team["a"].id]
    save_meeting(db, team["host"], source.id, revised, source.draft_version)
    command = PublishRequest(expected_version=source.draft_version, notify=notify, reason="工作备注修订")
    publish(db, team["host"], source.id, command)
    publish(db, team["host"], source.id, command)
    events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "email")))
    assert len(events) == int(notify)
    if notify:
        assert events[0].payload == {"meeting_id": source.id, "version": 2,
                                     "recipient_ids": [team["a"].id], "cc_ids": [], "cc_emails": []}
    entry = list(db.scalars(select(AuditLog).where(
        AuditLog.entity_id == source.id, AuditLog.action == "meeting.publish",
    ).order_by(AuditLog.created_at)))[-1]
    assert entry.after == {"version": 2, "notify": notify,
                           "recipient_ids": [team["a"].id], "cc_ids": [], "cc_emails": []}
