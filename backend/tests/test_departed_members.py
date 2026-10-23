from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Assessment, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting


def published(db, team, draft_factory):
    draft = draft_factory()
    draft.recorder_id = team["colleague"].id
    draft.attendee_ids = [team["a"].id]
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    return meeting, db.scalar(select(Task))


def test_departed_roles_preserved_when_revising_old_minutes(db, team, draft_factory):
    meeting, task = published(db, team, draft_factory)
    team["a"].active = team["colleague"].active = False
    revision(db, team["host"], meeting.id, "补充决议")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.decisions = "补记历史会议决议"
    save_meeting(db, team["host"], meeting.id, draft, 2)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="补充决议"))
    assert meeting.published_version == 2 and task.version == 1
    assert meeting.draft["recorder_name"] == team["colleague"].name


def test_review_retains_departed_owner_without_reassigning_task(db, team, draft_factory):
    _, task = published(db, team, draft_factory)
    team["a"].active = False
    draft = draft_factory(section="review", status="未完成", task_id=task.id, task_version=1)
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert db.scalar(select(Assessment).where(Assessment.user_id == team["a"].id)).status == "未完成"


@pytest.mark.parametrize("mutation", ["new_row", "new_host", "deadline", "recipient"])
def test_departed_member_cannot_receive_new_assignment(db, team, draft_factory, mutation):
    meeting, _ = published(db, team, draft_factory)
    team["a"].active = False
    revision(db, team["host"], meeting.id, "更改安排")
    draft = MeetingDraft.model_validate(meeting.draft)
    if mutation == "new_row":
        draft.items.append(
            MeetingItem(section="new", content="新要求", owner_ids=[team["a"].id], due_date=date(2026, 12, 1))
        )
    elif mutation == "new_host":
        draft.host_id = team["a"].id
    elif mutation == "deadline":
        draft.items[0].due_date = date(2026, 12, 1)
    else:
        draft.recipient_ids = [team["a"].id]
    with pytest.raises(HTTPException) as error:
        save_meeting(db, team["host"], meeting.id, draft, 2)
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="更改安排"))
    assert error.value.status_code == 422
    assert meeting.published_version == 1
