"""One transactional, serialized directory sync for manual and scheduled runs."""

from sqlalchemy import select, text

from app.models import AuditLog, now
from app.services.business import audit
from app.services.integrations import NotConfigured, ProviderRejected, sync_directory

LOCK_KEY = 7812042301


def synchronize_directory(db, actor, *, automatic=False, synchronizer=None):
    # The transaction owns the lock through commit, including the audit record.
    if db.get_bind().dialect.name == "postgresql" and not db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}
    ):
        return {"state": "busy", "error": "通讯录正在同步，请稍后查看结果"}
    hour_start = now().replace(minute=0, second=0, microsecond=0)
    if automatic and db.scalar(
        select(AuditLog.id).where(
            AuditLog.action.in_(["directory.sync", "directory.sync.failed"]),
            AuditLog.created_at >= hour_start,
            AuditLog.after["mode"].as_string() == "automatic",
        ).limit(1)
    ):
        return {"state": "skipped", "reason": "本小时已执行自动同步"}
    mode = "automatic" if automatic else "manual"
    try:
        with db.begin_nested():
            result = (synchronizer or sync_directory)(db)
    except Exception as exc:
        known = isinstance(exc, (NotConfigured, ProviderRejected))
        error = str(exc) if known else type(exc).__name__ + "：通讯录同步失败，请检查企业权限与网络连接"
        audit(db, actor, "directory.sync.failed", actor,
              after={"error": error, "mode": mode}, reason="自动同步失败" if automatic else "通讯录同步失败")
        return {"state": "failed", "error": error, "status_code": 422 if known else 503}
    audit(db, actor, "directory.sync", actor, after={**result, "mode": mode},
          reason="系统每小时自动同步企业微信通讯录" if automatic else "手动同步企业微信通讯录")
    return {"state": "succeeded", "result": result}
