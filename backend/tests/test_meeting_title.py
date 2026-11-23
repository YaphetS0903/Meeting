from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.main import app
from app.models import AuditLog, EmployeeRoleGrant, MeetingVersion, OutboxEvent, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting
from app.services.meeting_views import revision_in_progress
from tests.test_report_self_only import auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def recorded(db, team, draft_factory):
    draft = draft_factory()
    draft.recorder_id = team["colleague"].id
    return create_meeting(db, team["host"], draft)


def rename(client, actor, meeting, **changes):
    return client.put(f"/api/meetings/{meeting.id}/title", headers=auth(actor), json={
        "title": "新的会议名称", "expected_title": meeting.title,
        "expected_draft_version": meeting.draft_version, **changes,
    })


@pytest.mark.parametrize("actor", ["host", "colleague"])
@pytest.mark.parametrize("published", [False, True])
def test_admin_and_actual_recorder_rename_without_republishing(db, team, draft_factory, client, actor, published):
    meeting = recorded(db, team, draft_factory)
    if published:
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    before = deepcopy(meeting.draft)
    snapshots = [deepcopy(v.snapshot) for v in db.scalars(select(MeetingVersion))]
    counts = {model: db.scalar(select(func.count()).select_from(model)) for model in (Task, MeetingVersion, OutboxEvent)}
    old_version, had_draft = meeting.draft_version, meeting.has_draft
    response = rename(client, team[actor], meeting, title="  修正后的周例会  ")
    assert response.status_code == 200, response.text
    assert meeting.title == meeting.draft["title"] == "修正后的周例会"
    assert meeting.draft == {**before, "title": "修正后的周例会"}
    assert meeting.draft_version == old_version + 1 and meeting.has_draft == had_draft
    assert snapshots == [v.snapshot for v in db.scalars(select(MeetingVersion))]
    assert counts == {model: db.scalar(select(func.count()).select_from(model)) for model in counts}
    assert db.scalar(select(AuditLog).where(AuditLog.action == "meeting.title.rename")).after == {"title": meeting.title}
    listing = client.get("/api/meetings", params={"q": meeting.title}, headers=auth(team[actor]))
    assert listing.json()["items"][0]["title"] == meeting.title


@pytest.mark.parametrize("capacity", ["ordinary", "department_role"])
def test_non_recorders_cannot_rename_even_with_other_meeting_permissions(db, team, draft_factory, client, capacity):
    meeting = recorded(db, team, draft_factory)
    actor = team["a"]
    if capacity == "host":
        meeting.host_id = actor.id
    if capacity == "department_role":
        db.add(EmployeeRoleGrant(user_id=actor.id, role="department_meetings"))
    db.flush()
    title = meeting.title
    response = rename(client, actor, meeting)
    assert response.status_code == 403
    assert meeting.title == title
    assert client.get("/api/meeting-title-options", headers=auth(actor)).json()["items"] == []
    assert client.get("/api/me", headers=auth(actor)).json()["can_rename_meetings"] is False


def test_options_include_cross_department_and_historical_recording_only(db, team, draft_factory, client):
    own = recorded(db, team, draft_factory)
    own.department_id = team["other"].id
    own.historical = True
    other = create_meeting(db, team["host"], draft_factory())
    db.flush()
    assert client.get("/api/me", headers=auth(team["colleague"])).json()["can_rename_meetings"] is True
    result = client.get("/api/meeting-title-options", headers=auth(team["colleague"]),
                        params={"series_key": f"series:{team['series'].id}", "q": team["series"].name})
    assert result.status_code == 200, result.text
    assert [row["id"] for row in result.json()["items"]] == [own.id]
    admin = client.get("/api/meeting-title-options?limit=1", headers=auth(team["host"])).json()
    assert admin["total"] == 2 and len(admin["items"]) == 1
    assert other.id not in {row["id"] for row in result.json()["items"]}


def test_rename_preserves_revision_state_and_blocks_stale_save(db, team, draft_factory, client):
    meeting = recorded(db, team, draft_factory)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    revision(db, team["host"], meeting.id, "预备修订")
    snapshot = deepcopy(db.scalar(select(MeetingVersion)).snapshot)
    stale, expected = MeetingDraft.model_validate(meeting.draft), meeting.draft_version
    assert rename(client, team["colleague"], meeting).status_code == 200
    assert revision_in_progress(meeting, snapshot) is False
    changed = MeetingDraft.model_validate(meeting.draft)
    changed.items[0].notes = "原有待发布的任务修改"
    save_meeting(db, team["host"], meeting.id, changed, meeting.draft_version)
    assert revision_in_progress(meeting, snapshot) is True
    response = client.put(f"/api/meetings/{meeting.id}", headers=auth(team["host"]),
                          json={"draft": stale.model_dump(mode="json"), "expected_version": expected})
    assert response.status_code == 409 and meeting.title == "新的会议名称"
    assert db.scalar(select(MeetingVersion)).snapshot == snapshot


def test_conflicting_draft_name_and_outdated_requests_preserve_all_data(db, team, draft_factory, client):
    meeting = recorded(db, team, draft_factory)
    assert rename(client, team["colleague"], meeting, expected_draft_version=999).status_code == 409
    assert rename(client, team["colleague"], meeting, expected_title="过期名称").status_code == 409
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    revision(db, team["host"], meeting.id, "准备修改名称")
    changed = MeetingDraft.model_validate(meeting.draft)
    changed.title = "尚未发布的新名称"
    save_meeting(db, team["host"], meeting.id, changed, meeting.draft_version)
    before = deepcopy(meeting.draft)
    assert rename(client, team["colleague"], meeting).status_code == 409
    assert meeting.draft == before
    assert revision_in_progress(meeting, db.scalar(select(MeetingVersion)).snapshot) is True


@pytest.mark.parametrize("title", ["", "  ", "a" * 501])
def test_name_validation(db, team, draft_factory, client, title):
    meeting = recorded(db, team, draft_factory)
    before = (meeting.title, meeting.draft_version)
    assert rename(client, team["colleague"], meeting, title=title).status_code == 422
    assert (meeting.title, meeting.draft_version) == before


def test_options_search_and_pagination_do_not_truncate_accessible_history(db, team, draft_factory, client):
    for index in range(3):
        meeting = recorded(db, team, draft_factory)
        meeting.title = f"第{index}期周会"
        meeting.meeting_date = date(2026, 11, index + 1)
    db.flush()
    response = client.get("/api/meeting-title-options?limit=1&offset=1", headers=auth(team["colleague"])).json()
    assert response["total"] == 3 and response["items"][0]["title"] == "第1期周会"
    response = client.get("/api/meeting-title-options?q=第0期", headers=auth(team["colleague"])).json()
    assert response["total"] == 1 and response["items"][0]["title"] == "第0期周会"
