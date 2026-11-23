"""Retire the feature without removing issued tasks or their historical identities."""

from sqlalchemy import select

from app.models import BusinessRole, RecurringRule
from app.services.business import audit


def retire_recurrence(db, actor):
    stopped = changed = 0
    for row in db.scalars(select(RecurringRule).where(RecurringRule.active.is_(True)).with_for_update()):
        before = {"active": row.active, "version": row.version}
        row.active = False
        row.version += 1
        audit(
            db,
            actor,
            "recurrence.retire",
            row,
            before=before,
            after={"active": False, "version": row.version},
            reason="周期任务功能已移除，保留历史任务",
        )
        stopped += 1
    for role in db.scalars(select(BusinessRole).with_for_update()):
        remaining = [r for r in role.rules if r.get("module") != "recurrence"]
        if remaining == role.rules:
            continue
        before = {"rules": role.rules, "version": role.version}
        role.rules = remaining
        role.version += 1
        audit(
            db,
            actor,
            "role.update",
            role,
            before=before,
            after={"rules": remaining, "version": role.version},
            reason="移除已停用的周期任务权限",
        )
        changed += 1
    db.flush()
    return {"stopped_rules": stopped, "updated_roles": changed}
