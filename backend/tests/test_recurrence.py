import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import Assessment, OutboxEvent, Task
from app.schemas import PublishRequest, TaskChange
from app.services.business import change_task, create_meeting, publish
from app.services.recurrence import (
    RecurrenceCreate,
    RecurrenceUpdate,
    enable_rule,
    generate_instances,
    update_rule,
)


def seed(db, team, draft_factory, monkeypatch, frequency="weekly", due=date(2026, 9, 25)):
    monkeypatch.setattr("app.services.recurrence.today", lambda: date(2026, 9, 21))
    draft = draft_factory()
    draft.items[0].due_date = due
    m = create_meeting(db, team["host"], draft)
    publish(db, team["host"], m.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task).where(Task.source_meeting_id == m.id))
    rule = enable_rule(
        db,
        team["host"],
        task.id,
        RecurrenceCreate(expected_version=1, frequency=frequency, reason="按会议决议周期执行"),
    )
    db.flush()
    return task, rule


def test_weekly_instances_do_not_extend_previous_task_or_create_assessments(
    db, team, draft_factory, monkeypatch
):
    first, rule = seed(db, team, draft_factory, monkeypatch)
    assert generate_instances(db, date(2026, 9, 27)) == 0
    assert generate_instances(db, date(2026, 9, 28)) == 1
    assert generate_instances(db, date(2026, 9, 28)) == 0
    tasks = list(db.scalars(select(Task).order_by(Task.due_date)))
    assert [t.due_date for t in tasks] == [date(2026, 9, 25), date(2026, 10, 2)]
    assert tasks[0].id != tasks[1].id and first.version == 1
    assert tasks[1].recurrence_period == date(2026, 9, 28)
    assert db.scalar(select(func.count()).select_from(Assessment)) == 0
    assert len(list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_key.like("%:recurring"))))) == 1
    change_task(
        db,
        team["host"],
        tasks[1],
        TaskChange(
            expected_version=1,
            content=tasks[1].content,
            due_date=date(2026, 10, 10),
            owner_ids=[team["a"].id],
            verifier_id=team["host"].id,
            reason="本期延期",
        ),
    )
    assert generate_instances(db, date(2026, 10, 5)) == 1
    assert first.due_date == date(2026, 9, 25)
    assert db.scalar(select(Task).where(Task.recurrence_period == date(2026, 10, 5))).due_date == date(
        2026, 10, 9
    )


def test_month_end_and_end_date_are_calendar_based(db, team, draft_factory, monkeypatch):
    first, rule = seed(db, team, draft_factory, monkeypatch, "monthly", date(2027, 1, 31))
    rule.end_date = date(2027, 3, 31)
    assert generate_instances(db, date(2027, 2, 1)) == 1
    assert generate_instances(db, date(2027, 4, 1)) == 1
    assert [t.due_date for t in db.scalars(select(Task).order_by(Task.due_date))] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
    ]
    assert not rule.active


def update_body(rule, **changes):
    return RecurrenceUpdate(
        **{
            **rule.template,
            "expected_version": rule.version,
            "active": rule.active,
            "end_date": rule.end_date,
            "reason": "维护周期执行安排",
            **changes,
        }
    )


def test_pause_resume_skips_paused_periods_and_preserves_issued_instances(
    db, team, draft_factory, monkeypatch
):
    first, rule = seed(db, team, draft_factory, monkeypatch)
    body = update_body(rule, active=False)
    update_rule(db, team["host"], rule.id, body)
    with pytest.raises(HTTPException) as error:
        update_rule(db, team["host"], rule.id, body)
    assert error.value.status_code == 409
    assert generate_instances(db, date(2026, 10, 12)) == 0
    monkeypatch.setattr("app.services.recurrence.today", lambda: date(2026, 10, 12))
    update_rule(db, team["host"], rule.id, update_body(rule, active=True, content="新的周期要求"))
    assert generate_instances(db, date(2026, 10, 12)) == 1
    assert first.content != "新的周期要求" and first.due_date == date(2026, 9, 25)
    assert db.scalar(select(Task).where(Task.id != first.id)).due_date == date(2026, 10, 16)


def test_permissions_duplicate_creation_and_departed_people(db, team, draft_factory, monkeypatch):
    first, rule = seed(db, team, draft_factory, monkeypatch)
    with pytest.raises(HTTPException) as error:
        update_rule(db, team["a"], rule.id, update_body(rule, active=False))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        enable_rule(
            db,
            team["host"],
            first.id,
            RecurrenceCreate(expected_version=1, frequency="weekly", reason="重复"),
        )
    assert error.value.status_code == 409
    team["a"].active = False
    db.flush()
    assert generate_instances(db, date(2026, 9, 28)) == 0
    assert not rule.active and rule.last_error


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL concurrent generation")
def test_concurrent_workers_create_one_instance_per_period(db, team, draft_factory, monkeypatch):
    seed(db, team, draft_factory, monkeypatch)
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def generate(_):
        with factory.begin() as session:
            return generate_instances(session, date(2026, 9, 28))

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(generate, range(2))) == 1
    assert db.scalar(select(func.count()).select_from(Task)) == 2
