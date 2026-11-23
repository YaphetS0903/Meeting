from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.core.db import get_db
from app.core.security import issue_token
from app.extra_api import Retry, cancel_notification
from app.main import app
from app.models import (
    AuditLog,
    MeetingVersion,
    NotificationLog,
    OutboxEvent,
    Task,
    TaskOwner,
    TaskVersion,
)
from app.schemas import ApplyCompletion, MeetingDraft, PublishRequest
from app.services.business import (
    apply_completion,
    carry_forward,
    continue_meeting,
    create_meeting,
    publish,
    save_meeting,
    validate_draft,
)
from app.services.reporting import report


def publish_draft(db, team, draft, *, historical=False):
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    return meeting


def test_disabled_verifier_keeps_work_and_replacement_is_versioned_on_publish(db, team, draft_factory):
    original = publish_draft(db, team, draft_factory(verifier_id=team["colleague"].id))
    task = db.scalar(select(Task))
    request = apply_completion(db, team["a"], task.id, ApplyCompletion(
        expected_version=task.version, idempotency_key="disabled-verifier-check", explanation="完成待验证"))
    task_version = task.version
    team["colleague"].active = False
    followup = continue_meeting(db, team["host"], original.id)
    draft = MeetingDraft.model_validate(followup.draft)
    assert len(draft.items) == 1 and draft.items[0].task_id == task.id
    draft.items[0].status = "未完成"
    with pytest.raises(HTTPException, match="验证人.*已停用"):
        validate_draft(db, draft, team["host"])
    draft.items[0].verifier_id = team["host"].id
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    assert task.verifier_id == team["colleague"].id  # Saving never hands over live work.
    publish(db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False))
    assert task.verifier_id == team["host"].id and task.version == task_version + 1
    assert request.state == "invalidated"
    assert db.scalar(select(TaskVersion).where(TaskVersion.task_id == task.id, TaskVersion.number == task.version)).reason == "会议替换已停用验证人"
    original_snapshot = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == original.id)).snapshot
    assert original_snapshot["items"][0]["verifier_id"] == team["colleague"].id
    assert not team["colleague"].active


def test_unactivated_history_preserves_disabled_original_owners_and_requires_handover(db, team, draft_factory):
    draft = draft_factory()
    draft.items[0].owner_ids = [team["a"].id]
    original = publish_draft(db, team, draft, historical=True)
    team["a"].active = False
    carried = carry_forward(db, team["host"], team["series"].id)
    assert len(carried) == 1 and carried[0]["owner_ids"] == [team["a"].id]
    followup = continue_meeting(db, team["host"], original.id)
    current = MeetingDraft.model_validate(followup.draft)
    assert len(current.items) == 1 and current.items[0].owner_ids == [team["a"].id]
    current.items[0].status = "未完成"
    validate_draft(db, current, team["host"])
    arrangement = current.items[0].model_copy(deep=True, update={"id": "handover-row", "section": "new", "due_date": date(2026, 12, 31)})
    current.items.append(arrangement)
    with pytest.raises(HTTPException, match="人员不存在或已停用"):
        validate_draft(db, current, team["host"])
    arrangement.owner_ids = [team["b"].id]
    save_meeting(db, team["host"], followup.id, current, followup.draft_version)
    publish(db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False))
    task = db.scalar(select(Task))
    assert set(db.scalars(select(TaskOwner.user_id).where(TaskOwner.task_id == task.id))) == {team["b"].id}
    assert not team["a"].active


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


def test_inbox_overview_filters_totals_agree_without_changing_assessments(client, db, team, draft_factory, monkeypatch):
    monkeypatch.setattr("app.services.task_inbox.today", lambda: date(2026, 11, 5))
    meeting = publish_draft(db, team, draft_factory())
    task = db.scalar(select(Task))
    inbox = client.get("/api/tasks", params={"mine": True, "current": True, "offset": 0}, headers=auth(team["a"])).json()
    overview = client.get("/api/task-overview", headers=auth(team["host"])).json()
    assert inbox["items"][0]["todo_status"] == overview["items"][0]["status"] == "到期未验证"
    assert overview["summary"]["unverified_due"] == 1 and overview["summary"]["overdue"] == 0
    assert client.get("/api/task-overview", params={"status": "未完成"}, headers=auth(team["host"])).json()["total"] == 0
    assert client.get("/api/task-overview", params={"status": "到期未验证"}, headers=auth(team["host"])).json()["total"] == 1
    followup = continue_meeting(db, team["host"], meeting.id)
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    assert client.get("/api/task-overview", headers=auth(team["host"])).json()["items"][0]["status"] == "到期未验证"
    publish(db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False))
    assert client.get("/api/task-overview", params={"status": "未完成"}, headers=auth(team["host"])).json()["total"] == 1
    assert client.get("/api/tasks", params={"mine": True, "current": True, "state": "未完成"}, headers=auth(team["a"])).json()[0]["id"] == task.id


def test_inbox_ownership_pagination_query_count_does_not_grow_per_task(client, db, team):
    for index in range(85):
        task = Task(source_item_id=f"paged-{index}", code=f"PAGED-{index}", content=f"分页任务{index}", verifier_id=team["host"].id, issuer_id=team["host"].id)
        db.add(task)
        db.flush()
        db.add(TaskOwner(task_id=task.id, user_id=team["a"].id))
    db.flush()
    statements = []
    engine = db.get_bind()
    def count(*args):
        statements.append(args[2])
    event.listen(engine, "before_cursor_execute", count)
    try:
        result = client.get("/api/tasks", params={"mine": True, "current": True, "offset": 20, "limit": 20}, headers=auth(team["a"]))
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert result.status_code == 200
    assert result.json()["total"] == 85 and len(result.json()["items"]) == 20
    assert len(statements) < 25
    empty = client.get("/api/tasks", params={"mine": True, "current": True, "offset": 0}, headers=auth(team["b"])).json()
    assert empty == {"items": [], "total": 0}


def test_report_compact_summary_paged_details_and_export_contract_match(db, team, draft_factory):
    draft = draft_factory(section="review", status="未完成")
    publish_draft(db, team, draft, historical=True)
    args = (db, team["host"], date(2026, 1, 1), date(2026, 12, 31))
    full = report(*args, group_by="department_people")
    compact = report(*args, group_by="department_people", include_details=False)
    assert compact["summary"] == full["summary"] and compact["groups"] == full["groups"]
    assert compact["details"] == [] and compact["details_paginated"] is True
    page = report(*args, group_by="department_people", details_only=True, offset=1, limit=1)
    assert page["details"] == full["details"][1:2] and page["details_total"] == 2
    with pytest.raises(HTTPException):
        report(db, team["a"], args[2], args[3], group_by="department_people", person_id=team["b"].id, details_only=True)


def test_notification_cancellation_preserves_event_and_attempts_with_audit(db, team):
    item = OutboxEvent(event_key="isolated-cancel", channel="wecom", state="blocked", attempts=2,
                       last_error="未绑定", payload={"title": "隔离验证"})
    db.add(item)
    db.flush()
    assert cancel_notification(item.id, Retry(reason="确认测试通知，不再发送"), db, team["host"]) == {"state": "skipped"}
    assert item.attempts == 2 and item.payload["title"] == "隔离验证"
    assert db.scalar(select(NotificationLog).where(NotificationLog.event_id == item.id)).result == "skipped"
    assert db.scalar(select(AuditLog).where(AuditLog.entity_id == item.id)).before["last_error"] == "未绑定"
    with pytest.raises(HTTPException):
        cancel_notification(item.id, Retry(reason="重复取消"), db, team["host"])
