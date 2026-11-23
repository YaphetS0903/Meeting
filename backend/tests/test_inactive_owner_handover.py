from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import (
    carry_forward, continue_meeting, create_meeting, owner_ids, publish,
    save_meeting, validate_draft,
)


def authorization(person):
    return {"Authorization": "Bearer " + issue_token(person)}


@pytest.mark.parametrize("include_inactive", [False, True])
def test_inactive_names_are_opt_in_editor_options_without_delivery_details(db, team, draft_factory, include_inactive):
    host, departed = team["host"], team["outsider"]
    host.is_admin = False
    departed.active = False
    departed.email, departed.wecom_id = "departed@example.test", "departed-wecom"
    departed.mobile, departed.password_hash = "private-mobile", "private-password"
    meeting = create_meeting(db, host, draft_factory())
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get("/api/meeting-people", params={
                "meeting_id": meeting.id, "include_inactive": include_inactive,
            }, headers=authorization(host))
            assert response.status_code == 200
            users = {person["id"]: person for person in response.json()["users"]}
            assert (departed.id in users) == include_inactive
            if include_inactive:
                assert users[departed.id]["active"] is False
                assert users[departed.id]["email"] is None
                assert users[departed.id]["wecom_id"] is None
            assert "private-mobile" not in response.text and "private-password" not in response.text
            assert client.get("/api/meeting-people", params={
                "meeting_id": meeting.id, "include_inactive": True,
            }, headers=authorization(team["a"])).status_code == 403
            assert client.get("/api/meeting-people", params={
                "include_inactive": True,
            }, headers=authorization(departed)).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_historical_backfill_publishes_departed_owner_then_continues_to_active_owner(db, team, draft_factory):
    host, departed, replacement = team["host"], team["outsider"], team["a"]
    host.is_admin = False
    departed.active = False
    draft = draft_factory()
    draft.items[0].owner_ids = [departed.id]
    draft.items[0].historical_owner_record = True
    original = create_meeting(db, host, draft)
    publish(db, host, original.id, PublishRequest(expected_version=original.draft_version))
    task = db.scalar(select(Task))
    assert owner_ids(db, task) == [departed.id]
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == original.id))
    assert version.snapshot["items"][0]["owner_names"] == [departed.name]
    assert version.snapshot["items"][0]["historical_owner_record"] is True
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel.in_(["email", "wecom", "native_todo"]))))

    continued = continue_meeting(db, host, original.id)
    next_draft = MeetingDraft.model_validate(continued.draft)
    assert len(next_draft.items) == 1
    review = next_draft.items[0]
    assert review.owner_ids == [departed.id] and review.task_id == task.id
    assert not review.historical_owner_record
    assert any(row["task_id"] == task.id for row in carry_forward(db, host, team["series"].id))
    review.status = "未完成"
    arrangement = review.model_copy(deep=True, update={
        "id": "replacement-arrangement", "section": "new", "status": "未到期",
        "due_date": date(2026, 11, 9), "historical_owner_record": True,
    })
    next_draft.items.append(arrangement)
    # Even a forged history marker cannot turn an existing task into new work
    # for a disabled recipient.
    with pytest.raises(HTTPException, match="人员不存在或已停用"):
        validate_draft(db, next_draft, host)
    arrangement.owner_ids = [replacement.id]
    arrangement.notes = f"原责任人：{departed.name}（已停用）"
    save_meeting(db, host, continued.id, next_draft, continued.draft_version)
    publish(db, host, continued.id, PublishRequest(expected_version=continued.draft_version, notify=False))
    assert owner_ids(db, task) == [replacement.id]
    assert task.due_date == date(2026, 11, 9)
    assert version.snapshot["items"][0]["owner_ids"] == [departed.id]
    assert not departed.active


@pytest.mark.parametrize("field", ["owner_ids", "verifier_id"])
def test_history_marker_does_not_allow_missing_people_or_inactive_verifiers(db, team, draft_factory, field):
    draft = draft_factory()
    draft.items[0].historical_owner_record = True
    if field == "owner_ids":
        draft.items[0].owner_ids = ["missing-person"]
    else:
        team["outsider"].active = False
        draft.items[0].verifier_id = team["outsider"].id
    with pytest.raises(HTTPException, match="人员不存在或已停用"):
        validate_draft(db, draft, team["host"])


def test_historical_owner_can_preview_and_publish_through_the_editor_api(db, team, draft_factory):
    departed = team["outsider"]
    departed.active = False
    draft = draft_factory()
    draft.items[0].owner_ids = [departed.id]
    draft.items[0].historical_owner_record = True
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = authorization(team["host"])
            created = client.post("/api/meetings", headers=headers, json=draft.model_dump(mode="json"))
            assert created.status_code == 200, created.text
            meeting = created.json()
            preview = client.post(f"/api/meetings/{meeting['id']}/preview", headers=headers, json={
                "expected_version": meeting["draft_version"], "format": "xlsx",
            })
            assert preview.status_code == 200, preview.text
            assert preview.content.startswith(b"PK")
            result = client.post(f"/api/meetings/{meeting['id']}/publish", headers=headers, json={
                "expected_version": meeting["draft_version"], "notify": False, "shared_recipients": True,
            })
            assert result.status_code == 200, result.text
            assert result.json()["published_version"] == 1
            assert owner_ids(db, db.scalar(select(Task))) == [departed.id]
            assert not departed.active
    finally:
        app.dependency_overrides.clear()


def test_draft_can_record_inactive_original_owner_then_publish_only_active_replacement(db, team, draft_factory):
    host, departed, replacement = team["host"], team["outsider"], team["a"]
    host.is_admin = False
    departed.active = False
    draft = draft_factory()
    draft.items[0].owner_ids = [departed.id]
    draft.items[0].notes = "原有备注"
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = authorization(host)
            created = client.post("/api/meetings", headers=headers, json=draft.model_dump(mode="json"))
            assert created.status_code == 200, created.text
            meeting = created.json()
            assert meeting["draft"]["items"][0]["owner_ids"] == [departed.id]
            blocked = client.post(f"/api/meetings/{meeting['id']}/publish", headers=headers, json={
                "expected_version": meeting["draft_version"], "notify": False,
            })
            assert blocked.status_code == 422
            assert not list(db.scalars(select(Task)))
            assert not list(db.scalars(select(OutboxEvent)))
            draft.items[0].owner_ids = [replacement.id]
            draft.items[0].notes += f"\n原责任人：{departed.name}（已停用）"
            saved = client.put(f"/api/meetings/{meeting['id']}", headers=headers, json={
                "expected_version": meeting["draft_version"], "draft": draft.model_dump(mode="json"),
            })
            assert saved.status_code == 200, saved.text
            published = client.post(f"/api/meetings/{meeting['id']}/publish", headers=headers, json={
                "expected_version": saved.json()["draft_version"], "notify": False,
            })
            assert published.status_code == 200, published.text
            task = db.scalar(select(Task))
            assert owner_ids(db, task) == [replacement.id]
            detail = client.get(f"/api/meetings/{meeting['id']}", headers=headers).json()
            assert detail["versions"][0]["snapshot"]["items"][0]["notes"] == draft.items[0].notes
            assert not departed.active
            assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel.in_(["email", "wecom"]))))
    finally:
        app.dependency_overrides.clear()
