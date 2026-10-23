import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import AuditLog, Task
from app.schemas import PublishRequest
from app.services.business import create_meeting, publish


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def headers(user):
    return {"Authorization": "Bearer " + issue_token(user)}


def test_role_management_guards_and_audit(client, db, team):
    def change(actor, target, value, previous):
        return client.put(
            f"/api/admin/users/{target.id}/role",
            headers=headers(actor),
            json={"is_admin": value, "expected_is_admin": previous, "reason": "岗位权限调整"},
        )

    assert change(team["a"], team["a"], True, False).status_code == 403
    assert change(team["host"], team["host"], False, True).status_code == 422
    team["b"].active = False
    assert change(team["host"], team["b"], True, False).status_code == 422
    assert change(team["host"], team["a"], True, False).status_code == 200
    assert change(team["host"], team["a"], False, False).status_code == 409
    assert change(team["host"], team["host"], False, True).status_code == 200
    # Existing bearer tokens do not preserve revoked administrator powers.
    assert client.get("/api/admin/reassignment", headers=headers(team["host"])).status_code == 403
    assert db.scalar(select(AuditLog).where(AuditLog.action == "user.role")).reason == "岗位权限调整"


def test_departed_owner_and_verifier_alerts_exclude_closed_tasks(client, db, team, draft_factory):
    draft = draft_factory()
    draft.items[0].verifier_id = team["colleague"].id
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task))
    team["a"].active = team["colleague"].active = False
    assert client.get("/api/admin/reassignment", headers=headers(team["b"])).status_code == 403
    result = client.get("/api/admin/reassignment", headers=headers(team["host"])).json()
    assert len(result) == 1
    assert result[0]["inactive_owners"] == [team["a"].name]
    assert result[0]["inactive_verifier"] == team["colleague"].name
    task.status = "关闭"
    assert client.get("/api/admin/reassignment", headers=headers(team["host"])).json() == []
