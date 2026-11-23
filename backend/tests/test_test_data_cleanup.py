import json
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import (
    Assessment,
    Attachment,
    AuditLog,
    Base,
    CompletionRequest,
    DirectAssignmentRequest,
    Meeting,
    NativeTodoLink,
    OutboxEvent,
    RecurringRule,
    Task,
    TaskOwner,
    TaskVersion,
    uid,
)
from app.schemas import PublishRequest
from app.services.business import continue_meeting, create_meeting, publish
from app.test_data_cleanup import apply_plan, database_snapshot, fingerprint, preview


def issue(db, team, draft_factory, title="测试"):
    draft = draft_factory().model_copy(update={"title": title})
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def direct(db, team, content="测试"):
    task = Task(
        code=uid(),
        content=content,
        source_item_id=uid(),
        issuer_id=team["host"].id,
        verifier_id=team["host"].id,
        due_date=date(2026, 8, 17),
    )
    db.add(task)
    db.flush()
    db.add(TaskOwner(task_id=task.id, user_id=team["a"].id))
    db.flush()
    return task


def test_preview_only_selects_exact_test_names_and_never_changes_data(db, team, draft_factory):
    test = issue(db, team, draft_factory)
    real = issue(db, team, draft_factory, "生产系统测试验收")
    test_task = direct(db, team)
    real_task = direct(db, team, "产品测试流程修订")
    before = fingerprint(database_snapshot(db))
    plan = preview(db)
    assert plan["meeting_ids"] == [test.id]
    assert plan["direct_task_ids"] == [test_task.id]
    assert real.id not in plan["meeting_ids"] and real_task.id not in plan["direct_task_ids"]
    assert fingerprint(database_snapshot(db)) == before
    explicit = preview(db, [real.id], [])
    assert explicit["meeting_ids"] == [real.id] and explicit["direct_task_ids"] == []


def test_cleanup_removes_test_tasks_from_inbox_and_reports_preserves_real_data_and_files(
    db,
    team,
    draft_factory,
    tmp_path,
):
    test = issue(db, team, draft_factory)
    # A separate series prevents the latest-meeting rule obscuring the business record.
    draft = draft_factory().model_copy(update={"title": "真实工作会议", "series_id": None})
    real = create_meeting(db, team["host"], draft)
    publish(db, team["host"], real.id, PublishRequest(expected_version=1, notify=False))
    test_task, real_task = direct(db, team), direct(db, team, "真实业务任务")
    original_real = db.scalar(select(Task).where(Task.source_meeting_id == real.id))
    real_before = (original_real.content, original_real.version, original_real.due_date)
    artifact = tmp_path / "old-published-file.pdf"
    artifact.write_bytes(b"preserve old artifact")
    db.add(
        Attachment(
            filename="test.pdf",
            storage_key=str(artifact),
            sha256="0" * 64,
            mime_type="application/pdf",
            size=21,
            uploader_id=team["host"].id,
            meeting_id=test.id,
        )
    )
    db.flush()
    plan = preview(db)
    result = apply_plan(db, team["host"], plan, tmp_path / "backup.json")
    assert result["deleted_meetings"] == 1 and result["deleted_tasks"] == 2
    assert db.get(Meeting, test.id) is None and db.get(Task, test_task.id) is None
    assert artifact.read_bytes() == b"preserve old artifact"
    db.refresh(original_real)
    assert (original_real.content, original_real.version, original_real.due_date) == real_before
    assert db.get(Task, real_task.id)
    assert not list(db.scalars(select(Assessment).where(Assessment.meeting_id == test.id)))
    assert db.scalar(select(AuditLog).where(AuditLog.action == "system.test_data_cleanup"))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            auth = {"Authorization": "Bearer " + issue_token(team["a"])}
            response = client.get(
                "/api/tasks", params={"mine": True, "current": True, "offset": 0, "limit": 20}, headers=auth
            )
            assert response.status_code == 200
            assert {row["id"] for row in response.json()["items"]} == {original_real.id, real_task.id}
            admin = {"Authorization": "Bearer " + issue_token(team["host"])}
            report = client.get(
                "/api/reports/performance",
                params={
                    "start": "2026-08-01",
                    "end": "2026-08-31",
                },
                headers=admin,
            )
            assert report.status_code == 200
            # The returned report contains no deleted source meeting or task identifiers.
            assert test.id not in report.text and test_task.id not in report.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("change", ["task", "plan"])
def test_changed_data_or_tampered_plan_cannot_be_applied(db, team, tmp_path, change):
    task = direct(db, team)
    plan = preview(db)
    if change == "task":
        task.content = "现在是真实业务任务"
    else:
        plan["task_count"] += 1
    with pytest.raises(ValueError, match="已变化"):
        apply_plan(db, team["host"], plan, tmp_path / "backup.json")
    assert db.get(Task, task.id) and not (tmp_path / "backup.json").exists()


@pytest.mark.parametrize("role", ["employee", "inactive_admin"])
def test_admin_required(db, team, tmp_path, role):
    task = direct(db, team)
    plan = preview(db)
    actor = team["a"] if role == "employee" else team["host"]
    if role == "inactive_admin":
        actor.active = False
    with pytest.raises(ValueError, match="管理员"):
        apply_plan(db, actor, plan, tmp_path / "backup.json")
    assert db.get(Task, task.id)


def test_no_backup_no_deletion_and_existing_backup_is_not_overwritten(db, team, tmp_path):
    task = direct(db, team)
    plan = preview(db)
    backup = tmp_path / "backup.json"
    backup.write_text("previous evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        apply_plan(db, team["host"], plan, backup)
    assert backup.read_text(encoding="utf-8") == "previous evidence"
    assert db.get(Task, task.id)


@pytest.mark.parametrize("kind", ["draft_reference", "active_native", "sending"])
def test_references_native_todos_and_inflight_events_block_cleanup(
    db,
    team,
    draft_factory,
    tmp_path,
    kind,
):
    meeting = issue(db, team, draft_factory)
    task = db.scalar(select(Task).where(Task.source_meeting_id == meeting.id))
    if kind == "draft_reference":
        other = continue_meeting(db, team["host"], meeting.id)
        other.title = "真实后续会议"
    elif kind == "active_native":
        db.add(NativeTodoLink(task_id=task.id, todo_id="native-real", task_version=1, state="active"))
    else:
        db.add(OutboxEvent(event_key="busy", channel="wecom", payload={}, state="sending"))
    db.flush()
    plan = preview(db)
    assert plan["blockers"]
    with pytest.raises(ValueError, match="阻止清理"):
        apply_plan(db, team["host"], plan, tmp_path / "backup.json")
    assert db.get(Meeting, meeting.id) and db.get(Task, task.id)


def test_direct_dependencies_and_mixed_reminders_keep_real_tasks_and_send_history(db, team, tmp_path):
    test, real = direct(db, team), direct(db, team, "真实任务")
    request = CompletionRequest(
        task_id=test.id, task_version=1, applicant_id=team["a"].id, idempotency_key=uid(), explanation="test"
    )
    db.add_all(
        [
            request,
            TaskVersion(task_id=test.id, number=1, snapshot={}, reason="test", actor_id=team["host"].id),
            DirectAssignmentRequest(
                task_id=test.id, actor_id=team["host"].id, request_key=uid(), payload_hash="0" * 64
            ),
            RecurringRule(
                source_task_id=test.id,
                creator_id=team["host"].id,
                frequency="weekly",
                anchor_date=date(2026, 8, 1),
                next_due_date=date(2026, 8, 8),
                template={},
            ),
        ]
    )
    db.flush()
    mixed = OutboxEvent(
        event_key="digest",
        channel="wecom",
        payload={
            "deadline_items": [{"task_id": test.id}, {"task_id": real.id}],
            "reminder_task_ids": [test.id, real.id],
            "verification_request_ids": [request.id],
        },
    )
    pending = OutboxEvent(event_key=f"task:{test.id}:new", channel="wecom", payload={"task_id": test.id})
    sent = OutboxEvent(
        event_key=f"task:{test.id}:sent", channel="wecom", state="sent", payload={"task_id": test.id}
    )
    db.add_all([mixed, pending, sent])
    db.flush()
    apply_plan(db, team["host"], preview(db), tmp_path / "backup.json")
    assert mixed.payload["deadline_items"] == [{"task_id": real.id}]
    assert mixed.payload["reminder_task_ids"] == [real.id]
    assert mixed.payload["verification_request_ids"] == []
    assert mixed.state == "pending" and pending.state == "skipped" and sent.state == "sent"
    assert db.get(Task, real.id) and db.get(Task, test.id) is None
    assert not list(db.scalars(select(RecurringRule)))


def test_complete_backup_can_restore_into_an_empty_database(db, team, tmp_path):
    direct(db, team)
    expected = database_snapshot(db)
    backup = tmp_path / "backup.json"
    apply_plan(db, team["host"], preview(db), backup)
    saved = json.loads(backup.read_text(encoding="utf-8"))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            rows = saved["tables"][table.name]
            for row in rows:
                for column in table.columns:
                    value = row[column.name]
                    if isinstance(value, dict) and column.type.python_type.__name__ in {"date", "datetime"}:
                        row[column.name] = column.type.python_type.fromisoformat(next(iter(value.values())))
            if rows:
                connection.execute(table.insert(), rows)
        from sqlalchemy.orm import Session

        with Session(bind=connection) as restored:
            assert fingerprint(database_snapshot(restored)) == fingerprint(expected)
    engine.dispose()


def test_late_failure_rolls_back_all_selected_records(db, team, draft_factory, tmp_path, monkeypatch):
    meeting = issue(db, team, draft_factory)
    task = direct(db, team)
    plan = preview(db)
    before = fingerprint(database_snapshot(db))

    def fail(*args):
        raise HTTPException(409, "late conflict")

    monkeypatch.setattr("app.test_data_cleanup.cancel_direct_events", fail)
    with pytest.raises(HTTPException):
        apply_plan(db, team["host"], plan, tmp_path / "backup.json")
    assert fingerprint(database_snapshot(db)) == before
    assert db.get(Meeting, meeting.id) and db.get(Task, task.id)


def test_invalid_or_empty_selection_does_not_delete(db, team, tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        preview(db, ["missing"], [])
    with pytest.raises(ValueError, match="没有选中"):
        apply_plan(db, team["host"], preview(db), tmp_path / "backup.json")


def test_cli_preview_and_apply_use_a_separate_local_database(db, team, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app import test_data_cleanup

    test = direct(db, team)
    real = direct(db, team, "真实任务")
    engine = create_engine("sqlite:///" + (tmp_path / "isolated.db").as_posix())
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        rows = database_snapshot(db)
        for table in Base.metadata.sorted_tables:
            if rows[table.name]:
                connection.execute(table.insert(), rows[table.name])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.core.db.SessionLocal", sessions)
    plan, backup = tmp_path / "plan.json", tmp_path / "backup.json"
    monkeypatch.setattr("sys.argv", ["cleanup", "preview", "--output", str(plan)])
    test_data_cleanup.main()
    with sessions() as separate:
        assert separate.get(Task, test.id) and separate.get(Task, real.id)
    monkeypatch.setattr(
        "sys.argv",
        [
            "cleanup",
            "apply",
            "--plan",
            str(plan),
            "--backup",
            str(backup),
            "--username",
            team["host"].username,
        ],
    )
    test_data_cleanup.main()
    with sessions() as separate:
        assert separate.get(Task, test.id) is None and separate.get(Task, real.id)
    assert backup.exists()
    engine.dispose()


def test_published_test_assessments_are_removed_from_report_totals(db, team, tmp_path):
    from app.services.reporting import report
    from tests.test_department_people_report import issue as reviewed_meeting

    test = reviewed_meeting(db, team)
    test.title = "测试"
    real = reviewed_meeting(db, team)
    real.title = "正式业务会议"
    db.flush()

    def totals():
        return report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31), group_by="department_people")

    assert totals()["summary"]["items"] == 4
    apply_plan(db, team["host"], preview(db), tmp_path / "backup.json")
    remaining = totals()
    assert remaining["summary"]["items"] == 2
    assert {row["meeting_id"] for row in remaining["details"]} == {real.id}
