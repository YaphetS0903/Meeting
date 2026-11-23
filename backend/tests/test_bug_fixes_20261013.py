from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException

from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish
from app.services.historical_links import resolve_link, suggestions
from app.services.importing import confirm_candidate, correct_candidate
from tests.test_import_correction import candidate


def private_history(db, team):
    meeting = create_meeting(
        db,
        team["host"],
        MeetingDraft(
            title="财务专项历史会议",
            kind="special",
            host_id=team["outsider"].id,
            meeting_date=date(2026, 8, 18),
            items=[
                MeetingItem(
                    id="private-row",
                    section="review",
                    content="财务专项合同工作",
                    status="未完成",
                    owner_ids=[team["outsider"].id],
                    due_date=date(2026, 8, 20),
                )
            ],
        ),
        historical=True,
    )
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def test_history_suggestions_and_link_resolution_enforce_source_permissions(db, team):
    meeting = private_history(db, team)
    payload = {
        "kind": "special",
        "meeting_date": "2026-08-25",
        "items": [
            {
                "id": "current-row",
                "content": "财务专项合同工作",
                "owner_ids": [team["a"].id],
            }
        ],
    }
    assert suggestions(db, team["a"], payload, "current-row") == []
    assert suggestions(db, team["host"], payload, "current-row")[0]["meeting_id"] == meeting.id
    row = {
        "historical_reference": {
            "meeting_id": meeting.id,
            "version": 1,
            "item_id": "private-row",
            "reason": "核对同一事项",
        }
    }
    with pytest.raises(HTTPException) as error:
        resolve_link(db, team["a"], payload, row)
    assert error.value.status_code == 403
    assert resolve_link(db, team["host"], payload, row)["historical_reference"]["meeting_id"] == meeting.id


def test_candidate_save_and_confirmation_recheck_source_permissions(db, team):
    meeting = private_history(db, team)
    imported, issue = candidate(db, team)
    payload = deepcopy(imported.corrected)
    payload.update(host_id=team["a"].id, meeting_date="2026-08-25", confirmed_issue_ids=[issue.id])
    imported.corrected = deepcopy(payload)
    db.flush()
    payload["items"][0]["historical_reference"] = {
        "meeting_id": meeting.id,
        "version": 1,
        "item_id": "private-row",
        "reason": "核对来源",
    }
    with pytest.raises(HTTPException) as error:
        correct_candidate(db, team["a"], imported.id, payload, 1, "核对内容")
    assert error.value.status_code == 403
    assert imported.version == 1 and not imported.corrected["items"][0].get("historical_reference")
    # A saved reference may have been recorded before this permission fix or a later scope change.
    imported.corrected = deepcopy(payload)
    db.flush()
    with pytest.raises(HTTPException) as error:
        confirm_candidate(db, team["a"], imported.id, 1, "核对入库")
    assert error.value.status_code == 403
    assert imported.state != "confirmed"
