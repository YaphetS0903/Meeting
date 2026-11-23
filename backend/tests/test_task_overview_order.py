from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Meeting, Membership, Task, TaskOwner, User


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def fetch(client, actor, **params):
    return client.get(
        "/api/task-overview",
        params=params,
        headers={"Authorization": "Bearer " + issue_token(actor)},
    )


@pytest.fixture
def tasks(db, team):
    team["dep"].name = "信息部"
    team["a"].name, team["b"].name = "王晨", "袁明"

    def add(identifier, owners, due="2026-11-06", meeting=None):
        task = Task(
            id=identifier, code=identifier, content="核对" + identifier,
            due_date=date.fromisoformat(due) if due else None,
            verifier_id=team["host"].id, source_item_id=identifier,
            source_meeting_id=meeting.id if meeting else None,
            department_id=meeting.department_id if meeting else None,
            created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        db.add(task)
        db.flush()
        db.add_all(TaskOwner(task_id=task.id, user_id=p.id) for p in owners)
        db.flush()
        return task

    return add


@pytest.mark.parametrize("source", ["direct", "meeting"])
def test_orders_department_then_deadline_then_person(client, db, team, tasks, source):
    meeting = None
    if source == "meeting":
        meeting = Meeting(
            title="跨部门周会", department_id=team["other"].id,
            meeting_date=date(2026, 11, 2), host_id=team["host"].id,
        )
        db.add(meeting)
        db.flush()
    # Creation order, IDs and the meeting's department must not determine this order.
    tasks("z-early", [team["b"]], "2026-11-03", meeting)
    tasks("a-later", [team["a"]], "2026-11-13", meeting)
    tasks("a-yuan", [team["b"]], meeting=meeting)
    tasks("z-wang", [team["a"]], meeting=meeting)
    tasks("a-no-deadline", [team["a"]], None, meeting)
    tasks("a-finance", [team["outsider"]], "2026-10-01", meeting)
    response = fetch(client, team["host"], source=source)
    assert response.status_code == 200
    result = response.json()
    assert [r["id"] for r in result["items"]] == [
        "z-early", "z-wang", "a-yuan", "a-later", "a-no-deadline", "a-finance",
    ]
    assert result["items"][0]["department_names"] == ["信息部"]
    assert result["items"][-1]["department_names"] == ["财务部"]
    assert result["summary"]["total"] == result["total"] == 6
    for row in result["items"]:
        assert row["source_title"] == ("跨部门周会" if meeting else "直接派发")
        assert row["source_meeting_date"] == ("2026-11-02" if meeting else None)


def test_shared_tasks_and_memberships_do_not_duplicate_pages(client, db, team, tasks):
    db.add(Membership(user_id=team["a"].id, department_id=team["dep"].id))
    db.add(Membership(user_id=team["b"].id, department_id=team["other"].id))
    for index in reversed(range(27)):
        tasks(f"task-{index:02}", [team["b"], team["a"]])
    first = fetch(client, team["host"], limit=20).json()
    second = fetch(client, team["host"], offset=20, limit=20).json()
    combined = first["items"] + second["items"]
    assert [r["id"] for r in combined] == [f"task-{i:02}" for i in range(27)]
    assert first["total"] == second["total"] == 27
    assert first["summary"]["total"] == 27
    assert all(r["department_names"] == ["信息部", "财务部"] for r in combined)
    assert all(r["owner_names"] == ["王晨", "袁明"] for r in combined)
    # Filtering uses the same primary/additional department assignments.
    result = fetch(client, team["host"], department_id=team["other"].id, limit=100).json()
    assert result["total"] == len(result["items"]) == 27


def test_missing_department_sorts_last_and_membership_supplies_department(client, db, team, tasks):
    unknown = User(name="未知部门")
    member = User(name="只有兼职部门")
    db.add_all([unknown, member])
    db.flush()
    db.add(Membership(user_id=member.id, department_id=team["dep"].id))
    tasks("a-unknown", [unknown], "2026-01-01")
    tasks("z-member", [member], None)
    result = fetch(client, team["host"]).json()
    assert [r["id"] for r in result["items"]] == ["z-member", "a-unknown"]
    assert result["items"][0]["department_names"] == ["信息部"]
    assert result["items"][1]["department_names"] == []


def test_filters_and_manager_permissions_survive_sorting(client, db, team, tasks):
    team["colleague"].position = "信息部部长"
    new_grant(db,team["colleague"],[rule("overview",["view"])])
    tasks("it-early", [team["a"]], "2026-11-03")
    tasks("it-later", [team["b"]], "2026-11-13")
    tasks("finance", [team["outsider"]], "2026-11-01")
    result = fetch(client, team["colleague"]).json()
    assert [r["id"] for r in result["items"]] == ["it-early", "it-later"]
    assert result["total"] == result["summary"]["total"] == 2
    assert fetch(client, team["colleague"], person_id=team["outsider"].id).status_code == 403
    assert fetch(client, team["colleague"], department_id=team["other"].id).status_code == 403
    assert fetch(client, team["a"]).status_code == 403
    result = fetch(client, team["host"], start="2026-11-05", end="2026-11-20").json()
    assert [r["id"] for r in result["items"]] == ["it-later"]
    result = fetch(client, team["host"], q="no-match").json()
    assert result["total"] == 0 and result["items"] == []
