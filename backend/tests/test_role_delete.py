"""Deleting a role revokes only its assignments and preserves an audit trail."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import app
from app.models import AuditLog, BusinessRole, BusinessRoleAssignment, User
from app.services.role_migration import rule
from tests.test_custom_role_http import api_role, auth
from tests.test_custom_roles import grant


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def test_delete_revokes_only_target_role_and_records_members(client, db, team):
    admin, person = team["host"], team["colleague"]
    identifier = api_role(client, admin, person, [rule("reports", ["view"], "all")])
    other = grant(db, person, [rule("meetings", ["view"])], "保留的角色")
    # The same token must observe revocation on the next request.
    token = auth(person)
    assert client.get("/api/reports/access", headers=token).json()["mode"] == "all"
    response = client.request(
        "DELETE",
        f"/api/admin/roles/{identifier}",
        headers=auth(admin),
        json={"expected_version": 1, "expected_member_count": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["revoked_assignments"] == 1
    assert db.get(BusinessRole, identifier) is None
    assert db.get(User, person.id).active
    assert db.get(BusinessRoleAssignment, (person.id, other.id))
    assert client.get("/api/reports/access", headers=token).json()["mode"] == "self"
    log = db.scalar(select(AuditLog).where(AuditLog.action == "role.delete"))
    assert log.before["member_ids"] == [person.id]
    assert log.before["name"] and log.after["deleted"]


@pytest.mark.parametrize("version,count,status", [(2, 1, 409), (1, 0, 409), (1, -1, 422)])
def test_delete_stale_or_invalid_confirmation_preserves_role(client, db, team, version, count, status):
    identifier = api_role(client, team["host"], team["colleague"], [rule("reports", ["view"])])
    response = client.request(
        "DELETE",
        f"/api/admin/roles/{identifier}",
        headers=auth(team["host"]),
        json={"expected_version": version, "expected_member_count": count},
    )
    assert response.status_code == status
    assert db.get(BusinessRole, identifier)
    assert db.get(BusinessRoleAssignment, (team["colleague"].id, identifier))


def test_delete_empty_role_admin_only_and_unknown_role(client, db, team):
    role = BusinessRole(name="未分配角色", rules=[rule("reports", ["view"])])
    db.add(role)
    db.flush()
    payload = {"expected_version": role.version, "expected_member_count": 0}
    path = f"/api/admin/roles/{role.id}"
    assert client.request("DELETE", path, headers=auth(team["a"]), json=payload).status_code == 403
    assert client.request("DELETE", path, headers=auth(team["host"]), json={}).status_code == 422
    assert client.request("DELETE", path, headers=auth(team["host"]), json=payload).status_code == 200
    assert client.request("DELETE", path, headers=auth(team["host"]), json=payload).status_code == 404
