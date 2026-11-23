from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import app
from app.models import Department, Meeting, MeetingAttendee, MeetingSeries, Membership, ReportDepartmentGrant, Task, TaskOwner
from tests.test_report_self_only import auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def series_ids(client, actor, endpoint):
    response = client.get(endpoint, headers=auth(actor))
    assert response.status_code == 200, response.text
    body = response.json()
    if endpoint == "/api/directory":
        return {row["id"] for row in body["series"]}
    return {row["key"].removeprefix("series:") for row in body["items"]}


@pytest.mark.parametrize("endpoint", ["/api/directory", "/api/meeting-series", "/api/meeting-series?view=current", "/api/meeting-series?q=周会"])
def test_unrelated_empty_series_hidden_from_staff_but_visible_to_admin(db, team, client, endpoint):
    foreign = MeetingSeries(name="财务周会", department_id=team["other"].id)
    legacy = MeetingSeries(name="未分配部门周会")
    db.add_all([foreign, legacy])
    db.flush()
    member_ids = series_ids(client, team["colleague"], endpoint)
    assert foreign.id not in member_ids and legacy.id not in member_ids
    admin_ids = series_ids(client, team["host"], endpoint)
    assert {foreign.id, legacy.id} <= admin_ids
    assert series_ids(client, team["colleague"], "/api/directory") == {team["series"].id}


def test_new_series_records_department_and_remains_visible_before_first_meeting(db, team, client):
    actor = team["colleague"]
    new_grant(db,actor,[rule("series",["create"],"self")])
    response = client.post("/api/series", headers=auth(actor), json={"name": "新建周会"})
    assert response.status_code == 200, response.text
    identifier = response.json()["id"]
    assert db.get(MeetingSeries, identifier).department_id == team["dep"].id
    assert identifier in series_ids(client, actor, "/api/meeting-series?view=current")
    assert identifier in series_ids(client, actor, "/api/directory")
    assert identifier not in series_ids(client, team["outsider"], "/api/directory")
    repeated = client.post("/api/series", headers=auth(actor), json={"name": "新建周会"})
    assert repeated.json()["id"] == identifier


def test_existing_name_lookup_does_not_return_an_unrelated_departments_series(db, team, client):
    foreign = MeetingSeries(name="相同名称周会", department_id=team["other"].id)
    db.add(foreign)
    db.flush()
    new_grant(db,team["colleague"],[rule("series",["create"],"self")])
    response = client.post("/api/series", headers=auth(team["colleague"]), json={"name": foreign.name})
    assert response.status_code == 200, response.text
    assert response.json()["id"] != foreign.id
    assert response.json()["department_id"] == team["dep"].id


@pytest.mark.parametrize("role", ["host", "recorder", "attendee", "owner", "verifier"])
def test_cross_department_related_meeting_keeps_its_series_visible(db, team, client, role):
    actor = team["colleague"]
    new_grant(db,actor,[rule("series",["create"],"self")])
    series = MeetingSeries(name="跨部门相关会议", department_id=None)
    db.add(series)
    db.flush()
    meeting = Meeting(title="相关会议", series_id=series.id, department_id=team["other"].id,
                      host_id=actor.id if role == "host" else team["outsider"].id,
                      recorder_id=actor.id if role == "recorder" else team["outsider"].id,
                      meeting_date=date(2026, 11, 2), draft={"items": []})
    db.add(meeting)
    db.flush()
    if role == "attendee":
        db.add(MeetingAttendee(meeting_id=meeting.id, user_id=actor.id))
        db.flush()
    if role in {"owner", "verifier"}:
        task = Task(code="RELATED-TASK", content="相关任务", department_id=team["other"].id,
                    source_meeting_id=meeting.id, source_item_id="related-row", series_id=series.id,
                    verifier_id=actor.id if role == "verifier" else team["outsider"].id)
        db.add(task)
        db.flush()
        if role == "owner":
            db.add(TaskOwner(task_id=task.id, user_id=actor.id))
            db.flush()
    assert series.id in series_ids(client, actor, "/api/directory")
    assert series.id in series_ids(client, actor, "/api/meeting-series?view=current")


def test_leader_department_grants_include_empty_child_series_but_report_grants_do_not(db, team, client):
    actor = team["colleague"]
    new_grant(db,actor,[rule("series",["create"],"self")])
    child = Department(name="财务子部门", parent_id=team["other"].id)
    db.add(child)
    db.flush()
    series = MeetingSeries(name="子部门空系列", department_id=child.id)
    db.add_all([series, ReportDepartmentGrant(user_id=actor.id, department_id=team["other"].id)])
    db.flush()
    assert series.id not in series_ids(client, actor, "/api/directory")
    access=new_grant(db,actor,[rule("series",["view"],"selected",[team["other"].id])])
    actor.manual_leader_department_ids = [team["other"].id]
    db.flush()
    assert series.id in series_ids(client, actor, "/api/directory")
    assert series.id in series_ids(client, actor, "/api/meeting-series?view=current")
    db.delete(access)
    actor.manual_leader_department_ids = []
    db.flush()
    assert series.id not in series_ids(client, actor, "/api/directory")


@pytest.mark.parametrize("membership", [True, False])
def test_creating_series_without_primary_department_requires_an_existing_membership(db, team, client, membership):
    actor = team["colleague"]
    new_grant(db,actor,[rule("series",["create"],"self")])
    actor.department_id = None
    if membership:
        db.add(Membership(user_id=actor.id, department_id=team["dep"].id))
    db.flush()
    response = client.post("/api/series", headers=auth(actor), json={"name": "待建系列"})
    assert response.status_code == (200 if membership else 403), response.text
    if membership:
        assert response.json()["department_id"] == team["dep"].id
