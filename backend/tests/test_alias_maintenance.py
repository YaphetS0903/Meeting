# ruff: noqa: F811 -- imported pytest fixture is injected by name
from sqlalchemy import select

from app.models import AuditLog, UserAlias
from app.services.importing import resolve_name
from tests.test_admin_people import client, headers  # noqa: F401


def test_alias_maintenance_permissions_normalization_scope_and_audit(client, db, team):
    admin = headers(team["host"])
    member = headers(team["a"])
    payload = {"alias": "  王工  ", "user_id": team["a"].id, "department_id": team["dep"].id}
    assert client.post("/api/admin/aliases", headers=member, json=payload).status_code == 403
    assert client.get("/api/admin/aliases", headers=member).status_code == 403
    created = client.post("/api/admin/aliases", headers=admin, json=payload)
    assert created.status_code == 200
    identifier = created.json()["id"]
    assert client.post("/api/admin/aliases", headers=admin, json=payload).json()["id"] == identifier
    assert len(client.get("/api/admin/aliases", headers=admin).json()) == 1
    assert resolve_name(db, "王工", team["dep"].id) == team["a"].id
    assert resolve_name(db, "王工", team["other"].id) is None
    assert resolve_name(db, "王工") is None
    url = f"/api/admin/aliases/{identifier}"
    assert client.request("DELETE", url, headers=member, json={"reason": "人员变动"}).status_code == 403
    assert client.request("DELETE", url, headers=admin, json={"reason": "  "}).status_code == 422
    assert client.request("DELETE", url, headers=admin, json={"reason": "人员变动"}).status_code == 200
    db.flush()
    assert db.get(UserAlias, identifier) is None
    assert resolve_name(db, "王工", team["dep"].id) is None
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "directory.alias.remove"))
    assert audit.before["alias"] == "王工" and audit.reason == "人员变动"


def test_invalid_alias_and_ambiguous_matches_remain_unresolved(client, db, team):
    admin = headers(team["host"])
    payload = {"alias": "主管", "user_id": team["a"].id}
    assert client.post("/api/admin/aliases", headers=admin, json={**payload, "alias": " "}).status_code == 422
    assert (
        client.post(
            "/api/admin/aliases", headers=admin, json={**payload, "department_id": "missing"}
        ).status_code
        == 422
    )
    client.post("/api/admin/aliases", headers=admin, json=payload)
    client.post("/api/admin/aliases", headers=admin, json={**payload, "user_id": team["b"].id})
    assert resolve_name(db, "主管", team["dep"].id) is None
