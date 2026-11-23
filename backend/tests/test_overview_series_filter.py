# Pytest fixtures are intentionally imported for discovery and injected by name.
# ruff: noqa: F811
from datetime import date

from app.core.security import issue_token
from app.models import Meeting, MeetingSeries
from app.services.role_migration import rule
from tests.role_helpers import grant
from tests.test_task_overview_order import client, fetch, tasks  # noqa: F401


def series_meeting(db, team, series, day=2):
    meeting = Meeting(title=series.name, series_id=series.id, department_id=series.department_id,
                      meeting_date=date(2026, 11, day), host_id=team["host"].id)
    db.add(meeting)
    db.flush()
    return meeting


def choices(client, actor):
    return client.get("/api/task-overview/options", headers={"Authorization": "Bearer " + issue_token(actor)})


def test_series_includes_all_occurrences_and_legacy_tasks_with_matching_totals(client, db, team, tasks):
    series = MeetingSeries(name="管理层周例会", department_id=team["dep"].id)
    other = MeetingSeries(name="采购周会", department_id=team["other"].id)
    db.add_all([series, other])
    db.flush()
    first = series_meeting(db, team, series)
    second = series_meeting(db, team, series, 9)
    modern = tasks("modern", [team["a"]], "2026-11-03", first)
    modern.series_id = series.id
    tasks("legacy", [team["b"]], "2026-11-13", second)
    tasks("foreign", [team["a"]], meeting=series_meeting(db, team, other))
    tasks("direct", [team["a"]])
    response = fetch(client, team["host"], series_id=series.id, limit=1)
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == result["summary"]["total"] == 2
    next_page = fetch(client, team["host"], series_id=series.id, offset=1, limit=1).json()
    assert {result["items"][0]["id"], next_page["items"][0]["id"]} == {"modern", "legacy"}
    filtered = fetch(client, team["host"], series_id=series.id, person_id=team["b"].id,
                     start="2026-11-10", end="2026-11-20", q="legacy").json()
    assert filtered["total"] == filtered["summary"]["total"] == 1
    assert filtered["items"][0]["id"] == "legacy"
    assert fetch(client, team["host"], series_id=series.id, source="direct").json()["total"] == 0
    assert {item["id"] for item in choices(client, team["host"]).json()["series"]} == {series.id, other.id}


def test_series_choices_and_tasks_respect_scope_without_exposing_foreign_tasks(client, db, team, tasks):
    actor = team["colleague"]
    grant(db, actor, [rule("overview", ["view"])])
    own = MeetingSeries(name="本部门周会", department_id=team["dep"].id)
    hidden = MeetingSeries(name="无权查看的会议", department_id=team["other"].id)
    db.add_all([own, hidden])
    db.flush()
    tasks("our-helper", [team["outsider"]], meeting=series_meeting(db, team, own))
    foreign = series_meeting(db, team, hidden)
    tasks("hidden", [team["outsider"]], meeting=foreign)
    response = choices(client, actor)
    assert response.status_code == 200
    assert response.json()["series"] == [{"id": own.id, "name": own.name}]
    assert fetch(client, actor, series_id=hidden.id).status_code == 403
    assert fetch(client, actor, series_id="nonexistent").status_code == 403
    # A shared series is discoverable when one task is in scope, but this does not grant its other tasks.
    foreign.series_id = own.id
    db.flush()
    assert [row["id"] for row in fetch(client, actor, series_id=own.id).json()["items"]] == ["our-helper"]
    assert choices(client, team["a"]).status_code == 403


def test_empty_series_without_tasks_does_not_clutter_choices(client, db, team):
    db.add(MeetingSeries(name="只有空系列", department_id=team["dep"].id))
    db.flush()
    assert choices(client, team["host"]).json()["series"] == []
