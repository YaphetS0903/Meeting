from copy import deepcopy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Membership, Task, User
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, validate_draft


def test_ordinary_creator_can_choose_companywide_host_and_keep_editing_via_api(db, team, draft_factory):
    from app.models import OutboxEvent

    actor, host = team["colleague"], team["outsider"]
    draft = draft_factory()
    draft.host_id, draft.recorder_id = host.id, actor.id
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(actor)}
            assert host.id not in {person["id"] for person in client.get("/api/directory", headers=headers).json()["users"]}
            assert host.id in {person["id"] for person in client.get("/api/meeting-people", headers=headers).json()["users"]}
            created = client.post("/api/meetings", headers=headers, json=draft.model_dump(mode="json"))
            assert created.status_code == 200, created.text
            meeting = created.json()
            assert meeting["host_id"] == host.id and meeting["recorder_id"] == actor.id
            draft.title = "跨部门主持的会议草稿"
            saved = client.put(f"/api/meetings/{meeting['id']}", headers=headers, json={
                "expected_version": meeting["draft_version"], "draft": draft.model_dump(mode="json"),
            })
            assert saved.status_code == 200, saved.text
            assert saved.json()["draft"]["host_id"] == host.id
            assert client.get(f"/api/meetings/{meeting['id']}", headers=headers).json()["can_edit"]
            assert client.get(f"/api/meetings/{meeting['id']}", headers={
                "Authorization": "Bearer " + issue_token(host),
            }).json()["can_edit"]
            assert not list(db.scalars(select(OutboxEvent)))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("admin", [True, False])
def test_department_meeting_can_add_active_companywide_owners_without_inviting(db, team, draft_factory, admin):
    draft = draft_factory()
    team["host"].is_admin = admin
    draft.items[0].owner_ids = [team["outsider"].id]
    assert team["outsider"].id not in draft.attendee_ids
    validate_draft(db, draft, team["host"])


@pytest.mark.parametrize("identifier", ["inactive", "missing"])
def test_new_companywide_owner_must_exist_and_be_active(db, team, draft_factory, identifier):
    draft = draft_factory()
    team["outsider"].active = False
    draft.items[0].owner_ids = [team["outsider"].id if identifier == "inactive" else "missing"]
    with pytest.raises(HTTPException, match="人员不存在或已停用"):
        validate_draft(db, draft, team["host"])


def test_nonattendee_cross_department_verifier_can_save_publish_and_view_task(db, team, draft_factory):
    from app.services.access import task_access
    from app.services.business import save_meeting

    team["host"].is_admin = False
    draft = draft_factory()
    meeting = create_meeting(db, team["host"], draft)
    draft.items[0].verifier_id = team["outsider"].id
    assert team["outsider"].id not in draft.attendee_ids
    validate_draft(db, draft, team["host"])
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    task = db.scalar(select(Task))
    assert task.verifier_id == team["outsider"].id
    task_access(db, team["outsider"], task)


def test_new_verifier_must_be_active_or_existing_historical_person(db, team, draft_factory):
    draft = draft_factory()
    team["outsider"].active = False
    draft.items[0].verifier_id = team["outsider"].id
    with pytest.raises(HTTPException, match="人员不存在或已停用"):
        validate_draft(db, draft, team["host"])


def test_secondary_department_members_and_outside_host_are_assignable(db, team, draft_factory):
    draft = draft_factory()
    team["host"].department_id = team["other"].id
    db.add(Membership(user_id=team["outsider"].id, department_id=team["dep"].id))
    db.flush()
    draft.items[0].owner_ids = [team["host"].id, team["outsider"].id]
    validate_draft(db, draft, team["host"])
    team["outsider"].active = False
    with pytest.raises(HTTPException, match="停用"):
        validate_draft(db, draft, team["host"])


def test_special_and_historical_minutes_keep_their_existing_owner_scope(db, team, draft_factory):
    draft = draft_factory()
    draft.items[0].owner_ids = [team["outsider"].id]
    validate_draft(db, draft, team["host"], historical=True)
    draft.kind = "special"
    draft.department_id = None
    validate_draft(db, draft, team["host"])


@pytest.mark.parametrize("admin", [True, False])
def test_invited_cross_department_participant_can_own_specific_tasks(db, team, draft_factory, admin):
    team["host"].is_admin = admin
    draft = draft_factory()
    draft.attendee_ids = [team["outsider"].id]
    meeting = create_meeting(db, team["host"], draft)
    assert team["outsider"].id not in meeting.draft["items"][0]["owner_ids"]
    draft.items[0].owner_ids.append(team["outsider"].id)
    validate_draft(db, draft, team["host"])
    draft.attendee_ids = []
    validate_draft(db, draft, team["host"])
    draft.attendee_ids = [team["outsider"].id]
    team["outsider"].active = False
    with pytest.raises(HTTPException, match="停用"):
        validate_draft(db, draft, team["host"])


def test_meeting_people_roster_is_fresh_and_restricted_to_editors_for_existing_meeting(db, team, draft_factory):
    team["host"].is_admin = False
    team["outsider"].name = "金丽君"
    team["outsider"].mobile = "private-mobile"
    team["outsider"].password_hash = "private-password-hash"
    meeting = create_meeting(db, team["host"], draft_factory())
    inactive = User(name="离职成员", active=False, department_id=team["other"].id)
    db.add(inactive)
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            def headers(person):
                return {"Authorization": "Bearer " + issue_token(person)}
            host = headers(team["host"])
            assert client.get("/api/meeting-people").status_code == 401
            assert team["outsider"].id not in {u["id"] for u in client.get("/api/directory", headers=host).json()["users"]}
            result = client.get("/api/meeting-people", params={"meeting_id": meeting.id}, headers=host)
            assert result.status_code == 200
            users = {u["id"]: u for u in result.json()["users"]}
            assert users[team["outsider"].id]["name"] == "金丽君"
            assert "财务部" in users[team["outsider"].id]["department_name"]
            assert inactive.id not in users
            assert "private-mobile" not in result.text and "private-password-hash" not in result.text
            assert client.get("/api/meeting-people", params={"meeting_id": meeting.id}, headers=headers(team["a"])).status_code == 403
            assert client.get("/api/meeting-people", params={"meeting_id": "missing"}, headers=host).status_code == 404
            assert client.get("/api/meeting-people", headers=host).status_code == 200
            meeting.draft = {**meeting.draft, "attendee_ids": [team["outsider"].id]}
            db.flush()
            own = client.get("/api/directory", headers=host).json()["users"]
            assert team["outsider"].id in {u["id"] for u in own}
            reader = client.get("/api/directory", headers=headers(team["a"])).json()["users"]
            assert team["outsider"].id not in {u["id"] for u in reader}
    finally:
        app.dependency_overrides.clear()


def test_publishing_uninvited_external_owner_creates_their_task_and_selected_notifications(db, team, draft_factory):
    from app.models import OutboxEvent
    from app.services.business import owner_ids

    team["host"].is_admin = False
    team["outsider"].email = "invitee@example.com"
    team["outsider"].wecom_id = "wecom-invitee"
    draft = draft_factory()
    draft.recipient_ids = [team["outsider"].id]
    draft.items[0].owner_ids = [team["outsider"].id]
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, shared_recipients=True))
    task = db.scalar(select(Task))
    assert owner_ids(db, task) == [team["outsider"].id]
    mail = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "email"))
    assert mail.payload["recipient_ids"] == [team["outsider"].id]
    wecom = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "wecom"))
    assert wecom.payload["user_ids"] == [team["outsider"].id]


def test_host_can_save_and_publish_uninvited_companywide_owner_via_api(db, team, draft_factory):
    from app.models import OutboxEvent
    from app.services.business import owner_ids

    team["host"].is_admin = False
    outsider = team["outsider"]
    outsider.email, outsider.wecom_id = "outsider@example.test", "wecom-outsider"
    meeting = create_meeting(db, team["host"], draft_factory())
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].owner_ids = [outsider.id]
    draft.recipient_ids = [outsider.id]
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            saved = client.put(f"/api/meetings/{meeting.id}", headers=headers, json={
                "expected_version": meeting.draft_version, "draft": draft.model_dump(mode="json"),
            })
            assert saved.status_code == 200, saved.text
            assert saved.json()["draft"]["attendee_ids"] == []
            published = client.post(f"/api/meetings/{meeting.id}/publish", headers=headers, json={
                "expected_version": saved.json()["draft_version"], "shared_recipients": True,
            })
            assert published.status_code == 200, published.text
            assert owner_ids(db, db.scalar(select(Task))) == [outsider.id]
            assert {event.channel for event in db.scalars(select(OutboxEvent))} >= {"wecom", "email"}
            assert client.get(f"/api/meetings/{meeting.id}", headers={
                "Authorization": "Bearer " + issue_token(outsider),
            }).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_existing_published_owner_is_preserved_and_new_active_outsider_is_allowed(db, team, draft_factory):
    original = draft_factory().model_dump(mode="json")
    original["items"][0]["owner_ids"] = [team["outsider"].id]
    draft = MeetingDraft.model_validate(deepcopy(original))
    validate_draft(db, draft, team["host"], previous=original)
    another = User(name="另一外部门人员", department_id=team["other"].id)
    db.add(another)
    db.flush()
    draft.items[0].owner_ids.append(another.id)
    validate_draft(db, draft, team["host"], previous=original)
    team["outsider"].active = False
    draft.items[0].owner_ids = [team["outsider"].id]
    validate_draft(db, draft, team["host"], previous=original)


def test_linked_review_retains_original_owner_after_department_transfer(db, team, draft_factory):
    source = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task))
    team["a"].department_id = team["other"].id
    draft = draft_factory(section="review", task_id=task.id, task_version=task.version)
    validate_draft(db, draft, team["host"])
    draft.items[0].owner_ids.append(team["outsider"].id)
    validate_draft(db, draft, team["host"])


def test_directory_exposes_secondary_memberships_only_within_visible_departments(db, team):
    db.add(Membership(user_id=team["outsider"].id, department_id=team["dep"].id))
    db.add(Membership(user_id=team["colleague"].id, department_id=team["other"].id))
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            response = client.get("/api/directory", headers=headers)
            assert response.status_code == 200
            people = {person["id"]: person for person in response.json()["users"]}
            assert set(people[team["outsider"].id]["department_ids"]) == {team["dep"].id, team["other"].id}
            assert "password_hash" not in people[team["outsider"].id]
            team["host"].is_admin = False
            headers = {"Authorization": "Bearer " + issue_token(team["a"])}
            data = client.get("/api/directory", headers=headers).json()
            visible_departments = {row["id"] for row in data["departments"]}
            assert team["other"].id not in visible_departments
            people = {person["id"]: person for person in data["users"]}
            assert people[team["outsider"].id]["department_ids"] == [team["dep"].id]
    finally:
        app.dependency_overrides.clear()
