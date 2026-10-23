"""Transactional outbox with explicit ambiguous-delivery state (no blind resend)."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, locked_get
from app.models import CompletionRequest, Meeting, NotificationLog, OutboxEvent, Task, now
from app.services.business import enqueue, notification_enabled, owner_ids, task_wecom_enabled
from app.services.exports import generate_exports
from app.services.integrations import DeliveryUncertain, NotConfigured, send_mail, send_wecom
from app.services.monitoring import heartbeat
from app.services.native_todo import send_native_todo

celery = Celery("meeting_actions", broker=get_settings().redis_url)
celery.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=True,
    beat_schedule={
        "outbox": {"task": "app.worker.dispatch", "schedule": 15.0},
        "recurrence": {"task": "app.worker.recurrence", "schedule": crontab(minute=0)},
        "reminders": {"task": "app.worker.reminders", "schedule": crontab(minute=0)},
    },
)


@celery.task(name="app.worker.recurrence")
def recurrence():
    from app.services.recurrence import generate_instances

    with SessionLocal.begin() as db:
        return generate_instances(db)


@celery.task(name="app.worker.dispatch")
def dispatch(limit=50):
    with SessionLocal.begin() as db:
        heartbeat(db)
    processed = 0
    for _ in range(limit):
        with SessionLocal() as db:
            stale = list(
                db.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.state == "sending", OutboxEvent.claimed_at < now() - timedelta(minutes=10)
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for event in stale:
                event.state = "pending" if event.channel in {"export", "import"} else "uncertain"
                event.last_error = (
                    "发送进程中断，需核实外部送达状态"
                    if event.channel not in {"export", "import"}
                    else "导出任务中断，将恢复"
                )
            db.commit()
            event = db.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.state == "pending", OutboxEvent.available_at <= now())
                .order_by(OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not event:
                break
            event.state = "sending"
            event.claimed_at = now()
            event.attempts += 1
            event_id = event.id
            db.commit()
        with SessionLocal() as db:
            event = locked_get(db, OutboxEvent, event_id)
            try:
                if not notification_enabled(db, event.channel, event.payload):
                    result = {"skipped": "该来源的外部提醒已关闭，未发送且不会补发"}
                elif event.channel == "export":
                    locked_get(db, Meeting, event.payload["meeting_id"])
                    generate_exports(db, event.payload["meeting_id"], event.payload["version"])
                    result = {}
                elif event.channel == "import":
                    from app.models import Attachment, ImportBatch, ImportSource, User
                    from app.services.importing import parse_source

                    source = locked_get(db, ImportSource, event.payload["source_id"])
                    attachment = db.get(Attachment, source.attachment_id)
                    parse_source(
                        db,
                        db.get(User, event.payload["actor_id"]),
                        db.get(ImportBatch, source.batch_id),
                        source,
                        get_settings().storage_dir / attachment.storage_key,
                    )
                    result = {}
                elif event.channel == "wecom":
                    result = send_wecom(db, event)
                elif event.channel == "email":
                    locked_get(db, Meeting, event.payload["meeting_id"])
                    result = send_mail(db, event)
                elif event.channel == "native_todo":
                    result = send_native_todo(db, event)
                else:
                    raise ValueError("未知发送渠道")
                event.state = "skipped" if result.get("skipped") else "sent"
                event.sent_at = now()
                event.last_error = None
            except NotConfigured as exc:
                event.state = "blocked"
                event.last_error = str(exc)
                result = {}
            except DeliveryUncertain as exc:
                event.state = "uncertain"
                event.last_error = str(exc)
                result = {}
            except Exception as exc:
                event.state = "failed"
                # Transport exceptions may include a URL with an access token; never persist it.
                event.last_error = type(exc).__name__ + "：发送失败，请检查配置及服务日志（不含密钥）"
                result = {}
                # Configuration and ambiguous sends require operator action; known failures retry with backoff.
                if event.attempts < 5:
                    event.state = "pending"
                    event.available_at = now() + timedelta(seconds=min(3600, 30 * 2**event.attempts))
            db.add(NotificationLog(event_id=event.id, result=event.state, detail=result))
            db.commit()
            processed += 1
    return processed


@celery.task(name="app.worker.reminders")
def reminders():
    current = now()
    day = current.astimezone(ZoneInfo("Asia/Shanghai")).date()
    with SessionLocal() as db:
        from collections import defaultdict

        from app.models import SystemSetting

        entry = db.get(SystemSetting, "reminders")
        config = (
            entry.value
            if entry
            else {
                "enabled": True,
                "hour": 9,
                "before_days": 1,
                "overdue_enabled": True,
                "verification_hours": 24,
            }
        )
        if not config.get("enabled", True) and not get_settings().wecom_direct_notifications_enabled:
            return 0
        if current.astimezone(ZoneInfo("Asia/Shanghai")).hour != config["hour"]:
            return 0

        groups = defaultdict(list)
        for task in db.scalars(
            select(Task).where(
                Task.closed_at.is_(None),
                Task.due_date.is_not(None),
                Task.status.notin_(["关闭", "不统计", "已完成"]),
            )
        ):
            if not task_wecom_enabled(db, task):
                continue
            delta = (task.due_date - day).days
            if delta == config["before_days"] or (delta < 0 and config["overdue_enabled"]):
                for user_id in owner_ids(db, task):
                    groups[user_id].append(task.id)
        for user_id, lines in groups.items():
            enqueue(
                db,
                f"reminder:{day}:{user_id}",
                "wecom",
                {"user_ids": [user_id], "title": "任务到期 / 逾期汇总", "reminder_task_ids": lines},
            )
        verification_groups = defaultdict(list)
        for request, task in db.execute(
            select(CompletionRequest, Task)
            .join(Task, Task.id == CompletionRequest.task_id)
            .where(
                CompletionRequest.state == "pending",
                CompletionRequest.created_at <= current - timedelta(hours=config["verification_hours"]),
                Task.closed_at.is_(None),
                CompletionRequest.task_version == Task.version,
            )
        ):
            if not task_wecom_enabled(db, task):
                continue
            verification_groups[task.verifier_id].append(request.id)
        for verifier_id, request_ids in verification_groups.items():
            enqueue(
                db,
                f"verify-digest:{day}:{verifier_id}",
                "wecom",
                {
                    "verification_request_ids": sorted(request_ids),
                    "user_ids": [verifier_id],
                    "title": "待验证完成申请每日汇总",
                },
            )
        db.commit()
