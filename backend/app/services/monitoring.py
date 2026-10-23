"""Read-only operational checks and a durable, independent alert delivery state."""

import hashlib
import json
import logging
import os
import shutil
import smtplib
import ssl
import tempfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from redis import Redis
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AuditLog, OutboxEvent, RecurringRule, SystemSetting, now

# Alert webhooks may include credentials in their URL. Do not allow httpx's
# request logger to print URLs; alert failures are reported with redacted types.
for _logger_name in ("httpx", "httpcore"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def display_time(value):
    return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S（北京时间）")


def heartbeat(db, current=None):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    payload = {"at": (current or now()).isoformat()}
    db.execute(
        insert(SystemSetting)
        .values(key="worker.dispatch", value=payload)
        .on_conflict_do_update(index_elements=[SystemSetting.key], set_={"value": payload})
    )


def redis_ping():
    with Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3) as client:
        client.ping()


def collect(factory, config=None, current=None, ping=redis_ping):
    config, current = config or get_settings(), current or now()
    checks, issues = [], []

    def check(code, label, healthy, detail):
        item = {"code": code, "label": label, "healthy": healthy, "detail": detail}
        checks.append(item)
        if not healthy:
            issues.append(item)

    queues = {}
    try:
        with factory() as db:
            # Also bounds queries when connected to a busy PostgreSQL instance.
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                db.execute(text("SET LOCAL statement_timeout = '3000ms'"))
            queues = dict(
                db.execute(select(OutboxEvent.state, func.count()).group_by(OutboxEvent.state)).all()
            )
            beat = db.get(SystemSetting, "worker.dispatch")
            last = utc(beat.value["at"]) if beat else None
            check("database", "数据库", True, "连接正常")
            failed_rules = db.scalar(
                select(func.count()).select_from(RecurringRule).where(RecurringRule.last_error.is_not(None))
            )
            check(
                "recurrence_rules", "周期规则执行", not failed_rules, f"{failed_rules} 条周期规则需要人工处理"
            )
            check(
                "scheduler_worker",
                "调度执行心跳",
                bool(
                    last
                    and timedelta(0) <= current - last <= timedelta(seconds=config.monitor_heartbeat_seconds)
                ),
                "最后执行：" + display_time(last) if last else "尚未收到后台执行心跳",
            )
            for state, label in [
                ("failed", "明确失败"),
                ("blocked", "配置或人员阻塞"),
                ("uncertain", "送达结果不明"),
            ]:
                count = queues.get(state, 0)
                check("delivery_" + state, label, count == 0, f"{count} 条待处理")
            overdue = db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.state == "pending",
                    OutboxEvent.available_at <= current - timedelta(minutes=config.monitor_queue_minutes),
                )
            )
            check(
                "queue_overdue",
                "后台任务积压",
                not overdue,
                f"{overdue} 条超过 {config.monitor_queue_minutes} 分钟未执行",
            )
            stuck = db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.state == "sending", OutboxEvent.claimed_at <= current - timedelta(minutes=10)
                )
            )
            check("queue_stuck", "后台任务执行超时", not stuck, f"{stuck} 条超过 10 分钟未结束")
            sync = db.scalar(
                select(AuditLog)
                .where(AuditLog.action.in_(["directory.sync", "directory.sync.failed"]))
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
            check(
                "directory_sync",
                "最近通讯录同步",
                not sync or sync.action == "directory.sync",
                "尚未执行"
                if not sync
                else (
                    "最近一次同步成功"
                    if sync.action == "directory.sync"
                    else "最近一次同步失败，请查看同步记录并重试"
                ),
            )
    except Exception:
        checks.clear()
        issues.clear()
        queues = {}
        check("database", "数据库", False, "读取失败，请检查数据库或连接配置")
    try:
        ping()
        check("redis", "Redis", True, "连接正常")
    except Exception:
        check("redis", "Redis", False, "连接失败，后台任务及企业登录可能受影响")
    try:
        usage = shutil.disk_usage(config.storage_dir)
        free_mb = usage.free // (1024 * 1024)
        check(
            "storage_space",
            "附件存储空间",
            free_mb >= config.monitor_free_mb,
            f"剩余 {free_mb} MB，最低要求 {config.monitor_free_mb} MB",
        )
        with tempfile.TemporaryFile(dir=config.storage_dir) as probe:
            probe.write(b"meeting-actions-monitor")
            probe.flush()
        check("storage_write", "附件目录读写", True, "可读写")
    except OSError:
        check("storage_write", "附件目录读写", False, "目录不可访问或不可写")
    latest = None
    try:
        for manifest in Path(config.backup_dir).glob("*/manifest.json"):
            try:
                values = json.loads(manifest.read_text())
                if not all(
                    name in values
                    and (manifest.parent / name).is_file()
                    and (manifest.parent / name).stat().st_size > 0
                    for name in ("database.dump", "storage.tar.gz")
                ):
                    continue
                when = datetime.fromtimestamp(manifest.stat().st_mtime, timezone.utc)
                latest = max(latest, when) if latest else when
            except (OSError, ValueError, TypeError):
                continue
        fresh = bool(
            latest and timedelta(0) <= current - latest <= timedelta(hours=config.monitor_backup_hours)
        )
        check(
            "backup",
            "备份时效",
            fresh,
            "最新完整备份文件：" + display_time(latest) if latest else "未找到包含数据库和附件的备份清单",
        )
    except OSError:
        check("backup", "备份时效", False, "无法读取备份目录")
    return {
        "checked_at": current.isoformat(),
        "healthy": not issues,
        "checks": checks,
        "issues": issues,
        "queue_counts": queues,
        "latest_backup_at": latest.isoformat() if latest else None,
    }


def state_path(config):
    return config.monitor_state_dir / "state.json"


def load_state(config):
    try:
        value = json.loads(state_path(config).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def store_state(config, state):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def send_alert(config, payload):
    if not config.alert_webhook_url:
        return send_email_alert(config, payload)
    parsed = urlsplit(config.alert_webhook_url)
    if (
        parsed.username
        or parsed.password
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (
                config.app_env != "production"
                and parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise ValueError("告警地址必须使用 HTTPS；开发测试仅允许本机 HTTP")
    headers = {"Idempotency-Key": payload["event_id"]}
    if config.alert_webhook_token:
        headers["Authorization"] = "Bearer " + config.alert_webhook_token
    # Never follow redirects with credentials or persist the provider's URL/body in diagnostics.
    response = httpx.post(
        config.alert_webhook_url, json=payload, headers=headers, timeout=10, follow_redirects=False
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError("告警接收端未确认接收")


def send_email_alert(config, payload):
    if not all([config.smtp_host, config.smtp_from, config.alert_email_to]):
        raise ValueError("邮件告警接收端未完整配置")
    message = EmailMessage()
    event_type = payload["type"]
    subject = {"recovery": "运行已恢复", "test": "告警通道测试"}.get(event_type, "发现运行异常")
    message["Subject"] = "[会议任务系统告警] " + subject
    message["From"] = config.smtp_from
    message["To"] = config.alert_email_to
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = f'<meeting-actions-alert-{payload["event_id"]}@meeting-actions.local>'
    introduction = {
        "recovery": "会议纪要任务单系统运行已恢复。",
        "test": "这是一封会议纪要任务单系统告警通道测试邮件，无需处理。",
    }.get(event_type, "会议纪要任务单系统检测到运行异常。")
    lines = [
        introduction,
        "",
        "事件编号：" + payload["event_id"],
        "检查时间：" + payload["checked_at"],
    ]
    if payload.get("issues"):
        lines.extend(["", "异常项目："])
        lines.extend(f'- {item["label"]}：{item["detail"]}' for item in payload["issues"])
    lines.extend(["", "管理地址：" + payload["management_url"]])
    message.set_content("\n".join(lines))
    smtp_class = smtplib.SMTP_SSL if config.smtp_ssl else smtplib.SMTP
    kwargs = {"timeout": 10}
    if config.smtp_ssl:
        kwargs["context"] = ssl.create_default_context()
    smtp = smtp_class(config.smtp_host, config.smtp_port, **kwargs)
    try:
        if not config.smtp_ssl:
            smtp.ehlo()
            if config.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
        if config.smtp_username:
            smtp.login(config.smtp_username, config.smtp_password)
        refused = smtp.send_message(message, to_addrs=[config.alert_email_to])
        if refused:
            raise RuntimeError("告警收件人被邮件服务器拒绝")
    finally:
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            smtp.close()


def alert_channel(config):
    if config.alert_webhook_url:
        return "webhook"
    if all([config.smtp_host, config.smtp_from, config.alert_email_to]):
        return "email"
    return None


def update_alert(snapshot, config=None, current=None, sender=send_alert):
    config, current = config or get_settings(), current or now()
    state = load_state(config)
    state["snapshot"] = snapshot
    state["checked_at"] = current.isoformat()
    codes = sorted(item["code"] for item in snapshot["issues"])
    fingerprint = hashlib.sha256(json.dumps(codes).encode()).hexdigest() if codes else ""
    acknowledged = state.get("acknowledged_fingerprint", "")
    last_sent = utc(state["last_sent_at"]) if state.get("last_sent_at") else None
    reminder_minutes = (
        config.backup_alert_reminder_minutes if codes == ["backup"] else config.alert_reminder_minutes
    )
    needed = fingerprint != acknowledged or bool(
        fingerprint and (not last_sent or current - last_sent >= timedelta(minutes=reminder_minutes))
    )
    # A timeout can mean the incident arrived. Send a recovery even if its response was lost.
    if (
        not fingerprint
        and state.get("pending", {}).get("attempted_at")
        and state["pending"].get("fingerprint")
    ):
        needed = True
    if not needed:
        state.pop("pending", None)
        state["delivery"] = "idle"
    elif not alert_channel(config):
        state["delivery"] = "not_configured"
    else:
        pending = state.get("pending")
        if not pending or pending["fingerprint"] != fingerprint:
            pending = {
                "fingerprint": fingerprint,
                "event_id": str(uuid4()),
                "started_at": current.isoformat(),
            }
            state["pending"] = pending
        state["delivery"] = "pending"
        # Persist the stable ID before sending, so an uncertain delivery can be deduplicated on retry.
        store_state(config, state)
        if not pending.get("attempted_at") or current - utc(pending["attempted_at"]) >= timedelta(
            seconds=config.monitor_interval_seconds
        ):
            pending["attempted_at"] = current.isoformat()
            store_state(config, state)
            payload = {
                "event_id": pending["event_id"],
                "type": "incident" if fingerprint else "recovery",
                "service": "会议纪要任务单系统",
                "occurred_at": pending["started_at"],
                "checked_at": current.isoformat(),
                "issues": snapshot["issues"],
                "queue_counts": snapshot["queue_counts"],
                "management_url": config.public_url.rstrip("/") + "/settings",
            }
            try:
                sender(config, payload)
                state.update(
                    acknowledged_fingerprint=fingerprint,
                    last_sent_at=current.isoformat(),
                    delivery="sent",
                    last_error=None,
                )
                state.pop("pending", None)
            except Exception as exc:
                state["delivery"] = "failed"
                state["last_error"] = type(exc).__name__ + "：告警发送未确认，请检查独立告警渠道"
    store_state(config, state)
    return state


def public_monitor_state(config=None, current=None):
    config, current = config or get_settings(), current or now()
    state = load_state(config)
    try:
        fresh = bool(
            state.get("checked_at")
            and timedelta(0)
            <= current - utc(state["checked_at"])
            <= timedelta(seconds=config.monitor_interval_seconds * 3)
        )
    except (ValueError, TypeError):
        fresh = False
    return {
        "running": fresh,
        "checked_at": state.get("checked_at"),
        "snapshot": state.get("snapshot"),
        "webhook_configured": bool(config.alert_webhook_url),
        "alert_configured": bool(alert_channel(config)),
        "alert_channel": alert_channel(config),
        "delivery": state.get("delivery", "not_started"),
        "last_sent_at": state.get("last_sent_at"),
        "last_error": state.get("last_error"),
        "interval_seconds": config.monitor_interval_seconds,
    }
