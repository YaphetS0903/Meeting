from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import Assessment, MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import (
    carry_forward,
    continue_meeting,
    create_meeting,
    publish,
    revision,
    save_meeting,
    task_snapshot,
)
from tests.test_historical_links import confirmed, reference


def release(db, team, meeting):
    publish(db, team["host"], meeting.id, PublishRequest(
        expected_version=meeting.draft_version, notify=False,
        reason="核对完成" if meeting.published_version else "",
    ))


def assessed(db, team, draft_factory, *, status="未完成", extend=False):
    initial = draft_factory()
    initial.items[0].due_date = date(2026, 9, 1)
    source = create_meeting(db, team["host"], initial)
    release(db, team, source)
    review = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(review.draft)
    draft.meeting_date = date(2026, 8, 24)
    draft.items[0].status = status
    if extend:
        new = draft.items[0].model_copy(deep=True)
        new.id, new.section, new.status = "extension", "new", "未到期"
        new.due_date, new.change_reason = date(2026, 9, 30), "延续安排"
        draft.items.append(new)
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    release(db, team, review)
    return source, review


@pytest.mark.parametrize("status,extend", [("未完成", False), ("未完成", True), ("未到期", False)])
def test_followup_and_carry_preserve_only_explicit_extensions_or_not_due_work(
    db, team, draft_factory, status, extend,
):
    source, review = assessed(db, team, draft_factory, status=status, extend=extend)
    task = db.get(Task, source.draft["items"][0]["task_id"])
    document = deepcopy(review.draft)
    task_before = task_snapshot(db, task)
    counts = [db.scalar(select(func.count()).select_from(model)) for model in (Task, Assessment, OutboxEvent)]
    expected = [task.id] if extend or status == "未到期" else []
    for _ in range(2):
        assert [r["task_id"] for r in carry_forward(db, team["host"], team["series"].id)] == expected
        next_meeting = continue_meeting(db, team["host"], review.id)
        assert [r["task_id"] for r in next_meeting.draft["items"]] == expected
        assert all(r["status"] == "" for r in next_meeting.draft["items"])
    # Selecting an older meeting cannot resurrect a later failed assessment.
    assert [r["task_id"] for r in continue_meeting(db, team["host"], source.id).draft["items"]] == expected
    assert task_snapshot(db, task) == task_before
    assert review.draft == document
    assert [db.scalar(select(func.count()).select_from(model)) for model in (Task, Assessment, OutboxEvent)] == counts
    assert set(db.scalars(select(Assessment.status).where(Assessment.meeting_id == review.id))) == {status}


def test_failed_result_only_takes_effect_after_publication_and_respects_carry_date(db, team, draft_factory):
    source, review = assessed(db, team, draft_factory, status="未到期")
    identifier = source.draft["items"][0]["task_id"]
    revision(db, team["host"], review.id, "更正完成结果")
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    assert [r["task_id"] for r in carry_forward(db, team["host"], team["series"].id)] == [identifier]
    release(db, team, review)
    assert carry_forward(db, team["host"], team["series"].id) == []
    assert [r["task_id"] for r in carry_forward(
        db, team["host"], team["series"].id, meeting_date=date(2026, 8, 20),
    )] == [identifier]
    assert [r["task_id"] for r in carry_forward(
        db, team["host"], team["series"].id, exclude_meeting_id=review.id,
    )] == [identifier]


@pytest.mark.parametrize("manual", [None, "status", "postponed_due_date", "notes", "new"])
def test_revision_sync_removes_untouched_failed_inheritance_and_keeps_manual_work(
    db, team, draft_factory, manual,
):
    _, review = assessed(db, team, draft_factory, status="未到期")
    target = continue_meeting(db, team["host"], review.id)
    draft = MeetingDraft.model_validate(target.draft)
    draft.meeting_date = date(2026, 9, 1)
    if manual == "status":
        draft.items[0].status = "未到期"
    elif manual == "postponed_due_date":
        draft.items[0].postponed_due_date = date(2026, 10, 1)
    elif manual == "notes":
        draft.items[0].notes = "下期人工补充"
    elif manual == "new":
        row = draft.items[0].model_copy(deep=True)
        row.id, row.section, row.status = "manual-new", "new", "未到期"
        row.due_date, row.change_reason = date(2026, 10, 1), "下期安排"
        draft.items.append(row)
    save_meeting(db, team["host"], target.id, draft, target.draft_version)
    before = deepcopy(target.draft)
    revision(db, team["host"], review.id, "更正结果")
    changed = MeetingDraft.model_validate(review.draft)
    changed.items[0].status = "未完成"
    save_meeting(db, team["host"], review.id, changed, review.draft_version)
    release(db, team, review)
    expected = before["items"] if manual in {"status", "postponed_due_date", "notes"} else [r for r in before["items"] if r["section"] == "new"]
    assert target.draft["items"] == expected
    assert target.draft["meeting_date"] == "2026-09-01"
    assert db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == review.id, MeetingVersion.number == 1,
    )).snapshot["items"][0]["status"] == "未到期"


@pytest.mark.parametrize("status", ["未完成", "未到期"])
def test_imported_historical_assessments_obey_same_carry_rule(db, team, status):
    _, history = confirmed(db, team, status=status, due_date="2026-10-01")
    expected_count = int(status == "未到期")
    assert len(carry_forward(db, team["host"], team["series"].id)) == expected_count
    assert len(continue_meeting(db, team["host"], history.id).draft["items"]) == expected_count
    assert not list(db.scalars(select(Task)))


def test_older_import_cannot_resurrect_later_failed_assessment(db, team):
    _, first = confirmed(db, team, status="未到期", due_date="2026-10-01")
    _, latest = confirmed(db, team, "2026-08-25", reference(first), "未完成")
    assert carry_forward(db, team["host"], team["series"].id) == []
    assert continue_meeting(db, team["host"], first.id).draft["items"] == []
    assert continue_meeting(db, team["host"], latest.id).draft["items"] == []
