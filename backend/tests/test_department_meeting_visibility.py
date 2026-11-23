from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import app
from app.models import Department, Meeting, MeetingSeries, ReportDepartmentGrant
from app.services.access import can_edit_meeting, meeting_department_scope, visible_meetings
from tests.test_report_self_only import auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def nested_meetings(db, team):
    child = Department(name="开发组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    series = MeetingSeries(name="开发组周会", department_id=child.id)
    foreign_series = MeetingSeries(name="财务例会", department_id=team["other"].id)
    db.add_all([series, foreign_series])
    db.flush()
    own = Meeting(title="开发组工作会议", department_id=child.id, series_id=series.id,
                  host_id=team["host"].id, recorder_id=team["host"].id, meeting_date=date(2026, 8, 10),
                  draft={"items": []})
    foreign = Meeting(title="财务会议", department_id=team["other"].id, series_id=foreign_series.id,
                      host_id=team["outsider"].id, recorder_id=team["outsider"].id,
                      meeting_date=date(2026, 8, 10), draft={"items": []})
    db.add_all([own, foreign])
    db.flush()
    return child, series, own, foreign


def test_explicit_view_role_reads_child_series_without_editing(db,team,client):
    child, series, own, foreign = nested_meetings(db,team)
    actor=team["colleague"]
    new_grant(db,actor,[rule("meetings",["view"])])
    assert meeting_department_scope(db, actor) == {team["dep"].id, child.id}
    response = client.get("/api/meeting-series?view=current", headers=auth(actor))
    assert response.status_code == 200
    assert f"series:{series.id}" in {row["key"] for row in response.json()["items"]}
    listing = client.get("/api/meetings", params={"series_key": f"series:{series.id}"}, headers=auth(actor))
    assert [row["id"] for row in listing.json()["items"]] == [own.id]
    assert client.get(f"/api/meetings/{own.id}", headers=auth(actor)).status_code == 200
    assert client.get(f"/api/meetings/{foreign.id}", headers=auth(actor)).status_code == 403
    assert not can_edit_meeting(db, actor, own)
    assert client.post(f"/api/meetings/{own.id}/revision", json={"reason": "尝试修改"},
                       headers=auth(actor)).status_code == 403
    directory = client.get("/api/directory", headers=auth(actor)).json()
    assert child.id in {d["id"] for d in directory["departments"]}


def test_ordinary_staff_and_report_grants_do_not_gain_department_meeting_scope(db, team):
    child, _, own, foreign = nested_meetings(db, team)
    actor = team["colleague"]
    db.add(ReportDepartmentGrant(user_id=actor.id, department_id=team["other"].id))
    db.flush()
    assert not meeting_department_scope(db, actor)
    assert db.scalar(visible_meetings(db, actor).where(Meeting.id == own.id)) is None
    assert db.scalar(visible_meetings(db, actor).where(Meeting.id == foreign.id)) is None
    actor.leader_department_ids = [team["dep"].id]
    db.flush()
    assert not meeting_department_scope(db, actor)
    actor.active = False
    assert not meeting_department_scope(db, actor)
