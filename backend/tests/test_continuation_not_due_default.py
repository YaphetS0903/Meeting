from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import Assessment, MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft
from app.services import business
from tests.test_failed_review_carry import assessed, release
from tests.test_historical_links import confirmed
from tests.test_revision_followup_sync import edit_source, release_revision, released


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("due,expected", [
    (date(2026, 8, 23), ""),
    (date(2026, 8, 24), "未到期"),
    (date(2026, 9, 1), "未到期"),
])
def test_continuation_keeps_only_still_not_due_results(
    db, team, draft_factory, monkeypatch, historical, due, expected,
):
    monkeypatch.setattr(business, "today", lambda: date(2026, 8, 24))
    if historical:
        _, source = confirmed(db, team, status="未到期", due_date=due.isoformat())
    else:
        draft = draft_factory()
        draft.items[0].due_date = due
        source = business.create_meeting(db, team["host"], draft)
        release(db, team, source)
    before = deepcopy(source.draft)
    models = (Task, Assessment, OutboxEvent)
    counts = [db.scalar(select(func.count()).select_from(model)) for model in models]
    next_meeting = business.continue_meeting(db, team["host"], source.id)
    item = next_meeting.draft["items"][0]
    assert item["status"] == expected
    assert item["due_date"] == due.isoformat()
    assert item["source"].get("continuation_default_status", "") == expected
    draft = MeetingDraft.model_validate(next_meeting.draft)
    business.save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    db.expire(next_meeting)
    assert next_meeting.draft["items"][0]["status"] == expected
    assert source.draft == before
    assert db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == 1,
    )).snapshot == before
    assert [db.scalar(select(func.count()).select_from(model)) for model in models] == counts


@pytest.mark.parametrize("manual", [None, "status", "postponed_due_date", "notes"])
def test_revision_failure_removes_default_result_but_preserves_manual_result(
    db, team, draft_factory, monkeypatch, manual,
):
    monkeypatch.setattr(business, "today", lambda: date(2026, 8, 24))
    _, source = assessed(db, team, draft_factory, status="未到期")
    target = business.continue_meeting(db, team["host"], source.id)
    assert target.draft["items"][0]["status"] == "未到期"
    if manual:
        draft = MeetingDraft.model_validate(target.draft)
        if manual == "status":
            draft.items[0].source.pop("continuation_default_status")
        elif manual == "postponed_due_date":
            draft.items[0].postponed_due_date = date(2026, 10, 1)
        elif manual == "notes":
            draft.items[0].notes = "下期人工补充"
        business.save_meeting(db, team["host"], target.id, draft, target.draft_version)
    before = deepcopy(target.draft)
    business.revision(db, team["host"], source.id, "更正上期评定结果")
    draft = MeetingDraft.model_validate(source.draft)
    draft.items[0].status = "未完成"
    business.save_meeting(db, team["host"], source.id, draft, source.draft_version)
    release(db, team, source)
    assert target.draft["items"] == (before["items"] if manual else [])
    assert target.draft["meeting_date"] == before["meeting_date"]


def test_postponed_deadline_is_used_and_default_can_be_manually_changed(db, team, draft_factory, monkeypatch):
    monkeypatch.setattr(business, "today", lambda: date(2026, 8, 24))
    source, review = assessed(db, team, draft_factory, status="未到期")
    business.revision(db, team["host"], review.id, "延期")
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].postponed_due_date = date(2026, 10, 1)
    business.save_meeting(db, team["host"], review.id, draft, review.draft_version)
    release(db, team, review)
    followup = business.continue_meeting(db, team["host"], review.id)
    item = followup.draft["items"][0]
    assert item["due_date"] == "2026-10-01" and item["status"] == "未到期"
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].status = "未完成"
    draft.items[0].source.pop("continuation_default_status")
    business.save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    release(db, team, followup)
    assert followup.draft["items"][0]["status"] == "未完成"
    assert business.continue_meeting(db, team["host"], followup.id).draft["items"] == []
    assert source.draft["items"][0]["status"] == "未到期"


def test_unknown_deadline_or_closed_task_is_not_auto_assessed():
    row = {"status": "未到期"}
    assert business.continuation_result(row, None, date(2026, 8, 24)) == ""
    assert business.continuation_result(row, date(2026, 9, 1), date(2026, 8, 24), closed=True) == ""


@pytest.mark.parametrize("target_day,expected", [(date(2026, 11, 20), "未到期"), (date(2026, 12, 10), "")])
def test_sync_new_inheritance_uses_target_meeting_date(db, team, draft_factory, target_day, expected):
    source = released(db, team, draft_factory)
    target = business.continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(target.draft)
    draft.meeting_date = target_day
    draft.items[0].status = ""
    draft.items[0].source.pop("continuation_default_status", None)
    business.save_meeting(db, team["host"], target.id, draft, target.draft_version)
    edit_source(db, team, source, extra=True)
    release_revision(db, team, source)
    assert target.draft["items"][-1]["status"] == expected
    assert target.draft["meeting_date"] == target_day.isoformat()


def test_sync_clears_default_if_revised_deadline_precedes_target_meeting(db, team, draft_factory):
    source = released(db, team, draft_factory)
    target = business.continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(target.draft)
    draft.meeting_date = date(2026, 11, 20)
    business.save_meeting(db, team["host"], target.id, draft, target.draft_version)
    business.revision(db, team["host"], source.id, "调整计划时间")
    draft = MeetingDraft.model_validate(source.draft)
    draft.items[0].due_date = date(2026, 11, 15)
    draft.items[0].change_reason = "提前完成时间"
    business.save_meeting(db, team["host"], source.id, draft, source.draft_version)
    release_revision(db, team, source)
    assert target.draft["items"][0]["status"] == ""
    assert "continuation_default_status" not in target.draft["items"][0]["source"]
