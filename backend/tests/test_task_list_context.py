from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import (
    MeetingSeries,
    MeetingVersion,
    OutboxEvent,
    SystemSetting,
    Task,
    TaskOwner,
    TaskVersion,
    uid,
)
from app.schemas import PublishRequest, TaskChange
from app.services.business import change_task, create_meeting, publish


@pytest.fixture(autouse=True)
def inbox_date(monkeypatch):
    monkeypatch.setattr("app.services.task_inbox.today", lambda: date(2026, 11, 4))


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


def published_task(db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting, db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))


@pytest.mark.parametrize(
    ("created_at", "start_date"),
    [
        (datetime(2026, 10, 25, 15, 59, tzinfo=timezone.utc), "2026-10-25"),
        (datetime(2026, 10, 25, 16, tzinfo=timezone.utc), "2026-10-26"),
        (datetime(2026, 10, 25, 16, 30), "2026-10-26"),
    ],
)
def test_task_list_uses_first_creation_date_in_china_timezone(
    client, db, team, draft_factory, created_at, start_date
):
    meeting, task = published_task(db, team, draft_factory)
    task.created_at = created_at
    db.flush()

    response = client.get("/api/tasks", headers=auth(team["a"]))

    assert response.status_code == 200
    row = response.json()[0]
    assert row["start_date"] == start_date
    assert row["source_meeting_title"] == meeting.title
    returned_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    assert returned_at == created_at.replace(tzinfo=timezone.utc)
    assert row["start_date"] != meeting.meeting_date.isoformat()


def test_direct_task_has_direct_assignment_source_title(client, db, team):
    task = Task(
        code="RW-DIRECT-CONTEXT",
        content="直接派发的工作",
        issuer_id=team["host"].id,
        source_item_id=uid(),
        department_id=team["dep"].id,
        verifier_id=team["host"].id,
        due_date=date(2026, 10, 30),
        created_at=datetime(2026, 10, 25, 16, 1, tzinfo=timezone.utc),
    )
    db.add(task)
    db.flush()
    db.add(TaskOwner(task_id=task.id, user_id=team["a"].id))
    db.flush()

    response = client.get("/api/tasks", params={"mine": True}, headers=auth(team["a"]))

    assert response.status_code == 200
    row = response.json()[0]
    assert row["source_meeting_id"] is None
    assert row["source_meeting_title"] == "直接派发"
    assert row["start_date"] == "2026-10-26"


def test_task_visibility_does_not_grant_source_meeting_title(client, db, team, draft_factory):
    meeting, task = published_task(db, team, draft_factory)
    executive = team["outsider"]
    executive.position = "总经理"
    new_grant(db,executive,[rule("overview",["view"],"all")])
    db.flush()
    headers = auth(executive)

    assert client.get(f"/api/meetings/{meeting.id}", headers=headers).status_code == 403
    response = client.get("/api/tasks", headers=headers)

    assert response.status_code == 200
    row = next(row for row in response.json() if row["id"] == task.id)
    assert row["source_meeting_id"] == meeting.id
    assert row["source_meeting_title"] is None
    assert meeting.title not in response.text


def test_current_meeting_title_and_task_changes_preserve_original_start_and_snapshots(
    client, db, team, draft_factory
):
    meeting, task = published_task(db, team, draft_factory)
    task.created_at = datetime(2026, 8, 9, 16, 1, tzinfo=timezone.utc)
    meeting_version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    task_version = db.scalar(select(TaskVersion).where(TaskVersion.task_id == task.id))
    meeting_snapshot = deepcopy(meeting_version.snapshot)
    task_snapshot = deepcopy(task_version.snapshot)
    meeting.title = "更新后的会议主题"
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=task.version,
            content="调整后的工作安排",
            due_date=date(2026, 10, 30),
            owner_ids=[team["a"].id],
            verifier_id=team["host"].id,
            reason="调整要求",
        ),
        notify=False,
    )
    db.flush()

    row = client.get("/api/tasks", headers=auth(team["a"])).json()[0]

    assert row["source_meeting_title"] == "更新后的会议主题"
    assert row["start_date"] == "2026-08-10"
    assert row["version"] == 2
    assert meeting_version.snapshot == meeting_snapshot
    assert task_version.snapshot == task_snapshot


def test_historical_minutes_do_not_invent_task_start_dates(client, db, team, draft_factory):
    meeting = create_meeting(db, team["host"], draft_factory(section="review"), historical=True)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))

    response = client.get("/api/tasks", headers=auth(team["a"]))

    assert response.status_code == 200
    assert response.json() == []
    assert db.scalar(select(Task).where(Task.source_meeting_id == meeting.id)) is None


def inbox(client, person, **params):
    response = client.get(
        "/api/tasks", params={"current": True, "mine": True, **params}, headers=auth(person)
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize(
    ("due_date", "expected"),
    [(date(2026, 10, 30), "到期未验证"),
     (date(2026, 11, 3), "到期未验证"),
     (date(2026, 11, 4), "未到期验证"),
     (date(2026, 11, 16), "未到期验证"),
     (None, "未到期验证")],
)
def test_meeting_task_waits_for_published_review_with_date_aware_label_without_changing_live_data(
    client, db, team, draft_factory, due_date, expected
):
    draft = draft_factory()
    draft.items[0].due_date = due_date
    if due_date is None:
        # Existing legacy tasks may have no deadline, although new publication requires one.
        draft.items[0].due_date = date(2026, 11, 16)
    meeting, task = published_task(db, team, lambda: draft)
    task.due_date = due_date
    db.flush()
    before = {column.name: getattr(task, column.name) for column in Task.__table__.columns}
    events = list(db.scalars(select(OutboxEvent.id)))

    row = inbox(client, team["a"])[0]
    detail = client.get(f"/api/tasks/{task.id}", headers=auth(team["a"])).json()

    assert row["todo_status"] == detail["todo_status"] == expected
    assert row["inbox_meeting_date"] == meeting.meeting_date.isoformat()
    assert row["inbox_meeting_title"] == meeting.title
    assert inbox(client, team["a"], state="未完成") == []
    assert inbox(client, team["a"], state=expected)[0]["id"] == task.id
    other = "未到期验证" if expected == "到期未验证" else "到期未验证"
    assert inbox(client, team["a"], state=other) == []
    assert {column.name: getattr(task, column.name) for column in Task.__table__.columns} == before
    assert list(db.scalars(select(OutboxEvent.id))) == events


@pytest.mark.parametrize("result", ["未完成", "未到期", "不统计", "关闭", "已完成"])
@pytest.mark.parametrize("due_date", [date(2026, 8, 17), date(2026, 11, 16)])
def test_published_review_drives_inbox_and_hides_closed_work(client, db, team, draft_factory, result, due_date):
    original = draft_factory()
    original.items[0].due_date = due_date
    _, task = published_task(db, team, lambda: original)
    pending = "到期未验证" if due_date < date(2026, 11, 4) else "未到期验证"
    draft = draft_factory(
        section="review",
        task_id=task.id,
        task_version=task.version,
        status=result,
        when=date(2026, 8, 14 if result == "未到期" else 19),
    )
    draft.items[0].due_date = due_date
    review = create_meeting(db, team["host"], draft)
    # Merely creating/editing a review draft never changes a person's inbox.
    assert inbox(client, team["a"])[0]["todo_status"] == pending
    publish(db, team["host"], review.id, PublishRequest(expected_version=1, notify=False))

    rows = inbox(client, team["a"])
    if result in {"关闭", "已完成"}:
        # Minutes confirmation closes a completed live task as well.
        assert rows == []
    else:
        assert len(rows) == 1
        assert rows[0]["id"] == task.id
        assert rows[0]["todo_status"] == (pending if result == "未到期" else result)
        assert rows[0]["inbox_meeting_id"] == review.id


def test_unreviewed_label_changes_after_deadline_without_changing_task_or_minutes(
    client, db, team, draft_factory, monkeypatch
):
    draft = draft_factory()
    draft.items[0].due_date = date(2026, 11, 4)
    meeting, task = published_task(db, team, lambda: draft)
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    snapshot_before = deepcopy(version.snapshot)
    task_before = {column.name: getattr(task, column.name) for column in Task.__table__.columns}
    events_before = list(db.scalars(select(OutboxEvent.id)))
    assert inbox(client, team["a"])[0]["todo_status"] == "未到期验证"
    monkeypatch.setattr("app.services.task_inbox.today", lambda: date(2026, 11, 5))
    assert inbox(client, team["a"])[0]["todo_status"] == "到期未验证"
    detail = client.get(f"/api/tasks/{task.id}", headers=auth(team["a"])).json()
    assert detail["todo_status"] == "到期未验证"
    assert {column.name: getattr(task, column.name) for column in Task.__table__.columns} == task_before
    assert version.snapshot == snapshot_before
    assert list(db.scalars(select(OutboxEvent.id))) == events_before


def test_latest_roster_includes_carried_tasks_and_ignores_future_drafts(client, db, team, draft_factory):
    first, task = published_task(db, team, draft_factory)
    old = create_meeting(db, team["host"], draft_factory(when=date(2026, 8, 11)))
    publish(db, team["host"], old.id, PublishRequest(expected_version=1, notify=False))
    old_task = db.scalar(select(Task).where(Task.source_meeting_id == old.id))
    second = create_meeting(
        db,
        team["host"],
        draft_factory(
            section="review",
            task_id=task.id,
            task_version=task.version,
            status="未完成",
            when=date(2026, 8, 19),
        ),
    )
    publish(db, team["host"], second.id, PublishRequest(expected_version=1, notify=False))
    create_meeting(db, team["host"], draft_factory(when=date(2026, 8, 26)))

    rows = inbox(client, team["a"])
    assert [row["id"] for row in rows] == [task.id]
    assert rows[0]["inbox_meeting_date"] == "2026-08-19"
    assert rows[0]["source_meeting_id"] == first.id
    assert inbox(client, team["a"], q=old_task.code) == []
    assert inbox(client, team["a"], q="2026-08-19")[0]["id"] == task.id
    assert inbox(client, team["a"], q=second.title)[0]["id"] == task.id


def test_latest_is_independent_per_series_and_uses_meeting_date_order(client, db, team, draft_factory):
    first, first_task = published_task(db, team, draft_factory)
    series = MeetingSeries(name="另一会议系列", department_id=team["dep"].id)
    db.add(series)
    db.flush()
    draft = draft_factory(when=date(2026, 8, 20))
    draft.series_id = series.id
    draft.items[0].due_date = date(2026, 8, 30)
    second = create_meeting(db, team["host"], draft)
    publish(db, team["host"], second.id, PublishRequest(expected_version=1, notify=False))

    rows = inbox(client, team["a"])
    assert [row["inbox_meeting_id"] for row in rows] == [first.id, second.id]
    assert {row["id"] for row in rows} >= {first_task.id}


@pytest.mark.parametrize("current", [False, True])
def test_inbox_orders_meeting_and_direct_tasks_by_deadline_before_pagination(
    client, db, team, draft_factory, current
):
    _, meeting_task = published_task(db, team, draft_factory)
    meeting_task.due_date = date(2026, 11, 5)
    dates = [None, date(2026, 11, 16), date(2026, 11, 4), date(2026, 10, 30), date(2026, 11, 5)]
    tasks = []
    for index, deadline in enumerate(dates):
        task = Task(
            code=f"RW-DEADLINE-{index}", content="待办排序核对",
            issuer_id=team["host"].id, source_item_id=uid(),
            department_id=team["dep"].id, verifier_id=team["host"].id,
            due_date=deadline, created_at=datetime(2026, 11, 1, index, tzinfo=timezone.utc),
        )
        db.add(task)
        db.flush()
        db.add(TaskOwner(task_id=task.id, user_id=team["a"].id))
        tasks.append(task)
    db.flush()
    expected = sorted([meeting_task, *tasks], key=lambda task: (
        task.due_date is None, task.due_date or date.max,
        task.created_at.replace(tzinfo=timezone.utc), task.id,
    ))
    before = [(task.id, task.due_date, task.version) for task in expected]
    event_ids = list(db.scalars(select(OutboxEvent.id)))
    params = {"current": current, "mine": True}
    all_rows = client.get("/api/tasks", params=params, headers=auth(team["a"])).json()
    assert [row["id"] for row in all_rows] == [task.id for task in expected]
    paged = []
    for offset in (0, 2, 4):
        response = client.get("/api/tasks", params={**params, "offset": offset, "limit": 2},
                              headers=auth(team["a"]))
        assert response.status_code == 200
        assert response.json()["total"] == 6
        paged.extend(response.json()["items"])
    assert [row["id"] for row in paged] == [task.id for task in expected]
    filtered = client.get("/api/tasks", params={**params, "q": "待办排序核对"},
                          headers=auth(team["a"])).json()
    assert [row["id"] for row in filtered] == [task.id for task in expected if task != meeting_task]
    assert [(task.id, task.due_date, task.version) for task in expected] == before
    assert list(db.scalars(select(OutboxEvent.id))) == event_ids


def test_only_explicitly_hidden_test_ids_are_removed_and_direct_assignments_still_work(client, db, team):
    identifiers = []
    for content in ["测试", "生产系统测试验收", "正常派发任务"]:
        task = Task(
            code="RW-" + uid().replace("-", "")[:24],
            content=content,
            issuer_id=team["host"].id,
            source_item_id=uid(),
            department_id=team["dep"].id,
            verifier_id=team["host"].id,
            due_date=date(2026, 8, 1),
        )
        db.add(task)
        db.flush()
        db.add(TaskOwner(task_id=task.id, user_id=team["a"].id))
        identifiers.append(task.id)
    db.add(SystemSetting(key="task_inbox", value={"hidden_task_ids": [identifiers[0]]}))
    db.flush()

    rows = inbox(client, team["a"])
    assert {row["id"] for row in rows} == set(identifiers[1:])
    assert all(row["todo_status"] == "未完成" for row in rows)
    assert all(row["source_meeting_title"] == "直接派发" for row in rows)
    assert len(client.get("/api/tasks", headers=auth(team["a"])).json()) == 3
    assert inbox(client, team["outsider"]) == []


def test_current_inbox_does_not_disclose_hidden_meeting_date_or_title(client, db, team, draft_factory):
    meeting, task = published_task(db, team, draft_factory)
    team["outsider"].position = "总经理"
    new_grant(db,team["outsider"],[rule("overview",["view"],"all")])
    db.flush()
    row = inbox(client, team["outsider"], mine=False)[0]
    assert row["id"] == task.id
    assert row["inbox_meeting_title"] is None
    assert row["inbox_meeting_date"] is None
    assert meeting.title not in str(row)
