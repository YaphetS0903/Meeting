from datetime import date

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import NativeTodoLink, OutboxEvent, Task, TaskOwner, uid
from app.services.app_native_todo import enqueue_issuer_confirmation
from app.services.integrations import send_wecom
from app.task_management_api import TaskTransfer, transfer


@pytest.mark.parametrize("already_linked", [False, True])
def test_handover_confirms_current_version_with_new_issuer_only(db, team, monkeypatch, already_linked):
    settings = get_settings()
    for key in (
        "native_todo_enabled",
        "native_todo_app_enabled",
        "native_todo_direct_enabled",
        "wecom_direct_notifications_enabled",
    ):
        monkeypatch.setattr(settings, key, True)
    issuer, successor = team["host"], team["colleague"]
    issuer.wecom_id = "previous-issuer"
    successor.wecom_id = "current-issuer"
    successor.position = "部长"
    team["a"].wecom_id = "owner"
    task = Task(
        code="RW-TRANSFER-CONFIRM",
        content="移交后的工作仍由新派发人确认",
        issuer_id=issuer.id,
        department_id=team["dep"].id,
        source_item_id=uid(),
        verifier_id=issuer.id,
        due_date=date(2026, 11, 1),
    )
    db.add(task)
    db.flush()
    db.add(TaskOwner(task_id=task.id, user_id=team["a"].id))
    if already_linked:
        db.add(NativeTodoLink(task_id=task.id, task_version=1, todo_id="existing", provider="app"))
    db.flush()
    enqueue_issuer_confirmation(db, task, issuer)
    previous_events = list(db.scalars(select(OutboxEvent)))

    transfer(
        task.id,
        TaskTransfer(expected_version=task.version, issuer_id=successor.id, reason="交由部门负责人跟进"),
        db,
        issuer,
    )
    db.flush()
    confirmations = [
        event for event in db.scalars(select(OutboxEvent)) if event.payload.get("native_confirmation")
    ]
    if already_linked:
        assert confirmations == []
    else:
        current = [event for event in confirmations if event.payload["task_version"] == task.version]
        assert len(current) == 1
        assert current[0].payload["user_ids"] == [successor.id]
        assert send_wecom(db, previous_events[0]) == {"skipped": "任务已更新或关闭"}
