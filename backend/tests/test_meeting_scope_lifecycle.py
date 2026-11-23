from datetime import date, datetime, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from app.main import DeleteMeetingRequest, delete_meeting, deletion_impact
from app.models import Meeting, MeetingSeries, MeetingVersion, RecurringRule, Task
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
from app.services.role_migration import rule
from tests.role_helpers import grant as new_grant


@pytest.fixture(autouse=True)
def isolated_notifications_and_clock(monkeypatch):
    for module in ("app.services.business",):
        monkeypatch.setattr(module + ".enqueue", lambda *args, **kwargs: None)
        monkeypatch.setattr(module + ".enqueue_native_todo", lambda *args, **kwargs: None)
        monkeypatch.setattr(module + ".today", lambda: date(2026, 8, 18))
    monkeypatch.setattr("app.services.business.now", lambda: datetime(2026, 8, 18, 1, tzinfo=timezone.utc))


def issued(db, team, draft, *, historical=False):
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    return meeting, task


def republish(db, team, meeting, draft):
    revision(db, team["host"], meeting.id, "核对会议归属")
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(
        db,
        team["host"],
        meeting.id,
        PublishRequest(expected_version=meeting.draft_version, notify=False, reason="核对会议归属"),
    )


def test_revised_scope_moves_only_source_tasks_and_updates_access(db, team, draft_factory):
    other_meeting, other_task = issued(db, team, draft_factory())
    draft = draft_factory()
    draft.items.append(
        MeetingItem(
            section="review",
            content=other_task.content,
            task_id=other_task.id,
            task_version=other_task.version,
            owner_ids=[team["a"].id, team["b"].id],
            due_date=other_task.due_date,
            status="未完成",
        )
    )
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
    assert db.scalar(visible_tasks(db, team["outsider"]).where(Task.id == task.id)) is None
    new_grant(db, team["outsider"], [rule("overview", ["view"])])
    assert db.scalar(visible_tasks(db, team["outsider"]).where(Task.id == task.id)) is task
    assert [row["task_id"] for row in carry_forward(db, team["host"], target_series.id)] == [task.id]
    old_snapshot = db.scalar(
        select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == 1)
    ).snapshot
    assert old_snapshot["department_id"] == team["dep"].id
    assert old_snapshot["series_id"] == team["series"].id


def test_scope_move_preserves_historical_recurring_instances(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    rule = RecurringRule(
        source_task_id=task.id,
        creator_id=team["host"].id,
        frequency="weekly",
        anchor_date=date(2026, 8, 20),
        next_due_date=date(2026, 8, 27),
        template={},
        active=False,
    )
    db.add(rule)
    db.flush()
    task.recurrence_key = rule.id
    generated = Task(
        code="historical-period",
        source_item_id="historical-row",
        content="历史已生成任务",
        verifier_id=team["host"].id,
        source_meeting_id=meeting.id,
        department_id=meeting.department_id,
        series_id=meeting.series_id,
        recurrence_key=rule.id,
        recurrence_period=date(2026, 8, 24),
        due_date=date(2026, 8, 27),
    )
    db.add(generated)
    target_series = MeetingSeries(name="历史任务归档")
    db.add(target_series)
    db.flush()
    revised = MeetingDraft.model_validate(meeting.draft)
    revised.department_id, revised.series_id = team["other"].id, target_series.id
    republish(db, team, meeting, revised)
    tasks = list(db.scalars(select(Task).where(Task.recurrence_key == rule.id)))
    assert len(tasks) == 2 and not rule.active
    assert all((item.department_id, item.series_id) == (team["other"].id, target_series.id) for item in tasks)


def test_archive_series_shortcut_preserves_published_snapshot(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    target = MeetingSeries(name="重新归档")
    db.add(target)
    db.flush()
    move_meeting_series(db, team["host"], meeting.id, target.id, meeting.draft_version)
    assert task.series_id == target.id
    assert task.department_id == team["dep"].id
    assert (
        db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id)).snapshot["series_id"]
        == team["series"].id
    )


def test_continuation_reviews_verified_completion_once(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(
            expected_version=task.version,
            idempotency_key="followup-completion",
            explanation="已完成",
        ),
    )
    verify_completion(db, team["host"], request.id, VerifyCompletion(decision="approved", opinion="通过"))
    followup = continue_meeting(db, team["host"], meeting.id)
    assert len(followup.draft["items"]) == 1
    assert followup.draft["items"][0]["status"] == ""
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].status = "已完成"
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    assert followup.draft["items"][0]["task_id"] == task.id
    publish(
        db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False)
    )
    assert continue_meeting(db, team["host"], meeting.id).draft["items"] == []
    assert carry_forward(db, team["host"], team["series"].id) == []


def test_continuation_excludes_published_closed_review_after_owner_disabled(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].status = "关闭"
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    publish(
        db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False)
    )
    team["a"].active = False
    assert continue_meeting(db, team["host"], followup.id).draft["items"] == []


def test_delete_blocks_followup_draft_without_removing_source(db, team, draft_factory):
    meeting, task = issued(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    impact = deletion_impact(meeting.id, db, team["host"])
    assert impact["can_delete"] is False
    assert impact["blocking_meeting_count"] == 1
    assert impact["blocking_meetings"] == [
        {
            "id": followup.id,
            "title": followup.title,
            "draft": True,
            "published_versions": [],
        }
    ]
    with pytest.raises(HTTPException) as error:
        delete_meeting(
            meeting.id,
            BackgroundTasks(),
            DeleteMeetingRequest(
                expected_draft_version=meeting.draft_version,
            ),
            db,
            team["host"],
        )
    assert error.value.status_code == 409
    assert db.get(Task, task.id) is task
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    publish(
        db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False)
    )


def test_delete_protects_references_in_old_published_versions(db, team, draft_factory):
    meeting, _ = issued(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    draft = MeetingDraft.model_validate(followup.draft)
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], followup.id, draft, followup.draft_version)
    publish(
        db, team["host"], followup.id, PublishRequest(expected_version=followup.draft_version, notify=False)
    )
    revised = MeetingDraft.model_validate(followup.draft)
    revised.items.clear()
    republish(db, team, followup, revised)
    impact = deletion_impact(meeting.id, db, team["host"])
    assert impact["blocking_meetings"] == [
        {
            "id": followup.id,
            "title": followup.title,
            "draft": False,
            "published_versions": [1],
        }
    ]
    assert impact["can_delete"] is False


def test_delete_dependency_titles_respect_meeting_visibility(db, team, draft_factory):
    draft = draft_factory(verifier_id=team["outsider"].id)
    meeting, task = issued(db, team, draft)
    other = MeetingDraft(
        title="无权查看的会议",
        department_id=team["other"].id,
        host_id=team["outsider"].id,
        meeting_date=date(2026, 8, 18),
        items=[
            MeetingItem(
                section="review",
                content=task.content,
                task_id=task.id,
                task_version=task.version,
                owner_ids=[team["a"].id, team["b"].id],
                due_date=task.due_date,
                status="未完成",
                verifier_id=team["outsider"].id,
            )
        ],
    )
    issued(db, team, other)
    team["host"].is_admin = False
    with pytest.raises(HTTPException) as error:
        deletion_impact(meeting.id, db, team["host"])
    assert error.value.status_code == 403


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
    result = delete_meeting(
        meeting.id,
        BackgroundTasks(),
        DeleteMeetingRequest(
            expected_draft_version=meeting.draft_version,
        ),
        db,
        team["host"],
    )
    db.flush()
    assert result["deleted"] is True
    assert db.get(Task, task_id) is None
