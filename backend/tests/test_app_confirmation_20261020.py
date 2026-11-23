from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.main import DirectAssignmentCreate, create_direct_assignment
from app.models import NativeTodoLink, OutboxEvent, SystemSetting, Task
from app.schemas import TaskChange
from app.services import app_native_todo, integrations
from app.services.business import change_task
from app.task_management_api import TaskTransfer, transfer
from tests.role_helpers import dispatch_grant


@pytest.fixture
def app_todos(db, team, monkeypatch):
    settings = get_settings()
    for name, value in {
        "native_todo_enabled": True,
        "native_todo_app_enabled": True,
        "native_todo_direct_enabled": True,
        "native_todo_meeting_enabled": False,
        "wecom_direct_notifications_enabled": True,
        "public_url": "https://tasks.example.test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    for key in ("host", "a", "b", "colleague"):
        team[key].wecom_id = "wecom-" + key
    team["colleague"].position = "部长"
    db.add(SystemSetting(key="reminders", value={"enabled": False}))
    db.flush()
    sent = []

    class Provider:
        def send(self, users, text, *, card=None):
            sent.append({"users": users, "text": text, "card": card})
            return {"msgid": "isolated-" + str(len(sent))}

    def no_network(*args, **kwargs):
        raise AssertionError("Regression checks must not contact the real provider")

    monkeypatch.setattr(integrations, "WeCom", Provider)
    monkeypatch.setattr(integrations.httpx, "request", no_network)
    monkeypatch.setattr(integrations.httpx, "get", no_network)
    return sent


def direct_task(db, team, *, issuer=None, content="完成本周设备维护"):
    if issuer and not issuer.is_admin: dispatch_grant(db,issuer,"all")
    issuer = issuer or team["host"]
    result = create_direct_assignment(
        DirectAssignmentCreate(
            idempotency_key="isolated-confirmation-key",
            content=content,
            owner_ids=[team["a"].id],
            verifier_id=issuer.id,
            due_date=date(2026, 10, 23),
        ),
        db,
        issuer,
    )
    db.flush()
    return db.get(Task, result["id"])


def confirmations(db, task):
    db.flush()
    return list(
        db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.payload["task_id"].as_string() == task.id,
                OutboxEvent.payload["native_confirmation"].as_boolean().is_(True),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
    )


def update_task(db, team, task, *, notify=True):
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=task.version,
            content="更新后的设备维护要求",
            due_date=date(2026, 10, 24),
            owner_ids=[team["b"].id],
            verifier_id=task.verifier_id,
            reason="调整本周工作安排",
        ),
        notify=notify,
    )
    db.flush()


def test_edit_before_card_delivery_sends_latest_confirmation_to_actual_issuer(db, team, app_todos):
    task = direct_task(db, team, issuer=team["colleague"])
    original = confirmations(db, task)[0]
    update_task(db, team, task)
    events = confirmations(db, task)
    assert len(events) == 2
    current = next(event for event in events if event.payload["task_version"] == task.version)
    assert current.payload["user_ids"] == [team["colleague"].id]
    assert current.payload["content"] == task.content
    assert integrations.send_wecom(db, original).get("skipped")
    assert not app_todos
    assert integrations.send_wecom(db, current)["msgid"] == "isolated-1"
    assert app_todos[0]["users"] == [team["colleague"].wecom_id]
    assert "native_confirm=1" in app_todos[0]["card"]["url"]


def test_same_version_confirmation_is_idempotent_and_keeps_delivery_time(db, team, app_todos, monkeypatch):
    task = direct_task(db, team)
    event = confirmations(db, task)[0]
    due = event.available_at
    monkeypatch.setattr(app_native_todo, "now", lambda: due + timedelta(minutes=5))
    app_native_todo.enqueue_issuer_confirmation(db, task)
    app_native_todo.enqueue_issuer_confirmation(db, task, team["host"])
    assert [value.id for value in confirmations(db, task)] == [event.id]
    assert event.available_at == due


def test_existing_native_todo_suppresses_new_confirmation_on_edit(db, team, app_todos):
    task = direct_task(db, team)
    original = confirmations(db, task)[0]
    db.add(
        NativeTodoLink(
            task_id=task.id,
            todo_id="isolated-existing-todo",
            provider="app",
            task_version=task.version,
            follower_ids=[team["a"].wecom_id],
        )
    )
    db.flush()
    assert integrations.send_wecom(db, original).get("skipped")
    update_task(db, team, task)
    assert [event.id for event in confirmations(db, task)] == [original.id]
    update = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.channel == "native_todo",
            OutboxEvent.payload["task_id"].as_string() == task.id,
            OutboxEvent.payload["action"].as_string() == "update",
        )
    )
    assert update and update.payload["task_version"] == task.version
    assert not app_todos


def test_edit_with_notifications_disabled_does_not_queue_confirmation(db, team, app_todos):
    task = direct_task(db, team)
    original = confirmations(db, task)[0]
    update_task(db, team, task, notify=False)
    assert [event.id for event in confirmations(db, task)] == [original.id]
    assert not app_todos


def test_transfer_queues_current_version_confirmation_for_new_issuer(db, team, app_todos):
    task = direct_task(db, team, issuer=team["colleague"])
    original = confirmations(db, task)[0]
    transfer(
        task.id,
        TaskTransfer(expected_version=task.version, issuer_id=team["host"].id, reason="移交跟进"),
        db,
        team["host"],
    )
    events = confirmations(db, task)
    current = next(event for event in events if event.payload["task_version"] == task.version)
    assert len(events) == 2 and current.payload["user_ids"] == [team["host"].id]
    assert integrations.send_wecom(db, original).get("skipped")
    integrations.send_wecom(db, current)
    assert app_todos[0]["users"] == [team["host"].wecom_id]




class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags = []
        self.entities = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_entityref(self, name):
        self.entities.append("&" + name + ";")

    def handle_charref(self, name):
        self.entities.append("&#" + name + ";")


@pytest.mark.parametrize("confirmation", [False, True])
@pytest.mark.parametrize("content", ["逐项确认验收材料。" * 300, "中文🚀é<&\"'>" * 300, "<&\"'>" * 500])
def test_direct_cards_fit_escaped_utf8_budget_and_preserve_deadline_and_issuer(
    db, team, app_todos, content, confirmation
):
    team["host"].name = "王<&\"'晨宁"
    task = direct_task(db, team, content=content)
    event = (
        confirmations(db, task)[0]
        if confirmation
        else db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f"task:{task.id}:assigned"))
    )
    integrations.send_wecom(db, event)
    card = app_todos[0]["card"]
    description = card["description"]
    assert len(description.encode("utf-8")) <= 512
    assert "计划完成日期：2026-10-23" in description
    assert "派发人：" + escape(team["host"].name) in description
    assert card["url"].endswith("native_confirm=1" if confirmation else task.id)
    assert "�" not in description and "…" in description
    parser = CardParser()
    parser.feed(description)
    assert parser.tags == ["br"] * (3 if confirmation else 2)
    assert all(entity in {"&amp;", "&lt;", "&gt;", "&quot;", "&#x27;"} for entity in parser.entities)
    # Every ampersand must begin a complete escape, even when the body runs out of bytes.
    without_entities = description
    for entity in parser.entities:
        without_entities = without_entities.replace(entity, "")
    assert "&" not in without_entities


def test_long_escaped_issuer_name_cannot_consume_card_footer_budget(db, team, app_todos):
    team["host"].name = "<&\"'" * 30
    task = direct_task(db, team, content="中文任务" * 300)
    integrations.send_wecom(db, confirmations(db, task)[0])
    description = app_todos[0]["card"]["description"]
    assert len(description.encode("utf-8")) <= 512
    assert "计划完成日期：2026-10-23" in description and "派发人：" in description
    assert description.endswith("确认后向责任人创建待办")
