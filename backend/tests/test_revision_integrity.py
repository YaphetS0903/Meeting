from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Assessment, MeetingVersion, Task
from app.schemas import ApplyCompletion, MeetingDraft, PublishRequest, TaskChange, VerifyCompletion
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


def issued_task(db, team, draft_factory):
    draft = draft_factory()
    draft.recorder_id = team["colleague"].id
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    return meeting, db.scalar(select(Task))


def review_meeting(db, team, draft_factory, task, status="未完成"):
    draft = draft_factory(section="review", status=status, task_id=task.id, task_version=task.version)
    draft.recorder_id = team["colleague"].id
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    return meeting


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.items.clear(),
        lambda d: setattr(d.items[0], "id", "replacement-id"),
        lambda d: setattr(d.items[0], "notes", "修改历史考核说明"),
        lambda d: setattr(d, "meeting_date", date(2026, 9, 1)),
        lambda d: setattr(d, "kind", "special"),
    ],
)
def test_recorder_cannot_change_historical_assessment_indirectly(db, team, draft_factory, mutation):
    _, task = issued_task(db, team, draft_factory)
    meeting = review_meeting(db, team, draft_factory, task)
    revision(db, team["colleague"], meeting.id, "申请修订")
    draft = MeetingDraft.model_validate(meeting.draft)
    mutation(draft)
    save_meeting(db, team["colleague"], meeting.id, draft, 2)
    with pytest.raises(HTTPException) as error:
        publish(db, team["colleague"], meeting.id, PublishRequest(expected_version=3, reason="修订"))
    assert error.value.status_code == 403
    assert meeting.published_version == 1
    assert len(list(db.scalars(select(Assessment).where(Assessment.meeting_id == meeting.id)))) == 2


def test_recorder_can_correct_decisions_but_not_promote_self_to_host(db, team, draft_factory):
    _, task = issued_task(db, team, draft_factory)
    meeting = review_meeting(db, team, draft_factory, task)
    revision(db, team["colleague"], meeting.id, "补充决议")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.decisions = "补充说明，考核原样保留"
    save_meeting(db, team["colleague"], meeting.id, draft, 2)
    publish(db, team["colleague"], meeting.id, PublishRequest(expected_version=3, reason="补充决议"))
    assert meeting.published_version == 2
    revision(db, team["colleague"], meeting.id, "调整主持人")
    draft.host_id = team["colleague"].id
    with pytest.raises(HTTPException) as error:
        save_meeting(db, team["colleague"], meeting.id, draft, 4)
    assert error.value.status_code == 403


def test_unchanged_old_assignment_does_not_undo_current_deadline_or_pending_request(db, team, draft_factory):
    meeting, task = issued_task(db, team, draft_factory)
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=1,
            content=task.content,
            owner_ids=[team["a"].id, team["b"].id],
            due_date=date(2026, 12, 31),
            verifier_id=team["host"].id,
            reason="新期限",
        ),
    )
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(
            expected_version=2, idempotency_key="request-on-updated-task", explanation="已完成新要求"
        ),
    )
    revision(db, team["colleague"], meeting.id, "仅补充旧纪要决议")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.decisions = "旧纪要补记决议"
    draft.items[0].owner_ids.reverse()  # Display order is not a task change.
    save_meeting(db, team["colleague"], meeting.id, draft, 2)
    publish(db, team["colleague"], meeting.id, PublishRequest(expected_version=3, reason="补充决议"))
    assert task.version == 2 and task.due_date == date(2026, 12, 31)
    assert request.state == "pending"
    latest = db.scalar(
        select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == 2)
    )
    assert latest.snapshot["items"][0]["task_version"] == 1
    assert latest.snapshot["items"][0]["due_date"] == "2026-08-17"


def test_stripping_published_task_reference_cannot_overwrite_newer_task(db, team, draft_factory):
    meeting, task = issued_task(db, team, draft_factory)
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=1,
            content=task.content,
            owner_ids=[team["a"].id, team["b"].id],
            due_date=date(2026, 12, 31),
            verifier_id=team["host"].id,
            reason="新期限",
        ),
    )
    revision(db, team["host"], meeting.id, "修改旧纪要")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].task_id = None
    draft.items[0].task_version = None
    draft.items[0].due_date = date(2026, 9, 1)
    save_meeting(db, team["host"], meeting.id, draft, 2)
    with pytest.raises(HTTPException) as error:
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="修改旧纪要"))
    assert error.value.status_code in {409, 422}
    assert task.version == 2 and task.due_date == date(2026, 12, 31)


@pytest.mark.parametrize("another_effective_review", [False, True])
def test_correcting_completed_review_recomputes_carry_forward(
    db, team, draft_factory, another_effective_review, monkeypatch
):
    monkeypatch.setattr("app.services.business.now", lambda: datetime(2026, 8, 10, 1, tzinfo=timezone.utc))
    _, task = issued_task(db, team, draft_factory)
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="complete-before-review", explanation="已完成"),
    )
    verify_completion(db, team["host"], request.id, VerifyCompletion(decision="approved", opinion="通过"))
    meeting = review_meeting(db, team, draft_factory, task, "已完成")
    if another_effective_review:
        review_meeting(db, team, draft_factory, task, "已完成")
    assert carry_forward(db, team["host"], team["series"].id) == []
    revision(db, team["host"], meeting.id, "核实本次不属于回顾范围")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items.clear()
    save_meeting(db, team["host"], meeting.id, draft, 2)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=3, reason="核实回顾范围"))
    carried = carry_forward(db, team["host"], team["series"].id)
    assert len(carried) == (0 if another_effective_review else 1)
    if carried:
        assert carried[0]["status"] == "已完成"
    assert task.status == "关闭"
