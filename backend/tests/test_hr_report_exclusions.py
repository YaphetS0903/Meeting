from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.main import app
from app.models import Assessment, Department, Membership, User
from app.services.report_access import report_directory, report_exclusions
from app.services.reporting import report
from tests.test_department_people_report import issue
from tests.test_report_permissions import grant
from tests.test_report_quality import candidate, source
from tests.test_report_self_only import GROUPS, PARAMS, auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def hr(db, team):
    team["dep"].name = "人力资源部"
    branches = [Department(name=name, parent_id=team["dep"].id) for name in (
        "固定非在岗", "非在岗（待离职）", "信息化", "招聘培训科", "薪酬绩效&组织发展科", "HRBP组",
    )]
    db.add_all(branches)
    db.flush()
    child = Department(name="OA项目组", parent_id=branches[2].id)
    foreign = Department(name="信息化", parent_id=team["other"].id)
    db.add_all([child, foreign])
    db.flush()
    hidden = [User(name="隐藏" + d.name, department_id=d.id) for d in [*branches[:3], child]]
    mailbox = User(name="董事长信箱", department_id=team["dep"].id)
    ordinary = [User(name="正式" + d.name, department_id=d.id) for d in branches[3:]]
    foreign_user = User(name="其他部门员工", department_id=foreign.id)
    db.add_all([*hidden, mailbox, *ordinary, foreign_user])
    db.flush()
    # Secondary memberships do not move a normal employee out of the report population.
    db.add(Membership(user_id=team["a"].id, department_id=branches[2].id))
    db.flush()
    return {
        "hidden": hidden + [mailbox], "departments": [*branches[:3], child],
        "normal": ordinary, "foreign": foreign_user, "it": branches[2], "oa": child,
    }


def test_directory_excludes_only_marked_hr_branches_and_mailbox(db, team, hr):
    hidden = {p.id for p in hr["hidden"]}
    excluded_departments = {d.id for d in hr["departments"]}
    actual_departments, actual_users = report_exclusions(db)
    assert actual_departments == excluded_departments and actual_users == hidden
    directory = report_directory(db, team["host"])
    assert not hidden.intersection(p["id"] for p in directory["users"])
    assert not excluded_departments.intersection(directory["department_ids"])
    assert not excluded_departments.intersection(d["id"] for d in directory["departments"])
    assert {p.id for p in hr["normal"]} | {team["a"].id, hr["foreign"].id} <= {
        p["id"] for p in directory["users"]
    }
    assert all(db.get(User, identifier).active for identifier in hidden)


@pytest.mark.parametrize("group_by", GROUPS)
@pytest.mark.parametrize("export", [False, True])
def test_every_grouping_and_export_use_same_filtered_population(client, db, team, hr, group_by, export):
    issue(db, team, owners=[team["a"].id, team["b"].id, *[p.id for p in hr["hidden"]]])
    count = db.scalar(select(func.count()).select_from(Assessment))
    response = client.get(
        "/api/reports" + ("/export" if export else ""),
        params={**PARAMS, "group_by": group_by}, headers=auth(team["host"]),
    )
    assert response.status_code == 200
    if export:
        book = openpyxl.load_workbook(BytesIO(response.content))
        assert {row[3].value for row in list(book["考核明细"])[1:]} == {team["a"].name, team["b"].name}
        assert book["考核明细"].max_row == 3
    else:
        data = response.json()
        assert {r["user_id"] for r in data["details"]} == {team["a"].id, team["b"].id}
        assert data["summary"]["person_count"] == 2
        assert data["summary"]["counts"]["已完成"] == 2
        if group_by in {"person", "department_people"}:
            assert not {p.id for p in hr["hidden"]}.intersection(r["id"] for r in data["groups"])
    # Filtering is read-only; original immutable assessments are still available to the system.
    assert db.scalar(select(func.count()).select_from(Assessment)) == count == 7


def test_new_sync_accounts_follow_tree_and_correct_transfers_return_to_reports(db, team, hr):
    new_user = User(name="新增服务账号", department_id=hr["oa"].id)
    db.add(new_user)
    db.flush()
    assert new_user.id not in {p["id"] for p in report_directory(db, team["host"])["users"]}
    new_user.department_id = hr["normal"][0].department_id
    db.flush()
    assert new_user.id in {p["id"] for p in report_directory(db, team["host"])["users"]}
    # An identical branch name outside HR does not hide real employees.
    assert hr["foreign"].id in {p["id"] for p in report_directory(db, team["host"])["users"]}


def test_delegates_and_self_reports_cannot_reintroduce_excluded_people(db, team, hr):
    issue(db, team, owners=[team["a"].id, hr["hidden"][0].id])
    grant(db, team["colleague"], team["dep"])
    data = report(db, team["colleague"], date(2026, 8, 1), date(2026, 8, 31), "department_people")
    assert {r["user_id"] for r in data["details"]} == {team["a"].id}
    hidden_actor = hr["hidden"][0]
    assert report_directory(db, hidden_actor)["users"] == []
    data = report(db, hidden_actor, date(2026, 8, 1), date(2026, 8, 31), "department_people")
    assert data["groups"] == data["details"] == [] and data["summary"]["items"] == 0


def test_pending_checks_ignore_excluded_only_candidates_but_keep_mixed_work(db, team, hr):
    imported = source(db, team)
    for owners in ([hr["hidden"][0].id], [team["a"].id, hr["hidden"][0].id]):
        candidate(db, imported, {"department_id": team["dep"].id, "meeting_date": "2026-08-10",
                                  "items": [{"section": "review", "owner_ids": owners}]})
    data = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31), "department_people")
    assert data["pending_meetings"] == 1
