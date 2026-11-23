from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.extra_api import PreviewRequest, preview_minutes
from app.models import Assessment, Meeting, MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish, revision, save_meeting
from tests.test_historical_links import confirmed


def source_meeting(db, team, draft_factory):
    draft = draft_factory(notes="保留上次会议备注")
    source = create_meeting(db, team["host"], draft)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    return source


def test_continuation_review_result_is_blank_and_survives_save_reload(db, team, draft_factory):
    source = source_meeting(db, team, draft_factory)
    before = deepcopy(source.draft)
    followup = continue_meeting(db, team["host"], source.id)
    item = followup.draft["items"][0]
    assert item["section"] == "review" and item["status"] == ""
    assert set(item["owner_ids"]) == set(before["items"][0]["owner_ids"])
    for field in ("task_id", "due_date", "content", "notes"):
        assert item[field] == before["items"][0][field]
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].notes += "；本次补充"
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    db.flush()
    db.expire(followup)
    assert db.get(Meeting, followup.id).draft["items"][0]["status"] == ""
    assert source.draft == before
    assert db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == 1,
    )).snapshot == before


def test_historical_continuation_also_requires_a_fresh_result(db, team):
    _, source = confirmed(db, team, status="未到期", due_date="2026-10-01")
    before = deepcopy(source.draft)
    followup = continue_meeting(db, team["host"], source.id)
    item = followup.draft["items"][0]
    assert item["status"] == ""
    assert item["historical_reference"]["meeting_id"] == source.id
    assert item["historical_work_id"] == before["items"][0]["historical_work_id"]
    assert source.draft == before


@pytest.mark.parametrize("action", ["preview", "publish"])
def test_blank_result_blocks_preview_and_publish_without_side_effects(db, team, draft_factory, action):
    source = source_meeting(db, team, draft_factory)
    followup = continue_meeting(db, team["host"], source.id)
    models = (MeetingVersion, Task, Assessment, OutboxEvent)
    counts = [db.scalar(select(func.count()).select_from(model)) for model in models]
    before = deepcopy(followup.draft)
    with pytest.raises(HTTPException) as error:
        if action == "preview":
            preview_minutes(followup.id, PreviewRequest(expected_version=followup.draft_version), db, team["host"])
        else:
            publish(db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version))
    assert error.value.status_code == 422
    assert "第 1 项的完成结果" in error.value.detail
    assert [db.scalar(select(func.count()).select_from(model)) for model in models] == counts
    assert followup.draft == before and followup.published_version == 0


@pytest.mark.parametrize("status", ["已完成", "未完成", "未到期", "不统计", "关闭"])
def test_selected_result_can_preview_and_publish_and_revision_keeps_it(db, team, draft_factory, status):
    source = source_meeting(db, team, draft_factory)
    followup = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(followup.draft)
    draft.meeting_date = date(2026, 8, 11)
    draft.items[0].status = status
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    response = preview_minutes(followup.id, PreviewRequest(expected_version=followup.draft_version, format="xlsx"), db, team["host"])
    assert response.body[:2] == b"PK"
    publish(db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False))
    assert followup.draft["items"][0]["status"] == status
    revision(db, team["host"], followup.id, "调整会议备注")
    assert followup.draft["items"][0]["status"] == status


def test_blank_result_is_not_allowed_for_new_arrangements():
    assert MeetingItem(section="new").status == "未到期"
    with pytest.raises(ValidationError):
        MeetingItem(section="new", status="")
