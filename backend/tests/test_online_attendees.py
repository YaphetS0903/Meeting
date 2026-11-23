from copy import deepcopy
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfReader
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import MeetingVersion
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish
from app.services.exports import pdf_minutes, xlsx_minutes
from app.services.meeting_attendance import attendance_rows


def online_draft(team, draft_factory):
    data = draft_factory().model_dump(mode="json")
    data.update(attendee_ids=[team["a"].id, team["b"].id],
                online_attendee_ids=[team["host"].id, team["a"].id, team["a"].id])
    return MeetingDraft.model_validate(data)


def test_online_attendance_is_optional_distinct_and_limited_to_actual_attendees(team, draft_factory):
    assert draft_factory().online_attendee_ids == []
    draft = online_draft(team, draft_factory)
    assert draft.online_attendee_ids == [team["host"].id, team["a"].id]
    data = draft.model_dump(mode="json")
    data["online_attendee_ids"].append(team["outsider"].id)
    with pytest.raises(ValidationError, match="线上参会人员必须"):
        MeetingDraft.model_validate(data)


def test_online_attendance_preview_publish_and_continue(db, team, draft_factory, monkeypatch):
    draft = online_draft(team, draft_factory)
    meeting = create_meeting(db, team["host"], draft)
    assert meeting.draft["online_attendee_ids"] == draft.online_attendee_ids
    before = deepcopy(meeting.draft)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            for fmt in ["xlsx", "pdf"]:
                response = client.post(f"/api/meetings/{meeting.id}/preview",
                    headers={"Authorization": "Bearer " + issue_token(team["host"])},
                    json={"expected_version": meeting.draft_version, "format": fmt})
                assert response.status_code == 200, response.text
                if fmt == "xlsx":
                    book = openpyxl.load_workbook(BytesIO(response.content))
                    text = " ".join(str(c.value or "") for sheet in book for row in sheet for c in row)
                    for sheet in book:
                        onsite = next(r for r in sheet if r[0].value == "现场参会")
                        assert onsite[1].value == team["b"].name
                else:
                    text = "".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
                    onsite = text.split("现场参会", 1)[1].split("线上参会", 1)[0]
                    assert team["b"].name in onsite
                    assert team["a"].name not in onsite
                assert "线上参会" in text
                assert "、".join([team["host"].name, team["a"].name]) in text
    finally:
        app.dependency_overrides.clear()
    assert meeting.draft == before and meeting.published_version == 0
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    assert version.snapshot["online_attendee_ids"] == draft.online_attendee_ids
    assert version.snapshot["online_attendee_names"] == [team["host"].name, team["a"].name]
    assert version.snapshot["attendee_ids"] == draft.attendee_ids
    assert version.snapshot["attendee_names"] == [team["a"].name, team["b"].name]
    assert attendance_rows(version.snapshot) == [
        ("现场参会", team["b"].name),
        ("线上参会", "、".join([team["host"].name, team["a"].name])),
    ]
    monkeypatch.setattr("app.services.business.today", lambda: date(2026, 8, 12))
    followup = continue_meeting(db, team["host"], meeting.id)
    assert followup.draft["online_attendee_ids"] == draft.online_attendee_ids
    team["a"].active = False
    db.flush()
    followup2 = continue_meeting(db, team["host"], meeting.id)
    assert followup2.draft["online_attendee_ids"] == [team["host"].id]


@pytest.mark.parametrize("online", [False, True])
@pytest.mark.parametrize("tasks_only", [False, True])
def test_online_attendance_exports_and_old_snapshots(online, tasks_only):
    snapshot = dict(title="参会方式测试", meeting_date="2026-11-12", items=[],
                    host_name="主持人员", attendee_names=["现场人员", "远程人员"])
    if online:
        snapshot["online_attendee_names"] = ["主持人员", "远程人员"]
    before = deepcopy(snapshot)
    book = openpyxl.load_workbook(BytesIO(xlsx_minutes(snapshot, tasks_only)))
    for sheet in book:
        row = next((r for r in sheet if r[0].value == "线上参会"), None)
        assert bool(row) == online
        if online:
            assert row[1].value == "主持人员、远程人员"
        onsite = next(r for r in sheet if r[0].value == ("现场参会" if online else "参会人员"))
        assert onsite[1].value == ("现场人员" if online else "现场人员、远程人员")
    pdf = "".join(p.extract_text() for p in PdfReader(BytesIO(pdf_minutes(snapshot))).pages)
    assert ("线上参会" in pdf) == online
    if online:
        assert "主持人员、远程人员" in pdf
        onsite_text = pdf.split("现场参会", 1)[1].split("线上参会", 1)[0]
        assert "现场人员" in onsite_text and "远程人员" not in onsite_text
    assert snapshot == before


def test_attendance_split_uses_identity_for_employees_with_the_same_name():
    snapshot = dict(attendee_ids=["a", "b", "c"], attendee_names=["张敏", "张敏", "李林"],
                    online_attendee_ids=["b"], online_attendee_names=["张敏"])
    assert attendance_rows(snapshot) == [("现场参会", "张敏、李林"), ("线上参会", "张敏")]


def test_all_online_preserves_online_order_and_leaves_no_duplicate_names_onsite():
    snapshot = dict(attendee_ids=["a", "b"], attendee_names=["赵小龙", "李林"],
                    online_attendee_ids=["b", "a"], online_attendee_names=["李林", "赵小龙"])
    assert attendance_rows(snapshot) == [("现场参会", ""), ("线上参会", "李林、赵小龙")]


def test_empty_and_legacy_attendance_keep_the_existing_single_row():
    assert attendance_rows({}) == [("参会人员", "")]
    assert attendance_rows({"attendee_names": ["李林", "赵小龙"]}) == [("参会人员", "李林、赵小龙")]
