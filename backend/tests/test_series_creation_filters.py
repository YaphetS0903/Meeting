from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import app
from app.models import AuditLog, MeetingSeries
from app.services.business import create_meeting
from tests.test_report_self_only import auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def test_series_type_is_saved_audited_and_validated(db, team, client):
    new_grant(db,team["colleague"],[rule("series",["create"],"self")])
    headers = auth(team["colleague"])
    response = client.post("/api/series", headers=headers, json={"name": "项目专项会", "kind": "special"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "special" and body["department_id"] == team["dep"].id
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "meeting_series.create"))
    assert audit.after["kind"] == "special"
    duplicate = client.post("/api/series", headers=headers, json={"name": "项目专项会", "kind": "special"})
    assert duplicate.json()["id"] == body["id"]
    conflict = client.post("/api/series", headers=headers, json={"name": "项目专项会", "kind": "department"})
    assert conflict.status_code == 409
    assert db.get(MeetingSeries, body["id"]).kind == "special"
    assert client.post("/api/series", headers=headers, json={"name": "非法类型", "kind": "unknown"}).status_code == 422
    legacy = client.post("/api/series", headers=headers, json={"name": "部门新例会"})
    assert legacy.status_code == 200 and legacy.json()["kind"] == "department"


def test_archive_search_matches_series_name_only_and_meeting_search_remains_available(
    db, team, client, draft_factory,
):
    draft = draft_factory()
    draft.title = "只有会议名含有独特关键词"
    meeting = create_meeting(db, team["host"], draft)
    headers = auth(team["host"])
    response = client.get("/api/meeting-series", headers=headers, params={"q": "独特关键词", "view": "current"})
    assert response.status_code == 200 and response.json()["total"] == 0
    series = client.get("/api/meeting-series", headers=headers, params={"q": "技术部", "view": "current"}).json()
    assert [row["key"] for row in series["items"]] == ["series:" + team["series"].id]
    assert series["items"][0]["kinds"] == ["department"]
    inside = client.get("/api/meetings", headers=headers, params={
        "series_key": "series:" + team["series"].id, "q": "独特关键词", "view": "current",
    }).json()
    assert [row["id"] for row in inside["items"]] == [meeting.id]


def test_newest_empty_series_is_first_and_type_filter_keeps_it_visible(db, team, client, draft_factory):
    old = team["series"]
    old.created_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    draft = draft_factory(when=date(2026, 12, 1))
    create_meeting(db, team["host"], draft)
    new_grant(db,team["colleague"],[rule("series",["create"],"self")])
    headers = auth(team["colleague"])
    created = client.post("/api/series", headers=headers, json={"name": "最新专项系列", "kind": "special"}).json()
    response = client.get("/api/meeting-series?view=current", headers=headers).json()
    assert response["items"][0]["key"] == "series:" + created["id"]
    assert response["items"][0]["meeting_count"] == 0
    assert response["items"][0]["kinds"] == ["special"]
    filtered = client.get("/api/meeting-series", headers=headers, params={
        "view": "current", "kind": "special", "department_id": team["dep"].id,
    }).json()
    assert [row["key"] for row in filtered["items"]] == ["series:" + created["id"]]
    assert client.get("/api/meeting-series", headers=auth(team["outsider"]), params={
        "view": "current", "q": "最新专项系列",
    }).json()["total"] == 0


def test_series_type_is_enforced_on_create_save_and_publish(db, team, draft_factory):
    from fastapi import HTTPException
    from app.schemas import MeetingDraft, PublishRequest
    from app.services.business import publish, save_meeting

    team["series"].kind = "special"
    draft = draft_factory()
    with pytest.raises(HTTPException, match="会议类型由所属系列固定"):
        create_meeting(db, team["host"], draft)
    draft.kind, draft.department_id = "special", None
    meeting = create_meeting(db, team["host"], draft)
    changed = MeetingDraft.model_validate(meeting.draft)
    changed.kind, changed.department_id = "department", team["dep"].id
    with pytest.raises(HTTPException, match="会议类型由所属系列固定"):
        save_meeting(db, team["host"], meeting.id, changed, 1)
    # An old or tampered API client also cannot bypass validation at publish.
    meeting.draft = changed.model_dump(mode="json")
    with pytest.raises(HTTPException, match="会议类型由所属系列固定"):
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))


def test_legacy_meeting_type_and_snapshot_survive_while_continuation_uses_series(db, team, client, draft_factory):
    from copy import deepcopy
    from app.models import MeetingVersion
    from app.schemas import MeetingDraft, PublishRequest
    from app.services.business import continue_meeting, publish, revision, save_meeting

    draft = draft_factory()
    draft.kind, draft.department_id = "special", None
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    snapshot = deepcopy(db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id)).snapshot)
    # Model an existing mixed series without rewriting any historical meetings.
    team["series"].kind = "department"
    db.flush()
    headers = auth(team["host"])
    archive = client.get("/api/meeting-series", params={"kind": "department"}, headers=headers).json()
    assert archive["items"][0]["kinds"] == ["department"]
    assert client.get("/api/meeting-series", params={"kind": "special"}, headers=headers).json()["total"] == 0
    inside = client.get("/api/meetings", params={"series_key": "series:" + team["series"].id}, headers=headers).json()
    assert inside["items"][0]["kind"] == "special"
    revision(db, team["host"], meeting.id, "补充信息")
    changed = MeetingDraft.model_validate(meeting.draft)
    changed.title = "补充后的历史会议"
    save_meeting(db, team["host"], meeting.id, changed, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, reason="补充信息", notify=False))
    assert meeting.kind == "special"
    assert db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == 1)).snapshot == snapshot
    following = continue_meeting(db, team["host"], meeting.id)
    assert following.kind == following.draft["kind"] == "department"
    assert following.department_id == team["dep"].id
