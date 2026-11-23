from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Task
from app.schemas import ApplyCompletion, PublishRequest, VerifyCompletion
from app.services.business import apply_completion, create_meeting, publish, verify_completion


@pytest.mark.parametrize("meeting_date,allowed", [(date(2026, 8, 17), True), (date(2026, 8, 18), True)])
def test_recorder_completed_assessment_cannot_predate_verification_in_beijing(
    db, team, draft_factory, monkeypatch, meeting_date, allowed
):
    # 16:30 UTC is already the next calendar day in Beijing.
    monkeypatch.setattr(
        "app.services.business.now", lambda: datetime(2026, 8, 17, 16, 30, tzinfo=timezone.utc)
    )
    issued = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], issued.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task))
    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="chronology", explanation="提交成果"),
    )
    verify_completion(
        db, team["host"], request.id, VerifyCompletion(decision="approved", opinion="符合验收要求")
    )
    draft = draft_factory(section="review", status="已完成", when=meeting_date, task_id=task.id, task_version=1)
    draft.recorder_id = team["colleague"].id
    review = create_meeting(
        db,
        team["host"],
        draft,
    )
    if allowed:
        publish(db, team["colleague"], review.id, PublishRequest(expected_version=1))
        assert review.published_version == 1
    else:
        with pytest.raises(HTTPException, match="早于任务验证关闭日期"):
            publish(db, team["colleague"], review.id, PublishRequest(expected_version=1))
        assert review.published_version == 0
