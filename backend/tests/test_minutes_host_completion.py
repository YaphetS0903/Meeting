from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.extra_api import PreviewRequest, preview_minutes
from app.models import AuditLog, EmployeeRoleGrant, MeetingVersion, NativeTodoLink, OutboxEvent, Task
from app.services.employee_roles import MEETING_ROLE
from app.schemas import ApplyCompletion, MeetingDraft, PublishRequest, VerifyCompletion
from app.services.business import (
    apply_completion,
    carry_forward,
    create_meeting,
    publish,
    revision,
    save_meeting,
    verify_completion,
)


def documents(db, team, draft_factory):
    source_draft = draft_factory(verifier_id=team["colleague"].id)
    source = create_meeting(db, team["host"], source_draft)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task))
    review_draft = draft_factory(section="review", status="已完成", when=date(2026, 8, 24),
                                 task_id=task.id, task_version=task.version)
    review = create_meeting(db, team["host"], review_draft)
    return source, task, review


def test_preview_accepts_hosts_completion_without_mutating_task_or_requests(db, team, draft_factory, monkeypatch):
    _, task, review = documents(db, team, draft_factory)
    team["host"].is_admin = False
    events = db.scalar(select(func.count()).select_from(OutboxEvent))
    monkeypatch.setattr("app.extra_api.pdf_minutes", lambda _: b"%PDF-preview")
    response = preview_minutes(review.id, PreviewRequest(expected_version=1), db, team["host"])
    assert response.status_code == 200 and response.body == b"%PDF-preview"
    assert task.closed_at is None and task.status != "关闭"
    assert review.published_version == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == events


def test_nonadmin_host_confirms_pending_request_and_all_owners_once_on_publish(db, team, draft_factory):
    source, task, review = documents(db, team, draft_factory)
    source_snapshot = deepcopy(source.draft)
    request = apply_completion(db, team["a"], task.id, ApplyCompletion(
        expected_version=1, idempotency_key="minutes-confirmation", explanation="工作已完成",
    ))
    assert task.verifier_id == team["colleague"].id
    team["host"].is_admin = False
    command = PublishRequest(expected_version=1, notify=False)
    publish(db, team["host"], review.id, command)
    assert request.state == "approved" and request.verifier_id == team["host"].id
    assert request.verified_at == task.closed_at and task.status == "关闭"
    assert source.draft == source_snapshot
    assert carry_forward(db, team["host"], team["series"].id) == []
    publish(db, team["host"], review.id, command)
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "task.meeting_completion", AuditLog.entity_id == task.id,
    )) == 1
    record = db.scalar(select(AuditLog).where(AuditLog.action == "task.meeting_completion"))
    assert record.actor_id == team["host"].id and record.after["meeting_id"] == review.id
    assert record.after["meeting_version"] == 1


def test_recorder_cannot_verify_open_meeting_task_on_hosts_behalf(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    review.recorder_id = team["colleague"].id
    review.draft = {**review.draft, "recorder_id": team["colleague"].id}
    publish(db, team["colleague"], review.id, PublishRequest(expected_version=1, notify=False))
    assert task.closed_at is not None and review.published_version == 1


@pytest.mark.parametrize("department", ["own", "other", "special"])
def test_role_two_recorder_can_preview_and_publish_completion_in_own_meeting(
    db, team, draft_factory, monkeypatch, department,
):
    _, task, review = documents(db, team, draft_factory)
    actor = team["colleague"]
    db.add(EmployeeRoleGrant(user_id=actor.id, role=MEETING_ROLE))
    db.flush()
    draft = MeetingDraft.model_validate(review.draft)
    draft.recorder_id = actor.id
    if department != "own":
        draft.department_id = team["other"].id if department == "other" else None
        draft.kind = "special" if department == "special" else "department"
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    monkeypatch.setattr("app.extra_api.pdf_minutes", lambda _: b"%PDF-preview")
    version = review.draft_version
    response = preview_minutes(review.id, PreviewRequest(expected_version=version), db, actor)
    assert response.status_code == 200 and task.closed_at is None
    publish(db, actor, review.id, PublishRequest(expected_version=version, notify=False))
    assert review.published_version == 1 and task.closed_at is not None
    record = db.scalar(select(AuditLog).where(AuditLog.action == "task.meeting_completion"))
    assert record.actor_id == actor.id and "编制人" in record.reason


def test_cross_department_role_two_recorder_can_revise_and_publish_assessments(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    actor = team["colleague"]
    db.add(EmployeeRoleGrant(user_id=actor.id, role=MEETING_ROLE))
    db.flush()
    draft = MeetingDraft.model_validate(review.draft)
    draft.department_id, draft.recorder_id = team["other"].id, actor.id
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    publish(db, actor, review.id, PublishRequest(expected_version=review.draft_version, notify=False))
    revision(db, actor, review.id, "更新会议评定结果")
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].status = "已完成"
    save_meeting(db, actor, review.id, draft, review.draft_version)
    publish(db, actor, review.id, PublishRequest(
        expected_version=review.draft_version, notify=False, reason="更新会议评定结果",
    ))
    assert review.published_version == 2 and task.closed_at is not None


def test_role_two_cannot_confirm_other_departments_meeting_without_responsibility(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    actor = team["outsider"]
    db.add(EmployeeRoleGrant(user_id=actor.id, role=MEETING_ROLE))
    db.flush()
    with pytest.raises(HTTPException) as error:
        publish(db, actor, review.id, PublishRequest(expected_version=1, notify=False))
    assert error.value.status_code == 403 and task.closed_at is None


def test_admin_may_confirm_for_meeting_host(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    review.host_id = team["colleague"].id
    review.draft = {**review.draft, "host_id": team["colleague"].id}
    publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    assert task.closed_at is not None


def test_direct_assignment_still_uses_its_completion_verification(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    task.source_meeting_id, task.issuer_id = None, team["host"].id
    with pytest.raises(HTTPException, match="任务验证"):
        publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    assert task.closed_at is None


def test_host_can_record_historical_meeting_result_without_backdating_task_closure(db, team, draft_factory, monkeypatch):
    _, task, review = documents(db, team, draft_factory)
    actual_time = datetime(2026, 10, 27, 3, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.business.now", lambda: actual_time)
    publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    assert task.closed_at == actual_time
    assert review.meeting_date == date(2026, 8, 24)
    published = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == review.id))
    assert published.snapshot["items"][0]["status"] == "已完成"


def test_revision_marking_completion_updates_live_task_without_rewriting_old_document(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    review.draft = {**review.draft, "items": [{**row, "status": "未完成"} for row in review.draft["items"]]}
    publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    first = deepcopy(db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == review.id)).snapshot)
    revision(db, team["host"], review.id, "主持人更新进展")
    draft = deepcopy(review.draft)
    draft["items"][0]["status"] = "已完成"
    save_meeting(db, team["host"], review.id, MeetingDraft.model_validate(draft), review.draft_version)
    publish(db, team["host"], review.id, PublishRequest(expected_version=review.draft_version, notify=False, reason="确认完成"))
    assert task.closed_at is not None and review.published_version == 2
    assert db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == review.id, MeetingVersion.number == 1)).snapshot == first


def test_existing_verified_task_remains_closed_and_requests_not_reprocessed(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    request = apply_completion(db, team["a"], task.id, ApplyCompletion(
        expected_version=1, idempotency_key="already-verified", explanation="完成",
    ))
    verify_completion(db, team["colleague"], request.id, VerifyCompletion(decision="approved", opinion="通过"))
    closed = task.closed_at
    publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    assert task.closed_at == closed and request.verifier_id == team["colleague"].id


def test_publishing_host_confirmation_queues_one_finish_for_existing_native_todo(db, team, draft_factory, monkeypatch):
    _, task, review = documents(db, team, draft_factory)
    db.add(NativeTodoLink(task_id=task.id, todo_id="minutes-existing-todo", provider="app",
                          task_version=task.version, follower_ids=[], snapshot={}, state="active"))
    db.flush()
    monkeypatch.setattr("app.services.business.get_settings", lambda: SimpleNamespace(
        native_todo_source_enabled=lambda _: True, native_todo_app_enabled=True,
    ))
    command = PublishRequest(expected_version=1, notify=False)
    publish(db, team["host"], review.id, command)
    publish(db, team["host"], review.id, command)
    rows = list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")))
    assert len(rows) == 1 and rows[0].payload["action"] == "finish"
    assert rows[0].payload["task_id"] == task.id


def test_changed_task_version_still_blocks_stale_completion_without_closing_task(db, team, draft_factory):
    _, task, review = documents(db, team, draft_factory)
    task.version = 2
    with pytest.raises(HTTPException, match="关联任务发生变化"):
        publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))
    assert task.closed_at is None and review.published_version == 0
