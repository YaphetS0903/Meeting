from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingItem, PublishRequest
from app.services.business import carry_forward, continue_meeting, create_meeting, publish


def replacement(db, team, draft_factory, **changes):
    initial = draft_factory()
    initial.items[0].due_date = date(2026, 9, 1)
    first = create_meeting(db, team["host"], initial)
    publish(db, team["host"], first.id, PublishRequest(expected_version=1, notify=False))
    old = db.get(Task, first.draft["items"][0]["task_id"])
    draft = draft_factory(section="review", status="未到期", when=date(2026, 8, 24),
                          task_id=old.id, task_version=old.version)
    draft.items[0].due_date = old.due_date
    # Production carry writes the verifier explicitly; a manual arrangement
    # leaves it empty to use the same meeting host. They mean the same person.
    draft.items[0].verifier_id = team["host"].id
    row = draft.items[0].model_dump()
    row.update(id="replacement", section="new", status="未到期", task_id=None, task_version=None,
               verifier_id=None, due_date=date(2026, 9, 30))
    row.update(changes)
    draft.items.append(MeetingItem.model_validate(row))
    second = create_meeting(db, team["host"], draft)
    publish(db, team["host"], second.id, PublishRequest(expected_version=1, notify=False))
    new = db.get(Task, second.draft["items"][1]["task_id"])
    return first, second, old, new


def test_carry_and_continuation_prefer_replacement_without_mutating_tasks(db, team, draft_factory):
    first, second, old, new = replacement(db, team, draft_factory)
    snapshot = deepcopy(second.draft)
    before = (old.version, old.closed_at, old.status, old.due_date)
    events = db.scalar(select(func.count()).select_from(OutboxEvent))
    for _ in range(2):
        rows = carry_forward(db, team["host"], team["series"].id)
        assert [row["task_id"] for row in rows] == [new.id]
        assert rows[0]["due_date"] == "2026-09-30"
    followup = continue_meeting(db, team["host"], second.id)
    assert [row["task_id"] for row in followup.draft["items"]] == [new.id]
    assert (old.version, old.closed_at, old.status, old.due_date) == before
    assert second.draft == snapshot
    assert db.scalar(select(func.count()).select_from(Task)) == 2
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == events
    assert [row["task_id"] for row in carry_forward(
        db, team["host"], team["series"].id, meeting_date=date(2026, 8, 20))] == [old.id]
    assert [row["task_id"] for row in carry_forward(
        db, team["host"], team["series"].id, exclude_meeting_id=second.id)] == [old.id]


def test_completed_replacement_does_not_resurrect_older_task(db, team, draft_factory):
    _, second, old, new = replacement(db, team, draft_factory)
    draft = draft_factory(section="review", status="已完成", when=date(2026, 9, 30),
                          task_id=new.id, task_version=new.version)
    draft.items[0].due_date = new.due_date
    third = create_meeting(db, team["host"], draft)
    publish(db, team["host"], third.id, PublishRequest(expected_version=1, notify=False))
    assert new.closed_at and old.closed_at is None
    assert carry_forward(db, team["host"], team["series"].id) == []
    assert continue_meeting(db, team["host"], second.id).draft["items"] == []


@pytest.mark.parametrize("changes", [
    {"owner_ids": "other"}, {"requirements": "不同工作要求"},
    {"deliverable": "另一份成果"}, {"category": "另一个项目"},
    {"verifier_id": "other"},
])
def test_distinct_work_is_preserved(db, team, draft_factory, changes):
    changes = {key: ([team["colleague"].id] if key == "owner_ids" else team["colleague"].id)
               if value == "other" else value for key, value in changes.items()}
    _, second, old, new = replacement(db, team, draft_factory, **changes)
    rows = carry_forward(db, team["host"], team["series"].id)
    assert {row["task_id"] for row in rows} == {old.id, new.id}
    assert {row["task_id"] for row in continue_meeting(db, team["host"], second.id).draft["items"]} == {
        old.id, new.id,
    }


def test_same_name_in_separate_meetings_without_review_link_is_preserved(db, team, draft_factory):
    identifiers = []
    for day in [10, 24]:
        meeting = create_meeting(db, team["host"], draft_factory(when=date(2026, 8, day)))
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
        identifiers.append(meeting.draft["items"][0]["task_id"])
    assert {row["task_id"] for row in carry_forward(db, team["host"], team["series"].id)} == set(identifiers)


@pytest.mark.parametrize("kind", ["ambiguous", "withdrawn", "periodic", "new_periodic", "edited", "historical"])
def test_uncertain_or_inapplicable_replacement_does_not_hide_old_work(db, team, draft_factory, kind):
    _, second, old, new = replacement(db, team, draft_factory)
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == second.id))
    snapshot = deepcopy(version.snapshot)
    if kind == "ambiguous":
        snapshot["items"].append({**snapshot["items"][1], "id": "independent-arrangement"})
        version.snapshot = snapshot
    elif kind == "withdrawn":
        new.withdrawn_at = datetime.now(timezone.utc)
    elif kind == "periodic":
        old.recurrence_key = "monthly"
    elif kind == "new_periodic":
        new.recurrence_period = date(2026, 9, 1)
    elif kind == "edited":
        old.version += 1
        old.requirements = "之后单独修改过的独立安排"
    else:
        second.historical = True
    db.flush()
    assert old.id in {row["task_id"] for row in carry_forward(db, team["host"], team["series"].id)}
