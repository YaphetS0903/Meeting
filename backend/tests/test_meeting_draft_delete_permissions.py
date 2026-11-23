from app.models import BusinessRoleAssignment
from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
"""Explicit meeting roles may delete department drafts without deleting published meetings."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import app
from app.models import AuditLog, Department, EmployeeRoleGrant, Meeting, MeetingEditRequest, MeetingSeries
from app.schemas import PublishRequest
from app.services.access import can_delete_meeting
from app.services.business import create_meeting, publish, revision
from app.services.employee_roles import DISPATCH_ROLE, MEETING_ROLE, REPORT_ROLE
from tests.test_report_self_only import auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def grant(db, actor, role=MEETING_ROLE):
    db.add(new_grant(db,db.get(User,actor.id),DEFINITIONS[role]))
    db.flush()


def draft_in(db, team, draft_factory, department=None, recorder=None):
    draft = draft_factory()
    if department is not None:
        series = MeetingSeries(name=department.name + "例会", department_id=department.id)
        db.add(series)
        db.flush()
        draft.department_id, draft.series_id = department.id, series.id
    if recorder is not None:
        draft.recorder_id = recorder.id
    return create_meeting(db, team["host"], draft)


@pytest.mark.parametrize("descendant", [False, True])
def test_role_deletes_department_draft_with_consistent_list_detail_save_and_audit(
    db, team, client, draft_factory, descendant,
):
    actor = team["colleague"]
    grant(db, actor)
    department = None
    if descendant:
        department = Department(name="下属部门", parent_id=team["dep"].id)
        db.add(department)
        db.flush()
    meeting = draft_in(db, team, draft_factory, department)
    identifier = meeting.id
    headers = auth(actor)
    listing = client.get("/api/meetings", headers=headers).json()["items"]
    assert next(row for row in listing if row["id"] == identifier)["can_delete"]
    detail = client.get(f"/api/meetings/{identifier}", headers=headers)
    assert detail.status_code == 200 and detail.json()["can_delete"]
    saved = client.put(f"/api/meetings/{identifier}", headers=headers, json={
        "draft": detail.json()["draft"], "expected_version": meeting.draft_version,
    })
    assert saved.status_code == 200 and saved.json()["can_delete"]
    impact = client.get(f"/api/meetings/{identifier}/deletion-impact", headers=headers)
    assert impact.status_code == 200 and impact.json()["can_delete"]
    assert not impact.json()["published"]
    stale = client.request("DELETE", f"/api/meetings/{identifier}", headers=headers, json={
        "expected_draft_version": saved.json()["draft_version"] - 1,
    })
    assert stale.status_code == 409 and db.get(Meeting, identifier)
    deleted = client.request("DELETE", f"/api/meetings/{identifier}", headers=headers, json={
        "expected_draft_version": saved.json()["draft_version"],
    })
    assert deleted.status_code == 200 and deleted.json()["deleted"]
    assert db.get(Meeting, identifier) is None
    assert identifier not in {
        row["id"] for row in client.get("/api/meetings", headers=headers).json()["items"]
    }
    log = db.scalar(select(AuditLog).where(AuditLog.action == "meeting.delete"))
    assert log.actor_id == actor.id and log.reason == "会议任务单管理员删除部门草稿"


def test_new_draft_response_returns_role_deletion_permission(db, team, client, draft_factory):
    actor = team["colleague"]
    grant(db, actor)
    draft = draft_factory()
    draft.recorder_id = actor.id
    response = client.post("/api/meetings", headers=auth(actor), json=draft.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json()["can_delete"] and response.json()["host_id"] != actor.id


@pytest.mark.parametrize("revision_draft", [False, True])
def test_role_never_deletes_published_meeting_even_with_revision_draft(
    db, team, client, draft_factory, revision_draft,
):
    actor = team["colleague"]
    grant(db, actor)
    meeting = draft_in(db, team, draft_factory)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    if revision_draft:
        revision(db, actor, meeting.id, "核对任务")
        assert meeting.has_draft
    headers = auth(actor)
    assert not client.get(f"/api/meetings/{meeting.id}", headers=headers).json()["can_delete"]
    listing = client.get("/api/meetings", headers=headers).json()["items"]
    assert not next(row for row in listing if row["id"] == meeting.id)["can_delete"]
    for method, suffix in [("GET", "/deletion-impact"), ("DELETE", "")]:
        response = client.request(method, f"/api/meetings/{meeting.id}{suffix}", headers=headers)
        assert response.status_code == 403
    assert db.get(Meeting, meeting.id)


@pytest.mark.parametrize("relationship", ["ordinary", "recorder", "approved_editor", "legacy_leader",
                                           REPORT_ROLE, DISPATCH_ROLE])
def test_other_relationships_and_roles_do_not_grant_draft_deletion(
    db, team, client, draft_factory, relationship,
):
    actor = team["colleague"]
    meeting = draft_in(db, team, draft_factory, recorder=actor if relationship == "recorder" else None)
    if relationship in {REPORT_ROLE, DISPATCH_ROLE}:
        grant(db, actor, relationship)
    if relationship == "legacy_leader":
        actor.manual_leader_department_ids = [team["dep"].id]
    if relationship == "approved_editor":
        db.add(MeetingEditRequest(
            meeting_id=meeting.id, applicant_id=actor.id, host_id=meeting.host_id,
            permission_generation=meeting.permission_generation, state="approved",
        ))
    db.flush()
    if relationship=="recorder":
        assert can_delete_meeting(db,actor,meeting)
        assert client.get(f"/api/meetings/{meeting.id}/deletion-impact",headers=auth(actor)).status_code==200
        return
    assert not can_delete_meeting(db,actor,meeting)
    response=client.get(f"/api/meetings/{meeting.id}",headers=auth(actor))
    assert response.status_code==403 or not response.json()["can_delete"]
    assert client.get(f"/api/meetings/{meeting.id}/deletion-impact", headers=auth(actor)).status_code == 403
    assert client.delete(f"/api/meetings/{meeting.id}", headers=auth(actor)).status_code == 403


def test_scope_and_revocation_rechecked_at_delete_time(db, team, client, draft_factory):
    actor = team["colleague"]
    grant(db, actor)
    own = draft_in(db, team, draft_factory)
    foreign = draft_in(db, team, draft_factory, team["other"])
    assert not can_delete_meeting(db, actor, foreign)
    assert client.delete(f"/api/meetings/{foreign.id}", headers=auth(actor)).status_code == 403
    assert client.get(f"/api/meetings/{own.id}/deletion-impact", headers=auth(actor)).status_code == 200
    actor.department_id = team["other"].id
    db.flush()
    assert client.delete(f"/api/meetings/{own.id}", headers=auth(actor)).status_code == 403
    assert can_delete_meeting(db, actor, foreign)
    actor.department_id = team["dep"].id
    db.delete(db.scalar(select(BusinessRoleAssignment).where(BusinessRoleAssignment.user_id == actor.id)))
    db.flush()
    assert client.delete(f"/api/meetings/{own.id}", headers=auth(actor)).status_code == 403
    grant(db, actor)
    actor.active = False
    db.flush()
    assert not can_delete_meeting(db, actor, own)
    assert client.delete(f"/api/meetings/{own.id}", headers=auth(actor)).status_code == 401


def test_host_and_admin_retain_published_deletion_but_role_cannot_delete_series(
    db, team, client, draft_factory,
):
    actor = team["colleague"]
    grant(db, actor)
    meeting = draft_in(db, team, draft_factory)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    meeting.host_id = team["outsider"].id
    db.flush()
    assert can_delete_meeting(db, team["host"], meeting)
    assert not can_delete_meeting(db, team["outsider"], meeting)
    assert client.get(f"/api/meetings/{meeting.id}/deletion-impact", headers=auth(team["outsider"])).status_code == 403
    assert client.delete(f"/api/meetings/{meeting.id}", headers=auth(team["outsider"])).status_code == 403
    assert client.get(f"/api/meeting-series/{team['series'].id}/deletion-impact", headers=auth(actor)).status_code == 403
    assert client.delete(f"/api/meeting-series/{team['series'].id}", headers=auth(actor)).status_code == 403
