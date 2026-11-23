"""Offline, manifest-based cleanup. Preview never writes to the database.

Run as ``python -m app.test_data_cleanup preview --output plan.json``.
No HTTP route, scheduled cleanup, or automatic production execution is installed.
"""

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy import delete, or_, select, text

from app.models import (
    Assessment,
    Attachment,
    Base,
    CompletionRequest,
    DirectAssignmentRequest,
    Meeting,
    MeetingVersion,
    NativeTodoAttempt,
    NativeTodoLink,
    OutboxEvent,
    RecurringRule,
    Task,
    TaskCarryReplacement,
    TaskOwner,
    TaskVersion,
    User,
)

FORMAT = "meeting-test-cleanup-v1"
EXACT_TEST_NAMES = {"测试", "系统测试", "测试会议", "测试任务", "测试任务单"}


def encode(value):
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    raise TypeError(type(value).__name__)


def serialized(value):
    return json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def database_snapshot(db):
    """Complete row backup, including audit and send history; attachment files stay in storage."""
    db.flush()
    return {
        table.name: [dict(row) for row in db.execute(select(table).order_by(*table.primary_key)).mappings()]
        for table in Base.metadata.sorted_tables
    }


def fingerprint(snapshot):
    return hashlib.sha256(serialized(snapshot).encode("utf-8")).hexdigest()


def write_private_json(path, value):
    # Exclusive creation prevents clobbering an earlier plan or backup.
    fd = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(serialized(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def contains_id(value, identifiers):
    if isinstance(value, dict):
        return any(contains_id(item, identifiers) for item in value.values())
    if isinstance(value, list):
        return any(contains_id(item, identifiers) for item in value)
    return isinstance(value, str) and value in identifiers


def preview(db, meeting_ids=None, direct_task_ids=None):
    """Only exact standalone test names are suggested; explicit IDs can select other confirmed tests."""
    meetings = list(db.scalars(select(Meeting).order_by(Meeting.id)))
    direct = list(db.scalars(select(Task).where(Task.source_meeting_id.is_(None)).order_by(Task.id)))
    if meeting_ids is None and direct_task_ids is None:
        meeting_ids = [m.id for m in meetings if m.title.strip() in EXACT_TEST_NAMES]
        direct_task_ids = [t.id for t in direct if t.content.strip() in EXACT_TEST_NAMES]
    meeting_ids, direct_task_ids = set(meeting_ids or []), set(direct_task_ids or [])
    chosen_meetings = [m for m in meetings if m.id in meeting_ids]
    chosen_direct = [t for t in direct if t.id in direct_task_ids]
    if {m.id for m in chosen_meetings} != meeting_ids or {t.id for t in chosen_direct} != direct_task_ids:
        raise ValueError("清单含不存在的记录，或将会议任务误选为直接任务")
    tasks = list(
        db.scalars(
            select(Task)
            .where(or_(Task.source_meeting_id.in_(meeting_ids), Task.id.in_(direct_task_ids)))
            .order_by(Task.id)
        )
    )
    task_ids = {t.id for t in tasks}
    blockers = []
    from app.main import meeting_deletion_dependencies

    for meeting in chosen_meetings:
        own_tasks = [t.id for t in tasks if t.source_meeting_id == meeting.id]
        for dependency in meeting_deletion_dependencies(db, meeting, own_tasks):
            if dependency["id"] not in meeting_ids:
                blockers.append(f"保留会议仍引用待清理会议：{dependency['id']}")
    # Direct assignments may have been referenced in later meeting documents too.
    for meeting in meetings:
        if meeting.id not in meeting_ids and contains_id(meeting.draft, task_ids):
            blockers.append(f"保留会议草稿仍引用测试任务：{meeting.id}")
    for version in db.scalars(select(MeetingVersion).where(MeetingVersion.meeting_id.not_in(meeting_ids))):
        if contains_id(version.snapshot, task_ids):
            blockers.append(f"保留会议发布记录仍引用测试任务：{version.meeting_id}")
    for version in db.scalars(select(TaskVersion).where(TaskVersion.task_id.not_in(task_ids))):
        if contains_id(version.snapshot, task_ids):
            blockers.append(f"保留任务版本仍引用测试任务：{version.task_id}")
    for row in db.scalars(
        select(Assessment).where(Assessment.task_id.in_(task_ids), Assessment.meeting_id.not_in(meeting_ids))
    ):
        blockers.append(f"保留会议评定仍引用测试任务：{row.meeting_id}")
    for link in db.scalars(
        select(NativeTodoLink).where(NativeTodoLink.task_id.in_(task_ids), NativeTodoLink.state != "finished")
    ):
        blockers.append(f"企业微信原生待办尚未结束：{link.task_id}")
    for link in db.scalars(
        select(TaskCarryReplacement).where(
            or_(
                TaskCarryReplacement.previous_task_id.in_(task_ids),
                TaskCarryReplacement.next_task_id.in_(task_ids),
            )
        )
    ):
        if not {link.previous_task_id, link.next_task_id} <= task_ids or link.meeting_id not in meeting_ids:
            blockers.append(f"保留任务仍有延续关系：{link.id}")
    # Never delete while a worker is sending. Stop writers before preview/apply.
    if db.scalar(select(OutboxEvent.id).where(OutboxEvent.state == "sending").limit(1)):
        blockers.append("有通知或导出正在处理中，请停写后重新生成清单")
    return {
        "format": FORMAT,
        "meeting_ids": sorted(meeting_ids),
        "direct_task_ids": sorted(direct_task_ids),
        "meetings": [
            {
                "id": m.id,
                "title": m.title,
                "date": m.meeting_date.isoformat(),
                "published_version": m.published_version,
            }
            for m in chosen_meetings
        ],
        "tasks": [
            {"id": t.id, "content": t.content, "source_meeting_id": t.source_meeting_id, "version": t.version}
            for t in tasks
        ],
        "meeting_count": len(chosen_meetings),
        "task_count": len(tasks),
        "blockers": sorted(set(blockers)),
        "database_sha256": fingerprint(database_snapshot(db)),
    }


def cancel_direct_events(db, task_ids):
    request_ids = set(db.scalars(select(CompletionRequest.id).where(CompletionRequest.task_id.in_(task_ids))))
    for event in db.scalars(select(OutboxEvent).where(OutboxEvent.state.not_in(["sent", "skipped"]))):
        payload = event.payload
        if payload.get("task_id") in task_ids or any(
            event.event_key.startswith(f"task:{identifier}:") for identifier in task_ids
        ):
            event.state, event.last_error = "skipped", "管理员清理测试任务"
            continue
        remaining = dict(payload)
        changed = False
        for key, ids in (("reminder_task_ids", task_ids), ("verification_request_ids", request_ids)):
            if key in payload:
                remaining[key] = [item for item in payload[key] if item not in ids]
                changed |= remaining[key] != payload[key]
        if "deadline_items" in payload:
            remaining["deadline_items"] = [
                item for item in payload["deadline_items"] if item.get("task_id") not in task_ids
            ]
            changed |= remaining["deadline_items"] != payload["deadline_items"]
        if changed:
            event.payload = remaining
            if not any(
                remaining.get(key)
                for key in ("reminder_task_ids", "verification_request_ids", "deadline_items")
            ):
                event.state, event.last_error = "skipped", "管理员清理测试任务"


def apply_plan(db, actor, plan, backup_path):
    """Caller owns the outer transaction. No file deletion or notification callbacks are run."""
    from app.main import remove_meeting_records
    from app.services.business import audit

    if not actor.is_admin or not actor.active:
        raise ValueError("只有启用的系统管理员可以清理测试数据")
    if plan.get("format") != FORMAT:
        raise ValueError("不支持的清单格式")
    current = preview(db, plan["meeting_ids"], plan["direct_task_ids"])
    if current != plan:
        raise ValueError("清单或数据库已变化，请重新预览")
    if current["blockers"]:
        raise ValueError("存在阻止清理的关联：" + "；".join(current["blockers"]))
    if not current["task_count"] and not current["meeting_count"]:
        raise ValueError("没有选中测试数据")
    snapshot = database_snapshot(db)
    write_private_json(
        backup_path,
        {"format": FORMAT, "database_sha256": fingerprint(snapshot), "tables": snapshot, "plan": plan},
    )
    # A late FK or dependency failure rolls back all selected records together.
    with db.begin_nested():
        task_ids = {row["id"] for row in plan["tasks"]}
        db.execute(delete(TaskCarryReplacement).where(TaskCarryReplacement.previous_task_id.in_(task_ids)))
        # Meeting cleanup already handles mixed reminders, import sources and assessment records.
        # Keep physical files for recovery by intentionally not running its unlink callbacks.
        callbacks = BackgroundTasks()
        for identifier in plan["meeting_ids"]:
            remove_meeting_records(
                db,
                actor,
                db.get(Meeting, identifier),
                callbacks,
                deleting_meeting_ids=set(plan["meeting_ids"]),
            )
            db.flush()
        direct_ids = set(plan["direct_task_ids"])
        cancel_direct_events(db, direct_ids)
        for attachment in db.scalars(select(Attachment).where(Attachment.task_id.in_(direct_ids))):
            # Preserve the stored object and metadata; remove only its task association.
            attachment.task_id = None
        db.flush()
        for model in (
            CompletionRequest,
            RecurringRule,
            NativeTodoLink,
            NativeTodoAttempt,
            TaskOwner,
            TaskVersion,
            DirectAssignmentRequest,
        ):
            column = model.source_task_id if model is RecurringRule else model.task_id
            db.execute(delete(model).where(column.in_(direct_ids)))
        db.execute(delete(Task).where(Task.id.in_(direct_ids)))
        audit(
            db,
            actor,
            "system.test_data_cleanup",
            actor,
            before={"meeting_ids": plan["meeting_ids"], "task_ids": sorted(task_ids)},
            after={"deleted_meetings": plan["meeting_count"], "deleted_tasks": plan["task_count"]},
            reason="管理员按已核对清单清理历史测试数据；附件文件及历史发送日志保留",
        )
        db.flush()
    return {
        "deleted_meetings": plan["meeting_count"],
        "deleted_tasks": plan["task_count"],
        "backup": str(backup_path),
    }


def main():
    from app.core.db import SessionLocal

    parser = argparse.ArgumentParser(description="测试数据清理：默认仅预览，不按模糊关键词删除")
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("preview")
    dry.add_argument("--output", type=Path, required=True)
    dry.add_argument("--meeting-id", action="append")
    dry.add_argument("--direct-task-id", action="append")
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--backup", type=Path, required=True)
    apply.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            if args.command == "preview":
                plan = preview(db, args.meeting_id, args.direct_task_id)
                write_private_json(args.output, plan)
                db.rollback()
                print(json.dumps(plan, ensure_ascii=False, indent=2))
            else:
                with db.begin():
                    # Serialize every writer for the full backup/check/delete transaction.
                    # Workers and imports must already be stopped, as documented.
                    connection = db.connection()
                    if connection.dialect.name == "postgresql":
                        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
                        names = ", ".join(
                            connection.dialect.identifier_preparer.quote(t.name)
                            for t in Base.metadata.sorted_tables
                        )
                        connection.execute(text(f"LOCK TABLE {names} IN EXCLUSIVE MODE"))
                    elif connection.dialect.name == "sqlite":
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                    else:
                        raise ValueError("仅支持 PostgreSQL 或 SQLite")
                    actor = db.scalar(select(User).where(User.username == args.username))
                    if actor is None:
                        raise ValueError("管理员账号不存在")
                    plan = json.loads(args.plan.read_text(encoding="utf-8"))
                    result = apply_plan(db, actor, plan, args.backup)
                print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f"清理未执行：{error}\n")


if __name__ == "__main__":
    main()
