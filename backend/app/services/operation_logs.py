"""Human-readable audit history and request context, without credentials or request bodies."""

import json
import re
from contextvars import ContextVar
from datetime import timezone

from fastapi.exceptions import RequestValidationError
from sqlalchemy import select

from app.error_messages import error_message
from app.models import (
    Attachment,
    AuditLog,
    Meeting,
    MeetingSeries,
    MeetingVersion,
    NotificationLog,
    OutboxEvent,
    Task,
    User,
)

request_context = ContextVar("operation_request", default=None)
ACTION_LABELS = {
    "auth.login": "登录系统",
    "auth.login_failed": "登录失败",
    "auth.logout": "退出登录",
    "user.password.change": "修改密码",
    "meeting.create": "新建会议",
    "meeting.save": "保存会议任务单",
    "meeting.publish": "发布会议",
    "meeting.revision": "修改已发布会议",
    "meeting.continue": "持续新建会议",
    "meeting.continuation.sync": "同步下期会议草稿",
    "meeting.history.supplement": "历史会议自动补齐下期上期任务",
    "meeting.delete": "删除会议",
    "meeting.revision.cancel": "取消会议修订",
    "meeting.title.rename": "修改会议名称",
    "meeting_series.create": "新建会议系列",
    "meeting_series.name.rename": "修改会议系列名称",
    "meeting_series.delete": "删除会议系列",
    "meeting.edit_request": "申请编辑会议",
    "meeting.edit_decision": "处理会议编辑申请",
    "meeting.archive_series": "调整会议系列",
    "meeting.import_template": "补录历史任务",
    "meeting_sheet.recognize": "识别导入任务单",
    "meeting_sheet.create_draft": "导入生成会议草稿",
    "meeting_sheet.upload": "上传任务单文件",
    "meeting_sheet.retry": "重新识别任务单",
    "meeting_sheet.recognition_failed": "任务单识别失败",
    "task.direct_assign": "直接派发任务",
    "task.change": "修改任务",
    "task.scope.move": "调整任务所属部门",
    "task.meeting_completion": "评定任务完成结果",
    "task.completion_request": "申请任务完成",
    "task.completion_verify": "验证任务完成",
    "task.verify": "验证任务完成",
    "task.handover": "移交任务",
    "task.withdraw": "撤回任务",
    "task.activate_historical": "延续历史任务",
    "task.carry_replacement": "更换延续任务责任人",
    "user.role": "调整系统管理员权限",
    "user.department_roles": "调整员工角色",
    "user.report_access": "调整报表权限",
    "user.department_leader": "调整部门负责人权限",
    "directory.sync": "同步员工通讯录",
    "directory.sync.failed": "通讯录同步失败",
    "directory.alias": "保存人员别名",
    "role.create": "创建业务角色",
    "role.update": "修改业务角色",
    "role.delete": "删除业务角色",
    "user.business_roles": "分配员工角色",
    "directory.alias.remove": "删除人员别名",
    "file.upload": "上传附件",
    "notification.cancel": "取消发送通知",
    "notification.retry": "重试发送通知",
    "notification.resolve": "核实通知发送结果",
    "notification.delivery": "系统发送通知",
    "meeting.export": "生成会议纪要文件",
    "integration.check": "检查系统连接",
    "recurrence.create": "新建周期派发规则",
    "recurrence.update": "修改周期派发规则",
    "recurrence.generate": "系统生成周期任务",
    "recurrence.stop_on_withdrawal": "停止周期任务",
    "recurrence.retire": "移除周期任务功能",
    "system.test_data_cleanup": "清理历史测试数据",
    "import.correct": "修正历史导入",
    "import.confirm": "确认历史导入",
    "import.scope": "调整历史导入范围",
    "import.rematch": "重新匹配导入人员",
    "import.acknowledge_file_issue": "核对导入文件问题",
}
CATEGORIES = {
    "auth": "登录与账号",
    "meeting": "会议与任务单",
    "task": "任务与验证",
    "import": "导入与附件",
    "report": "统计报表",
    "permission": "权限管理",
    "notification": "通知发送",
    "system": "系统管理",
}
ENTITY_NAMES = {
    "meetings": "会议",
    "meeting_series": "会议系列",
    "tasks": "任务",
    "users": "员工账号",
    "attachments": "附件",
    "outbox_events": "通知",
    "meeting_sheet_imports": "导入任务单",
}
FIELD_NAMES = {
    "title": "会议名称",
    "name": "名称",
    "meeting_date": "会议日期",
    "meeting_time": "会议时间",
    "location": "会议地点",
    "department_id": "所属部门",
    "series_id": "会议系列",
    "host_id": "主持人",
    "recorder_id": "编制人",
    "reviewer_id": "审核人",
    "attendee_ids": "参会人员",
    "absentees": "缺席情况",
    "items": "任务内容",
    "content": "工作内容",
    "requirements": "工作要求",
    "owner_ids": "责任人",
    "verifier_id": "验证人",
    "due_date": "计划完成时间",
    "postponed_due_date": "延期日期",
    "status": "完成结果",
    "deliverable": "输出物",
    "notes": "备注",
    "decisions": "会议议定事项",
    "recipient_ids": "邮件收件人",
    "cc_ids": "邮件抄送人",
    "cc_emails": "额外抄送邮箱",
    "wecom_recipient_ids": "企业微信通知对象",
    "notify": "发送通知",
    "version": "发布批次",
    "is_admin": "系统管理员",
    "roles": "员工角色",
    "department_ids": "授权部门",
    "filename": "文件名",
    "size": "文件大小",
    "state": "处理状态",
    "channel": "发送渠道",
    "reason": "说明",
    "method": "登录方式",
    "attempt": "发送次数",
    "error": "失败说明",
    "start": "开始日期",
    "end": "结束日期",
    "keyword": "搜索关键词",
    "category": "操作分类",
    "result": "操作结果",
    "group_by": "统计方式",
    "person_id": "统计人员",
    "series_key": "会议主题",
    "senior_only": "仅中高层会议",
    "leader_id": "参会领导",
}
SECRET = re.compile(r"password|secret|token|authorization|cookie|credential|api_key|access_key", re.I)
SUPPORT_READ_PATHS = {
    "/api/health",
    "/api/me",
    "/api/directory",
    "/api/meeting-people",
    "/api/reports/access",
    "/api/direct-assignments/options",
    "/api/task-overview/options",
    "/api/meeting-series-name-options",
    "/api/meeting-title-options",
    "/api/history-meeting-departments",
    "/api/wecom/sdk",
}
SUPPORT_READ_PATTERN = re.compile(
    r"/api/(?:tasks/[^/]+/(?:transfer-options|native-todo|recurrence)|meetings/[^/]+/native-todos)"
)


def passive_read(context):
    """Only successful auxiliary reads may be omitted; never business writes or exports."""
    if context["method"] != "GET":
        return False
    path = context["path"]
    if path.startswith("/api/files/") or "/export" in path or path.endswith("/preview"):
        return False
    return (
        path in SUPPORT_READ_PATHS
        or SUPPORT_READ_PATTERN.fullmatch(path) is not None
        or context.get("background", False)
    )


def sanitize(value):
    if isinstance(value, dict):
        return {str(k): ("[已隐藏]" if SECRET.search(str(k)) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?i)((?:access_token|secret|password|api_key)=)[^\s&]+", r"\1[已隐藏]", value)
    return value


def set_actor(user):
    context = request_context.get()
    if context is not None:
        context["actor"] = {"id": user.id, "name": user.name, "username": user.username}


def failed_login_actor(db, username):
    context = request_context.get()
    root = db.scalar(select(User).where(User.username == "admin"))
    if context is not None and root:
        context["actor"] = {"id": root.id, "name": "未登录访客", "username": username[:120]}
        context["failed_login"] = True


class OperationContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        from urllib.parse import parse_qsl

        allowed = {
            "q",
            "keyword",
            "content",
            "start",
            "end",
            "start_date",
            "end_date",
            "status",
            "department_id",
            "person_id",
            "owner_id",
            "series_key",
            "series",
            "group_by",
            "page",
            "page_size",
            "publication",
            "category",
            "result",
            "version",
            "kind",
            "senior_only",
            "leader_id",
            "view",
            "scope",
            "offset",
            "limit",
            "reminder_day",
        }
        context = {
            "scope": scope,
            "method": scope["method"],
            "path": scope["path"],
            "background": scope["method"] == "GET"
            and dict(scope.get("headers", [])).get(b"x-operation-context") == b"background",
            "query": {
                k: v[:250]
                for k, v in parse_qsl(scope.get("query_string", b"").decode("utf-8", "replace"))
                if k in allowed
            },
        }
        token = request_context.set(context)
        try:
            await self.app(scope, receive, send)
        finally:
            request_context.reset(token)


def category_for(action, path=""):
    if action.startswith(("auth.", "user.password")) or "/auth/" in path or path.endswith("/me/password"):
        return "auth"
    if action.startswith("notification."):
        return "notification"
    if action.startswith(("user.", "role.")):
        return "permission"
    if action.startswith(("meeting_sheet.", "import.", "file.")) or "/files" in path:
        return "import"
    if action.startswith(("meeting.", "meeting_series.")) or any(
        v in path for v in ("/meetings", "/series", "/meeting-series")
    ):
        return "meeting"
    if action.startswith(("task.", "recurrence.", "native_todo.")) or any(
        v in path for v in ("/tasks", "/task-", "/direct-assignments", "/verifications")
    ):
        return "task"
    if "/reports" in path:
        return "report"
    return "system"


def target_name(entity):
    for key in ("title", "name", "content", "filename"):
        if value := getattr(entity, key, None):
            return str(value)[:500]
    return ENTITY_NAMES.get(entity.__tablename__, "系统操作")


def audit_context(actor, action, entity):
    context = request_context.get()
    if context is not None:
        context["business_logged"] = True
    return {
        "actor_name": actor.name,
        "actor_username": actor.username,
        "target_name": target_name(entity),
        "category": category_for(action),
        "result": "success",
        **({key: context[key] for key in ("method", "path", "query")} if context else {}),
    }


def finish_request(db, error=None):
    context = request_context.get()
    if not context or context.get("finished") or not context.get("actor"):
        return
    context["finished"] = True
    if error is None and context.get("business_logged"):
        return
    path, method = context["path"], context["method"]
    if path == "/api/client-errors" or method in {"OPTIONS", "HEAD"} or (error is None and passive_read(context)):
        return
    scope = context["scope"]
    identifier = scope.get("path_params", {}).get("identifier")
    resource = path.removeprefix("/api/").split("/")[0]
    model = {
        "meetings": Meeting,
        "meeting-series": MeetingSeries,
        "series": MeetingSeries,
        "tasks": Task,
        "files": Attachment,
    }.get(resource)
    entity = db.get(model, identifier) if model and identifier else None
    names = {
        "meetings": "会议",
        "meeting-series": "会议系列",
        "series": "会议系列",
        "tasks": "任务",
        "direct-assignments": "我派发的任务",
        "task-management": "任务总览",
        "reports": "统计报表",
        "files": "附件",
        "admin": "系统管理",
        "validation": "历史导入",
        "meeting-sheet-imports": "导入任务单",
        "native-todo": "企业微信待办",
    }
    verb = (
        "导出"
        if "/export" in path
        else "下载"
        if resource == "files" and method == "GET"
        else {"GET": "查看", "POST": "执行", "PUT": "修改", "PATCH": "修改", "DELETE": "删除"}.get(
            method, "操作"
        )
    )
    label = verb + names.get(resource, "系统功能")
    if (path == "/api/meetings" and method == "POST") or (resource == "meetings" and method == "PUT"):
        label = "保存会议草稿"
    elif path.endswith("/preview"):
        label = "预览会议任务单"
    elif path.endswith("/publish") and resource == "meetings":
        label = "发布会议任务单"
    elif path.endswith("/continue") and resource == "meetings":
        label = "接续新建会议"
    elif resource == "meeting-sheet-imports" and method == "POST":
        label = "导入会议任务单"
    elif resource == "direct-assignments" and method == "POST":
        label = "直接派发任务"
    elif "/operation-logs" in path or path == "/api/admin/audit":
        label = "查看操作日志"
    filters = {
        key: value
        for key, value in context.get("query", {}).items()
        if key not in {"view", "offset", "limit", "page", "page_size"}
        and not (key == "scope" and value == "all")
    }
    if filters and verb == "查看":
        label = label.replace("查看", "查询", 1)
    actor = context["actor"]
    values = {key: context[key] for key in ("method", "path", "query")}
    if context.get("background"):
        values["background"] = True
    values.update(
        actor_name=actor["name"],
        actor_username=actor["username"],
        target_name=target_name(entity) if entity else names.get(resource, "系统功能"),
        category=category_for("", path),
        operation_label=label,
        result="failed" if error else "success",
        http_status=getattr(error, "status_code", 500) if error else 200,
    )
    detail = error_message(error, path) if error else ""
    if error:
        values["http_status"] = 422 if isinstance(error, RequestValidationError) else getattr(error, "status_code", 500)
        values["error_source"] = "服务端"
        values["route"] = getattr(scope.get("route"), "path", "")
        page = dict(scope.get("headers", [])).get(b"x-client-page", b"").decode("ascii", "ignore")
        if re.fullmatch(r"/[a-zA-Z0-9/_-]{0,180}", page):
            values["page"] = page
        values["query"] = {k: v for k, v in values["query"].items() if k not in {"q", "keyword", "content"}}
    db.add(
        AuditLog(
            actor_id=actor["id"],
            action="auth.login_failed" if context.get("failed_login") else "request." + method.lower(),
            entity_type=entity.__tablename__ if entity else "system",
            entity_id=entity.id if entity else actor["id"],
            context=sanitize(values),
            reason=str(sanitize(detail))[:1000] if error else "",
        )
    )


def people_snapshot(db, identifiers, address="email"):
    people = (
        {p.id: p for p in db.scalars(select(User).where(User.id.in_(identifiers)))} if identifiers else {}
    )
    return [
        {
            "id": identifier,
            "name": people[identifier].name if identifier in people else "账号已不存在",
            "address": getattr(people[identifier], address) if identifier in people else None,
        }
        for identifier in dict.fromkeys(identifiers)
    ]


def audience(db, payload, channel):
    if channel == "email":
        return {
            "to": people_snapshot(db, payload.get("recipient_ids", [])),
            "cc": people_snapshot(db, payload.get("cc_ids", []))
            + [{"name": "手动添加", "address": address} for address in payload.get("cc_emails", [])],
        }
    return {"to": people_snapshot(db, payload.get("user_ids", []), "wecom_id"), "cc": []}


def publication_plan(db, meeting_id, version):
    events = list(
        db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_key.in_(
                    [f"meeting:{meeting_id}:v{version}:wecom", f"meeting:{meeting_id}:v{version}:mail"]
                )
            )
        )
    )
    return [
        {
            "event_id": event.id,
            "channel": event.channel,
            "state": event.state,
            **audience(db, event.payload, event.channel),
        }
        for event in events
    ]


def actual_audience(planned, actual):
    """Use receipt addresses, retaining frozen names even after directory changes."""
    people = {str(person.get("address") or "").casefold(): person
              for kind in ("to", "cc") for person in planned.get(kind, [])}
    result = {}
    for kind in ("to", "cc"):
        previous, addresses = planned.get(kind, []), actual.get(kind, [])
        result[kind] = []
        for index, address in enumerate(addresses):
            person = people.get(address.casefold())
            if person is None and len(previous) == len(addresses):
                # SMTP preserves payload order; equal lengths mean no deduplication.
                person = previous[index]
            result[kind].append({**(person or {"name": "收件邮箱"}), "address": address})
    return result


def delivery_record(db, planned):
    event = db.get(OutboxEvent, planned.get("event_id")) if planned.get("event_id") else None
    receipts = list(db.scalars(select(NotificationLog).where(NotificationLog.event_id == event.id)
                              .order_by(NotificationLog.created_at, NotificationLog.id))) if event else []
    if receipts:
        history = [{"created_at": log.created_at, "state": log.result,
                    "error": log.detail.get("error"), "actual_to": log.detail.get("to", []),
                    "actual_cc": log.detail.get("cc", [])} for log in receipts]
        successful = [log.detail for log in receipts if log.result == "sent" and "to" in log.detail]
    else:
        attempts = list(db.scalars(select(AuditLog).where(
            AuditLog.entity_id == event.id, AuditLog.action == "notification.delivery")
            .order_by(AuditLog.created_at, AuditLog.id))) if event else []
        history = [{"created_at": attempt.created_at, "state": (attempt.after or {}).get("state"),
                    "error": (attempt.after or {}).get("error"),
                    "actual_to": (attempt.after or {}).get("detail", {}).get("to", []),
                    "actual_cc": (attempt.after or {}).get("detail", {}).get("cc", [])} for attempt in attempts]
        successful = [(attempt.after or {}).get("detail", {}) for attempt in attempts
                      if (attempt.after or {}).get("state") == "sent"
                      and "to" in (attempt.after or {}).get("detail", {})]
    value = {**planned, "state": event.state if event else planned.get("state", "historical"),
             "created_at": event.created_at if event else None,
             "attempts": event.attempts if event else 0,
             "sent_at": event.sent_at if event and event.state == "sent" else None,
             "error": event.last_error if event else None, "history": history,
             "actual_recorded": bool(successful)}
    if successful:
        value.update(actual_audience(planned, successful[-1]))
        value["historical"] = False
        if not value["sent_at"]:
            value["sent_at"] = next(row["created_at"] for row in reversed(history) if row["state"] == "sent")
    return value


def entry_deliveries(db, entry):
    context, after = entry.context or {}, entry.after or {}
    if entry.action == "meeting.publish":
        plan = context.get("notification_plan")
        if plan is None:
            events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_key.in_([
                f"meeting:{entry.entity_id}:v{after.get('version')}:mail",
                f"meeting:{entry.entity_id}:v{after.get('version')}:wecom"]))))
            plan = [{"event_id": event.id, "channel": event.channel, "historical": True,
                     **audience(db, event.payload, event.channel)} for event in events]
            if not plan and after.get("notify", True):
                plan = [{"channel": "email", "historical": True, **audience(db, after, "email")}]
        return [delivery_record(db, planned) for planned in plan]
    if entry.action == "notification.delivery":
        actual = after.get("detail", {})
        value = {"event_id": entry.entity_id, "channel": after.get("channel"),
                 "state": after.get("state"), **after.get("recipients", {}),
                 "created_at": after.get("created_at"), "error": after.get("error"),
                 "sent_at": (after.get("sent_at") or entry.created_at) if after.get("state") == "sent" else None,
                 "actual_recorded": after.get("state") == "sent" and "to" in actual}
        if value["actual_recorded"]:
            value.update(actual_audience(value, actual))
        return [value]
    return []


def meeting_record(db, entry):
    identifier = entry.entity_id if entry.entity_type == "meetings" else (entry.after or {}).get("meeting_id")
    if not identifier:
        return None
    creation = entry if entry.action == "meeting.create" else db.scalar(select(AuditLog).where(
        AuditLog.entity_id == identifier, AuditLog.action == "meeting.create")
        .order_by(AuditLog.created_at, AuditLog.id).limit(1))
    meeting = db.get(Meeting, identifier)
    if not creation and not meeting:
        return None
    actor = db.get(User, creation.actor_id) if creation else None
    return {"created_at": meeting.created_at if meeting else creation.created_at,
            "created_by": ((creation.context or {}).get("actor_name") or (actor.name if actor else "原账号"))
                          if creation else "历史记录未保存创建人",
            "meeting_date": (entry.after or {}).get("meeting_date") or (str(meeting.meeting_date) if meeting else None)}


def log_delivery(db, event, result):
    payload = event.payload
    meeting = db.get(Meeting, payload.get("meeting_id")) if payload.get("meeting_id") else None
    task = db.get(Task, payload.get("task_id")) if payload.get("task_id") else None
    version = (
        db.scalar(
            select(MeetingVersion).where(
                MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == payload.get("version")
            )
        )
        if meeting
        else None
    )
    actor_id = (
        payload.get("actor_id")
        or (version.publisher_id if version else None)
        or (task.issuer_id if task else None)
    )
    actor = db.get(User, actor_id) if actor_id else db.scalar(select(User).where(User.username == "admin"))
    if not actor:
        return
    values = {
        "channel": event.channel,
        "state": event.state,
        "attempt": event.attempts,
        "recipients": audience(db, payload, event.channel),
        "detail": result,
        "error": event.last_error,
        "meeting_id": meeting.id if meeting else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "sent_at": event.sent_at.isoformat() if event.sent_at and event.state == "sent" else None,
        **({"reminder_day": payload.get("reminder_day"), "deadline_items": payload["deadline_items"]}
           if "deadline_items" in payload else {}),
    }
    action = "meeting.export" if event.channel == "export" else "notification.delivery"
    db.add(
        AuditLog(
            actor_id=actor.id,
            action=action,
            entity_type="outbox_events",
            entity_id=event.id,
            after=sanitize(values),
            context={
                "actor_name": "系统自动执行",
                "actor_username": "",
                "initiated_by": actor.name,
                "target_name": meeting.title if meeting else task.content if task else payload.get("title", "系统通知"),
                "category": category_for(action),
                "result": "success" if event.channel == "export" and event.state == "sent" else event.state,
            },
        )
    )


def readable(value, key, db):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        if key == "items":
            return (
                "\n\n".join(
                    f"{i + 1}. {v.get('content', '')}\n"
                    + "；".join(
                        f"{FIELD_NAMES.get(field, field)}：{readable(v[field], field, db)}"
                        for field in (
                            "requirements",
                            "owner_ids",
                            "verifier_id",
                            "due_date",
                            "postponed_due_date",
                            "status",
                            "deliverable",
                            "notes",
                        )
                        if v.get(field) not in (None, "", [])
                    )
                    for i, v in enumerate(value)
                    if isinstance(v, dict)
                )
                or "无任务"
            )
        return "、".join(readable(v, key.removesuffix("s"), db) for v in value) or "无"
    if key in {
        "owner_id",
        "host_id",
        "recorder_id",
        "reviewer_id",
        "verifier_id",
        "attendee_id",
        "recipient_id",
        "cc_id",
        "wecom_recipient_id",
        "person_id",
    }:
        person = db.get(User, value)
        return person.name if person else "账号已不存在"
    if key == "department_id":
        from app.models import Department

        entity = db.get(Department, value)
        return entity.name if entity else "原部门已不存在"
    if key == "series_id":
        entity = db.get(MeetingSeries, value)
        return entity.name if entity else "原会议系列已不存在"
    if isinstance(value, dict):
        return json.dumps(
            {FIELD_NAMES.get(k, k): sanitize(v) for k, v in value.items()}, ensure_ascii=False, indent=2
        )
    return str(value)


def present(db, entry, detail=False):
    context = entry.context or {}
    actor = db.get(User, entry.actor_id)
    before, after = sanitize(entry.before or {}), sanitize(entry.after or {})
    action = context.get("operation_label") or ACTION_LABELS.get(entry.action)
    if not action:
        action = "操作" + ENTITY_NAMES.get(entry.entity_type, "系统功能")
    target = (
        context.get("target_name")
        or after.get("title")
        or before.get("title")
        or after.get("content")
        or after.get("name")
    )
    if not target:
        model = {
            "meetings": Meeting,
            "meeting_series": MeetingSeries,
            "tasks": Task,
            "attachments": Attachment,
        }.get(entry.entity_type)
        entity = db.get(model, entry.entity_id) if model else None
        target = target_name(entity) if entity else ENTITY_NAMES.get(entry.entity_type, "系统记录")
    when = entry.created_at
    if when and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    result = context.get("result", "failed" if entry.action.endswith(".failed") else "success")
    if entry.action == "auth.login_failed":
        action = ACTION_LABELS[entry.action]
    value = {
        "id": entry.id,
        "created_at": when,
        "actor_name": context.get("actor_name") or (actor.name if actor else "原账号"),
        "actor_username": context.get("actor_username", actor.username if actor else ""),
        "action": entry.action,
        "operation": action,
        "target": target,
        "category": context.get("category") or category_for(entry.action),
        "result": result,
        "summary": sanitize(entry.reason)
        or (
            (
                f"已发布；通知 {len(context['notification_plan'])} 个渠道"
                if context.get("notification_plan")
                else "已发布；未生成发送通知"
                if "notification_plan" in context
                else "历史会议发布记录"
            )
            if entry.action == "meeting.publish"
            else action
        ),
    }
    deliveries = entry_deliveries(db, entry)
    mail = next((delivery for delivery in deliveries if delivery.get("channel") == "email"), None)
    if entry.action == "meeting.create" and after.get("meeting_date"):
        value["summary"] = f"会议日期：{after['meeting_date']}"
    if mail:
        states = {"sent": "已发送", "pending": "待发送", "sending": "发送中", "blocked": "发送受阻",
                  "failed": "发送失败", "uncertain": "待核实", "skipped": "已跳过", "historical": "历史发送状态未保存"}
        value["summary"] = ("已发布；" if entry.action == "meeting.publish" else "") + (
            f"邮件{states.get(mail['state'], mail['state'])}；收件 {len(mail.get('to', []))} 人，"
            f"抄送 {len(mail.get('cc', []))} 人")
        value["sent_at"] = mail.get("sent_at")
    if detail:
        ignored = {
            "notification_plan",
            "notification_recipient_ids",
            "notification_cc_ids",
            "notification_cc_emails",
            "sheet_import_id",
            "import_confirmed_issues",
        }
        value["changes"] = [
            {
                "field": FIELD_NAMES.get(key, key),
                "before": readable(before.get(key), key, db),
                "after": readable(after.get(key), key, db),
            }
            for key in dict.fromkeys([*before, *after])
            if key not in ignored and before.get(key) != after.get(key)
        ]
        value["context"] = sanitize(context)
        value["filters"] = [
            {"field": FIELD_NAMES.get(key, key), "value": readable(raw, key, db)}
            for key, raw in context.get("query", {}).items()
            if key not in {"page", "page_size"}
        ]
        value["before"], value["after"] = before, after
        value["deliveries"] = sanitize(deliveries)
        value["meeting_record"] = meeting_record(db, entry)
    return sanitize(value)
