"""Explicit dispatch roles enforce recipients without granting unrelated data access."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.models import Department, User
from app.services.access import direct_assignees, managed_tasks
from app.services.employee_roles import DISPATCH_ROLE, MEETING_ROLE, REPORT_ROLE
from tests.test_department_leader_grants import dispatch
from tests.test_department_people_report import issue
from tests.test_employee_roles import change
from tests.test_report_permissions import client  # noqa: F401
from tests.test_report_self_only import auth


@pytest.mark.parametrize("roles", [[], [REPORT_ROLE], [MEETING_ROLE], [DISPATCH_ROLE],
                                  [REPORT_ROLE, MEETING_ROLE, DISPATCH_ROLE]])
def test_dispatch_role_scope_and_admin_boundaries(db, team, client, roles):
    actor = team["colleague"]
    child = Department(name="下属班组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    member = User(name="下属员工", department_id=child.id)
    inactive = User(name="离职员工", department_id=team["dep"].id, active=False)
    db.add_all([member, inactive])
    db.flush()
    assert change(client, team["host"], actor, roles).status_code == 200
    token = auth(actor)
    options = client.get("/api/direct-assignments/options", headers=token).json()
    allowed = {row["id"] for row in options["users"]}
    enabled = bool({MEETING_ROLE, DISPATCH_ROLE} & set(roles))
    assert options["can_assign"] == enabled
    assert options["scope"] == ("all" if DISPATCH_ROLE in roles else "department")
    assert (member.id in allowed) == enabled
    assert (team["outsider"].id in allowed) == (DISPATCH_ROLE in roles)
    assert inactive.id not in allowed
    assert dispatch(client, actor, member).status_code == (201 if enabled else 403)
    assert dispatch(client, actor, team["outsider"]).status_code == (201 if DISPATCH_ROLE in roles else 403)
    assert dispatch(client, actor, member, team["outsider"]).status_code == (201 if DISPATCH_ROLE in roles else 403)
    assert dispatch(client, actor, inactive).status_code == 403
    assert client.get("/api/admin/config", headers=token).status_code == 403


def test_company_dispatch_does_not_expose_foreign_tasks_meetings_or_reports(db, team, client):
    actor = team["colleague"]
    foreign = issue(db, team, department=team["other"], owners=[team["outsider"].id])
    assert change(client, team["host"], actor, [DISPATCH_ROLE]).status_code == 200
    token = auth(actor)
    inactive = User(name="其他部门已停用员工", department_id=team["other"].id, active=False)
    db.add(inactive)
    db.flush()
    directory = client.get("/api/directory", headers=token).json()
    assert team["outsider"].id in {row["id"] for row in directory["users"]}
    assert inactive.id not in {row["id"] for row in directory["users"]}
    assert foreign.series_id not in {row["id"] for row in directory["series"]}
    own = dispatch(client, actor, team["outsider"]).json()
    unrelated = dispatch(client, team["host"], team["outsider"]).json()
    assert client.get("/api/me", headers=token).json()["can_assign_tasks"]
    assert client.get("/api/me", headers=token).json()["can_oversee_tasks"]
    assert client.get("/api/reports/access", headers=token).json()["mode"] == "self"
    assert client.get(f"/api/meetings/{foreign.id}", headers=token).status_code == 403
    assert client.get(f"/api/tasks/{unrelated['id']}", headers=token).status_code == 403
    assert {t.id for t in db.scalars(managed_tasks(db, actor))} == {own["id"]}
    assert client.get("/api/task-overview", headers=token).json()["total"] == 1
    detail = client.get(f"/api/tasks/{own['id']}", headers=token)
    assert detail.status_code == 200 and detail.json()["can_edit"]


def test_roles_follow_department_and_revocation_blocks_create_and_edit(db, team, client):
    actor = team["colleague"]
    assert change(client, team["host"], actor, [MEETING_ROLE]).status_code == 200
    assert dispatch(client, actor, team["a"]).status_code == 201
    actor.department_id = team["other"].id
    db.flush()
    assert dispatch(client, actor, team["a"]).status_code == 403
    assert dispatch(client, actor, team["outsider"]).status_code == 201
    assert change(client, team["host"], actor, [MEETING_ROLE, DISPATCH_ROLE], [MEETING_ROLE]).status_code == 200
    task = dispatch(client, actor, team["a"]).json()
    assert change(client, team["host"], actor, [MEETING_ROLE], [MEETING_ROLE, DISPATCH_ROLE]).status_code == 200
    edited = client.put(f"/api/tasks/{task['id']}", headers=auth(actor), json={
        "expected_version": task["version"], "content": "撤销全公司范围后不能调整跨部门任务",
        "owner_ids": [team["a"].id], "verifier_id": actor.id, "due_date": "2026-12-02", "reason": "测试权限变化",
    })
    assert edited.status_code == 403
    assert change(client, team["host"], actor, [], [MEETING_ROLE]).status_code == 200
    assert dispatch(client, actor, team["outsider"]).status_code == 403
    assert not client.get("/api/me", headers=auth(actor)).json()["can_assign_tasks"]


def test_company_dispatch_without_department_and_inactive_account(db, team, client):
    actor = team["colleague"]
    actor.department_id = None
    db.flush()
    assert change(client, team["host"], actor, [MEETING_ROLE]).status_code == 422
    assert change(client, team["host"], actor, [DISPATCH_ROLE]).status_code == 200
    assert dispatch(client, actor, team["outsider"]).status_code == 201
    actor.active = False
    db.flush()
    assert direct_assignees(db, actor) == []
    assert dispatch(client, actor, team["a"]).status_code == 401
    assert change(client, team["host"], actor, [], [DISPATCH_ROLE]).status_code == 200
    assert change(client, team["host"], actor, [DISPATCH_ROLE]).status_code == 422


def test_system_admin_already_dispatches_companywide_without_employee_roles(db, team, client):
    options = client.get("/api/direct-assignments/options", headers=auth(team["host"])).json()
    assert options["can_assign"] and options["scope"] == "all"
    assert team["outsider"].id in {row["id"] for row in options["users"]}
    assert dispatch(client, team["host"], team["outsider"]).status_code == 201




def test_dispatch_role_migration_preserves_grants_and_guards_downgrade():
    directory = Path(__file__).parents[1] / "migrations/versions"
    modules = []
    for filename in ("20261103_employee_roles.py", "20261104_dispatch_role.py"):
        spec = importlib.util.spec_from_file_location(filename, directory / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users VALUES ('employee')"))
        with Operations.context(MigrationContext.configure(connection)):
            modules[0].upgrade()
            connection.execute(text("INSERT INTO employee_role_grants VALUES ('employee', 'department_meetings')"))
            modules[1].upgrade()
            connection.execute(text("INSERT INTO employee_role_grants VALUES ('employee', 'company_dispatch')"))
            assert connection.scalar(text("SELECT count(*) FROM employee_role_grants")) == 2
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("INSERT INTO employee_role_grants VALUES ('employee', 'unknown')"))
            with pytest.raises(RuntimeError, match="先撤销"):
                modules[1].downgrade()
            connection.execute(text("DELETE FROM employee_role_grants WHERE role = 'company_dispatch'"))
            modules[1].downgrade()
            assert connection.scalar(text("SELECT role FROM employee_role_grants")) == MEETING_ROLE
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("INSERT INTO employee_role_grants VALUES ('employee', 'company_dispatch')"))
    engine.dispose()
