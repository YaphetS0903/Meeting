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
    assert rows[team["a"].id]["total"] == dict(items=4, assessed=4, completed=2, incomplete=2, rate=.5)
    assert rows[team["b"].id]["total"] == dict(items=3, assessed=2, completed=2, incomplete=0, rate=1)
    assert data["summary"]["total"]["items"] == 7
    assert data["summary"]["total"]["rate"] == pytest.approx(4 / 6)
    assert rows[team["colleague"].id]["total"]["rate"] is None
    assert rows[team["a"].id]["meetings"][0]["title"] == "人员统计会议"


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
