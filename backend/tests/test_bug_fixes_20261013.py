from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import DirectAssignmentCreate, app, create_direct_assignment
from app.models import RecurringRule, SystemSetting, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest, TaskChange
from app.services.business import change_task, create_meeting, publish
from app.services.historical_links import resolve_link, suggestions
from app.services.importing import confirm_candidate, correct_candidate
from app.services.recurrence import (
    RecurrenceCreate,
    RecurrenceUpdate,
    enable_rule,
    generate_instances,
    update_rule,
)
from app.task_management_api import TaskTransfer, transfer
from tests.test_import_correction import candidate


def private_history(db, team):
    meeting = create_meeting(
        db,
        team["host"],
        MeetingDraft(
            title="财务专项历史会议",
            kind="special",
            host_id=team["outsider"].id,
            meeting_date=date(2026, 8, 18),
            items=[
                MeetingItem(
                    id="private-row",
                    section="review",
                    content="财务专项合同工作",
                    status="未完成",
                    owner_ids=[team["outsider"].id],
                    due_date=date(2026, 8, 20),
                )
            ],
        ),
        historical=True,
    )
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def test_history_suggestions_and_link_resolution_enforce_source_permissions(db, team):
    meeting = private_history(db, team)
    payload = {
        "kind": "special",
        "meeting_date": "2026-08-25",
        "items": [
            {
                "id": "current-row",
                "content": "财务专项合同工作",
                "owner_ids": [team["a"].id],
            }
        ],
    }
    assert suggestions(db, team["a"], payload, "current-row") == []
    assert suggestions(db, team["host"], payload, "current-row")[0]["meeting_id"] == meeting.id
    row = {
        "historical_reference": {
            "meeting_id": meeting.id,
            "version": 1,
            "item_id": "private-row",
            "reason": "核对同一事项",
        }
    }
    with pytest.raises(HTTPException) as error:
        resolve_link(db, team["a"], payload, row)
    assert error.value.status_code == 403
    assert resolve_link(db, team["host"], payload, row)["historical_reference"]["meeting_id"] == meeting.id


def test_candidate_save_and_confirmation_recheck_source_permissions(db, team):
    meeting = private_history(db, team)
    imported, issue = candidate(db, team)
    payload = deepcopy(imported.corrected)
    payload.update(host_id=team["a"].id, meeting_date="2026-08-25", confirmed_issue_ids=[issue.id])
    imported.corrected = deepcopy(payload)
    db.flush()
    payload["items"][0]["historical_reference"] = {
        "meeting_id": meeting.id,
        "version": 1,
        "item_id": "private-row",
        "reason": "核对来源",
    }
    with pytest.raises(HTTPException) as error:
        correct_candidate(db, team["a"], imported.id, payload, 1, "核对内容")
    assert error.value.status_code == 403
    assert imported.version == 1 and not imported.corrected["items"][0].get("historical_reference")
    # A saved reference may have been recorded before this permission fix or a later scope change.
    imported.corrected = deepcopy(payload)
    db.flush()
    with pytest.raises(HTTPException) as error:
        confirm_candidate(db, team["a"], imported.id, 1, "核对入库")
    assert error.value.status_code == 403
    assert imported.state != "confirmed"


def direct_rule(db, team, monkeypatch, department_creator=False):
    monkeypatch.setattr("app.services.recurrence.today", lambda: date(2026, 10, 13))
    db.add(SystemSetting(key="reminders", value={"enabled": False}))
    creator = team["colleague"] if department_creator else team["host"]
    creator.position = "部长" if department_creator else ""
    db.flush()
    task = create_direct_assignment(
        DirectAssignmentCreate(
            idempotency_key="isolated-rule-key",
            content="首期工作",
            owner_ids=[team["a"].id],
            verifier_id=creator.id,
            due_date=date(2026, 10, 16),
        ),
        db,
        creator,
    )
    source = db.get(Task, task["id"])
    rule = enable_rule(
        db,
        creator,
        source.id,
        RecurrenceCreate(
            expected_version=source.version,
            frequency="weekly",
            reason="周期执行",
        ),
    )
    db.flush()
    return source, rule, creator


def changed_rule(rule, **changes):
    return RecurrenceUpdate(
        **{
            **rule.template,
            "expected_version": rule.version,
            "active": True,
            "end_date": None,
            "reason": "维护后续工作",
            **changes,
        }
    )


def test_old_owner_receives_only_schedule_after_future_personnel_change(db, team, monkeypatch):
    source, rule, _ = direct_rule(db, team, monkeypatch)
    update_rule(
        db,
        team["host"],
        rule.id,
        changed_rule(
            rule,
            content="外部门后续工作",
            owner_ids=[team["outsider"].id],
        ),
    )
    assert generate_instances(db, date(2026, 10, 19)) == 1
    generated = db.scalar(select(Task).where(Task.id != source.id))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            owner_headers = {"Authorization": "Bearer " + issue_token(team["a"])}
            assert client.get(f"/api/tasks/{generated.id}", headers=owner_headers).status_code == 403
            visible = client.get(f"/api/tasks/{source.id}/recurrence", headers=owner_headers)
            assert visible.status_code == 200
            body = visible.json()
            assert body["template"] is None and not body["can_manage"]
            assert "外部门后续工作" not in visible.text
            assert "last_error" not in body and "source_task_id" not in body
            admin_headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            full = client.get(f"/api/tasks/{source.id}/recurrence", headers=admin_headers).json()
            assert full["can_manage"] and full["template"]["content"] == "外部门后续工作"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("inactive_creator", [False, True])
def test_admin_cannot_activate_future_work_outside_actual_creator_scope(
    db, team, monkeypatch, inactive_creator
):
    source, rule, creator = direct_rule(db, team, monkeypatch, department_creator=True)
    original = deepcopy(rule.template)
    if inactive_creator:
        creator.active = False
        db.flush()
    with pytest.raises(HTTPException) as error:
        update_rule(
            db,
            team["host"],
            rule.id,
            changed_rule(
                rule,
                owner_ids=[team["outsider"].id],
                verifier_id=team["host"].id,
            ),
        )
    assert error.value.status_code == 422 and "移交派发人" in error.value.detail
    assert rule.version == 1 and rule.template == original
    if inactive_creator:
        change_task(
            db,
            team["host"],
            source,
            TaskChange(
                expected_version=source.version,
                content=source.content,
                due_date=source.due_date,
                owner_ids=[team["a"].id],
                verifier_id=team["host"].id,
                reason="更换已停用验证人",
            ),
        )
        update_rule(
            db,
            team["host"],
            rule.id,
            changed_rule(
                rule,
                active=False,
                verifier_id=team["host"].id,
            ),
        )
    transfer(
        source.id,
        TaskTransfer(
            expected_version=source.version,
            issuer_id=team["host"].id,
            reason="移交公司范围派发人",
        ),
        db,
        team["host"],
    )
    update_rule(
        db,
        team["host"],
        rule.id,
        changed_rule(
            rule,
            owner_ids=[team["outsider"].id],
            verifier_id=team["host"].id,
        ),
    )
    assert generate_instances(db, date(2026, 10, 19)) == 1
    generated = db.scalar(select(Task).where(Task.id != source.id))
    assert generated.issuer_id == team["host"].id
    assert db.get(RecurringRule, rule.id).active
