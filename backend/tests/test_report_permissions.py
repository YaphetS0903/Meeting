from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import app
from app.models import AuditLog, Department, Membership, ReportDepartmentGrant, User
from app.services.report_access import report_access, report_directory
from app.services.reporting import report
from tests.test_department_people_report import issue
from tests.test_report_quality import candidate, source
from tests.test_report_self_only import GROUPS, PARAMS, auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def result(db, actor, **kwargs):
    return report(db, actor, date(2026, 8, 1), date(2026, 8, 31), "department_people", **kwargs)


def grant(db, person, *departments):
    roles=[report_grant(db,person,d) for d in departments]
    db.flush()
    return roles[-1]


def test_explicit_department_leader_can_report_on_granted_department_and_descendants(db, team):
    meeting = issue(db, team)
    actor = team["colleague"]
    actor.manual_leader_department_ids = [team["dep"].id]
    grant(db,actor,team["dep"])
    child = Department(name="开发组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    member = User(name="下属员工", department_id=child.id)
    db.add(member)
    db.flush()
    issue(db, team, team["other"], [member.id])
    issue(db, team, team["other"], [team["outsider"].id])
    db.add(Membership(user_id=team["outsider"].id, department_id=team["dep"].id))
    db.flush()
    data = result(db, actor)
    assert {r["user_id"] for r in data["details"]} == {team["a"].id, team["b"].id, member.id}
    assert member.id in {r["id"] for r in data["groups"]}
    assert team["outsider"].id not in {r["id"] for r in data["groups"]}
    assert report_access(db, actor).mode == "departments"


def test_host_of_other_department_does_not_gain_that_departments_report(db, team):
    meeting = issue(db, team, team["other"], [team["outsider"].id])
    meeting.host_id = team["colleague"].id
    db.flush()
    assert report_access(db, team["colleague"]).mode == "self"
    assert report_access(db, team["colleague"]).departments == set()
    assert team["outsider"].id not in {r["id"] for r in result(db, team["colleague"])["groups"]}


@pytest.mark.parametrize("group_by", GROUPS)
@pytest.mark.parametrize("endpoint", ["/api/reports", "/api/reports/export"])
def test_scoped_report_and_exports_enforce_people_across_all_groupings(db, team, client, group_by, endpoint):
    issue(db, team)
    # These meetings are not visible to the delegate through the meeting endpoints.
    foreign_meeting = issue(db, team, team["other"], [team["outsider"].id])
    actor = team["colleague"]
    grant(db, actor, team["other"])
    params = {**PARAMS, "group_by": group_by}
    response = client.get(endpoint, params=params, headers=auth(actor))
    assert response.status_code == 200
    if endpoint.endswith("export"):
        book = openpyxl.load_workbook(BytesIO(response.content))
        assert book["考核明细"].max_row == 2
        assert book["考核明细"]["D2"].value == team["outsider"].name
    else:
        data = response.json()
        assert data["summary"]["items"] == 1
        assert {r["user_id"] for r in data["details"]} == {team["outsider"].id}
    assert client.get(f"/api/meetings/{foreign_meeting.id}", headers=auth(actor)).status_code == 403
    assert client.get(endpoint, params={**params, "department_id": team["dep"].id},
                      headers=auth(actor)).status_code == 403
    assert client.get(endpoint, params={**params, "person_id": team["a"].id},
                      headers=auth(actor)).status_code == 403


def test_delegate_directory_does_not_expose_unrelated_people_or_contact_details(db, team):
    grant(db, team["colleague"], team["other"])
    data = report_directory(db, team["colleague"])
    assert data["mode"] == "departments"
    assert {p["id"] for p in data["users"]} == {team["outsider"].id,team["colleague"].id}
    assert set(data["users"][0]) == {"id", "name", "active", "department_id"}
    assert data["department_ids"] == [team["other"].id]
    assert report_directory(db, team["a"])["mode"] == "self"


def test_multiple_explicit_grants_union_and_are_not_expanded_by_position_or_transfer(db, team):
    issue(db, team)
    team["colleague"].position = "部长"
    team["colleague"].manual_leader_department_ids = [team["dep"].id]
    dep_grant=grant(db,team["colleague"],team["dep"])
    grant(db, team["colleague"], team["other"])
    assert report_access(db, team["colleague"]).departments == {team["dep"].id, team["other"].id}
    team["colleague"].department_id = team["other"].id
    db.flush()
    assert report_access(db, team["colleague"]).departments == {team["dep"].id, team["other"].id}
    team["colleague"].manual_leader_department_ids = []
    db.delete(dep_grant)
    db.flush()
    assert report_access(db, team["colleague"]).departments == {team["other"].id}
    assert result(db, team["colleague"])["details"] == []


def test_scoped_pending_quality_does_not_count_foreign_candidates_or_file_failures(db, team):
    imported = source(db, team)
    candidate(db, imported, {"department_id": team["dep"].id, "meeting_date": "2026-08-10",
                             "items": [{"section": "review", "owner_ids": [team["a"].id]}]})
    candidate(db, imported, {"department_id": team["other"].id, "meeting_date": "2026-08-10",
                             "items": [{"section": "review", "owner_ids": [team["outsider"].id]}]})
    candidate(db, imported, {"meeting_date": "2026-08-10"})
    grant(db, team["colleague"], team["dep"])
    data = result(db, team["colleague"])
    assert data["pending_meetings"] == 1
    assert data["pending_file_issues"] == 0 and not data["quality_note"]








def test_department_grant_does_not_allow_parent_or_sibling_department(db, team, client):
    child = Department(name="开发组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    grant(db, team["colleague"], child)
    response = client.get("/api/reports/access", headers=auth(team["colleague"]))
    assert response.json()["department_ids"] == [child.id]
    assert {d["id"] for d in response.json()["departments"]} == {team["dep"].id, child.id}
    assert client.get("/api/reports", headers=auth(team["colleague"]),
                      params={**PARAMS, "department_id": team["dep"].id}).status_code == 403
