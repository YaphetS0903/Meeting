from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import MeetingVersion, OutboxEvent, Task, TaskOwner, uid
from app.schemas import PublishRequest
from app.services.business import create_meeting, publish
from app.services.role_migration import rule
from tests.role_helpers import grant


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


def archive(client, person, **params):
    response = client.get("/api/task-history", params=params, headers=auth(person))
    assert response.status_code == 200, response.text
    return response.json()


def release(db, team, draft, *, historical=False):
    meeting = create_meeting(db, team["host"], draft, historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def direct(db, team, status="未完成", *, owner="a", closed=False, withdrawn=False):
    task = Task(
        code="RW-" + uid()[:12],
        content=f"直接派发历史任务 {status}",
        source_item_id=uid(),
        issuer_id=team["host"].id,
        verifier_id=team["host"].id,
        department_id=team["dep"].id,
        due_date=date(2026, 8, 17),
        status=status,
        closed_at=datetime(2026, 9, 1, tzinfo=timezone.utc) if closed else None,
        withdrawn_at=datetime(2026, 9, 1, tzinfo=timezone.utc) if withdrawn else None,
    )
    db.add(task)
    db.flush()
    db.add(TaskOwner(task_id=task.id, user_id=team[owner].id))
    db.flush()
    return task


@pytest.mark.parametrize("status", ["未完成", "已完成", "未到期", "不统计", "关闭"])
def test_imported_review_without_live_task_is_readable_and_filterable(
    client, db, team, draft_factory, status
):
    meeting = release(db, team, draft_factory(section="review", status=status), historical=True)
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    before = deepcopy(version.snapshot)
    events = list(db.scalars(select(OutboxEvent.id)))
    assert db.scalar(select(Task)) is None

    result = archive(client, team["a"], state=status)
    assert result["total"] == 1
    row = result["items"][0]
    assert row["history_only"] is True
    assert row["todo_status"] == status
    assert row["content"] == before["items"][0]["content"]
    assert row["source_meeting_id"] == meeting.id
    assert row["inbox_meeting_date"] == "2026-08-10"
    assert archive(client, team["a"], state="未完成" if status != "未完成" else "关闭")["total"] == 0
    assert client.get("/api/tasks?mine=true&current=true", headers=auth(team["a"])).json() == []
    assert db.scalar(select(Task)) is None
    assert version.snapshot == before
    assert list(db.scalars(select(OutboxEvent.id))) == events


def test_archive_includes_superseded_meeting_tasks_and_deduplicates_linked_reviews(
    client, db, team, draft_factory
):
    first = release(db, team, draft_factory())
    task = db.scalar(select(Task).where(Task.source_meeting_id == first.id))
    older = release(db, team, draft_factory(when=date(2026, 8, 11)))
    older_task = db.scalar(select(Task).where(Task.source_meeting_id == older.id))
    review = release(db, team, draft_factory(
        section="review", task_id=task.id, task_version=task.version,
        status="未完成", when=date(2026, 8, 19),
    ))
    current = client.get("/api/tasks?mine=true&current=true", headers=auth(team["a"])).json()
    assert [row["id"] for row in current] == [task.id]

    rows = archive(client, team["a"])["items"]
    assert {row["id"] for row in rows} == {task.id, older_task.id}
    assert rows[0]["id"] == task.id
    assert rows[0]["inbox_meeting_date"] == review.meeting_date.isoformat()
    assert archive(client, team["a"], q=older_task.code)["items"][0]["id"] == older_task.id


@pytest.mark.parametrize(
    ("status", "closed", "withdrawn", "expected"),
    [("未完成", False, False, "未完成"), ("不统计", False, False, "不统计"),
     ("关闭", True, False, "关闭"), ("不统计", False, True, "已撤回")],
)
def test_direct_task_lifecycle_filters(client, db, team, status, closed, withdrawn, expected):
    task = direct(db, team, status, closed=closed, withdrawn=withdrawn)
    result = archive(client, team["a"], state=expected)
    assert result["total"] == 1
    assert result["items"][0]["id"] == task.id
    assert result["items"][0]["todo_status"] == expected
    assert archive(client, team["a"], state="已完成")["total"] == 0


def test_closed_meeting_task_remains_closed_over_older_review(client, db, team, draft_factory):
    meeting = release(db, team, draft_factory())
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    release(db, team, draft_factory(
        section="review", task_id=task.id, task_version=task.version,
        status="未完成", when=date(2026, 8, 19),
    ))
    task.status = "关闭"
    task.closed_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db.flush()
    rows = archive(client, team["a"], state="关闭")["items"]
    assert len(rows) == 1 and rows[0]["id"] == task.id
    assert rows[0]["recorded_status"] == "未完成"
    assert rows[0]["todo_status"] == "关闭"


def test_personal_scope_and_unchecking_mine_never_grant_unrelated_data(client, db, team, draft_factory):
    release(db, team, draft_factory(section="review", status="不统计"), historical=True)
    task = direct(db, team)
    unrelated = direct(db, team, owner="outsider")
    assert archive(client, team["a"])["total"] == 2
    assert archive(client, team["host"])["total"] == 0
    assert archive(client, team["host"], mine=False)["total"] == 3
    other_rows = archive(client, team["outsider"], mine=False)["items"]
    assert [row["id"] for row in other_rows] == [unrelated.id]
    assert task.id not in str(other_rows)
    assert archive(client, team["colleague"], mine=False)["total"] == 0


def test_unpublished_changes_and_old_revision_are_not_exposed(client, db, team, draft_factory):
    meeting = release(db, team, draft_factory(section="review", status="不统计"), historical=True)
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    old = deepcopy(version.snapshot)
    old["items"][0]["content"] = "旧修订内容不应重复出现"
    db.add(MeetingVersion(meeting_id=meeting.id, number=2, snapshot=old, publisher_id=team["host"].id))
    meeting.draft = {**meeting.draft, "items": [{**meeting.draft["items"][0], "content": "未发布秘密"}]}
    draft = draft_factory(section="review")
    draft.items[0].content = "草稿任务不可见"
    create_meeting(db, team["host"], draft, historical=True)
    db.flush()
    rows = archive(client, team["a"])["items"]
    assert len(rows) == 1
    assert rows[0]["content"] == version.snapshot["items"][0]["content"]
    assert archive(client, team["a"], q="未发布秘密")["total"] == 0


def test_partial_result_follows_each_person_and_all_view_shows_mixed_result(client, db, team, draft_factory):
    draft = draft_factory(section="review")
    draft.items[0].status = "部分完成"
    draft.items[0].owner_results = {team["a"].id: "已完成", team["b"].id: "未完成"}
    release(db, team, draft, historical=True)
    assert archive(client, team["a"], state="已完成")["total"] == 1
    assert archive(client, team["a"], state="未完成")["total"] == 0
    assert archive(client, team["b"], state="未完成")["total"] == 1
    assert archive(client, team["host"], mine=False, state="部分完成")["total"] == 1


def test_search_count_and_pagination_share_the_same_scope(client, db, team, draft_factory):
    for day in [10, 11, 12]:
        release(db, team, draft_factory(section="review", when=date(2026, 8, day)), historical=True)
    first = archive(client, team["a"], q="技术部周例会", offset=0, limit=2)
    second = archive(client, team["a"], q="技术部周例会", offset=2, limit=2)
    assert first["total"] == second["total"] == 3
    assert [row["inbox_meeting_date"] for row in first["items"] + second["items"]] == [
        "2026-08-12", "2026-08-11", "2026-08-10"
    ]
    assert archive(client, team["a"], q="2026-08-11")["total"] == 1
    assert archive(client, team["a"], offset=100)["items"] == []
    for params in [{"offset": -1}, {"limit": 101}]:
        assert client.get("/api/task-history", params=params, headers=auth(team["a"])).status_code == 422


def test_task_overview_access_does_not_expose_restricted_meeting_metadata(client, db, team, draft_factory):
    meeting = release(db, team, draft_factory())
    viewer = team["outsider"]
    grant(db, viewer, [rule("overview", ["view"], "all")])
    db.flush()
    assert client.get(f"/api/meetings/{meeting.id}", headers=auth(viewer)).status_code == 403
    result = archive(client, viewer, mine=False)
    assert result["total"] == 1
    row = result["items"][0]
    assert row.get("source_meeting_title") is None
    assert row.get("inbox_meeting_title") is None
    assert row.get("inbox_meeting_date") is None
    assert archive(client, viewer, mine=False, q=meeting.title)["total"] == 0
    assert client.get("/api/task-history").status_code == 401
