from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from tests.test_department_people_report import issue, result
from tests.test_report_quality import candidate, source

GROUPS = ["meeting_department", "person_department", "person", "kind", "department_people"]
PARAMS = {"start": "2026-08-01", "end": "2026-08-31"}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def auth(user):
    return {"Authorization": "Bearer " + issue_token(user)}


@pytest.mark.parametrize("group_by", GROUPS)
def test_member_only_gets_own_aggregates_details_and_export(db, team, client, group_by):
    # A shared meeting contains both the requesting employee and other owners.
    issue(db, team)
    issue(db, team, owners=[team["b"].id], status="未完成")
    params = {**PARAMS, "group_by": group_by}
    response = client.get("/api/reports", params=params, headers=auth(team["a"]))
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["items"] == data["summary"]["person_count"] == 1
    assert data["summary"]["counts"]["已完成"] == 1
    assert data["summary"]["counts"]["未完成"] == 0
    assert {r["user_id"] for r in data["details"]} == {team["a"].id}
    if group_by in {"person", "department_people"}:
        assert {r["id"] for r in data["groups"]} == {team["a"].id}
    excel = client.get("/api/reports/export", params=params, headers=auth(team["a"]))
    assert excel.status_code == 200
    book = openpyxl.load_workbook(BytesIO(excel.content))
    assert book["考核明细"].max_row == 2
    assert book["考核明细"]["D2"].value == team["a"].name
    assert team["b"].name not in {c.value for row in book["考核汇总"] for c in row}


@pytest.mark.parametrize("group_by", GROUPS)
@pytest.mark.parametrize("endpoint", ["/api/reports", "/api/reports/export"])
def test_member_cannot_request_another_person_even_in_the_same_department(db, team, client, group_by, endpoint):
    issue(db, team)
    response = client.get(endpoint, params={**PARAMS, "group_by": group_by, "person_id": team["b"].id},
                          headers=auth(team["a"]))
    assert response.status_code == 403
    assert response.json()["detail"] == "只能查看本人的任务统计"


def test_empty_member_report_does_not_reveal_other_people_in_roster(db, team):
    issue(db, team)
    data = result(db, team, actor=team["colleague"])
    assert data["summary"]["items"] == 0 and data["details"] == []
    assert [row["id"] for row in data["groups"]] == [team["colleague"].id]


def test_non_admin_host_only_gets_personal_report(db, team):
    issue(db, team)
    team["host"].is_admin = False
    data = result(db, team)
    assert data["summary"]["items"] == 0
    assert data["details"] == []
    assert {row["id"] for row in data["groups"]} == {team["host"].id}


def test_admin_can_still_filter_another_person(db, team, client):
    issue(db, team)
    response = client.get("/api/reports", params={**PARAMS, "group_by": "department_people", "person_id": team["b"].id},
                          headers=auth(team["host"]))
    assert response.status_code == 200
    data = response.json()
    assert {r["id"] for r in data["groups"]} == {team["b"].id}
    assert data["summary"]["items"] == 1


def test_member_pending_quality_only_counts_own_review_tasks(db, team):
    imported = source(db, team)
    for owners in ([team["a"].id], [team["b"].id], []):
        candidate(db, imported, {"department_id": team["dep"].id, "meeting_date": "2026-08-10",
                                  "items": [{"section": "review", "owner_ids": owners}]})
    data = result(db, team, actor=team["a"])
    assert data["pending_meetings"] == 1 and data["pending_file_issues"] == 0
    assert result(db, team)["pending_meetings"] == 3
