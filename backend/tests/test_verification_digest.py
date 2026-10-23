from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.models import OutboxEvent, Task
from app.schemas import ApplyCompletion, PublishRequest
from app.services import integrations
from app.services.business import apply_completion, create_meeting, publish


def test_daily_digest_groups_by_verifier_rechecks_versions_and_deduplicates(
    db, team, draft_factory, monkeypatch
):
    clock = datetime(2026, 9, 21, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(worker, "now", lambda: clock)
    monkeypatch.setattr("app.models.now", lambda: clock)
    requests, tasks = [], []
    for index, verifier in enumerate(["host", "host", "host", "colleague"]):
        draft = draft_factory()
        draft.items[0].content = f"核对项目 {index}"
        draft.items[0].verifier_id = team[verifier].id
        meeting = create_meeting(db, team["host"], draft)
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
        task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
        req = apply_completion(
            db,
            team["a"],
            task.id,
            ApplyCompletion(
                expected_version=1,
                idempotency_key=f"digest-apply-{index}",
                explanation="已提交验收材料",
            ),
        )
        req.created_at = clock - timedelta(hours=25)
        requests.append(req)
        tasks.append(task)
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
    worker.reminders()
    worker.reminders()
    events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_key.like("verify-digest:%"))))
    assert len(events) == 2
    event = next(e for e in events if e.payload["user_ids"] == [team["host"].id])
    assert set(event.payload["verification_request_ids"]) == {r.id for r in requests[:3]}
    requests[0].state = "approved"
    tasks[1].version += 1
    team["host"].wecom_id = "host-wecom"
    db.flush()
    calls = []

    class Provider:
        def send(self, users, text):
            calls.append((users, text))
            return {"msgid": "digest-test"}

    monkeypatch.setattr(integrations, "WeCom", Provider)
    assert integrations.send_wecom(db, event)["msgid"] == "digest-test"
    assert calls[0][0] == ["host-wecom"]
    assert "共 1 项待验证" in calls[0][1] and "/verifications" in calls[0][1]
    assert tasks[2].code in calls[0][1]
    assert all(t.code not in calls[0][1] for t in [tasks[0], tasks[1], tasks[3]])
    tasks[2].verifier_id = team["colleague"].id
    db.flush()
    assert integrations.send_wecom(db, event).get("skipped")
    assert len(calls) == 1
