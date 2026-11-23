from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import MeetingVersion, OutboxEvent, Task
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


def closed_review(db, team, draft_factory, status):
    initial = draft_factory()
    initial.items[0].due_date = date(2026, 9, 1)
    source = create_meeting(db, team["host"], initial)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    task = db.get(Task, source.draft["items"][0]["task_id"])
    draft = draft_factory(
        section="review", status=status, when=date(2026, 8, 24),
        task_id=task.id, task_version=task.version,
    )
    draft.items[0].due_date = task.due_date
    review = create_meeting(db, team["host"], draft)
    publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    return source, task, review


@pytest.mark.parametrize("status", ["关闭", "不统计"])
def test_terminal_minutes_review_is_not_carried_even_when_live_task_is_open(
    db, team, draft_factory, status,
):
    source, task, review = closed_review(db, team, draft_factory, status)
    before_task = task_snapshot(db, task)
    before_document = deepcopy(review.draft)
    events = db.scalar(select(func.count()).select_from(OutboxEvent))
    assert task.closed_at is None
    for _ in range(2):
        assert carry_forward(db, team["host"], team["series"].id) == []
    # Continuing an earlier document must respect the current published closure
    # as well; its original new-work row still says it is unfinished.
    assert continue_meeting(db, team["host"], source.id).draft["items"] == []
    assert task_snapshot(db, task) == before_task
    assert review.draft == before_document
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == events


@pytest.mark.parametrize("status", ["关闭", "不统计"])
def test_only_published_revision_can_make_closed_review_eligible_again(
    db, team, draft_factory, status,
):
    source, task, review = closed_review(db, team, draft_factory, status)
    old_snapshot = deepcopy(review.draft)
    before_task = task_snapshot(db, task)
    revision(db, team["host"], review.id, "更正原回顾结果")
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].status = "未到期"
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    assert carry_forward(db, team["host"], team["series"].id) == []
    publish(db, team["host"], review.id, PublishRequest(
        expected_version=review.draft_version, reason="工作仍需跟进", notify=False,
    ))
    assert [row["task_id"] for row in carry_forward(
        db, team["host"], team["series"].id,
    )] == [task.id]
    assert [row["task_id"] for row in continue_meeting(
        db, team["host"], source.id,
    ).draft["items"]] == [task.id]
    assert task_snapshot(db, task) == before_task
    assert db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == review.id, MeetingVersion.number == 1,
    )).snapshot == old_snapshot
