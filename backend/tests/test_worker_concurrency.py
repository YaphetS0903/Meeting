import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import worker
from app.models import CompletionRequest, OutboxEvent, Task, User
from app.schemas import ApplyCompletion, PublishRequest
from app.services.business import apply_completion, create_meeting, publish
from app.services.integrations import DeliveryUncertain, NotConfigured


def test_outbox_committed_delivery_is_not_repeated(db, team, draft_factory, monkeypatch):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    calls = []
    monkeypatch.setattr(worker, "send_wecom", lambda s, e: calls.append(e.id) or {"msgid": "test"})
    monkeypatch.setattr(worker, "generate_exports", lambda *args: [])
    worker.dispatch()
    worker.dispatch()
    assert len(calls) == 1
    db.expire_all()
    assert all(e.state == "sent" for e in db.scalars(select(OutboxEvent)))


@pytest.mark.parametrize(
    "error,state", [(DeliveryUncertain("需要人工核实"), "uncertain"), (NotConfigured("未配置"), "blocked")]
)
def test_uncertain_or_unconfigured_delivery_never_auto_retries(db, team, monkeypatch, error, state):
    e = OutboxEvent(event_key="test-event", channel="wecom", payload={})
    db.add(e)
    db.commit()
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind(), expire_on_commit=False))

    def fail(*args):
        raise error

    monkeypatch.setattr(worker, "send_wecom", fail)
    worker.dispatch()
    worker.dispatch()
    db.expire_all()
    assert db.get(OutboxEvent, e.id).state == state
    assert db.get(OutboxEvent, e.id).attempts == 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL row-lock concurrency acceptance")
def test_concurrent_publish_and_joint_applications(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def publish_once(_):
        with factory.begin() as session:
            return publish(
                session, session.get(User, team["host"].id), meeting.id, PublishRequest(expected_version=1)
            ).published_version

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(publish_once, range(2))) == [1, 1]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    task = db.scalar(select(Task))
    db.commit()

    def apply_once(name):
        from fastapi import HTTPException

        try:
            with factory.begin() as session:
                apply_completion(
                    session,
                    session.get(User, team[name].id),
                    task.id,
                    ApplyCompletion(
                        expected_version=1, idempotency_key="concurrent-" + name, explanation="完成"
                    ),
                )
                return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(apply_once, ["a", "b"])) == [200, 409]
    assert db.scalar(select(func.count()).select_from(CompletionRequest)) == 1
