from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Assessment, CompletionRequest, MeetingVersion, OutboxEvent, Task
from app.schemas import (
    ApplyCompletion,
    MeetingDraft,
    MeetingItem,
    PublishRequest,
    TaskChange,
    VerifyCompletion,
)
from app.services.access import meeting_access, task_access
from app.services.business import (
    apply_completion,
    carry_forward,
    change_task,
    create_meeting,
    publish,
    revision,
    save_meeting,
    verify_completion,
)
from app.services.reporting import report


def published(db, team, draft, historical=False):
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.flush()
    return meeting


def get_task(db):
    return db.scalar(select(Task))


def summary(db, team):
    return report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31))["summary"]


def test_new_completed_does_not_count_and_cannot_bypass_verification(db, team, draft_factory):
    meeting = published(db, team, draft_factory(status="已完成"))
    assert summary(db, team)["person_count"] == 0
    assert summary(db, team)["rate"] is None
    assert get_task(db).status != "关闭"
    assert get_task(db).verifier_id == team["host"].id
    assert meeting.published_version == 1


def test_publish_without_notifications_does_not_notify_linked_changes(db, team, draft_factory):
    published(db, team, draft_factory())
    task = get_task(db)
    initial = db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.channel == "wecom"))
    draft = draft_factory(task_id=task.id, task_version=1)
    draft.items[0].due_date = date(2026, 12, 31)
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    assert task.due_date == date(2026, 12, 31)
    assert (
        db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.channel == "wecom"))
        == initial
    )


def test_shared_task_apply_verify_and_frozen_history(db, team, draft_factory, monkeypatch):
    monkeypatch.setattr("app.services.business.now", lambda: datetime(2026, 8, 20, 9, tzinfo=timezone.utc))
    published(db, team, draft_factory())
    task = get_task(db)
    old = published(
        db, team, draft_factory(section="review", status="未完成", task_id=task.id, task_version=1)
    )
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="request-shared", explanation="修订完成"),
    )
    assert summary(db, team)["counts"]["已完成"] == 0
    with pytest.raises(HTTPException) as error:
        apply_completion(
            db,
            team["b"],
            task.id,
            ApplyCompletion(expected_version=1, idempotency_key="request-second", explanation="完成"),
        )
    assert error.value.status_code == 409
    verify_completion(
        db, team["host"], request.id, VerifyCompletion(decision="approved", opinion="文件符合要求")
    )
    assert task.status == "关闭"
    for person in [team["a"], team["b"]]:
        task_access(db, person, task)
    assert [a.status for a in db.scalars(select(Assessment).where(Assessment.meeting_id == old.id))] == [
        "未完成",
        "未完成",
    ]
    carried = carry_forward(db, team["host"], team["series"].id)
    assert carried[0]["status"] == "已完成"
    published(
        db,
        team,
        draft_factory(
            section="review", status="已完成", task_id=task.id, task_version=1, when=date(2026, 8, 24)
        ),
    )
    result = summary(db, team)
    assert result["counts"]["已完成"] == result["counts"]["未完成"] == 2
    assert result["rate"] == 0.5
    assert carry_forward(db, team["host"], team["series"].id) == []


def test_separate_meetings_same_week_count_separately(db, team, draft_factory):
    published(db, team, draft_factory(section="review", status="已完成"), historical=True)
    published(
        db, team, draft_factory(section="review", status="未完成", when=date(2026, 8, 11)), historical=True
    )
    assert summary(db, team)["items"] == 2
    assert summary(db, team)["person_count"] == 4
    assert summary(db, team)["rate"] == 0.5


def test_publish_idempotent_and_revision_replaces_only_effective_assessments(db, team, draft_factory):
    meeting = published(db, team, draft_factory(section="review", status="未完成"), historical=True)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert db.scalar(select(func.count()).select_from(MeetingVersion)) == 1
    revision(db, team["host"], meeting.id, "原纪要状态填错")
    value = draft_factory(section="review", status="已完成")
    save_meeting(db, team["host"], meeting.id, value, 2)
    assert summary(db, team)["rate"] == 0
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="核实原始完成证明"))
    assert summary(db, team)["rate"] == 1
    assert summary(db, team)["person_count"] == 2
    assert db.scalar(select(func.count()).select_from(Assessment)) == 4


def test_deadline_update_invalidates_application_and_preserves_assessment(db, team, draft_factory):
    published(db, team, draft_factory())
    task = get_task(db)
    published(db, team, draft_factory(section="review", status="未完成", task_id=task.id, task_version=1))
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="change-apply", explanation="提交初版"),
    )
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=1,
            content=task.content,
            due_date=date(2026, 9, 30),
            owner_ids=[team["a"].id, team["b"].id],
            verifier_id=team["host"].id,
            reason="增加验证范围",
        ),
    )
    assert request.state == "invalidated"
    with pytest.raises(HTTPException) as error:
        verify_completion(
            db, team["host"], request.id, VerifyCompletion(decision="approved", opinion="旧版已看")
        )
    assert error.value.status_code == 409
    assert task.closed_at is None
    assert summary(db, team)["counts"]["未完成"] == 2


def test_only_verifier_can_close_and_rejection_does_not_close(db, team, draft_factory):
    published(db, team, draft_factory())
    task = get_task(db)
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="verify-test", explanation="说明"),
    )
    with pytest.raises(HTTPException) as error:
        verify_completion(
            db, team["b"], request.id, VerifyCompletion(decision="approved", opinion="自行确认")
        )
    assert error.value.status_code == 403
    verify_completion(
        db, team["host"], request.id, VerifyCompletion(decision="rejected", opinion="缺少现场证据")
    )
    assert request.state == "rejected" and task.closed_at is None
    second = apply_completion(
        db,
        team["b"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="verify-retry", explanation="补充说明"),
    )
    assert second.id != request.id


def test_department_and_related_permissions(db, team, draft_factory):
    meeting = published(db, team, draft_factory())
    meeting_access(db, team["colleague"], meeting)
    with pytest.raises(HTTPException):
        meeting_access(db, team["outsider"], meeting)
    assert report(db, team["outsider"], date(2026, 8, 1), date(2026, 8, 31))["summary"]["items"] == 0
    with pytest.raises(HTTPException):
        meeting_access(db, team["colleague"], meeting, edit=True)


def test_person_department_snapshot_survives_transfer(db, team, draft_factory):
    published(db, team, draft_factory(section="review", status="已完成"), historical=True)
    team["a"].department_id = team["other"].id
    db.flush()
    result = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31), "person_department")
    assert len(result["groups"]) == 1 and result["groups"][0]["id"] == team["dep"].id


def test_overdue_undue_strictly_greater_not_equal(db, team, draft_factory):
    published(db, team, draft_factory(section="review", when=date(2026, 8, 17)), historical=True)
    meeting = create_meeting(
        db, team["host"], draft_factory(section="review", when=date(2026, 8, 18)), historical=True
    )
    with pytest.raises(HTTPException) as error:
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert error.value.status_code == 422


def test_historical_import_does_not_notify_or_create_live_tasks(db, team, draft_factory):
    published(db, team, draft_factory(section="review", status="已完成"), historical=True)
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert set(db.scalars(select(OutboxEvent.channel))) == {"export"}


def test_special_meeting_allows_empty_department_and_recorder(db, team):
    draft = MeetingDraft(
        title="跨部门专项会议",
        kind="special",
        department_id=None,
        recorder_id=None,
        host_id=team["host"].id,
        meeting_date=date(2026, 8, 20),
        items=[
            MeetingItem(
                section="new",
                content="完成跨部门专项整改",
                owner_ids=[team["a"].id],
                due_date=date(2026, 8, 30),
            )
        ],
    )
    meeting = published(db, team, draft)
    task = get_task(db)
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))

    assert meeting.department_id is None
    assert meeting.recorder_id is None
    assert task.department_id is None
    assert version.snapshot["department_name"] == ""
    assert version.snapshot["recorder_name"] == ""

    historical = MeetingDraft(
        title="专项复盘会议",
        kind="special",
        department_id=None,
        recorder_id=None,
        host_id=team["host"].id,
        meeting_date=date(2026, 8, 21),
        items=[
            MeetingItem(
                section="review",
                status="已完成",
                content="复盘跨部门专项整改",
                owner_ids=[team["a"].id],
                due_date=date(2026, 8, 20),
            )
        ],
    )
    published(db, team, historical, historical=True)
    result = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31))
    assert result["groups"][0]["label"] == "专项会议（无所属部门）"
    assert result["details"][0]["department_name"] == "专项会议（无所属部门）"


def test_idempotent_application_and_validation(db, team, draft_factory):
    published(db, team, draft_factory())
    task = get_task(db)
    command = ApplyCompletion(expected_version=1, idempotency_key="idempotent-apply", explanation="已完成")
    first = apply_completion(db, team["a"], task.id, command)
    assert apply_completion(db, team["a"], task.id, command).id == first.id
    verdict = VerifyCompletion(decision="approved", opinion="通过")
    verify_completion(db, team["host"], first.id, verdict)
    verify_completion(db, team["host"], first.id, verdict)
    db.flush()
    assert db.scalar(select(func.count()).select_from(CompletionRequest)) == 1


def test_unverified_task_cannot_be_written_complete(db, team, draft_factory):
    published(db, team, draft_factory())
    task = get_task(db)
    meeting = create_meeting(
        db, team["host"], draft_factory(section="review", status="已完成", task_id=task.id, task_version=1)
    )
    with pytest.raises(HTTPException) as error:
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert error.value.status_code == 422


def test_period_filters_use_meeting_date(db, team, draft_factory):
    published(
        db, team, draft_factory(section="review", status="已完成", when=date(2026, 8, 31)), historical=True
    )
    published(
        db, team, draft_factory(section="review", status="未完成", when=date(2026, 9, 1)), historical=True
    )
    assert summary(db, team)["rate"] == 1
    week = report(db, team["host"], date(2026, 8, 31), date(2026, 9, 6))
    assert week["summary"]["rate"] == 0.5
