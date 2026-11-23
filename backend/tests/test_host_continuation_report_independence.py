"""A host with personal-only reports can still continue their published meeting."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.main import app
from app.models import OutboxEvent
from app.schemas import PublishRequest
from app.services import business
from app.services.report_access import report_access
from tests.test_report_self_only import auth


@pytest.mark.parametrize("cross_department", [False, True])
def test_personal_report_host_can_continue_tomorrows_meeting_without_department_grants(
    db, team, draft_factory, monkeypatch, cross_department,
):
    actor = team["host"]
    draft = draft_factory(when=date(2026, 10, 27))
    if cross_department:
        draft.department_id = team["other"].id
    source = business.create_meeting(db, actor, draft)
    business.publish(db, actor, source.id, PublishRequest(expected_version=1, notify=False))
    actor.is_admin = False
    db.flush()
    monkeypatch.setattr(business, "today", lambda: date(2026, 11, 3))
    assert not actor.manual_leader_department_ids and not actor.leader_department_ids
    assert report_access(db, actor).mode == "self"
    before_notifications = db.scalar(select(func.count()).select_from(OutboxEvent))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = auth(actor)
            params = {"view": "current", "series_key": "series:" + source.series_id}
            detail = client.get(f"/api/meetings/{source.id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["can_continue"] is True
            listing = client.get("/api/meetings", params=params, headers=headers)
            assert listing.status_code == 200
            assert next(row for row in listing.json()["items"] if row["id"] == source.id)["can_continue"] is True
            response = client.post(f"/api/meetings/{source.id}/continue", headers=headers)
            assert response.status_code == 200, response.text
            created = response.json()
            assert created["meeting_date"] == "2026-11-03"
            assert created["host_id"] == created["recorder_id"] == actor.id
            assert created["series_id"] == source.series_id
            assert created["department_id"] == source.department_id
            assert created["published_version"] == 0
            assert created["draft"]["items"] and all(row["status"] == "" for row in created["draft"]["items"])
            assert client.get("/api/reports/access", headers=headers).json()["mode"] == "self"
    finally:
        app.dependency_overrides.clear()
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == before_notifications
