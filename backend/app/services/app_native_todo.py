"""Native todos owned by the business application, without robot authorization."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.models import NativeTodoAttempt, NativeTodoLink, OutboxEvent, User, now
from app.services.access import can_manage_direct_task
from app.services.business import enqueue, owner_ids
from app.services.integrations import ProviderRejected, WeCom, truncate_utf8


def enabled():
    settings = get_settings()
    return settings.native_todo_app_enabled and settings.native_todo_source_enabled("direct")


def task_content(task, *, legacy=False):
    title = f"【{task.code}】" + task.content.replace("\r", " ").replace("\n", " ")
    if legacy:
        return truncate_utf8(title, 512)
    # createTodo has no separate description/deliverable fields. Reserve room for every
    # supplied field and the full system link, rather than truncating away the description.
    fields = [("", title)]
    if task.requirements:
        fields.append(("补充说明：", task.requirements.strip()))
    if task.deliverable:
        fields.append(("输出物：", task.deliverable.strip()))
    url = get_settings().public_url.rstrip("/") + "/tasks?id=" + task.id
    suffix = "\n详情：" + url
    budget = 512 - len(suffix.encode()) - sum(len(label.encode()) for label, _ in fields) - (len(fields) - 1)
    if budget < 90:
        raise ProviderRejected("系统访问地址过长，无法保留原生待办内容和详情链接，请联系管理员")
    allocations = [0] * len(fields)
    lengths = [len(value.encode()) for _, value in fields]
    while budget and any(allocations[i] < lengths[i] for i in range(len(fields))):
        for i in range(len(fields)):
            if budget and allocations[i] < lengths[i]:
                allocations[i] += 1
                budget -= 1
    return (
        "\n".join(label + truncate_utf8(value, allocations[i]) for i, (label, value) in enumerate(fields))
        + suffix
    )


def task_payload(db, task):
    members = [db.get(User, identifier) for identifier in owner_ids(db, task)]
    if not members or any(not p or not p.active or not p.wecom_id for p in members):
        raise ProviderRejected("责任人尚未绑定企业微信或已停用，请先核对人员；应用提醒单独处理")
    if len(members) > 20:
        raise ProviderRejected("原生待办最多同步20位责任人，此任务请通过应用消息和系统任务列表处理")
    content = task_content(task)
    payload = {"content": content, "attendees": sorted({p.wecom_id for p in members})}
    if task.due_date:
        payload["endTime"] = int(
            datetime.combine(task.due_date, time(23, 59, 59), ZoneInfo("Asia/Shanghai")).timestamp()
        )
    return payload


def enqueue_issuer_confirmation(db, task, actor=None):
    """Invite only the bound issuer; a client-confirmed todo suppresses this reminder."""
    if not enabled() or task.source_meeting_id or task.closed_at or task.withdrawn_at:
        return
    actor = actor or (db.get(User, task.issuer_id) if task.issuer_id else None)
    if (
        not actor or not actor.wecom_id or task.issuer_id != actor.id
        or not can_manage_direct_task(db, actor, task)
        or db.scalar(select(NativeTodoLink.id).where(NativeTodoLink.task_id == task.id))
    ):
        return
    try:
        task_payload(db, task)
    except ProviderRejected:
        return
    key = f"task:{task.id}:v{task.version}:native-confirm:{actor.id}"
    if db.scalar(select(OutboxEvent.id).where(OutboxEvent.event_key == key)):
        return
    enqueue(
        db,
        key,
        "wecom",
        {
            "task_id": task.id,
            "task_version": task.version,
            "user_ids": [actor.id],
            "native_confirmation": True,
            "title": "请确认派发企微待办",
            "content": task.content,
        },
    )
    db.flush()
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == key))
    if event and event.state == "pending":
        event.available_at = now() + timedelta(seconds=60)


def native_state(db, actor, task):
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    attempt = db.scalar(select(NativeTodoAttempt).where(NativeTodoAttempt.task_id == task.id))
    manager = task.issuer_id == actor.id and can_manage_direct_task(db, actor, task)
    current = None
    reason = ""
    if enabled() and manager and not task.closed_at and not task.withdrawn_at and not link:
        if not actor.wecom_id:
            reason = "请使用绑定企业微信的派发人账号创建原生待办"
        else:
            try:
                current = task_payload(db, task)
            except ProviderRejected as exc:
                reason = str(exc)
    message = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.channel == "wecom",
            OutboxEvent.event_key.in_(
                [
                    f"task:{task.id}:assigned",
                    f"task:{task.id}:recurring",
                ]
            ),
        )
    )
    warning = ""
    if link and link.provider == "app" and not task.closed_at:
        original = link.snapshot
        # These fields cannot be modified by the official server API.
        content = task_content(task, legacy=original.get("content_version", 1) < 2)
        if original.get("content") != content or original.get("due_date") != (
            task.due_date.isoformat() if task.due_date else None
        ):
            warning = "系统任务的内容、说明、输出物或截止日期已调整，企微原生待办仍保留创建时的安排，请以系统任务及应用通知为准。"
        elif original.get("content_version", 1) < 2 and (task.requirements or task.deliverable):
            warning = "此待办创建时尚未携带补充说明和输出物，请在系统任务详情查看完整安排。"
    return {
        "enabled": enabled(),
        "can_create": current is not None,
        "is_issuer": task.issuer_id == actor.id,
        "reason": reason,
        "link": {"provider": link.provider, "todo_id": link.todo_id, "state": link.state} if link else None,
        "pending_attempt": {
            "id": attempt.id,
            "can_recover": attempt.actor_id == actor.id or actor.is_admin,
            "due_date": attempt.snapshot.get("due_date"),
            "content": attempt.snapshot.get("content"),
            "owner_names": attempt.snapshot.get("owner_names", []),
            "todo_id": attempt.snapshot.get("callback_todo_id")
            if attempt.actor_id == actor.id or actor.is_admin
            else None,
            "verification_error": attempt.snapshot.get("verification_error", ""),
        }
        if attempt and attempt.state == "pending" and not link
        else None,
        "warning": warning,
        "message_state": message.state if message else None,
    }


def send_app_native_todo(db, task, link, action):
    provider = WeCom()
    if action == "finish":
        if not task.closed_at:
            return {"skipped": "系统任务尚未验证关闭或撤回"}
        provider.request("todo/update", body={"todo_id": link.todo_id, "status": 0}, write=True)
        link.state = "finished"
    else:
        if task.closed_at or task.withdrawn_at:
            return {"skipped": "系统任务已关闭或撤回"}
        payload = task_payload(db, task)
        current = provider.request("todo/get", body={"todo_id": link.todo_id})
        states = {p["userid"]: p.get("status", 1) for p in current.get("attendees", [])}
        provider.request(
            "todo/update",
            body={
                "todo_id": link.todo_id,
                "status": 1,
                "attendees": [
                    {"userid": identifier, "status": states.get(identifier, 1)}
                    for identifier in payload["attendees"]
                ],
            },
            write=True,
        )
        verified = provider.request("todo/get", body={"todo_id": link.todo_id})
        participants = {p["userid"] for p in verified.get("attendees", [])}
        desired = set(payload["attendees"])
        if not desired <= participants or participants - desired - {verified.get("creator")}:
            raise ProviderRejected("企微待办参与人更新后核验未通过，请稍后核对同步结果")
        link.follower_ids = payload["attendees"]
        link.state = "active"
    link.task_version = task.version
    link.last_synced_at = now()
    return {"todo_id": link.todo_id, "provider": "app", "action": action}
