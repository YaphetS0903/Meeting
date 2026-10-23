import os
from datetime import date, datetime, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.main import DeleteMeetingRequest, delete_meeting, deletion_impact
from app.models import Meeting, MeetingSeries, MeetingVersion, RecurringRule, Task, User
from app.schemas import (
    ApplyCompletion,
    HistoricalReference,
    MeetingDraft,
    MeetingItem,
    PublishRequest,
    VerifyCompletion,
)
from app.services.access import visible_meetings, visible_tasks
from app.services.business import (
    apply_completion,
    carry_forward,
    continue_meeting,
    create_meeting,
    move_meeting_series,
    publish,
    revision,
    save_meeting,
    verify_completion,
)
from app.services.recurrence import RecurrenceCreate, enable_rule, generate_instances


@pytest.fixture(autouse=True)
def isolated_notifications_and_clock(monkeypatch):
    for module in ("app.services.business", "app.services.recurrence"):
        monkeypatch.setattr(module + ".enqueue", lambda *args, **kwargs: None)
        monkeypatch.setattr(module + ".enqueue_native_todo", lambda *args, **kwargs: None)
        monkeypatch.setattr(module + ".today", lambda: date(2026, 8, 18))
    monkeypatch.setattr(
        "app.services.business.now", lambda: datetime(2026, 8, 18, 1, tzinfo=timezone.utc)
    )


def issued(db, team, draft, *, historical=False):
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    return meeting, task


def republish(db, team, meeting, draft):
    revision(db, team["host"], meeting.id, "核对会议归属")
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(
        db, team["host"], meeting.id,
        PublishRequest(expected_version=meeting.draft_version, notify=False, reason="核对会议归属"),
    )


def test_revised_scope_moves_only_source_tasks_and_updates_access(db, team, draft_factory):
    other_meeting, other_task = issued(db, team, draft_factory())
    draft = draft_factory()
    draft.items.append(MeetingItem(
        section="review", content=other_task.content, task_id=other_task.id,
        task_version=other_task.version, owner_ids=[team["a"].id, team["b"].id],
        due_date=other_task.due_date, status="未完成",
    ))
    meeting, task = issued(db, team, draft)
    target_series = MeetingSeries(name="新的归档系列")
    db.add(target_series)
    db.flush()
    revised = MeetingDraft.model_validate(meeting.draft)
    revised.department_id, revised.series_id = team["other"].id, target_series.id
    republish(db, team, meeting, revised)

    assert (task.department_id, task.series_id) == (team["other"].id, target_series.id)
    assert (other_task.department_id, other_task.series_id) == (team["dep"].id, team["series"].id)
    assert other_task.source_meeting_id == other_meeting.id
    assert db.scalar(visible_tasks(db, team["colleague"]).where(Task.id == task.id)) is None
    assert db.scalar(visible_meetings(db, team["colleague"]).where(Meeting.id == meeting.id)) is None
    assert db.scalar(visible_tasks(db, team["outsider"]).where(Task.id == task.id)) is task
    assert [row["task_id"] for row in carry_forward(db, team["host"], target_series.id)] == [task.id]
    old_snapshot = db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == 1
    )).snapshot
    assert old_snapshot["department_id"] == team["dep"].id
    assert old_snapshot["series_id"] == team["series"].id


def test_scope_move_includes_existing_and_future_recurring_instances(db, team, draft_factory):
    draft = draft_factory()
    draft.items[0].due_date = date(2026, 8, 20)
    meeting, task = issued(db, team, draft)
    rule = enable_rule(db, team["host"], task.id, RecurrenceCreate(
        expected_version=task.version, frequency="weekly", reason="每周执行",
    ))
    assert generate_instances(db, date(2026, 8, 27)) == 1
    target_series = MeetingSeries(name="迁移周期归档")
    db.add(target_series)
    db.flush()
    revised = MeetingDraft.model_validate(meeting.draft)
    revised.department_id, revised.series_id = team["other"].id, target_series.id
    republish(db, team, meeting, revised)
    assert generate_instances(db, date(2026, 9, 3)) == 1
    tasks = list(db.scalars(select(Task).where(Task.recurrence_key == rule.id)))
    assert len(tasks) == 3
    assert all((item.department_id, item.series_id) == (team["other"].id, target_series.id) for item in tasks)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL concurrent rule locking")
def test_scope_move_retries_locked_rule_without_partial_publication(db, team, draft_factory):
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL SKIP LOCKED")
    draft = draft_factory()
    draft.items[0].due_date = date(2026, 8, 20)
    meeting, task = issued(db, team, draft)
    rule = enable_rule(db, team["host"], task.id, RecurrenceCreate(
        expected_version=task.version, frequency="weekly", reason="并发归属核验",
    ))
    target = MeetingSeries(name="等待周期生成后迁移")
    db.add(target)
    db.flush()
    revised = MeetingDraft.model_validate(meeting.draft)
    revised.department_id, revised.series_id = team["other"].id, target.id
    revised.items.append(MeetingItem(
        section="new", content="此次修订新增工作", owner_ids=[team["a"].id],
        due_date=date(2026, 8, 30),
    ))
    revision(db, team["host"], meeting.id, "调整部门及系列")
    save_meeting(db, team["host"], meeting.id, revised, meeting.draft_version)
    command = PublishRequest(expected_version=meeting.draft_version, notify=False, reason="调整归属")
    meeting_id, actor_id, task_id, rule_id = meeting.id, team["host"].id, task.id, rule.id
    old_scope, new_scope = (team["dep"].id, team["series"].id), (team["other"].id, target.id)
    engine = db.get_bind()
    db.commit()

    # A separate transaction represents generation/maintenance holding the rule.
    with Session(engine) as locker, locker.begin():
        locker.scalar(select(RecurringRule).where(RecurringRule.id == rule_id).with_for_update())
        with pytest.raises(HTTPException) as error:
            with Session(engine) as writer, writer.begin():
                writer.execute(text("SET LOCAL lock_timeout = '2s'"))
                publish(writer, writer.get(User, actor_id), meeting_id, command)
        assert error.value.status_code == 409
        assert "周期任务" in error.value.detail
        db.expire_all()
        source = db.get(Meeting, meeting_id)
        original_task = db.get(Task, task_id)
        assert (source.department_id, source.series_id) == old_scope
        assert (original_task.department_id, original_task.series_id) == old_scope
        assert source.published_version == 1 and source.has_draft
        assert db.scalar(select(func.count()).select_from(MeetingVersion)) == 1
        assert db.scalar(select(func.count()).select_from(Task)) == 1
        db.rollback()

    with Session(engine) as writer, writer.begin():
        publish(writer, writer.get(User, actor_id), meeting_id, command)
    db.expire_all()
    source = db.get(Meeting, meeting_id)
    assert (source.department_id, source.series_id) == new_scope
    assert source.published_version == 2 and not source.has_draft
    tasks = list(db.scalars(select(Task).where(Task.source_meeting_id == meeting_id)))
    assert len(tasks) == 2
    assert all((item.department_id, item.series_id) == new_scope for item in tasks)


def test_archive_series_shortcut_preserves_published_snapshot(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    target = MeetingSeries(name="重新归档")
    db.add(target)
    db.flush()
    move_meeting_series(db, team["host"], meeting.id, target.id, meeting.draft_version)
    assert task.series_id == target.id
    assert task.department_id == team["dep"].id
    assert db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id)).snapshot[
        "series_id"
    ] == team["series"].id


def test_continuation_reviews_verified_completion_once(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    request = apply_completion(db, team["a"], task.id, ApplyCompletion(
        expected_version=task.version, idempotency_key="followup-completion", explanation="已完成",
    ))
    verify_completion(db, team["host"], request.id, VerifyCompletion(decision="approved", opinion="通过"))
    followup = continue_meeting(db, team["host"], meeting.id)
    assert len(followup.draft["items"]) == 1
    assert followup.draft["items"][0]["status"] == "已完成"
    assert followup.draft["items"][0]["task_id"] == task.id
    publish(db, team["host"], followup.id, PublishRequest(expected_version=1, notify=False))
    assert continue_meeting(db, team["host"], meeting.id).draft["items"] == []
    assert carry_forward(db, team["host"], team["series"].id) == []


def test_continuation_keeps_inactive_owner_exclusion(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    task.closed_at = datetime(2026, 8, 18, tzinfo=timezone.utc)
    task.status = "关闭"
    team["a"].active = False
    assert continue_meeting(db, team["host"], meeting.id).draft["items"] == []


def test_delete_blocks_followup_draft_without_removing_source(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    impact = deletion_impact(meeting.id, db, team["host"])
    assert impact["can_delete"] is False
    assert impact["blocking_meeting_count"] == 1
    assert impact["blocking_meetings"] == [{
        "id": followup.id, "title": followup.title, "draft": True, "published_versions": [],
    }]
    with pytest.raises(HTTPException) as error:
        delete_meeting(meeting.id, BackgroundTasks(), DeleteMeetingRequest(
            expected_draft_version=meeting.draft_version,
        ), db, team["host"])
    assert error.value.status_code == 409
    assert db.get(Task, task.id) is task
    publish(db, team["host"], followup.id, PublishRequest(expected_version=1, notify=False))


def test_delete_protects_references_in_old_published_versions(db, team, draft_factory):
    meeting, _ = issued(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    publish(db, team["host"], followup.id, PublishRequest(expected_version=1, notify=False))
    revised = MeetingDraft.model_validate(followup.draft)
    revised.items.clear()
    republish(db, team, followup, revised)
    impact = deletion_impact(meeting.id, db, team["host"])
    assert impact["blocking_meetings"] == [{
        "id": followup.id, "title": followup.title, "draft": False, "published_versions": [1],
    }]
    assert impact["can_delete"] is False


def test_delete_dependency_titles_respect_meeting_visibility(db, team, draft_factory):
    draft = draft_factory(verifier_id=team["outsider"].id)
    meeting, task = issued(db, team, draft)
    other = MeetingDraft(
        title="无权查看的会议", department_id=team["other"].id,
        host_id=team["outsider"].id, meeting_date=date(2026, 8, 18),
        items=[MeetingItem(section="review", content=task.content, task_id=task.id,
                           task_version=task.version, owner_ids=[team["a"].id, team["b"].id],
                           due_date=task.due_date, status="未完成", verifier_id=team["outsider"].id)],
    )
    issued(db, team, other)
    team["host"].is_admin = False
    impact = deletion_impact(meeting.id, db, team["host"])
    assert impact["blocking_meeting_count"] == 1
    assert impact["blocking_meetings"] == []
    assert impact["hidden_blocking_meeting_count"] == 1


def test_delete_protects_historical_references_without_active_task(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory(), historical=True)
    assert task is None
    followup = continue_meeting(db, team["host"], meeting.id)
    reference = HistoricalReference.model_validate(followup.draft["items"][0]["historical_reference"])
    assert reference.meeting_id == meeting.id
    assert deletion_impact(meeting.id, db, team["host"])["blocking_meeting_count"] == 1


def test_unreferenced_meeting_can_still_be_deleted(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    task_id = task.id
    assert deletion_impact(meeting.id, db, team["host"])["can_delete"] is True
    result = delete_meeting(meeting.id, BackgroundTasks(), DeleteMeetingRequest(
        expected_draft_version=meeting.draft_version,
    ), db, team["host"])
    db.flush()
    assert result["deleted"] is True
    assert db.get(Task, task_id) is None
