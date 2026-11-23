from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, EmployeeRoleGrant, Meeting, MeetingSeries, MeetingVersion, OutboxEvent, Task
from app.schemas import PublishRequest
from app.services.business import publish
from tests.test_meeting_title import client as client
from tests.test_meeting_title import recorded
from tests.test_report_self_only import auth


def rename(client, actor, series, **changes):
    return client.put(f"/api/meeting-series/{series.id}/name", headers=auth(actor), json={
        "name": "新的系列名称", "expected_name": series.name, **changes,
    })


def test_options_are_series_not_meetings_including_empty_and_history(db, team, draft_factory, client):
    for index in range(15):
        meeting = recorded(db, team, draft_factory)
        meeting.historical = index < 7
    db.add_all([MeetingSeries(name=f"空系列{index}", department_id=team["dep"].id) for index in range(8)])
    db.flush()
    result = client.get("/api/meeting-series-name-options", headers=auth(team["host"])).json()
    assert result["total"] == 9 == len(result["items"])
    assert len({row["id"] for row in result["items"]}) == 9
    assert all(row["can_rename"] for row in result["items"])
    assert sum(row["name"] == team["series"].name for row in result["items"]) == 1


@pytest.mark.parametrize("actor", ["host", "colleague"])
def test_rename_changes_only_series_name_and_audits(db, team, draft_factory, client, actor):
    meeting = recorded(db, team, draft_factory)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    before = (meeting.title, deepcopy(meeting.draft), meeting.draft_version, meeting.published_version)
    snapshot = deepcopy(db.scalar(select(MeetingVersion)).snapshot)
    counts = {model: db.scalar(select(func.count()).select_from(model)) for model in (Meeting, Task, MeetingVersion, OutboxEvent)}
    response = rename(client, team[actor], team["series"], name="  采购部周会  ")
    assert response.status_code == 200, response.text
    assert response.json() == {"id": team["series"].id, "name": "采购部周会"}
    assert before == (meeting.title, meeting.draft, meeting.draft_version, meeting.published_version)
    assert snapshot == db.scalar(select(MeetingVersion)).snapshot
    assert counts == {model: db.scalar(select(func.count()).select_from(model)) for model in counts}
    log = db.scalar(select(AuditLog).where(AuditLog.action == "meeting_series.name.rename"))
    assert log.before == {"name": "技术部周例会"} and log.after == {"name": "采购部周会"}
    listing = client.get("/api/meeting-series?view=current", headers=auth(team[actor])).json()
    assert listing["items"][0]["name"] == "采购部周会"
    directory = client.get("/api/directory", headers=auth(team[actor])).json()
    assert next(row["name"] for row in directory["series"] if row["id"] == team["series"].id) == "采购部周会"


def test_recorder_can_rename_cross_department_history_but_not_unrelated_series(db, team, draft_factory, client):
    meeting = recorded(db, team, draft_factory)
    meeting.department_id = team["other"].id
    meeting.historical = True
    team["series"].department_id = team["other"].id
    other = MeetingSeries(name="其他部门系列", department_id=team["other"].id)
    db.add(other)
    db.flush()
    options = client.get("/api/meeting-series-name-options", headers=auth(team["colleague"])).json()
    assert options["items"] == [{"id": team["series"].id, "name": team["series"].name, "can_rename": True}]
    assert rename(client, team["colleague"], team["series"]).status_code == 200
    assert rename(client, team["colleague"], other).status_code == 403


@pytest.mark.parametrize("capacity", ["ordinary", "host", "department_role", "disabled"])
def test_non_recorder_roles_cannot_rename_series(db, team, draft_factory, client, capacity):
    meeting = recorded(db, team, draft_factory)
    actor = team["a"]
    if capacity == "host":
        meeting.host_id = actor.id
    elif capacity == "department_role":
        db.add(EmployeeRoleGrant(user_id=actor.id, role="department_meetings"))
    elif capacity == "disabled":
        meeting.recorder_id = actor.id
        actor.active = False
    db.flush()
    name = team["series"].name
    response = rename(client, actor, team["series"])
    assert response.status_code in (401, 403)
    assert team["series"].name == name
    if capacity != "disabled":
        options = client.get("/api/meeting-series-name-options", headers=auth(actor)).json()
        assert options["items"][0]["can_rename"] is False


def test_admin_can_rename_empty_series_and_stale_save_is_rejected(db, team, client):
    series = team["series"]
    original = series.name
    assert rename(client, team["host"], series).status_code == 200
    assert rename(client, team["host"], series, expected_name=original).status_code == 409
    assert series.name == "新的系列名称"
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_duplicate_name_is_conflict_and_no_change(db, team, client):
    existing = MeetingSeries(name="已有系列", department_id=team["dep"].id)
    db.add(existing)
    db.flush()
    before = team["series"].name
    assert rename(client, team["host"], team["series"], name=existing.name).status_code == 409
    assert team["series"].name == before
    existing.department_id = team["other"].id
    db.flush()
    assert rename(client, team["host"], team["series"], name=existing.name).status_code == 200


@pytest.mark.parametrize("name", ["", "  ", "a" * 251])
def test_invalid_name_preserves_series(db, team, client, name):
    original = team["series"].name
    assert rename(client, team["host"], team["series"], name=name).status_code == 422
    assert team["series"].name == original


def test_noop_and_missing_series(db, team, client):
    assert rename(client, team["host"], team["series"], name=team["series"].name).status_code == 200
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert client.put("/api/meeting-series/missing/name", headers=auth(team["host"]),
                      json={"name": "名称", "expected_name": "旧名称"}).status_code == 404
