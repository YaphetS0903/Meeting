from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import app
from app.models import MeetingSeries, ReportDepartmentGrant
from app.services.report_access import report_directory
from app.services.reporting import report
from tests.test_department_people_report import issue
from tests.test_report_quality import candidate, source
from tests.test_report_self_only import PARAMS, auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def series(db, name, department):
    item = MeetingSeries(name=name, department_id=department.id)
    db.add(item)
    db.flush()
    return item


def issue_in(db, team, topic, **kwargs):
    meeting = issue(db, team, **kwargs)
    meeting.series_id = topic.id
    db.flush()
    return meeting


def test_topics_include_meetings_and_cross_department_work_independent_of_period(db, team):
    own = team["series"]
    foreign = series(db, "跨部门协作会", team["other"])
    issue_in(db, team, own)
    issue_in(db, team, foreign, department=team["other"], owners=[team["a"].id])
    data = report_directory(db, team["host"])
    topics = {row["key"]: row for row in data["topics"]}
    assert set(topics[f"series:{own.id}"]["department_ids"]) == {team["dep"].id}
    assert set(topics[f"series:{foreign.id}"]["department_ids"]) == {team["dep"].id, team["other"].id}
    assert report(db, team["host"], date(2026, 11, 1), date(2026, 11, 30),
                  series_key=f"series:{own.id}")["summary"]["items"] == 0
    assert f"series:{own.id}" in {row["key"] for row in report_directory(db, team["a"])["topics"]}


@pytest.mark.parametrize("endpoint", ["/api/reports", "/api/reports/export"])
@pytest.mark.parametrize("selected", ["first", "second", "unassigned"])
def test_topic_filter_matches_aggregates_details_and_excel(db, team, client, endpoint, selected):
    first, second = team["series"], series(db, "第二个主题", team["dep"])
    one = issue_in(db, team, first, owners=[team["a"].id])
    two = issue_in(db, team, second, owners=[team["a"].id], status="未完成")
    three = issue(db, team, owners=[team["a"].id])
    key, expected = {"first": (f"series:{first.id}", one), "second": (f"series:{second.id}", two),
                     "unassigned": ("unassigned", three)}[selected]
    response = client.get(endpoint, headers=auth(team["a"]), params={
        **PARAMS, "group_by": "department_people", "series_key": key,
    })
    assert response.status_code == 200
    if endpoint.endswith("export"):
        book = openpyxl.load_workbook(BytesIO(response.content))
        assert book["考核明细"].max_row == 2
        assert book["考核明细"]["I2"].value == ("未完成" if selected == "second" else "已完成")
    else:
        result = response.json()
        assert result["summary"]["items"] == 1
        assert {row["meeting_id"] for row in result["details"]} == {expected.id}


def test_report_delegate_topics_do_not_grant_meeting_access_or_disclose_other_topics(db, team, client):
    allowed = series(db, "授权人员相关主题", team["other"])
    excluded = team["series"]
    foreign = issue_in(db, team, allowed, department=team["other"], owners=[team["outsider"].id])
    issue_in(db, team, excluded)
    actor = team["colleague"]
    db.add(report_grant(db,db.get(User,actor.id),db.get(Department,team["other"].id)))
    db.flush()
    topics = report_directory(db, actor)["topics"]
    assert {row["key"] for row in topics} == {f"series:{allowed.id}"}
    assert client.get(f"/api/meetings/{foreign.id}", headers=auth(actor)).status_code == 403
    for endpoint in ["/api/reports", "/api/reports/export"]:
        response = client.get(endpoint, headers=auth(actor), params={
            **PARAMS, "person_id": team["a"].id, "series_key": f"series:{allowed.id}",
        })
        assert response.status_code == 403


@pytest.mark.parametrize("endpoint", ["/api/reports", "/api/reports/export"])
def test_invalid_topic_keys_are_rejected_and_cannot_expand_self_scope(db, team, client, endpoint):
    issue_in(db, team, team["series"], owners=[team["b"].id])
    assert client.get(endpoint, params={**PARAMS, "series_key": "invalid"},
                      headers=auth(team["a"])).status_code == 422
    response = client.get(endpoint, params={**PARAMS, "series_key": f"series:{team['series'].id}"},
                          headers=auth(team["a"]))
    assert response.status_code == 200
    if endpoint.endswith("export"):
        assert openpyxl.load_workbook(BytesIO(response.content))["考核明细"].max_row == 1
    else:
        assert response.json()["summary"]["items"] == 0


def test_pending_quality_only_counts_candidates_for_the_selected_topic(db, team):
    imported = source(db, team)
    for identifier in [team["series"].id, "another-series", None]:
        candidate(db, imported, {"series_id": identifier, "department_id": team["dep"].id,
                                  "meeting_date": "2026-08-10"})
    result = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31),
                    series_key=f"series:{team['series'].id}")
    assert result["pending_meetings"] == 1 and result["pending_file_issues"] == 0
