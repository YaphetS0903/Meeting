from datetime import date
from io import BytesIO
from uuid import uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import CompletionRequest, Department, Task, TaskOwner, TaskVersion, User, now
from app.services.direct_reporting import direct_report
from app.services.report_access import report_directory
from app.services.role_migration import rule
from tests.role_helpers import dispatch_grant, grant, report_grant

START, END = date(2026, 11, 1), date(2026, 11, 30)
PARAMS = {"start": str(START), "end": str(END)}


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch):
    monkeypatch.setattr("app.services.direct_reporting.today", lambda: date(2026, 11, 12))


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


def task(db, team, *, owners=None, due=date(2026, 11, 12), request=None, version=1, **kwargs):
    item = Task(code=uuid4().hex[:20], content="完成采购流程", source_item_id=str(uuid4()),
                verifier_id=team["host"].id, issuer_id=team["host"].id,
                department_id=team["dep"].id, due_date=due, version=version, **kwargs)
    db.add(item)
    db.flush()
    for owner in owners or [team["a"]]:
        db.add(TaskOwner(task_id=item.id, user_id=owner.id))
    if request:
        db.add(CompletionRequest(task_id=item.id, task_version=version, applicant_id=team["a"].id,
                                 idempotency_key=str(uuid4()), explanation="完成", state=request))
    db.flush()
    return item


def test_exclusive_states_due_today_and_rate_excludes_future_completed(db, team):
    task(db, team, request="approved", closed_at=now(), status="关闭")
    task(db, team, request="pending")
    task(db, team, request="rejected")
    task(db, team, due=date(2026, 11, 13))
    task(db, team, request="approved", due=date(2026, 11, 13))
    task(db, team, withdrawn_at=now())
    # Merely closing a task is not evidence of verification.
    task(db, team, closed_at=now(), status="关闭")
    data = direct_report(db, team["a"], START, END, include_details=True)
    assert data["summary"]["counts"] == {"已完成": 2, "待验证": 1, "未完成": 2, "未到期": 1}
    assert data["summary"]["items"] == 6
    assert data["summary"]["due_items"] == 4
    assert data["summary"]["rate"] == .25
    assert len(data["details"]) == 6


def test_old_completion_versions_do_not_count_and_undated_is_separate(db, team):
    item = task(db, team, request="approved")
    item.version = 2
    task(db, team, request="pending", due=date(2026, 11, 20))
    task(db, team, due=None)
    task(db, team, due=date(2026, 12, 1))
    db.flush()
    data = direct_report(db, team["a"], START, END)
    assert data["summary"]["counts"] == {"已完成": 0, "待验证": 1, "未完成": 1, "未到期": 0}
    assert data["undated_count"] == 1


def test_shared_owners_person_counts_and_company_dedup(db, team):
    task(db, team, owners=[team["a"], team["b"], team["outsider"]], request="approved")
    data = direct_report(db, team["host"], START, END, include_details=True)
    assert data["summary"]["items"] == 1
    assert len(data["groups"]) == 3
    assert sum(g["items"] for g in data["groups"]) == 3
    assert len(data["details"]) == 1
    assert data["details_total"] == 1


@pytest.mark.parametrize("endpoint", ["/api/reports/direct", "/api/reports/direct/export"])
def test_self_only_and_dispatch_role_does_not_grant_reports(db, team, client, endpoint):
    dispatch_grant(db, team["a"], "all")
    task(db, team, owners=[team["b"]])
    denied = client.get(endpoint, params={**PARAMS, "person_id": team["b"].id}, headers=auth(team["a"]))
    assert denied.status_code == 403
    allowed = client.get(endpoint, params=PARAMS, headers=auth(team["a"]))
    assert allowed.status_code == 200
    if not endpoint.endswith("export"):
        assert allowed.json()["summary"]["items"] == 0
        assert allowed.json()["groups"] == []


@pytest.mark.parametrize("endpoint", ["/api/reports/direct", "/api/reports/direct/export"])
def test_department_permissions_details_and_export_no_coowner_leak(db, team, client, endpoint):
    report_grant(db, team["colleague"], team["dep"])
    task(db, team, owners=[team["a"], team["outsider"]])
    task(db, team, owners=[team["outsider"]])
    response = client.get(endpoint, params={**PARAMS, "include_details": True}, headers=auth(team["colleague"]))
    assert response.status_code == 200
    if endpoint.endswith("export"):
        book = openpyxl.load_workbook(BytesIO(response.content))
        assert book["派发任务明细"].max_row == 2
        assert team["outsider"].name not in {c.value for row in book["派发任务明细"] for c in row}
    else:
        data = response.json()
        assert data["summary"]["items"] == 1
        assert data["details"][0]["owner_names"] == [team["a"].name]
    for key, value in [("person_id", team["outsider"].id), ("department_id", team["other"].id)]:
        denied = client.get(endpoint, params={**PARAMS, key: value, "details_only": True}, headers=auth(team["colleague"]))
        assert denied.status_code == 403


def test_department_children_postponed_date_paging_and_safe_excel(db, team, client):
    report_grant(db, team["colleague"], team["dep"])
    child = Department(name="下属部门", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    person = User(name="子部门员工", department_id=child.id)
    db.add(person)
    db.flush()
    item = task(db, team, owners=[person], due=date(2026, 11, 20))
    item.content = '=HYPERLINK("unsafe")'
    db.add(TaskVersion(task_id=item.id, number=1, snapshot={"due_date": "2026-10-31"}, reason="创建", actor_id=team["host"].id))
    db.add(TaskVersion(task_id=item.id, number=2, snapshot={"due_date": "2026-11-20"}, reason="延期", actor_id=team["host"].id))
    db.flush()
    params = {**PARAMS, "department_id": team["dep"].id, "include_details": True}
    data = client.get("/api/reports/direct", params=params, headers=auth(team["colleague"])).json()
    assert data["groups"][0]["label"] == person.name
    assert data["summary"]["rate"] is None
    assert data["details"][0]["date_history"] == ["2026-10-31"]
    empty = client.get("/api/reports/direct", params={**params, "details_only": True, "offset": 1}, headers=auth(team["colleague"])).json()
    assert empty["details_total"] == 1 and empty["details"] == []
    excel = client.get("/api/reports/direct/export", params=params, headers=auth(team["colleague"]))
    book = openpyxl.load_workbook(BytesIO(excel.content))
    assert book["派发任务明细"]["A2"].value == item.content
    assert book["派发任务明细"]["A2"].data_type == "s"
    assert book["派发任务明细"]["F2"].value == "2026-10-31"


def test_export_requires_export_rights_and_validates_query(db, team, client):
    grant(db, team["colleague"], [rule("reports", ["view"], "all")])
    task(db, team)
    assert client.get("/api/reports/direct/export", params=PARAMS, headers=auth(team["colleague"])).status_code == 403
    for values in [{"start": "2026-12-01"}, {"status": "关闭"}, {"limit": 101}, {"offset": -1}]:
        assert client.get("/api/reports/direct", params={**PARAMS, **values}, headers=auth(team["host"])).status_code == 422


def test_company_export_role_is_exposed_to_page(db, team, client):
    grant(db, team["colleague"], [rule("reports", ["view", "export"], "all")])
    task(db, team)
    assert report_directory(db, team["colleague"])["can_export"] is True
    assert client.get("/api/reports/direct/export", params=PARAMS, headers=auth(team["colleague"])).status_code == 200
