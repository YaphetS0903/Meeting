from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Task
from app.schemas import PublishRequest
from app.services.business import create_meeting, publish


def test_meeting_approval_and_delete_rights(db, team, draft_factory):
    host = team["host"]
    host.is_admin = False
    from app.models import MeetingAttendee
    draft=draft_factory();draft.attendee_ids=[team["colleague"].id]
    meeting = create_meeting(db, host, draft)
    publish(db, host, meeting.id, PublishRequest(expected_version=1, notify=False))
    db.flush()
    task_id = db.scalar(select(Task.id).where(Task.source_meeting_id == meeting.id))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            def headers(user):
                return {"Authorization": "Bearer " + issue_token(user)}

            attendee = headers(team["colleague"])
            chair = headers(host)
            detail = client.get(f"/api/meetings/{meeting.id}", headers=attendee)
            assert detail.status_code == 200, detail.text
            assert detail.json()["can_edit"] is False
            assert detail.json()["can_continue"] is False
            assert client.get(f"/api/tasks/{task_id}", headers=attendee).json()["can_edit"] is False
            assert client.get("/api/meeting-series?scope=all", headers=chair).status_code == 200
            request = client.post(f"/api/meetings/{meeting.id}/edit-requests", headers=attendee)
            assert request.status_code == 200, request.text
            request_id = request.json()["id"]
            assert client.get("/api/meeting-series?scope=all", headers=chair).json()["items"][0][
                "pending_edit_count"
            ] == 1
            assert client.post(f"/api/meetings/{meeting.id}/continue", headers=attendee).status_code == 403
            assert client.post(
                f"/api/meetings/{meeting.id}/edit-requests/{request_id}/decision",
                json={"decision": "approved"}, headers=attendee,
            ).status_code == 403
            approved = client.post(
                f"/api/meetings/{meeting.id}/edit-requests/{request_id}/decision",
                json={"decision": "approved"}, headers=chair,
            )
            assert approved.status_code == 200, approved.text
            assert client.get(f"/api/meetings/{meeting.id}", headers=attendee).json()["can_edit"] is True
            assert client.get(f"/api/tasks/{task_id}", headers=attendee).json()["can_edit"] is True
            assert client.get(f"/api/meetings/{meeting.id}/deletion-impact", headers=attendee).status_code == 403
            assert client.delete(f"/api/meetings/{meeting.id}", headers=attendee).status_code == 403
            assert client.get(f"/api/meetings/{meeting.id}/deletion-impact", headers=chair).status_code == 403
            followup = client.post(f"/api/meetings/{meeting.id}/continue", headers=attendee)
            assert followup.status_code == 200, followup.text
            assert followup.json()["host_id"] == host.id
            assert followup.json()["recorder_id"] == team["colleague"].id
    finally:
        app.dependency_overrides.clear()
