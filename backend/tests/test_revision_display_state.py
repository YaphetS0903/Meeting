from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import AuditLog, Meeting, MeetingVersion, OutboxEvent
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting
from app.services.meeting_views import revision_in_progress


def issued(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    revision(db, team["host"], meeting.id, "准备修订")
    return meeting


def test_viewing_empty_revision_changes_neither_status_nor_data(db, team, draft_factory):
    meeting = issued(db, team, draft_factory)
    before = (deepcopy(meeting.draft), meeting.draft_version, meeting.has_draft,
              {model.__tablename__: db.scalar(select(func.count()).select_from(model))
               for model in (Meeting, MeetingVersion, AuditLog, OutboxEvent)})
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            for _ in range(3):
                detail = client.get(f"/api/meetings/{meeting.id}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert detail.json()["has_draft"] is True  # Editing remains available.
                assert detail.json()["is_revising"] is False
                listing = client.get("/api/meetings?view=current", headers=headers).json()
                assert listing["items"][0]["is_revising"] is False
    finally:
        app.dependency_overrides.clear()
    assert (meeting.draft, meeting.draft_version, meeting.has_draft,
            {model.__tablename__: db.scalar(select(func.count()).select_from(model))
             for model in (Meeting, MeetingVersion, AuditLog, OutboxEvent)}) == before


def test_revision_filters_count_content_changes_before_pagination_and_series_counts(db, team, draft_factory):
    unchanged = issued(db, team, draft_factory)
    # A save strips generated display names, but does not change the document.
    save_meeting(db, team["host"], unchanged.id, MeetingDraft.model_validate(unchanged.draft), unchanged.draft_version)
    changed = issued(db, team, draft_factory)
    draft = MeetingDraft.model_validate(changed.draft)
    draft.items[0].notes = "尚未发布的新备注"
    save_meeting(db, team["host"], changed.id, draft, changed.draft_version)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            for state, expected in (("published", unchanged), ("revising", changed)):
                response = client.get("/api/meetings", params={"publication": state, "limit": 1}, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()["total"] == 1
                assert response.json()["items"][0]["id"] == expected.id
                assert response.json()["items"][0]["is_revising"] is (state == "revising")
                page_two = client.get("/api/meetings", params={"publication": state, "limit": 1, "offset": 1}, headers=headers).json()
                assert page_two["total"] == 1 and page_two["items"] == []
                groups = client.get("/api/meeting-series", params={"publication": state}, headers=headers).json()
                assert groups["meeting_total"] == 1
            # Restoring the published content clears the pending status without deleting the draft.
            draft.items[0].notes = ""
            save_meeting(db, team["host"], changed.id, draft, changed.draft_version)
            assert client.get("/api/meetings?publication=revising", headers=headers).json()["total"] == 0
            assert changed.has_draft is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("field,value", [("notes", "新备注"), ("content", "新工作要求"), ("status", "已完成")])
def test_substantive_task_changes_are_revisions(db, team, draft_factory, field, value):
    meeting = issued(db, team, draft_factory)
    snapshot = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id)).snapshot
    draft = MeetingDraft.model_validate(meeting.draft)
    setattr(draft.items[0], field, value)
    meeting.draft = draft.model_dump(mode="json")
    assert revision_in_progress(meeting, snapshot) is True
