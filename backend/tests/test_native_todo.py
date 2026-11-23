from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.extra_api import resume_native_todo, sync_native_todo_task
from app.main import DirectAssignmentCreate, create_direct_assignment
from app.models import NativeTodoLink, OutboxEvent, Task
from app.schemas import ApplyCompletion, PublishRequest, TaskChange, VerifyCompletion
from app.services import native_todo
from app.services.business import apply_completion, change_task, create_meeting, publish, verify_completion
from app.services.integrations import NotConfigured


def test_meeting_app_channel_requires_confirmation_and_does_not_fall_back_to_cli(
    db, team, draft_factory, monkeypatch,
):
    configure_pilot(monkeypatch, team)
    settings = get_settings()
    monkeypatch.setattr(settings, "native_todo_app_enabled", True)
    monkeypatch.setattr(settings, "native_todo_all_active_users", True)
    calls = []

    def provider(*arguments, payload, **_):
        calls.append((arguments, payload))
        return {"items": [{"success": True, "todo_id": "meeting-automatic-todo"}]}

    monkeypatch.setattr(native_todo, "_run", provider)
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert event(db, "create") is None
    task = db.scalar(select(Task))
    old_event = SimpleNamespace(payload={"task_id": task.id, "task_version": 1, "action": "create"})
    assert "skipped" in native_todo.send_native_todo(db, old_event)
    assert calls == [] and db.scalar(select(NativeTodoLink)) is None
    # Retrying publication of an already committed draft cannot create another event.
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")))


def configure_pilot(monkeypatch, team, *, mapped=True):
    settings = get_settings()
    monkeypatch.setattr(settings, "native_todo_enabled", True)
    monkeypatch.setattr(settings, "native_todo_direct_enabled", True)
    monkeypatch.setattr(settings, "native_todo_meeting_enabled", True)
    monkeypatch.setattr(settings, "native_todo_app_enabled", False)
    monkeypatch.setattr(settings, "native_todo_all_active_users", False)
    monkeypatch.setattr(settings, "native_todo_allowed_wecom_ids", "wecom-a")
    team["a"].wecom_id = "wecom-a"
    team["a"].native_todo_userid = "wo-native-a" if mapped else None
    team["b"].wecom_id = "wecom-b"
    team["b"].native_todo_userid = "wo-native-b"


def event(db, action):
    return db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.channel == "native_todo",
            OutboxEvent.payload["action"].as_string() == action,
        )
    )


@pytest.mark.parametrize("source", ["meeting", "direct"])
def test_native_todo_follows_verified_system_lifecycle(db, team, draft_factory, monkeypatch, source):
    configure_pilot(monkeypatch, team)
    calls = []

    def provider(*arguments, payload, **_):
        calls.append((arguments, payload))
        todo_id = payload["items"][0].get("todo_id", "todo-test-1")
        return {"items": [{"success": True, "todo_id": todo_id}]}

    monkeypatch.setattr(native_todo, "_run", provider)
    if source == "meeting":
        meeting = create_meeting(db, team["host"], draft_factory())
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    else:
        direct_task(db, team)
    db.flush()
    task = db.scalar(select(Task))

    create_event = event(db, "create")
    assert create_event and create_event.state == "pending"
    result = native_todo.send_native_todo(db, create_event)
    db.flush()
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    assert result == {"todo_id": "todo-test-1", "action": "create"}
    assert link.follower_ids == ["wo-native-a"]
    assert calls[0][1]["items"][0]["follower_ids"] == ["wo-native-a"]
    assert task.closed_at is None and task.status != "关闭"

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
            reason="原生待办改期测试",
        ),
    )
    db.flush()
    update_event = event(db, "update")
    native_todo.send_native_todo(db, update_event)
    assert calls[-1][0] == ("todo", "update")
    assert calls[-1][1]["items"][0]["deadline"]["value"] == "2026-09-30"
    assert calls[-1][1]["items"][0]["followers"] == [{"userid": "wo-native-a"}]

    request = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(
            expected_version=2,
            idempotency_key="native-todo-completion",
            explanation="已完成，申请验证",
        ),
    )
    verify_completion(
        db,
        team["host"],
        request.id,
        VerifyCompletion(decision="approved", opinion="验证通过"),
    )
    db.flush()
    finish_event = event(db, "finish")
    native_todo.send_native_todo(db, finish_event)
    assert calls[-1][0] == ("todo", "finish")
    assert calls[-1][1]["items"][0] == {"todo_id": "todo-test-1", "finished_all": True}
    assert link.state == "finished"
    assert task.status == "关闭"


def test_native_todo_missing_mapping_blocks_without_provider_call(
    db, team, draft_factory, monkeypatch
):
    configure_pilot(monkeypatch, team, mapped=False)
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.flush()

    monkeypatch.setattr(
        native_todo,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    with pytest.raises(NotConfigured, match="责任人甲"):
        native_todo.send_native_todo(db, event(db, "create"))


def test_native_todo_is_not_queued_while_feature_is_disabled(db, team, draft_factory, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "native_todo_enabled", False)
    monkeypatch.setattr(settings, "native_todo_allowed_wecom_ids", "wecom-a")
    team["a"].wecom_id = "wecom-a"
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.flush()
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")) is None


def direct_task(db, team):
    return create_direct_assignment(
        DirectAssignmentCreate(
            idempotency_key="native-direct-task", content="直接派发工作",
            owner_ids=[team["a"].id], verifier_id=team["host"].id, due_date=date(2026, 10, 14),
        ), db, team["host"],
    )


@pytest.mark.parametrize("action", ["create", "update", "finish"])
def test_direct_only_blocks_meeting_queue_manual_sync_and_old_events(
    db, team, draft_factory, monkeypatch, action,
):
    from fastapi import HTTPException

    configure_pilot(monkeypatch, team)
    monkeypatch.setattr(get_settings(), "native_todo_meeting_enabled", False)
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.flush()
    meeting_task = db.scalar(select(Task))
    assert event(db, "create") is None
    with pytest.raises(HTTPException) as error:
        sync_native_todo_task(meeting_task.id, db, team["host"])
    assert error.value.status_code == 409
    monkeypatch.setattr(native_todo, "_run", lambda *_a, **_k: pytest.fail("meeting must not call provider"))
    old_event = SimpleNamespace(payload={"task_id": meeting_task.id, "task_version": 1, "action": action})
    assert "skipped" in native_todo.send_native_todo(db, old_event)
    direct_task(db, team)
    db.flush()
    assert event(db, "create")


def test_all_active_members_are_auto_bound_before_direct_todo_creation(db, team, monkeypatch):
    configure_pilot(monkeypatch, team, mapped=False)
    monkeypatch.setattr(get_settings(), "native_todo_all_active_users", True)
    monkeypatch.setattr(get_settings(), "native_todo_allowed_wecom_ids", "")
    monkeypatch.setattr(native_todo, "search_users", lambda _keywords: [
        {"userid": "wo-auto-a", "name": team["a"].name, "email": ""},
        {"userid": "wo-wrong", "name": "模糊搜索另一人", "email": ""},
    ])
    calls = []

    def provider(*arguments, payload, **_):
        calls.append((arguments, payload))
        return {"items": [{"success": True, "todo_id": "auto-bound-todo"}]}

    monkeypatch.setattr(native_todo, "_run", provider)
    direct_task(db, team)
    db.flush()
    native_todo.send_native_todo(db, event(db, "create"))
    assert team["a"].native_todo_userid == "wo-auto-a"
    assert calls[0][1]["items"][0]["follower_ids"] == ["wo-auto-a"]


@pytest.mark.parametrize("candidates", [
    [{"userid": "wo-a", "name": "责任人甲", "email": "wrong@example.com"}],
    [{"userid": "wo-a", "name": "责任人甲", "email": ""},
     {"userid": "wo-b", "name": "责任人甲", "email": ""}],
])
def test_auto_binding_rejects_ambiguous_or_conflicting_members(db, team, monkeypatch, candidates):
    configure_pilot(monkeypatch, team, mapped=False)
    monkeypatch.setattr(get_settings(), "native_todo_all_active_users", True)
    team["a"].email = "correct@example.com"
    monkeypatch.setattr(native_todo, "search_users", lambda _: candidates)
    monkeypatch.setattr(native_todo, "_run", lambda *_a, **_k: pytest.fail("must not create guessed todo"))
    direct_task(db, team)
    db.flush()
    with pytest.raises(NotConfigured, match="唯一匹配"):
        native_todo.send_native_todo(db, event(db, "create"))
    assert team["a"].native_todo_userid is None


def test_expired_provider_permissions_are_configuration_errors(monkeypatch, tmp_path):
    cli = tmp_path / "wecom-cli"
    cli.touch()
    monkeypatch.setattr(get_settings(), "native_todo_cli_path", cli)
    monkeypatch.setattr(native_todo.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=1, stderr="", stdout='{"errcode":850003,"help_message":"[授权](https://work.weixin.qq.com/ai/aiHelper/authorizationList?type=5)"}',
    ))
    with pytest.raises(native_todo.AuthorizationRequired, match="待办使用权限已过期") as error:
        native_todo._run("todo", "create", payload={})
    assert error.value.authorization_url.endswith("?type=5")


def test_saved_credentials_do_not_claim_expired_permissions_are_valid(monkeypatch, tmp_path):
    cli = tmp_path / "wecom-cli"
    cli.touch()
    monkeypatch.setattr(get_settings(), "native_todo_cli_path", cli)
    monkeypatch.setattr(native_todo.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=0, stdout="Status: Authorized", stderr="",
    ))
    monkeypatch.setattr(native_todo, "_run", lambda *_a, **_k: (_ for _ in ()).throw(
        native_todo.AuthorizationRequired("待办", ""),
    ))
    current = native_todo.status()
    assert current["credentials_saved"] and not current["authorized"]
    assert len(current["permissions"]) == 2


def test_permission_recovery_only_resumes_enabled_blocked_events(db, team, draft_factory, monkeypatch):
    configure_pilot(monkeypatch, team)
    direct_task(db, team)
    db.flush()
    direct = event(db, "create")
    direct.state = "blocked"
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    db.flush()
    meeting_event = db.scalar(select(OutboxEvent).where(
        OutboxEvent.channel == "native_todo", OutboxEvent.id != direct.id,
    ))
    meeting_event.state = "blocked"
    uncertain = OutboxEvent(event_key="uncertain-todo", channel="native_todo", state="uncertain", payload=direct.payload)
    db.add(uncertain)
    db.flush()
    monkeypatch.setattr(get_settings(), "native_todo_meeting_enabled", False)
    monkeypatch.setattr(native_todo, "status", lambda: {"authorized": True})
    assert resume_native_todo(db, team["host"]) == {"queued": 1}
    assert direct.state == "pending"
    assert meeting_event.state == "blocked" and uncertain.state == "uncertain"
