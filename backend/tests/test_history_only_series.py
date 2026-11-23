from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import app
from app.models import Meeting, MeetingSeries
from tests.test_report_self_only import auth
from tests.test_series_visibility import series_ids


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def historical_meeting(db, team, series, department, recorder):
    meeting = Meeting(
        title="历史周例会", series_id=series.id, department_id=department.id,
        host_id=team["host"].id, recorder_id=recorder.id,
        meeting_date=date(2026, 11, 9), historical=True, published_version=1,
        has_draft=False, draft={"history_backfill": True, "items": []},
    )
    db.add(meeting)
    db.flush()
    return meeting


@pytest.mark.parametrize("actor", ["host", "colleague"])
def test_history_only_series_shows_latest_published_import_in_workbench(db, team, client, actor):
    series = team["series"]
    series.kind = "department"
    meeting = historical_meeting(db, team, series, team["dep"], team["colleague"])
    headers = auth(team[actor])
    archive = client.get("/api/meeting-series", headers=headers, params={
        "view": "current", "q": series.name, "kind": "department", "department_id": team["dep"].id,
    }).json()
    assert [row["key"] for row in archive["items"]] == ["series:" + series.id]
    assert archive["meeting_total"] == 1
    params = {"series_key": "series:" + series.id, "view": "current"}
    current = client.get("/api/meetings", headers=headers, params=params).json()
    assert [row["id"] for row in current["items"]] == [meeting.id]
    assert current["items"][0]["historical"] is True
    params["view"] = "history"
    history = client.get("/api/meetings", headers=headers, params=params).json()
    assert history["items"] == []


@pytest.mark.parametrize("filters", [
    {"q": "不匹配"}, {"kind": "special"}, {"department_id": "other"},
    {"start": "2026-11-10"}, {"end": "2026-11-08"},
    {"publication": "draft"}, {"status": "已完成"},
])
def test_current_history_entry_does_not_bypass_archive_filters(db, team, client, filters):
    historical_meeting(db, team, team["series"], team["dep"], team["colleague"])
    if filters.get("department_id") == "other":
        filters = {"department_id": team["other"].id}
    response = client.get("/api/meeting-series", headers=auth(team["colleague"]),
                          params={"view": "current", **filters})
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_history_entry_retains_department_and_personal_access(db, team, client):
    foreign = MeetingSeries(name="外部门历史系列", department_id=team["other"].id)
    db.add(foreign)
    db.flush()
    meeting = historical_meeting(db, team, foreign, team["other"], team["outsider"])
    assert foreign.id not in series_ids(client, team["colleague"], "/api/meeting-series?view=current")
    assert foreign.id in series_ids(client, team["host"], "/api/meeting-series?view=current")
    meeting.recorder_id = team["colleague"].id
    db.flush()
    assert foreign.id in series_ids(client, team["colleague"], "/api/meeting-series?view=current")


def test_duplicate_series_response_identifies_existing_department_series(db, team, client):
    series = team["series"]
    headers = auth(team["host"])
    duplicate = client.post("/api/series", headers=headers, json={"name": series.name}).json()
    assert duplicate["id"] == series.id and duplicate["existing"] is True
    created = client.post("/api/series", headers=headers, json={
        "name": series.name, "department_id": team["other"].id,
    }).json()
    assert created["id"] != series.id and created["existing"] is False
