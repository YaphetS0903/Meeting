from sqlalchemy import select

from app.models import MeetingVersion
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, save_meeting


def test_manual_recipient_choice_is_persisted_without_changing_attendance(db, team, draft_factory):
    draft = draft_factory()
    draft.attendee_ids = [team["a"].id, team["b"].id]
    draft.recipient_ids = [team["b"].id]
    draft.recipient_customized = True
    meeting = create_meeting(db, team["host"], draft)
    restored = MeetingDraft.model_validate(meeting.draft)
    assert restored.recipient_customized is True
    assert restored.recipient_ids == [team["b"].id]
    save_meeting(db, team["host"], meeting.id, restored, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(
        expected_version=meeting.draft_version, notify=False, shared_recipients=True,
    ))
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    assert version.snapshot["recipient_customized"] is True
    assert version.snapshot["notification_recipient_ids"] == [team["b"].id]
    assert version.snapshot["attendee_ids"] == [team["a"].id, team["b"].id]
