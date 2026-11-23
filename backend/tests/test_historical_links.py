from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.extra_api import Activate, activate
from app.models import Assessment, CompletionRequest, OutboxEvent, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish
from app.services.historical_links import suggestions
from app.services.importing import confirm_candidate, correct_candidate
from tests.test_import_correction import candidate


def confirmed(db, team, when="2026-08-18", reference=None, status="未完成", *, due_date=None):
    c, issue = candidate(db, team)
    p = deepcopy(c.corrected)
    p.update(
        meeting_date=when,
        confirmed_issue_ids=[issue.id],
        reviewer_id=team["colleague"].id,
        series_id=team["series"].id,
    )
    p["items"][0].update(historical_reference=reference, status=status)
    if due_date:
        p["items"][0]["due_date"] = due_date
    correct_candidate(db, team["host"], c.id, p, 1, "核对原文并关联")
    return c, confirm_candidate(db, team["host"], c.id, 2, "核对通过")


def reference(meeting):
    return {
        "meeting_id": meeting.id,
        "version": 1,
        "item_id": "line-1",
        "reason": "内容和责任人一致，是同一未结事项",
    }


def test_history_links_preserve_each_assessment_and_do_not_activate(db, team):
    c1, first = confirmed(db, team)
    c2, second = confirmed(db, team, "2026-08-25", reference(first), "已完成")
    assert first.draft["items"][0]["historical_work_id"] == second.draft["items"][0]["historical_work_id"]
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(CompletionRequest)) == 0
    assert set(db.scalars(select(OutboxEvent.channel))) == {"export"}
    assert set(db.scalars(select(Assessment.status))) == {"未完成", "已完成"}
    assert second.series_id == team["series"].id and second.draft["reviewer_id"] is None
    assert second.draft["items"][0]["source"]["row"] == 18
    result = suggestions(db, team["host"], c2.corrected, "line-1")
    assert any(r["meeting_id"] == first.id and "同一会议系列" in r["match_reasons"] for r in result)


def test_special_meeting_link_suggestions_do_not_require_department(db, team):
    meeting = create_meeting(
        db,
        team["host"],
        MeetingDraft(
            title="专项整改会",
            kind="special",
            host_id=team["host"].id,
            meeting_date=date(2026, 8, 18),
            items=[
                MeetingItem(
                    id="special-history",
                    section="review",
                    status="未完成",
                    content="完成跨部门专项整改",
                    owner_ids=[team["a"].id],
                    due_date=date(2026, 8, 20),
                )
            ],
        ),
        historical=True,
    )
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    payload = {
        "kind": "special",
        "department_id": None,
        "meeting_date": "2026-08-25",
        "items": [
            {
                "id": "special-current",
                "content": "完成跨部门专项整改",
                "owner_ids": [team["a"].id],
                "due_date": "2026-08-20",
            }
        ],
    }

    result = suggestions(db, team["host"], payload, "special-current")

    assert result[0]["meeting_id"] == meeting.id


def test_same_historical_work_only_activates_once_from_different_meetings(db, team):
    _, first = confirmed(db, team)
    _, second = confirmed(db, team, "2026-08-25", reference(first))
    body = Activate(
        item_id="line-1",
        due_date=date(2026, 10, 1),
        owner_ids=[team["a"].id],
        expected_version=1,
        reason="继续跟进",
    )
    one = activate(first.id, body, db, team["host"])
    two = activate(second.id, body, db, team["host"])
    assert one == two and db.scalar(select(func.count()).select_from(Task)) == 1
    assert len(list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "wecom")))) == 1


@pytest.mark.parametrize("change", [{"version": 9}, {"item_id": "missing"}, {"reason": " "}])
def test_invalid_or_stale_history_reference_is_rejected(db, team, change):
    _, first = confirmed(db, team)
    with pytest.raises(HTTPException) as error:
        confirmed(db, team, "2026-08-25", {**reference(first), **change})
    assert error.value.status_code in {409, 422}
