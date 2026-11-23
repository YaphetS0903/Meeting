from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.extra_api import Activate, activate
from app.models import Assessment, OutboxEvent, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import carry_forward, create_meeting, publish
from tests.test_historical_links import confirmed as confirm_history
from tests.test_historical_links import reference


def confirmed(db, team, when="2026-08-18", reference=None, status="未到期"):
    return confirm_history(db, team, when, reference, status, due_date="2026-10-01")


def test_confirmed_import_carries_without_creating_or_notifying_tasks(db, team):
    _, history = confirmed(db, team)
    rows = carry_forward(db, team["host"], team["series"].id, meeting_date=date(2026, 8, 25))
    assert len(rows) == 1
    assert rows[0]["task_id"] is None
    assert rows[0]["historical_reference"]["meeting_id"] == history.id
    assert rows[0]["historical_work_id"] == history.draft["items"][0]["historical_work_id"]
    current = create_meeting(db, team["host"], MeetingDraft(
        title="本次新会议", department_id=team["dep"].id, series_id=team["series"].id,
        host_id=team["host"].id, meeting_date=date(2026, 8, 25),
        items=[MeetingItem.model_validate(row) for row in rows],
    ))
    publish(db, team["host"], current.id, PublishRequest(expected_version=1, notify=False))
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(Assessment).where(Assessment.meeting_id == current.id)) == 1
    assert set(db.scalars(select(OutboxEvent.channel))) == {"export"}


def test_latest_historical_completion_suppresses_older_unfinished_row(db, team):
    _, first = confirmed(db, team)
    confirmed(db, team, "2026-08-25", reference(first), "已完成")
    assert carry_forward(db, team["host"], team["series"].id) == []
    rows = carry_forward(db, team["host"], team["series"].id, meeting_date=date(2026, 8, 20))
    assert len(rows) == 1 and rows[0]["historical_reference"]["meeting_id"] == first.id


def test_linked_history_only_carries_once_with_latest_reference(db, team):
    _, first = confirmed(db, team)
    _, second = confirmed(db, team, "2026-08-25", reference(first))
    rows = carry_forward(db, team["host"], team["series"].id)
    assert len(rows) == 1
    assert rows[0]["historical_reference"]["meeting_id"] == second.id


def test_activated_history_carries_existing_task_instead_of_duplicate_history(db, team):
    _, history = confirmed(db, team)
    task = activate(history.id, Activate(
        item_id="line-1", due_date=date(2026, 10, 1), owner_ids=[team["a"].id],
        expected_version=1, reason="继续跟进",
    ), db, team["host"])
    rows = carry_forward(db, team["host"], team["series"].id)
    assert len(rows) == 1 and rows[0]["task_id"] == task["id"]


def test_history_carry_obeys_series_visibility_and_date(db, team):
    _, history = confirmed(db, team)
    team["host"].is_admin = False
    assert carry_forward(db, team["outsider"], team["series"].id) == []
    assert carry_forward(db, team["host"], team["series"].id, meeting_date=date(2026, 8, 17)) == []
    assert carry_forward(db, team["host"], team["series"].id, exclude_meeting_id=history.id) == []
    assert len(carry_forward(db, team["host"], team["series"].id)) == 1


def test_current_and_future_online_meetings_are_not_carried(db, team, draft_factory):
    meetings = []
    for when in [date(2026, 8, 10), date(2026, 8, 30)]:
        meeting = create_meeting(db, team["host"], draft_factory(when=when))
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
        meetings.append(meeting)
    assert carry_forward(db, team["host"], team["series"].id,
                         meeting_date=date(2026, 8, 20), exclude_meeting_id=meetings[0].id) == []
    rows = carry_forward(db, team["host"], team["series"].id,
                         meeting_date=date(2026, 8, 20))
    assert len(rows) == 1


def test_missing_series_is_explicit_error(db, team):
    with pytest.raises(HTTPException) as error:
        carry_forward(db, team["host"], "missing")
    assert error.value.status_code == 404


def test_extended_history_is_carried_as_its_online_task_without_duplicate(db, team):
    confirmed(db, team)
    row = carry_forward(db, team["host"], team["series"].id)[0]
    arrangement = {**row, "id": "extended-arrangement", "section": "new", "due_date": "2026-11-01", "status": "未到期"}
    meeting = create_meeting(db, team["host"], MeetingDraft(
        title="本次延续安排", department_id=team["dep"].id, series_id=team["series"].id,
        host_id=team["host"].id, meeting_date=date(2026, 10, 27),
        items=[MeetingItem.model_validate(row), MeetingItem.model_validate(arrangement)],
    ))
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    rows = carry_forward(db, team["host"], team["series"].id, meeting_date=date(2026, 11, 2))
    assert len(rows) == 1
    assert rows[0]["task_id"] == meeting.draft["items"][1]["task_id"]
    assert rows[0]["historical_work_id"] == row["historical_work_id"]
