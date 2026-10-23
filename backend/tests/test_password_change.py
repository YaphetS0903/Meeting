from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.security import issue_token, passwords
from app.main import app


def test_password_change_invalidates_prior_sessions(db, team):
    account = team["host"]
    account.password_hash = passwords.hash("original-passphrase-2026")
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            original = {"Authorization": "Bearer " + issue_token(account)}
            assert client.get("/api/me", headers=original).json()["has_password"] is True
            short = client.put(
                "/api/me/password",
                json={"current_password": "original-passphrase-2026", "new_password": "short-pass"},
                headers=original,
            )
            assert short.status_code == 422
            wrong = client.put(
                "/api/me/password",
                json={"current_password": "incorrect", "new_password": "updated-passphrase-2026"},
                headers=original,
            )
            assert wrong.status_code == 422
            assert client.get("/api/me", headers=original).status_code == 200
            response = client.put(
                "/api/me/password",
                json={
                    "current_password": "original-passphrase-2026",
                    "new_password": "updated-passphrase-2026",
                },
                headers=original,
            )
            assert response.status_code == 200, response.text
            updated = {"Authorization": "Bearer " + response.json()["access_token"]}
            assert client.get("/api/me", headers=original).status_code == 401
            assert client.get("/api/me", headers=updated).status_code == 200
            assert client.post(
                "/api/auth/login", json={"username": "host", "password": "original-passphrase-2026"}
            ).status_code == 401
            assert client.post(
                "/api/auth/login", json={"username": "host", "password": "updated-passphrase-2026"}
            ).status_code == 200
            no_password = {"Authorization": "Bearer " + issue_token(team["colleague"])}
            assert client.get("/api/me", headers=no_password).json()["has_password"] is False
            assert client.put(
                "/api/me/password",
                json={"current_password": "anything", "new_password": "updated-passphrase-2026"},
                headers=no_password,
            ).status_code == 403
    finally:
        app.dependency_overrides.clear()
