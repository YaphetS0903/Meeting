from datetime import date, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import native_todo_api
from app.core.config import get_settings
from app.extra_api import sync_native_todo_task
from app.models import MeetingVersion, NativeTodoAttempt, NativeTodoLink, OutboxEvent, Task
from app.native_todo_api import Bind, Prepare, Reset
from app.schemas import PublishRequest
from app.services import app_native_todo, native_todo
from app.services.access import direct_assignees
from app.services.business import create_meeting, enqueue_native_todo, publish


@pytest.fixture
def meeting_app(db, team, draft_factory, monkeypatch):
    settings = get_settings()
    for key, value in {
        "native_todo_enabled": True,
        "native_todo_app_enabled": True,
        "native_todo_meeting_enabled": True,
        "native_todo_direct_enabled": False,
        "native_todo_all_active_users": False,
        "native_todo_allowed_wecom_ids": "",
        "public_url": "https://meetings.example.test",
    }.items():
        monkeypatch.setattr(settings, key, value)
    team["host"].is_admin = False
    team["host"].position = "工程师"
    for name in ("host", "a", "b", "colleague"):
        team[name].wecom_id = "wecom-" + name
        team[name].native_todo_userid = None
    monkeypatch.setattr(native_todo, "_run", lambda *_a, **_k: pytest.fail("must not use robot CLI"))
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    return meeting, task


def mock_created_todo(db, monkeypatch, task, prepared, *, creator="wecom-host", attendees=None):
    attempt = db.get(NativeTodoAttempt, prepared["attempt_id"])
    created = attempt.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    remote = {
        "creator": creator, "content": prepared["payload"]["content"],
        "create_time": int(created.timestamp()),
        "end_time": prepared["payload"].get("endTime", 0),
        "attendees": [{"userid": value, "status": 1} for value in (attendees or ["wecom-a", "wecom-b"])],
    }
    calls = []

    class Provider:
        def request(self, endpoint, *, body, write=False):
            calls.append((endpoint, body, write))
            if endpoint == "todo/update":
                remote.update(body)
                return {}
            return dict(remote)

    monkeypatch.setattr(native_todo_api, "WeCom", Provider)
    monkeypatch.setattr(app_native_todo, "WeCom", Provider)
    return calls


def test_meeting_editor_without_direct_assignment_role_can_confirm_all_owners(
    db, team, meeting_app, monkeypatch,
):
    meeting, task = meeting_app
    assert direct_assignees(db, team["host"]) == []
    assert native_todo_api.sdk_identity(team["host"])["corpId"] == get_settings().wecom_corp_id
    plan = native_todo_api.meeting_native_todos(meeting.id, db, team["host"])
    assert plan["can_confirm"] and plan["published_version"] == 1
    assert plan["tasks"][0]["id"] == task.id
    assert set(plan["tasks"][0]["owner_names"]) == {team["a"].name, team["b"].name}
    assert plan["tasks"][0]["native_todo"]["can_create"]
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")))
    prepared = native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    assert prepared["payload"]["attendees"] == ["wecom-a", "wecom-b"]
    calls = mock_created_todo(db, monkeypatch, task, prepared, attendees=["wecom-a"])
    body = Bind(attempt_id=prepared["attempt_id"], todo_id="meeting-app-todo")
    native_todo_api.record_native_callback(task.id, body, db, team["host"])
    result = native_todo_api.bind_native(task.id, body, db, team["host"])
    assert result["link"] == {"provider": "app", "todo_id": "meeting-app-todo", "state": "active"}
    assert calls[1][1]["attendees"] == [{"userid": "wecom-a", "status": 1}, {"userid": "wecom-b", "status": 1}]
    assert not result["pending_attempt"]
    with pytest.raises(HTTPException, match="已有关联待办"):
        native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    assert native_todo_api.bind_native(task.id, body, db, team["host"])["link"] == result["link"]


@pytest.mark.parametrize("actor", ["a", "colleague", "outsider"])
def test_responsible_or_same_department_member_cannot_dispatch_meeting_tasks(db, team, meeting_app, actor):
    meeting, task = meeting_app
    with pytest.raises(HTTPException) as exc:
        native_todo_api.meeting_native_todos(meeting.id, db, team[actor])
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team[actor])
    assert exc.value.status_code == 403
    assert db.scalar(select(NativeTodoAttempt)) is None


def test_meeting_recorder_can_dispatch_but_cannot_overwrite_another_creators_attempt(db, team, meeting_app):
    meeting, task = meeting_app
    meeting.recorder_id = team["colleague"].id
    assert native_todo_api.meeting_native_todos(meeting.id, db, team["colleague"])["can_confirm"]
    prepared = native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    with pytest.raises(HTTPException, match="上次原生待办"):
        native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["colleague"])
    with pytest.raises(HTTPException) as exc:
        native_todo_api.record_native_callback(task.id, Bind(attempt_id=prepared["attempt_id"], todo_id="other"), db, team["colleague"])
    assert exc.value.status_code == 403


def test_meeting_uses_latest_published_arrangements_not_draft_or_review(db, team, meeting_app):
    meeting, task = meeting_app
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    original = version.snapshot["items"][0]
    version.snapshot = {**version.snapshot, "items": [{**original, "section": "review"}]}
    meeting.draft = {**meeting.draft, "items": [original]}
    assert native_todo_api.meeting_native_todos(meeting.id, db, team["host"])["tasks"] == []
    with pytest.raises(HTTPException) as exc:
        native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    assert exc.value.status_code == 403


def test_plan_reports_unavailable_and_cross_meeting_arrangements_without_leaking_live_details(
    db, team, meeting_app, draft_factory,
):
    meeting, _ = meeting_app
    other_draft = draft_factory()
    other_draft.host_id = other_draft.recorder_id = team["colleague"].id
    other_draft.series_id = None
    other = create_meeting(db, team["colleague"], other_draft)
    publish(db, team["colleague"], other.id, PublishRequest(expected_version=1))
    foreign = db.scalar(select(Task).where(Task.source_meeting_id == other.id))
    foreign.content = "原任务后续不应泄露的内容"
    version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    version.snapshot = {**version.snapshot, "items": [*version.snapshot["items"],
        {"section": "new", "task_id": foreign.id, "content": "已发布的引用说明", "owner_names": [team["a"].name]},
        {"section": "new", "content": "无系统任务事项", "owner_names": []},
    ]}
    plan = native_todo_api.meeting_native_todos(meeting.id, db, team["host"])
    assert len(plan["tasks"]) == 1
    assert plan["excluded_tasks"][0]["content"] == "已发布的引用说明"
    assert "原任务发布者" in plan["excluded_tasks"][0]["reason"]
    assert "没有可派发" in plan["excluded_tasks"][1]["reason"]
    other.recorder_id = team["host"].id
    plan = native_todo_api.meeting_native_todos(meeting.id, db, team["host"])
    assert len(plan["tasks"]) == 2 and len(plan["excluded_tasks"]) == 1
    assert plan["tasks"][1]["native_todo"]["can_create"]


@pytest.mark.parametrize("restriction", ["disabled", "unbound", "historical", "stale", "closed"])
def test_meeting_creation_guards_before_any_sdk_call(db, team, meeting_app, monkeypatch, restriction):
    meeting, task = meeting_app
    if restriction == "disabled":
        monkeypatch.setattr(get_settings(), "native_todo_meeting_enabled", False)
    elif restriction == "unbound":
        team["host"].wecom_id = None
    elif restriction == "historical":
        meeting.historical = True
    elif restriction == "stale":
        task.version = 2
    else:
        from app.models import now

        task.closed_at = now()
    with pytest.raises(HTTPException) as exc:
        native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    assert exc.value.status_code in {403, 409}
    assert db.scalar(select(NativeTodoAttempt)) is None


def test_cancelled_attempt_replaced_and_late_callback_cannot_bind_new_creation(db, team, meeting_app):
    _, task = meeting_app
    first = native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    native_todo_api.reset_native(task.id, Reset(attempt_id=first["attempt_id"], reason="cancelled"), db, team["host"])
    second = native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    assert second["attempt_id"] != first["attempt_id"]
    with pytest.raises(HTTPException) as exc:
        native_todo_api.bind_native(task.id, Bind(attempt_id=first["attempt_id"], todo_id="late-todo"), db, team["host"])
    assert exc.value.status_code == 409


def test_failed_binding_keeps_callback_for_recovery_without_duplicate_creation(db, team, meeting_app, monkeypatch):
    _, task = meeting_app
    prepared = native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    mock_created_todo(db, monkeypatch, task, prepared, creator="wrong-creator")
    body = Bind(attempt_id=prepared["attempt_id"], todo_id="unverified-todo")
    result = native_todo_api.bind_native(task.id, body, db, team["host"])
    assert result.status_code == 422
    db.flush()
    db.expire_all()
    state = native_todo_api.get_native_state(task.id, db, team["host"])
    assert state["pending_attempt"]["todo_id"] == "unverified-todo"
    assert "创建人" in state["pending_attempt"]["verification_error"]
    assert state["link"] is None
    with pytest.raises(HTTPException, match="上次原生待办"):
        native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])


def test_meeting_app_link_synchronizes_and_finishes_through_application_provider(db, team, meeting_app, monkeypatch):
    _, task = meeting_app
    prepared = native_todo_api.prepare_native(task.id, Prepare(expected_version=1), db, team["host"])
    calls = mock_created_todo(db, monkeypatch, task, prepared)
    body = Bind(attempt_id=prepared["attempt_id"], todo_id="meeting-app-todo")
    native_todo_api.bind_native(task.id, body, db, team["host"])
    link = db.scalar(select(NativeTodoLink))
    task.version += 1
    enqueue_native_todo(db, task, "update")
    db.flush()
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "native_todo"))
    native_todo.send_native_todo(db, event)
    assert link.task_version == task.version
    from app.models import now

    task.closed_at = now()
    enqueue_native_todo(db, task, "finish")
    result = native_todo.send_native_todo(db, SimpleNamespace(payload={"task_id": task.id, "task_version": task.version, "action": "finish"}))
    assert result["provider"] == "app" and link.state == "finished"
    assert calls[-1] == ("todo/update", {"todo_id": "meeting-app-todo", "status": 0}, True)


def test_existing_cli_meeting_link_remains_synchronized_when_app_mode_is_enabled(db, team, meeting_app, monkeypatch):
    _, task = meeting_app
    team["a"].native_todo_userid = "native-a"
    team["b"].native_todo_userid = "native-b"
    monkeypatch.setattr(get_settings(), "native_todo_all_active_users", True)
    db.add(NativeTodoLink(task_id=task.id, todo_id="legacy-robot-todo", provider="cli", task_version=1))
    db.flush()
    calls = []

    def cli(*arguments, payload, **kwargs):
        calls.append((arguments, payload))
        return {"items": [{"success": True, "todo_id": "legacy-robot-todo"}]}

    monkeypatch.setattr(native_todo, "_run", cli)
    task.due_date = date(2026, 10, 30)
    enqueue_native_todo(db, task, "update")
    db.flush()
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "native_todo"))
    assert native_todo.send_native_todo(db, event)["action"] == "update"
    assert calls[0][0] == ("todo", "update")


def test_manual_sync_of_unconfirmed_meeting_task_points_to_application_confirmation(db, team, meeting_app):
    _, task = meeting_app
    team["host"].is_admin = True
    with pytest.raises(HTTPException, match="会议发布者在企业微信中确认") as exc:
        sync_native_todo_task(task.id, db, team["host"])
    assert exc.value.status_code == 409
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")))
