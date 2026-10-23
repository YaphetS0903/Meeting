import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import extra_api
from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import AuditLog, User


def test_failed_sync_rolls_back_directory_but_keeps_sanitized_diagnostic(db, team, monkeypatch):
    db.commit()
    factory = sessionmaker(db.bind, expire_on_commit=False)

    def connection():
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = connection

    def failed(session):
        person = session.get(User, team["a"].id)
        person.name = "不应落库的部分同步"
        session.flush()
        raise httpx.ConnectError("https://wecom.example.test?secret=private-secret")

    monkeypatch.setattr(extra_api, "sync_directory", failed)
    headers = {"Authorization": "Bearer " + issue_token(team["host"])}
    try:
        with TestClient(app) as client:
            result = client.post("/api/admin/wecom/sync", headers=headers)
            assert result.status_code == 503 and "private-secret" not in result.text
            db.expire_all()
            assert db.get(User, team["a"].id).name == "责任人甲"
            failure = db.scalar(select(AuditLog).where(AuditLog.action == "directory.sync.failed"))
            assert failure and "private-secret" not in str(failure.after)
            history = client.get("/api/admin/directory-sync-history", headers=headers).json()
            assert history[0]["state"] == "failed"
            assert "ConnectError" in history[0]["result"]["error"]
            assert (
                client.get(
                    "/api/admin/directory-sync-history",
                    headers={"Authorization": "Bearer " + issue_token(team["a"])},
                ).status_code
                == 403
            )
            monkeypatch.setattr(extra_api, "sync_directory", lambda _: {"users": 2, "departments": 1})
            assert client.post("/api/admin/wecom/sync", headers=headers).status_code == 200
            assert (
                client.get("/api/admin/directory-sync-history", headers=headers).json()[0]["state"]
                == "succeeded"
            )
    finally:
        app.dependency_overrides.clear()
