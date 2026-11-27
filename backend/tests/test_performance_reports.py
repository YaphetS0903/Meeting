from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.main import app
from app.models import Assessment, User
from app.services.employee_accounts import is_employee_account
from app.services.performance_reporting import performance_report
from app.services.report_access import report_directory
from app.services.reporting import report
from tests.role_helpers import report_grant
from tests.test_department_people_report import issue
from tests.test_direct_reports import auth, task

START, END = date(2026, 8, 1), date(2026, 8, 31)
PARAMS = dict(start=str(START), end=str(END))


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    for module in ("direct_reporting", "performance_reporting"):
        monkeypatch.setattr(f"app.services.{module}.today", lambda: date(2026, 8, 20))


def test_combines_person_results_with_weighted_existing_denominators(db, team):
    issue(db, team)
    issue(db, team, owners=[team["a"].id], status="未完成")
    task(db, team, owners=[team["a"], team["b"]], due=date(2026, 8, 17), request="approved")
    task(db, team, due=date(2026, 8, 17), request="pending")
    task(db, team, owners=[team["b"]], due=date(2026, 8, 25), request="approved")
    data = performance_report(db, team["host"], START, END)
    rows = {r["id"]: r for r in data["groups"]}
    assert rows[team["a"].id]["regular"]["rate"] == .5
    assert rows[team["a"].id]["direct"]["rate"] == .5
    assert rows[team["a"].id]["total"] == dict(items=4, assessed=4, completed=2, incomplete=2, not_due=0, excluded=0, closed=0, rate=.5)
    assert rows[team["b"].id]["total"] == dict(items=3, assessed=2, completed=2, incomplete=0, not_due=1, excluded=0, closed=0, rate=1)
    assert data["summary"]["total"]["items"] == 7
    assert data["summary"]["total"]["rate"] == pytest.approx(4 / 6)
    assert rows[team["colleague"].id]["total"]["rate"] is None
    assert rows[team["a"].id]["meetings"][0]["title"] == "人员统计会议"



@pytest.mark.parametrize("actor", ["a", "host"])
def test_meeting_reports_share_performance_deadline_rules(db, team, client, actor):
    person = team["a"]
    cases = [
        ("OA验收流程开发", "未到期", date(2026, 8, 3), date(2026, 8, 10)),
        ("OA验收流程开发", "未到期", date(2026, 8, 10), date(2026, 8, 17)),
        ("OA验收流程开发", "未到期", date(2026, 8, 17), date(2026, 9, 4)),
        ("费用预算OA系统", "未完成", date(2026, 8, 10), date(2026, 8, 10)),
        ("费用预算OA系统", "未完成", date(2026, 8, 17), date(2026, 8, 17)),
    ]
    tasks = {}
    for title, status, meeting_date, due_date in cases:
        meeting = issue(db, team, owners=[person.id], status=status)
        meeting.meeting_date = meeting_date
        assessment = db.scalar(select(Assessment).where(Assessment.meeting_id == meeting.id))
        if title not in tasks:
            tasks[title] = task(db, team, due=due_date, source_meeting_id=meeting.id)
        tasks[title].due_date = due_date
        assessment.task_id = tasks[title].id
        assessment.content = title
        assessment.due_date = due_date
    db.flush()

    headers = auth(team[actor])
    for period, expected_rows in [
        (PARAMS, [("费用预算OA系统", "未完成")] * 2),
        ({"start": "2026-09-01", "end": "2026-09-30"}, [("OA验收流程开发", "未到期")]),
    ]:
        params = {**period, "person_id": person.id}
        meeting_params = {**params, "group_by": "department_people"}
        def get(endpoint, query):
            response = client.get(endpoint, params=query, headers=headers)
            assert response.status_code == 200, response.text
            return response

        meeting_summary = get("/api/reports", {**meeting_params, "include_details": False}).json()["summary"]
        performance = get("/api/reports/performance", params).json()["summary"]["regular"]
        assert meeting_summary["items"] == performance["items"] == len(expected_rows)
        assert meeting_summary["counts"]["未完成"] == performance["incomplete"]
        assert meeting_summary["counts"]["未到期"] == performance["not_due"]
        assert meeting_summary["rate"] == performance["rate"]

        meeting_details = get("/api/reports", {**meeting_params, "details_only": True}).json()
        performance_details = get("/api/reports/performance/details", params).json()
        assert meeting_details["details_total"] == performance_details["details_total"] == len(expected_rows)
        assert meeting_details["details"] == performance_details["details"]
        assert [(row["content"], row["status"]) for row in meeting_details["details"]] == expected_rows
        if period != PARAMS:
            assert meeting_details["details"][0]["due_date"] == "2026-09-04"

        exported = get("/api/reports/export", meeting_params)
        workbook = openpyxl.load_workbook(BytesIO(exported.content))
        assert int(workbook["考核汇总"]["B3"].value) == len(expected_rows)
        assert [(row[6], row[8]) for row in workbook["考核明细"].iter_rows(min_row=2, values_only=True)] == expected_rows


@pytest.mark.parametrize("actor", ["a", "host"])
def test_status_details_match_performance_counts_before_pagination(db, team, client, actor):
    person = team["a"]
    for status in ("已完成", "未完成", "未到期", "不统计", "关闭"):
        issue(db, team, owners=[person.id], status=status)
    approved = task(db, team, due=date(2026, 8, 20), request="approved")
    pending = task(db, team, due=date(2026, 8, 17), request="pending")
    unfinished = task(db, team, due=date(2026, 8, 18))
    future = task(db, team, due=date(2026, 8, 25), request="approved")
    task(db, team, owners=[team["outsider"]], due=date(2026, 8, 19))
    headers = auth(team[actor])
    params = {**PARAMS, "person_id": person.id, "department_id": team["dep"].id}
    result = client.get("/api/reports/performance", params=params, headers=headers)
    assert result.status_code == 200, result.text
    summary = result.json()["summary"]
    for key, status, direct_ids in [
        ("completed", "已完成", [approved.id]),
        ("incomplete", "未完成", [pending.id, unfinished.id]),
        ("not_due", "未到期", [future.id]),
        ("excluded", "不统计", []),
        ("closed", "已关闭", []),
    ]:
        for source in ("regular", "direct"):
            def page(offset):
                response = client.get("/api/reports/performance/details", headers=headers, params={
                    **params, "source": source, "status": status, "limit": 1, "offset": offset,
                })
                assert response.status_code == 200, response.text
                return response.json()

            first = page(0)
            assert first["details_total"] == summary[source][key]
            if source == "regular":
                assert [row["status"] for row in first["details"]] == ["关闭" if key == "closed" else status]
            else:
                rows = first["details"] + (page(1)["details"] if len(direct_ids) > 1 else [])
                assert [row["id"] for row in rows] == direct_ids
                assert all(row["status"] == status for row in rows)

    if actor == "a":
        for source in ("regular", "direct"):
            denied = client.get("/api/reports/performance/details", headers=headers, params={
                **PARAMS, "person_id": team["outsider"].id, "source": source, "status": "未完成",
            })
            assert denied.status_code == 403


def test_existing_exclusions_apply_to_directory_counts_details_and_exports(db, team, client):
    bot = User(name="it机器人", department_id=team["dep"].id)
    rpa = User(name="张测试员", position="rpa机器人", department_id=team["dep"].id)
    db.add_all([bot, rpa])
    db.flush()
    issue(db, team, owners=[team["a"].id, bot.id, rpa.id])
    task(db, team, owners=[bot, rpa], due=date(2026, 8, 17))
    before = db.scalar(select(func.count()).select_from(Assessment))
    assert not {bot.id, rpa.id} & {p["id"] for p in report_directory(db, team["host"])["users"]}
    regular = report(db, team["host"], START, END, "department_people")
    assert regular["summary"]["items"] == 1
    assert {row["user_id"] for row in regular["details"]} == {team["a"].id}
    result = client.get("/api/reports/performance", params=PARAMS, headers=auth(team["host"]))
    assert result.status_code == 200
    assert result.json()["summary"]["total"]["items"] == 1
    exported = client.get("/api/reports/performance/export", params=PARAMS, headers=auth(team["host"]))
    assert exported.status_code == 200
    sheet = openpyxl.load_workbook(BytesIO(exported.content)).active
    assert sheet.title == "绩效统计"
    assert [cell.value for cell in sheet[2]] == [
        "姓名", "任务总数", "已完成", "未完成", "未到期", "不统计", "已关闭", "绩效达成率",
    ]
    assert all("机器人" not in str(cell.value) for row in sheet for cell in row)
    assert before == db.scalar(select(func.count()).select_from(Assessment)) == 3
    assert db.get(User, bot.id).active


@pytest.mark.parametrize("endpoint", ["/api/reports/performance", "/api/reports/performance/export"])
def test_self_and_department_permissions_are_preserved(db, team, client, endpoint):
    issue(db, team, owners=[team["a"].id, team["outsider"].id])
    task(db, team, owners=[team["a"], team["outsider"]], due=date(2026, 8, 17))
    denied = client.get(endpoint, params={**PARAMS, "person_id": team["outsider"].id}, headers=auth(team["a"]))
    assert denied.status_code == 403
    report_grant(db, team["colleague"], team["dep"])
    allowed = client.get(endpoint, params=PARAMS, headers=auth(team["colleague"]))
    assert allowed.status_code == 200
    if not endpoint.endswith("export"):
        assert team["outsider"].id not in {r["id"] for r in allowed.json()["groups"]}
        assert allowed.json()["summary"]["total"]["items"] == 2
    else:
        sheet = openpyxl.load_workbook(BytesIO(allowed.content)).active
        assert team["outsider"].name not in {cell.value for row in sheet for cell in row}


def test_self_with_only_direct_work_and_empty_or_future_period(db, team, client):
    task(db, team, due=date(2026, 8, 25))
    data = performance_report(db, team["a"], START, END)
    assert len(data["groups"]) == 1
    assert data["summary"]["total"]["items"] == 1
    assert data["summary"]["total"]["rate"] is None
    assert client.get("/api/reports/performance", params=dict(start=str(END), end=str(START)), headers=auth(team["a"])).status_code == 422


@pytest.mark.parametrize("name,position,expected", [
    ("it机器人", "", False), ("RPA", "", False), ("robot_42", "", False),
    ("测试账号1", "", False), ("ｂｏｔ", "", False), ("赵彩霞", "虚拟岗位", False),
    ("赵彩霞", "", True), ("王晨宁", "工程师", True), ("Alice Smith", "", True),
    ("欧阳娜娜", "", True),
])
def test_service_detection_matches_employee_picker(name, position, expected):
    assert is_employee_account(name, position) is expected
