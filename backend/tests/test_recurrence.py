"""Retirement closes every entry point while preserving issued business data."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import AuditLog, BusinessRole, RecurringRule, Task
from app.schemas import PublishRequest
from app.services.business import create_meeting, publish, task_snapshot
from app.services.retire_recurrence import retire_recurrence
from app.services.role_migration import rule
from app.services.role_permissions import ACTION_KEYS, allows, capabilities
from app.worker import celery, recurrence
from tests.role_helpers import grant


@pytest.fixture
def source(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    legacy = RecurringRule(
        source_task_id=task.id,
        creator_id=team["host"].id,
        frequency="weekly",
        anchor_date=date(2026, 8, 20),
        next_due_date=date(2026, 8, 27),
        template=task_snapshot(db, task),
    )
    db.add(legacy)
    db.flush()
    task.recurrence_key = legacy.id
    db.flush()
    return task, legacy


def test_retirement_is_idempotent_and_keeps_tasks_roles_and_other_permissions(db, team, source):
    task, legacy = source
    role = grant(db, team["a"], [rule("reports", ["view"]), rule("recurrence", ["create", "edit"])])
    before = task_snapshot(db, task)
    role_version = role.version
    counts = (
        db.scalar(select(func.count()).select_from(Task)),
        db.scalar(select(func.count()).select_from(RecurringRule)),
    )
    assert retire_recurrence(db, team["host"]) == {"stopped_rules": 1, "updated_roles": 1}
    assert not legacy.active
    assert role.rules == [rule("reports", ["view"])]
    assert role.version == role_version + 1
    assert task_snapshot(db, task) == before
    assert counts == (
        db.scalar(select(func.count()).select_from(Task)),
        db.scalar(select(func.count()).select_from(RecurringRule)),
    )
    assert db.get(BusinessRole, role.id).active
    assert allows(db, team["a"], "reports.view", team["dep"].id)
    assert retire_recurrence(db, team["host"]) == {"stopped_rules": 0, "updated_roles": 0}
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == "recurrence.retire")))) == 1


def test_old_scheduled_jobs_cannot_create_tasks_or_notifications(db, source):
    task, legacy = source
    before = task_snapshot(db, task)
    assert "recurrence" not in celery.conf.beat_schedule
    assert recurrence.run() == 0
    assert legacy.active  # Even an unretired record cannot trigger the removed generator.
    assert task_snapshot(db, task) == before
    assert set(celery.conf.beat_schedule) >= {"outbox", "deadline-reminders", "reminders", "directory-sync"}


def test_removed_permissions_are_denied_even_for_admin_and_old_grants(db, team):
    grant(db, team["a"], [rule("recurrence", ["create", "edit", "toggle"], "all")])
    assert "recurrence" not in ACTION_KEYS
    for actor in (team["host"], team["a"]):
        assert "recurrence" not in capabilities(db, actor)
        for action in ("create", "edit", "toggle", "view"):
            assert not allows(db, actor, "recurrence." + action, team["dep"].id, related=True)


def test_removed_routes_catalog_and_task_details(db, team, source):
    task, legacy = source
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            assert not any("recurrence" in path for path in client.get("/openapi.json").json()["paths"])
            for method, path in [
                ("GET", f"/api/tasks/{task.id}/recurrence"),
                ("POST", f"/api/tasks/{task.id}/recurrence"),
                ("PUT", f"/api/recurrences/{legacy.id}"),
            ]:
                assert client.request(method, path, headers=headers, json={}).status_code == 404
            catalog = client.get("/api/admin/role-catalog", headers=headers).json()
            assert len(catalog) == 7 and all(m["key"] != "recurrence" for m in catalog)
            data = client.get(f"/api/tasks/{task.id}", headers=headers).json()
            assert data["content"] == task.content and "can_manage_recurrence" not in data
            assert (
                client.post(
                    "/api/admin/roles",
                    headers=headers,
                    json={
                        "name": "废弃模块",
                        "rules": [rule("recurrence", ["create"], "all")],
                    },
                ).status_code
                == 422
            )
    finally:
        app.dependency_overrides.clear()
