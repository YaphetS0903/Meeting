from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import ImportBatch, ImportCandidate, ImportSource, OutboxEvent, Task, ValidationIssue
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def auth(person):
    return {"Authorization": "Bearer " + issue_token(person)}


def issue(db, team, draft, historical=False):
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    return meeting


def test_publication_filter_separates_drafts_published_and_revising(client, db, team, draft_factory):
    draft = create_meeting(db, team["host"], draft_factory())
    published = issue(db, team, draft_factory())
    revising = issue(db, team, draft_factory())
    revision(db, team["host"], revising.id, "补记决议")
    for state, expected in [("draft", draft), ("published", published), ("revising", revising)]:
        result = client.get("/api/meetings", params={"publication": state}, headers=auth(team["host"])).json()
        assert result["total"] == 1 and result["items"][0]["id"] == expected.id
    assert (
        client.get("/api/meetings", params={"publication": "draft"}, headers=auth(team["outsider"])).json()[
            "total"
        ]
        == 0
    )


def test_detail_stats_preserve_versions_and_exclude_new_arrangements(client, db, team, draft_factory):
    draft = draft_factory(section="review", status="已完成")
    draft.items.append(
        MeetingItem(
            section="new",
            content="本次新安排已完成也不算考核",
            owner_ids=[team["a"].id],
            due_date=date(2026, 8, 17),
            status="已完成",
        )
    )
    meeting = issue(db, team, draft, historical=True)
    revision(db, team["host"], meeting.id, "核实历史结果")
    revised = MeetingDraft.model_validate(meeting.draft)
    revised.items[0].status = "未完成"
    save_meeting(db, team["host"], meeting.id, revised, 2)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="核实历史结果"))
    data = client.get(f"/api/meetings/{meeting.id}", headers=auth(team["a"])).json()
    old, current = data["assessments"]["1"], data["assessments"]["2"]
    assert old["rate"] == 1 and not old["effective"]
    assert current["rate"] == 0 and current["effective"]
    assert old["person_count"] == current["person_count"] == 2
    assert current["items"] == 1 and len(current["details"]) == 2
    assert client.get(f"/api/meetings/{meeting.id}", headers=auth(team["outsider"])).status_code == 403


def test_detail_file_issues_are_scoped_and_originals_admin_only(client, db, team, draft_factory):
    meeting = issue(db, team, draft_factory(section="review", status="未完成"), historical=True)
    batch = ImportBatch(name="历史来源", creator_id=team["host"].id)
    db.add(batch)
    db.flush()
    source = ImportSource(batch_id=batch.id, filename="多个部门原件.xlsx", sha256="a" * 64)
    unrelated = ImportSource(batch_id=batch.id, filename="无关原件.xlsx", sha256="b" * 64)
    db.add_all([source, unrelated])
    db.flush()
    candidate = ImportCandidate(
        source_id=source.id,
        sheet_name="当前会议",
        original={},
        corrected={},
        content_hash="c" * 64,
        state="confirmed",
        meeting_id=meeting.id,
    )
    db.add(candidate)
    db.add_all(
        [
            ValidationIssue(source_id=source.id, code="parse_error", message="对象需核实"),
            ValidationIssue(source_id=unrelated.id, code="parse_error", message="无关异常"),
        ]
    )
    db.flush()
    own = client.get(f"/api/meetings/{meeting.id}", headers=auth(team["a"])).json()
    assert own["pending_issue_count"] == 1
    assert own["source_issue_details"] == own["import_sources"] == []
    admin = client.get(f"/api/meetings/{meeting.id}", headers=auth(team["host"])).json()
    assert len(admin["source_issue_details"]) == len(admin["import_sources"]) == 1
    assert admin["import_sources"][0]["candidates"][0]["id"] == candidate.id
    assert "无关原件" not in str(admin)
    listing = client.get("/api/meetings", headers=auth(team["a"])).json()
    assert listing["items"][0]["pending_issue_count"] == 1


def test_task_links_and_notifications_do_not_leak_other_meetings(client, db, team, draft_factory):
    first = issue(db, team, draft_factory())
    task = db.scalar(select(Task).where(Task.source_meeting_id == first.id))
    other = issue(db, team, draft_factory())
    other_task = db.scalar(select(Task).where(Task.source_meeting_id == other.id))
    db.add(
        OutboxEvent(
            event_key="digest",
            channel="wecom",
            payload={
                "title": "每日汇总",
                "reminder_task_ids": [task.id, other_task.id],
                "user_ids": [team["a"].id],
            },
        )
    )
    data = client.get(f"/api/meetings/{first.id}", headers=auth(team["host"])).json()
    assert len(data["task_links"]) == 1 and data["task_links"][0]["task_id"] == task.id
    assert len(data["task_notifications"]) == 2
    assert all(event["task_codes"] == [task.code] for event in data["task_notifications"])
    reader = client.get(f"/api/meetings/{first.id}", headers=auth(team["a"])).json()
    assert reader["task_notifications"] == []


def test_cross_department_host_can_manage_tasks_from_own_meeting(client, db, team, draft_factory):
    draft = draft_factory()
    draft.host_id = team["outsider"].id
    draft.items[0].verifier_id = team["host"].id
    meeting = issue(db, team, draft)
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    r = client.get(f"/api/tasks/{task.id}", headers=auth(team["outsider"]))
    assert r.status_code == 200 and r.json()["can_edit"]


def test_person_and_type_filters_match_report_export(client, db, team, draft_factory):
    issue(db, team, draft_factory(section="review", status="已完成"), historical=True)
    second = draft_factory(section="review", status="未完成")
    second.kind = "special"
    issue(db, team, second, historical=True)
    params = {"start": "2026-08-01", "end": "2026-08-31", "person_id": team["a"].id, "kind": "special"}
    report = client.get("/api/reports", params=params, headers=auth(team["host"])).json()
    assert report["summary"]["person_count"] == 1 and report["summary"]["rate"] == 0
    assert report["details"][0]["user_name"] == team["a"].name
    excel = client.get("/api/reports/export", params=params, headers=auth(team["host"]))
    book = openpyxl.load_workbook(BytesIO(excel.content))
    assert book["考核明细"].max_row == 2
    assert book["考核明细"]["D2"].value == team["a"].name
    assert book["考核明细"]["I2"].value == "未完成"


def test_meeting_list_filters_by_review_status_before_pagination(client, db, team, draft_factory):
    completed = issue(db, team, draft_factory(section="review", status="已完成"), historical=True)
    completed_two = issue(db, team, draft_factory(section="review", status="已完成"), historical=True)
    issue(db, team, draft_factory(section="review", status="未完成"), historical=True)
    issue(db, team, draft_factory(section="new", status="已完成"))

    response = client.get(
        "/api/meetings",
        params={"status": "已完成", "offset": 0, "limit": 1},
        headers=auth(team["host"]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] in {completed.id, completed_two.id}

    response = client.get(
        "/api/meetings", params={"status": "未完成"}, headers=auth(team["outsider"])
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0
