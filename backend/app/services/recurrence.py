"""Calendar-based independent instances. Rule locks and a database key prevent duplicate periods."""

from calendar import monthrange
from datetime import date, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db import locked_get
from app.models import Meeting, RecurringRule, Task, TaskOwner, TaskVersion, User, uid
from app.services.access import can_edit_meeting, can_manage_direct_task, direct_assignees, meeting_access
from app.services.app_native_todo import enqueue_issuer_confirmation
from app.services.business import active_user, audit, enqueue, enqueue_native_todo, task_snapshot, today


class RecurrenceCreate(BaseModel):
    expected_version: int
    frequency: Literal["weekly", "monthly"]
    end_date: date | None = None
    reason: str = Field(min_length=1, max_length=1000)


class RecurrenceUpdate(BaseModel):
    expected_version: int
    active: bool
    end_date: date | None = None
    content: str = Field(min_length=1, max_length=20000)
    requirements: str = ""
    deliverable: str = ""
    owner_ids: list[str] = Field(min_length=1)
    verifier_id: str
    reason: str = Field(min_length=1, max_length=1000)


def period_start(day, frequency):
    return day - timedelta(days=day.weekday()) if frequency == "weekly" else day.replace(day=1)


def next_due(day, frequency, anchor):
    if frequency == "weekly":
        return day + timedelta(days=7)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def rule_snapshot(rule):
    return {
        "id": rule.id,
        "source_task_id": rule.source_task_id,
        "frequency": rule.frequency,
        "anchor_date": rule.anchor_date.isoformat(),
        "next_due_date": rule.next_due_date.isoformat(),
        "end_date": rule.end_date.isoformat() if rule.end_date else None,
        "active": rule.active,
        "version": rule.version,
        "template": rule.template,
        "last_error": rule.last_error,
    }


def can_manage_rule(db, actor, rule):
    if not rule:
        return False
    source = db.get(Task, rule.source_task_id)
    if source.source_meeting_id:
        return can_edit_meeting(db, actor, db.get(Meeting, source.source_meeting_id))
    return actor.active and (
        actor.is_admin or (actor.id == rule.creator_id and bool(direct_assignees(db, actor)))
    )


def visible_rule_snapshot(db, actor, rule):
    if can_manage_rule(db, actor, rule):
        return {**rule_snapshot(rule), "can_manage": True}
    return {
        "id": rule.id,
        "frequency": rule.frequency,
        "anchor_date": rule.anchor_date.isoformat(),
        "next_due_date": rule.next_due_date.isoformat(),
        "end_date": rule.end_date.isoformat() if rule.end_date else None,
        "active": rule.active,
        "template": None,
        "can_manage": False,
    }


def validate_direct_rule_scope(db, creator_id, template):
    creator = db.get(User, creator_id)
    allowed = {person.id for person in direct_assignees(db, creator)} if creator else set()
    if (
        not creator
        or not allowed
        or not set(template["owner_ids"]) <= allowed
        or template["verifier_id"] not in allowed | {creator.id}
    ):
        raise HTTPException(422, "后续责任人或验证人不在周期派发人的范围内，请先由管理员移交派发人")


def enable_rule(db, actor, task_id, body):
    task = locked_get(db, Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.source_meeting_id:
        meeting_access(db, actor, db.get(Meeting, task.source_meeting_id), edit=True)
    elif not can_manage_direct_task(db, actor, task):
        raise HTTPException(403, "只有管理员或派发人可以为直接任务设置周期")
    if task.withdrawn_at:
        raise HTTPException(409, "已撤回任务不能设置周期")
    if task.version != body.expected_version or task.recurrence_key:
        raise HTTPException(409, "任务已变化或已经设置周期，请刷新")
    if task.due_date is None:
        raise HTTPException(422, "请先设置截止日期，再启用周期任务")
    if task.due_date < today():
        raise HTTPException(422, "请使用当前或未来期限的任务作为首期，历史逾期任务不能自动补生成")
    if not body.reason.strip() or (body.end_date and body.end_date < task.due_date):
        raise HTTPException(422, "请填写依据，结束日期不能早于首期期限")
    template = task_snapshot(db, task)
    for member in [*template["owner_ids"], task.verifier_id]:
        active_user(db, member)
    if task.source_meeting_id is None:
        validate_direct_rule_scope(db, actor.id, template)
    rule = RecurringRule(
        source_task_id=task.id,
        creator_id=actor.id,
        frequency=body.frequency,
        anchor_date=task.due_date,
        next_due_date=next_due(task.due_date, body.frequency, task.due_date),
        end_date=body.end_date,
        template=template,
    )
    db.add(rule)
    db.flush()
    task.recurrence_key = rule.id
    task.recurrence_period = period_start(task.due_date, body.frequency)
    audit(db, actor, "recurrence.create", rule, after=rule_snapshot(rule), reason=body.reason)
    return rule


def update_rule(db, actor, rule_id, body):
    rule = locked_get(db, RecurringRule, rule_id)
    if not rule:
        raise HTTPException(404, "周期规则不存在")
    source = db.get(Task, rule.source_task_id)
    if not can_manage_rule(db, actor, rule):
        raise HTTPException(403, "没有维护此周期规则的权限")
    if rule.version != body.expected_version:
        raise HTTPException(409, "周期规则已变化，请刷新")
    if not body.reason.strip() or (body.end_date and body.end_date < rule.anchor_date):
        raise HTTPException(422, "请填写原因，结束日期不能早于首期期限")
    if body.active:
        for member in [*body.owner_ids, body.verifier_id]:
            active_user(db, member)
        if source.source_meeting_id is None:
            validate_direct_rule_scope(db, rule.creator_id, body.model_dump())
    before = rule_snapshot(rule)
    rule.template = {
        **rule.template,
        **body.model_dump(
            mode="json",
            include={
                "content",
                "requirements",
                "deliverable",
                "owner_ids",
                "verifier_id",
            },
        ),
    }
    # Paused periods are deliberately skipped on resume; already issued instances remain intact.
    if body.active and not rule.active:
        while period_start(rule.next_due_date, rule.frequency) < period_start(today(), rule.frequency):
            rule.next_due_date = next_due(rule.next_due_date, rule.frequency, rule.anchor_date)
    rule.active, rule.end_date = body.active, body.end_date
    rule.version += 1
    rule.last_error = None
    audit(db, actor, "recurrence.update", rule, before=before, after=rule_snapshot(rule), reason=body.reason)
    return rule


def generate_instances(db, current=None):
    current = current or today()
    generated = 0
    for rule in db.scalars(
        select(RecurringRule)
        .where(RecurringRule.active.is_(True))
        .order_by(RecurringRule.id)
        .with_for_update(skip_locked=True)
    ):
        template = rule.template
        people = [db.get(User, i) for i in [*template["owner_ids"], template["verifier_id"]]]
        if any(not person or not person.active for person in people):
            rule.last_error = "周期责任人或验证人已停用，请调整规则后恢复"
            rule.active = False
            rule.version += 1
            continue
        source = db.get(Task, rule.source_task_id)
        actor = db.get(User, rule.creator_id)
        if source.source_meeting_id is None:
            allowed = {person.id for person in direct_assignees(db, actor)} if actor else set()
            if not actor or not allowed or not set(template["owner_ids"]) <= allowed or template["verifier_id"] not in allowed | {actor.id}:
                rule.last_error = "派发人权限或责任范围已变化，请重新确认周期规则"
                rule.active = False
                rule.version += 1
                continue
        for _ in range(100):
            due = rule.next_due_date
            if rule.end_date and due > rule.end_date:
                rule.active = False
                rule.version += 1
                break
            period = period_start(due, rule.frequency)
            if period > current:
                break
            existing = db.scalar(
                select(Task.id).where(
                    Task.recurrence_key == rule.id,
                    Task.recurrence_period == period,
                )
            )
            if not existing:
                task = Task(
                    code="RW-" + uid().replace("-", "")[:12].upper(),
                    content=template["content"],
                    category=template.get("category", ""),
                    requirements=template.get("requirements", ""),
                    deliverable=template.get("deliverable", ""),
                    department_id=source.department_id,
                    source_meeting_id=source.source_meeting_id,
                    issuer_id=rule.creator_id if source.source_meeting_id is None else source.issuer_id,
                    source_item_id=uid(),
                    series_id=source.series_id,
                    verifier_id=template["verifier_id"],
                    due_date=due,
                    status="未完成" if due < current else "未到期",
                    recurrence_key=rule.id,
                    recurrence_period=period,
                )
                db.add(task)
                db.flush()
                for identifier in set(template["owner_ids"]):
                    db.add(TaskOwner(task_id=task.id, user_id=identifier))
                db.flush()
                db.add(
                    TaskVersion(
                        task_id=task.id,
                        number=1,
                        snapshot=task_snapshot(db, task),
                        reason="周期规则自动生成",
                        actor_id=rule.creator_id,
                        source_meeting_id=source.source_meeting_id,
                    )
                )
                enqueue(
                    db,
                    f"task:{task.id}:recurring",
                    "wecom",
                    {
                        "task_id": task.id,
                        "task_version": 1,
                        "user_ids": sorted(set(template["owner_ids"])),
                        "title": "新的周期会议任务" if source.source_meeting_id else "新的周期直接派发任务",
                        "content": task.content,
                    },
                )
                enqueue_native_todo(db, task, "create", key_suffix="recurring")
                enqueue_issuer_confirmation(db, task, actor)
                audit(db, actor, "recurrence.generate", task, after=task_snapshot(db, task), reason=rule.id)
                generated += 1
            rule.next_due_date = next_due(due, rule.frequency, rule.anchor_date)
            rule.version += 1
            rule.last_error = None
    return generated
