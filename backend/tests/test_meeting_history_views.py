from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from datetime import date, datetime, timezone

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import AuditLog, Meeting, MeetingSeries, MeetingVersion, OutboxEvent
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish, revision


def make_meeting(db, team, title, day, *, series=None, department=None, historical=False):
    draft = MeetingDraft(
        title=title, meeting_date=date(2026, 10, day),
        department_id=department or team["dep"].id,
        series_id=series.id if series else None,
        host_id=team["host"].id, items=[],
    )
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def test_current_and_history_partition_before_search_dates_and_department_filters(db, team):
    series = team["series"]
    old = make_meeting(db, team, "旧会议关键词", 10, series=series)
    current = make_meeting(db, team, "最新会议", 28, series=series, department=team["other"].id)
    imported = make_meeting(db, team, "日期更新的导入记录", 30, series=series, department=team["other"].id, historical=True)
    loose_first = make_meeting(db, team, "独立会议甲", 11)
    loose_second = make_meeting(db, team, "独立会议乙", 12)
    count_before = db.scalar(select(func.count()).select_from(Meeting))
    versions_before = db.scalar(select(func.count()).select_from(MeetingVersion))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            current_rows = client.get("/api/meetings?view=current", headers=headers).json()
            assert {row["id"] for row in current_rows["items"]} == {imported.id, loose_first.id, loose_second.id}
            history = client.get("/api/meetings?view=history", headers=headers).json()
            assert {row["id"] for row in history["items"]} == {old.id, current.id}
            for filters in ({"q": "旧会议关键词"}, {"end": "2026-10-10"}, {"department_id": team["dep"].id, "series_key": "series:" + series.id}):
                response = client.get("/api/meetings", params={"view": "current", **filters}, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()["total"] == 0
            assert client.get("/api/meetings", params={"view": "history", "q": "旧会议关键词"}, headers=headers).json()["items"][0]["id"] == old.id
            # Old integrations may explicitly request the complete archive.
            assert client.get("/api/meetings", headers=headers).json()["total"] == 5
            assert client.get("/api/meetings?view=invalid", headers=headers).status_code == 422
            assert client.get(f"/api/meetings/{old.id}", headers=headers).status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert db.scalar(select(func.count()).select_from(Meeting)) == count_before
    assert db.scalar(select(func.count()).select_from(MeetingVersion)) == versions_before


def test_history_department_counts_are_permission_and_series_scoped(db, team):
    new_grant(db,team["colleague"],[rule("meetings",["view"])])
    tech = team["series"]
    finance = MeetingSeries(name="财务周会", department_id=team["other"].id)
    db.add(finance)
    db.flush()
    old = make_meeting(db, team, "技术旧会", 10, series=tech)
    make_meeting(db, team, "技术新会", 28, series=tech)
    make_meeting(db, team, "财务历史", 9, series=finance, department=team["other"].id, historical=True)
    make_meeting(db, team, "财务最新", 20, series=finance, department=team["other"].id)
    special = create_meeting(db, team["host"], MeetingDraft(
        title="历史专项草稿", meeting_date=date(2026, 10, 8),
        department_id=team["dep"].id, host_id=team["host"].id, items=[],
    ), historical=True)
    special.department_id = None
    # Visibility must not be granted merely because a user requests a department.
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            admin = {"Authorization": "Bearer " + issue_token(team["host"])}
            member = {"Authorization": "Bearer " + issue_token(team["colleague"])}
            summary = client.get("/api/history-meeting-departments", headers=admin).json()
            assert summary["meeting_total"] == 3
            assert {row["key"] for row in summary["items"]} == {team["dep"].id, team["other"].id, "none"}
            scoped = client.get("/api/history-meeting-departments", params={"series_key": "series:" + tech.id}, headers=admin).json()
            assert scoped["meeting_total"] == 1 and scoped["items"][0]["key"] == team["dep"].id
            summary = client.get("/api/history-meeting-departments", headers=member).json()
            assert summary["meeting_total"] == 1
            assert [row["key"] for row in summary["items"]] == [team["dep"].id]
            assert client.get("/api/meetings", params={"view": "history", "department_id": team["other"].id}, headers=member).json()["total"] == 0
            assert client.get("/api/meetings", params={"view": "history", "department_id": "none"}, headers=admin).json()["items"][0]["id"] == special.id
            assert client.get(f"/api/meetings/{old.id}", headers=member).status_code == 200
            assert client.get("/api/history-meeting-departments?series_key=invalid", headers=admin).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_current_series_counts_include_latest_import_and_history_paginates(db, team):
    old = make_meeting(db, team, "第一场", 7, series=team["series"])
    second = make_meeting(db, team, "第二场", 14, series=team["series"])
    current = make_meeting(db, team, "第三场", 28, series=team["series"])
    imported_series = MeetingSeries(name="只有历史的系列")
    empty_series = MeetingSeries(name="未开始的系列")
    db.add_all([imported_series, empty_series])
    db.flush()
    make_meeting(db, team, "导入旧单", 1, series=imported_series, historical=True)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            summary = client.get("/api/meeting-series?view=current", headers=headers).json()
            groups = {row["key"]: row for row in summary["items"]}
            assert groups["series:" + team["series"].id]["meeting_count"] == 1
            assert groups["series:" + team["series"].id]["latest_date"] == "2026-10-28"
            assert groups["series:" + imported_series.id]["meeting_count"] == 1
            assert groups["series:" + empty_series.id]["meeting_count"] == 0
            params = {"view": "history", "series_key": "series:" + team["series"].id, "limit": 1}
            page_one = client.get("/api/meetings", params=params, headers=headers).json()
            page_two = client.get("/api/meetings", params={**params, "offset": 1}, headers=headers).json()
            assert page_one["total"] == page_two["total"] == 2
            assert [page_one["items"][0]["id"], page_two["items"][0]["id"]] == [second.id, old.id]
            assert current.id not in {page_one["items"][0]["id"], page_two["items"][0]["id"]}
    finally:
        app.dependency_overrides.clear()


def test_same_day_latest_uses_creation_time_and_id_as_stable_tie_breakers(db, team):
    first = make_meeting(db, team, "同日甲", 28, series=team["series"])
    second = make_meeting(db, team, "同日乙", 28, series=team["series"])
    first.created_at = second.created_at = datetime(2026, 10, 28, tzinfo=timezone.utc)
    db.flush()
    expected = max(first.id, second.id)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            assert client.get("/api/meetings?view=current", headers=headers).json()["items"][0]["id"] == expected
            assert client.get("/api/meetings?view=history", headers=headers).json()["items"][0]["id"] != expected
    finally:
        app.dependency_overrides.clear()


def test_new_drafts_never_archive_latest_published_meeting_and_reads_create_nothing(db, team):
    old = make_meeting(db, team, "上期会议", 21, series=team["series"])
    latest = make_meeting(db, team, "28号会议", 28, series=team["series"])
    revision(db, team["host"], latest.id, "待完成的备注修订")
    pending = continue_meeting(db, team["host"], latest.id)
    pending.meeting_date = date(2026, 10, 29)
    db.flush()
    before = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
              for model in (Meeting, MeetingVersion, AuditLog, OutboxEvent)}
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            params = {"view": "current", "series_key": "series:" + team["series"].id}
            for _ in range(3):
                detail = client.get(f"/api/meetings/{latest.id}", headers=headers)
                assert detail.status_code == 200
                assert detail.json()["published_version"] == 1
                assert [row["id"] for row in client.get("/api/meetings", params=params, headers=headers).json()["items"]] == [latest.id, pending.id]
            history = client.get("/api/meetings", params={**params, "view": "history"}, headers=headers).json()
            assert [row["id"] for row in history["items"]] == [old.id]
            assert client.get("/api/meetings", params={**params, "publication": "draft"}, headers=headers).json()["items"][0]["id"] == pending.id
            assert client.get("/api/meetings", params={**params, "q": "上期会议"}, headers=headers).json()["total"] == 0
    finally:
        app.dependency_overrides.clear()
    assert {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Meeting, MeetingVersion, AuditLog, OutboxEvent)} == before


@pytest.mark.parametrize("historical", [False, True])
def test_only_publication_moves_previous_minutes_to_history_and_keeps_other_drafts(db, team, historical):
    latest = make_meeting(db, team, "当前已发布会议", 28, series=team["series"], historical=historical)
    drafts = []
    for day in (29, 30):
        draft = MeetingDraft(title=f"{day}号待发布会议", meeting_date=date(2026, 10, day),
                             series_id=team["series"].id, department_id=team["dep"].id,
                             host_id=team["host"].id, items=[])
        drafts.append(create_meeting(db, team["host"], draft))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            params = {"view": "current", "series_key": "series:" + team["series"].id}
            rows = client.get("/api/meetings", params=params, headers=headers).json()["items"]
            assert [row["id"] for row in rows] == [latest.id, drafts[1].id, drafts[0].id]
            publish(db, team["host"], drafts[0].id, PublishRequest(expected_version=1, notify=False))
            rows = client.get("/api/meetings", params=params, headers=headers).json()["items"]
            assert [row["id"] for row in rows] == [drafts[0].id, drafts[1].id]
            history = client.get("/api/meetings", params={**params, "view": "history"}, headers=headers).json()
            assert [row["id"] for row in history["items"]] == [latest.id]
    finally:
        app.dependency_overrides.clear()


def test_series_without_publication_keeps_all_drafts_in_workbench(db, team):
    drafts = [create_meeting(db, team["host"], MeetingDraft(
        title=f"草稿{day}", meeting_date=date(2026, 10, day), series_id=team["series"].id,
        department_id=team["dep"].id, host_id=team["host"].id, items=[],
    )) for day in (28, 29)]
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            assert {row["id"] for row in client.get("/api/meetings?view=current", headers=headers).json()["items"]} == {row.id for row in drafts}
            assert client.get("/api/meetings?view=history", headers=headers).json()["total"] == 0
    finally:
        app.dependency_overrides.clear()
