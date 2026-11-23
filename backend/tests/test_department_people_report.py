from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Department, User
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting
from app.services.reporting import report


def issue(db, team, department=None, owners=None, status="已完成", kind="department"):
    draft = MeetingDraft(
        title="人员统计会议", department_id=(department or team["dep"]).id,
        kind=kind, host_id=team["host"].id, recorder_id=team["host"].id,
        meeting_date=date(2026, 8, 10), items=[MeetingItem(
            section="review", content="人员统计事项", status=status,
            owner_ids=owners or [team["a"].id, team["b"].id], due_date=date(2026, 8, 17),
        )],
    )
    meeting = create_meeting(db, team["host"], draft, historical=True)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.flush()
    return meeting


def result(db, team, **kwargs):
    return report(db, kwargs.pop("actor", team["host"]), date(2026, 8, 1), date(2026, 8, 31),
                  group_by="department_people", department_id=kwargs.pop("department", team["dep"].id),
                  **kwargs)


def test_department_lists_all_five_people_and_cross_department_work(db, team):
    fifth = User(name="第五位员工", department_id=team["dep"].id)
    db.add(fifth)
    issue(db, team)
    issue(db, team, team["other"], [team["a"].id, team["outsider"].id], "未完成")
    data = result(db, team)
    rows = {r["id"]: r for r in data["groups"]}
    assert len(rows) == 5 and rows[fifth.id]["items"] == 0
    assert rows[fifth.id]["rate"] is None
    assert rows[team["a"].id]["items"] == 2
    assert rows[team["a"].id]["counts"]["未完成"] == 1
    assert rows[team["b"].id]["counts"]["已完成"] == 1
    assert team["outsider"].id not in rows
    assert data["summary"]["items"] == sum(r["items"] for r in rows.values()) == 3
    assert data["summary"]["rate"] == pytest.approx(2 / 3)
    assert len(data["details"]) == 3


def test_current_department_and_name_do_not_rewrite_historical_department(db, team):
    issue(db, team)
    team["a"].department_id = team["other"].id
    team["a"].name = "改名后的员工"
    db.flush()
    assert team["a"].id not in {r["id"] for r in result(db, team)["groups"]}
    finance = result(db, team, department=team["other"].id)
    moved = next(r for r in finance["groups"] if r["id"] == team["a"].id)
    assert moved["items"] == 1 and moved["label"] == "改名后的员工"
    assert finance["details"][0]["user_name"] == "改名后的员工"
    historical = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31), "person_department")
    assert historical["groups"][0]["id"] == team["dep"].id


def test_revisions_count_only_latest_published_results_and_keep_one_person_row(db, team):
    meeting = issue(db, team)
    revision(db, team["host"], meeting.id, "核实完成结果")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version))
    data = result(db, team)
    assert data["summary"]["items"] == 2
    assert data["summary"]["counts"]["已完成"] == 0
    assert data["summary"]["counts"]["未完成"] == 2
    assert len([r for r in data["groups"] if r["id"] == team["a"].id]) == 1


def test_inactive_people_keep_records_but_no_empty_inactive_rows(db, team):
    issue(db, team)
    team["a"].active = False
    team["colleague"].active = False
    db.flush()
    rows = {r["id"]: r for r in result(db, team)["groups"]}
    assert rows[team["a"].id]["active"] is False
    assert rows[team["a"].id]["items"] == 1
    assert team["colleague"].id not in rows


def test_person_kind_filters_and_meeting_permissions_remain_effective(db, team):
    issue(db, team)
    issue(db, team, team["other"], [team["a"].id], "未完成", "special")
    own = result(db, team, person_id=team["a"].id, kind="special")
    assert len(own["groups"]) == 1 and own["summary"]["items"] == 1
    assert own["details"][0]["kind"] == "special"
    denied = result(db, team, actor=team["outsider"])
    assert denied["summary"]["items"] == 0
    assert denied["details"] == []
    # An inaccessible department roster must not be disclosed by zero rows either.
    empty = result(db, team, actor=team["colleague"], department=team["other"].id)
    assert empty["groups"] == []


def test_report_api_and_excel_share_person_rows_without_person_count_column(db, team):
    issue(db, team)
    headers = {"Authorization": "Bearer " + issue_token(team["host"])}
    params = {"start": "2026-08-01", "end": "2026-08-31", "group_by": "department_people",
              "department_id": team["dep"].id, "person_id": team["a"].id}
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get("/api/reports", params=params, headers=headers)
            assert response.status_code == 200
            data = response.json()
            excel = client.get("/api/reports/export", params=params, headers=headers)
            assert excel.status_code == 200
    finally:
        app.dependency_overrides.clear()
    book = openpyxl.load_workbook(BytesIO(excel.content))
    sheet = book["考核汇总"]
    assert [c.value for c in sheet[2]] == ["姓名", "事项数", "已完成", "未完成", "未到期", "不统计", "关闭", "达成率"]
    assert sheet["A4"].value == data["groups"][0]["label"]
    assert int(sheet["B4"].value) == data["groups"][0]["items"] == 1
    assert int(sheet["C4"].value) == data["groups"][0]["counts"]["已完成"] == 1
    assert sheet.max_row == 4 and book["考核明细"].max_row == 2


def test_parent_department_includes_all_descendants_without_other_departments(db, team):
    child = Department(name="信息化", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    grandchild = Department(name="OA项目组", parent_id=child.id)
    db.add(grandchild)
    db.flush()
    engineer = User(name="下属部门员工", department_id=child.id)
    project_owner = User(name="下属项目组员工", department_id=grandchild.id)
    empty_person = User(name="本期无任务员工", department_id=grandchild.id)
    db.add_all([engineer, project_owner, empty_person])
    db.flush()
    issue(db, team, team["other"], [team["a"].id, engineer.id, project_owner.id, team["outsider"].id])
    data = result(db, team)
    rows = {r["id"]: r for r in data["groups"]}
    assert rows[engineer.id]["items"] == rows[project_owner.id]["items"] == 1
    assert rows[empty_person.id]["items"] == 0
    assert team["outsider"].id not in rows
    assert data["summary"]["items"] == 3
    assert {row["user_id"] for row in data["details"]} == {team["a"].id, engineer.id, project_owner.id}
    assert result(db, team, department=child.id)["summary"]["items"] == 2
    assert result(db, team, person_id=project_owner.id)["summary"]["items"] == 1


def test_parent_filter_preserves_employee_self_only_boundary(db, team):
    parent = Department(name="运营中心")
    db.add(parent)
    db.flush()
    team["dep"].parent_id = parent.id
    issue(db, team)
    data = result(db, team, actor=team["a"], department=parent.id)
    assert {row["id"] for row in data["groups"]} == {team["a"].id}
    assert {row["user_id"] for row in data["details"]} == {team["a"].id}
    assert data["summary"]["items"] == 1
