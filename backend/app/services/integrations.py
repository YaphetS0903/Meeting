import hashlib
import json
import logging
import re
import secrets
import smtplib
import ssl
import time
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formatdate
from html import escape
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from fastapi import HTTPException
from redis import Redis
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.models import Department, Membership, Task, User
from app.services.access import can_assign_companywide, has_department_assignment_role
from app.services.exports import generate_exports

# httpx logs request URLs at INFO by default. Enterprise credentials are passed as
# query parameters to the WeCom API, so those URLs must never reach service logs.
# Keep transport diagnostics available through the explicit, redacted errors below.
for _logger_name in ("httpx", "httpcore"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)


class NotConfigured(Exception):
    pass


class DeliveryUncertain(Exception):
    pass


class ProviderRejected(Exception):
    pass


def truncate_utf8(value, byte_limit):
    raw = value.encode("utf-8")
    if len(raw) <= byte_limit:
        return value
    marker = "…"
    if byte_limit < len(marker.encode("utf-8")):
        return raw[: max(0, byte_limit)].decode("utf-8", errors="ignore")
    return raw[: max(0, byte_limit - len(marker.encode("utf-8")))].decode("utf-8", errors="ignore") + marker


def truncate_escaped_utf8(value, byte_limit):
    """Keep HTML entities intact while fitting escaped text into a UTF-8 byte budget."""
    escaped = escape(value)
    if len(escaped.encode("utf-8")) <= byte_limit:
        return escaped
    marker = "…" if byte_limit >= 3 else ""
    remaining = max(0, byte_limit - len(marker.encode("utf-8")))
    fragments = []
    for char in value:
        fragment = escape(char)
        size = len(fragment.encode("utf-8"))
        if size > remaining:
            break
        fragments.append(fragment)
        remaining -= size
    return "".join(fragments) + marker


def wecom_text(title, body, url, *, due_date=None, source_title=None):
    """WeCom text content is limited to 2048 UTF-8 bytes, including the complete link."""
    prefix = truncate_utf8(title, 180) + "\n"
    footer = ("\n截止日期：" + due_date.isoformat()) if due_date else ""
    if source_title:
        footer += "\n会议来源：" + truncate_utf8(source_title, 240)
    footer += "\n" + url
    budget = 2048 - len((prefix + footer).encode("utf-8"))
    if budget < 64:
        raise NotConfigured("应用访问地址过长，无法生成完整企业微信消息链接")
    return prefix + truncate_utf8(body, budget) + footer


def redis_client():
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


class WeCom:
    def __init__(self, *, directory=False):
        self.config = get_settings()
        self.directory = directory
        self.secret = self.config.wecom_directory_secret if directory else self.config.wecom_secret
        if not self.config.wecom_corp_id or not self.secret or (not directory and not self.config.wecom_agent_id):
            raise NotConfigured("企业微信通讯录同步凭据未配置" if directory else "企业微信自建应用尚未配置")

    def token(self):
        cache = redis_client()
        key = "wecom:token:" + self.config.wecom_corp_id + ":" + hashlib.sha256(self.secret.encode()).hexdigest()
        cached = cache.get(key)
        if cached:
            return cached
        data = httpx.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": self.config.wecom_corp_id, "corpsecret": self.secret},
            timeout=20,
        ).json()
        if data.get("errcode", 0):
            raise ProviderRejected("企业微信获取授权失败，错误码 " + str(data["errcode"]))
        cache.setex(key, max(60, int(data["expires_in"]) - 120), data["access_token"])
        return data["access_token"]

    def get(self, path, **params):
        data = self.request(path, params=params)
        return data

    def request(self, path, *, params=None, body=None, write=False):
        if self.directory and body is not None:
            raise NotConfigured("通讯录凭据不能调用业务写接口")
        for attempt in range(2):
            token = self.token()
            try:
                result = httpx.request(
                    "GET" if body is None else "POST",
                    "https://qyapi.weixin.qq.com/cgi-bin/" + path,
                    params={"access_token": token, **(params or {})},
                    json=body, timeout=20,
                )
                result.raise_for_status()
                data = result.json()
            except (httpx.HTTPError, ValueError) as exc:
                error = DeliveryUncertain if write else ProviderRejected
                raise error("企业微信响应不明确，请稍后核实或重试") from exc
            if data.get("errcode") in {40014, 42001} and attempt == 0:
                key = "wecom:token:" + self.config.wecom_corp_id + ":" + hashlib.sha256(self.secret.encode()).hexdigest()
                redis_client().delete(key)
                continue
            break
        if data.get("errcode", 0):
            raise ProviderRejected("企业微信接口失败，错误码 " + str(data["errcode"]))
        return data

    def signature(self, url: str, *, agent: bool):
        parsed = urlsplit(url)
        public = urlsplit(self.config.public_url)
        if (
            parsed.scheme != public.scheme or parsed.netloc != public.netloc
            or parsed.username or parsed.password or parsed.fragment
        ):
            raise ProviderRejected("只能为系统当前访问域名签名，请从企业微信工作台打开系统")
        key = "wecom:ticket:" + self.config.wecom_corp_id + ":" + hashlib.sha256(self.secret.encode()).hexdigest() + (":agent" if agent else ":corp")
        cache = redis_client()
        ticket = cache.get(key)
        if not ticket:
            # Tickets have a strict rate limit; serialize refreshes across all backend processes.
            with cache.lock(key + ":lock", timeout=50, blocking_timeout=45):
                ticket = cache.get(key)
                if not ticket:
                    data = self.get("ticket/get", type="agent_config") if agent else self.get("get_jsapi_ticket")
                    ticket = data["ticket"]
                    cache.setex(key, max(60, int(data["expires_in"]) - 120), ticket)
        timestamp, nonce = int(time.time()), secrets.token_hex(16)
        value = f"jsapi_ticket={ticket}&noncestr={nonce}&timestamp={timestamp}&url={url}"
        return {"timestamp": timestamp, "nonceStr": nonce, "signature": hashlib.sha1(value.encode()).hexdigest()}

    def send(self, ids, content, *, card=None):
        if self.directory:
            raise NotConfigured("通讯录凭据不能发送业务消息")
        # One stable body per event; provider duplicate protection supplements the local outbox.
        body = {
            "touser": "|".join(ids),
            "msgtype": "text",
            "agentid": int(self.config.wecom_agent_id),
            "text": {"content": content},
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 14400,
        }
        if card:
            body.pop("text")
            body.update(msgtype="textcard", textcard=card)
        result = self.request("message/send", body=body, write=True)
        if result.get("invaliduser") or result.get("unlicenseduser"):
            raise DeliveryUncertain("部分收件人未送达，请按企业微信记录人工核实，避免重复通知已送达成员")
        return {"msgid": result.get("msgid")}


def sync_directory(db):
    provider = WeCom()
    app = provider.get("agent/get", agentid=get_settings().wecom_agent_id)
    departments = provider.get("department/list")["department"]
    remote_users = {}
    for department in departments:
        for item in provider.get("user/list", department_id=department["id"], fetch_child=0)["userlist"]:
            remote_users[item["userid"]] = item
    # A separate directory credential may see private contacts. It never owns login,
    # active status, department membership, messaging or native todos.
    directory_users = {}
    if get_settings().wecom_directory_secret:
        directory_provider = WeCom(directory=True)
        for department in departments:
            for item in directory_provider.get(
                "user/list", department_id=department["id"], fetch_child=0
            )["userlist"]:
                directory_users[item["userid"]] = item
    direct_user_ids = {
        item["userid"] for item in (app.get("allow_userinfos") or {}).get("user", []) if item.get("userid")
    }
    for user_id in direct_user_ids - remote_users.keys():
        item = provider.get("user/get", userid=user_id)
        remote_users[item["userid"]] = item
    mapping = {}
    for remote in departments:
        dep = db.scalar(select(Department).where(Department.wecom_id == str(remote["id"])))
        if not dep:
            dep = Department(wecom_id=str(remote["id"]), name=remote["name"])
            db.add(dep)
            db.flush()
        dep.name = remote["name"]
        mapping[remote["id"]] = dep
    for remote in departments:
        parent = mapping.get(remote.get("parentid"))
        mapping[remote["id"]].parent_id = parent.id if parent else None
    for remote in remote_users.values():
        user = db.scalar(select(User).where(User.wecom_id == remote["userid"]))
        if not user:
            user = User(wecom_id=remote["userid"], name=remote["name"])
            db.add(user)
            db.flush()
        user.name = remote["name"]
        details = directory_users.get(remote["userid"], remote)
        remote_email = details.get("biz_mail") or details.get("email") or remote.get("biz_mail") or remote.get("email")
        if remote_email:
            user.email = remote_email
        if details.get("mobile"):
            user.mobile = details["mobile"]
        user.position = str(details.get("position") or remote.get("position") or "").strip()
        user.active = remote.get("status", 1) in {1, 4}
        deps = [mapping[x].id for x in remote.get("department", []) if x in mapping]
        primary = mapping.get(remote.get("main_department"))
        user.department_id = primary.id if primary else (deps[0] if deps else None)
        db.execute(delete(Membership).where(Membership.user_id == user.id))
        for dep in set(deps):
            db.add(Membership(user_id=user.id, department_id=dep))
    # Only deactivate previously synced members in the successfully read scope.
    for user in db.scalars(
        select(User).where(
            User.wecom_id.is_not(None), User.department_id.in_([d.id for d in mapping.values()])
        )
    ):
        if user.wecom_id not in remote_users:
            user.active = False
    db.flush()
    return {
        "departments": len(mapping),
        "users": len(remote_users),
        "direct_users": len(direct_user_ids),
        "directory_enriched_users": len(set(directory_users) & set(remote_users)),
        "directory_mobile_users": sum(
            bool(u.mobile) for u in db.scalars(select(User).where(User.wecom_id.in_(remote_users)))
        ),
        "direct_assignment_leaders": sum(
            can_assign_companywide(u) or (has_department_assignment_role(u) and bool(u.department_id))
            for u in db.scalars(select(User).where(User.wecom_id.in_(remote_users), User.active.is_(True)))
        ),
    }


def oauth_destination(value):
    if len(value) > 1024 or "\\" in value:
        raise HTTPException(422, "登录返回地址无效")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise HTTPException(422, "登录返回地址无效") from exc
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not re.fullmatch(
            r"/(tasks|task-overview|assignments|verifications|meetings(?:/[A-Za-z0-9-]+)?|reports|validation|settings)", parsed.path
        )
    ):
        raise HTTPException(422, "登录仅能返回系统内页面")
    key = {"/tasks": "id", "/verifications": "request"}.get(parsed.path)
    values = parse_qs(parsed.query)
    identifier = values.get(key, []) if key else []
    if identifier:
        if len(identifier) != 1 or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", identifier[0]):
            raise HTTPException(422, "登录目标编号无效")
        query = {key: identifier[0]}
        if parsed.path == "/tasks" and values.get("native_confirm") == ["1"]:
            query["native_confirm"] = "1"
        return parsed.path + "?" + urlencode(query)
    if parsed.path in {"/meetings", "/meetings/new"} and values.get("series"):
        series = values["series"]
        if len(series) != 1 or not re.fullmatch(r"(?:series:)?[A-Za-z0-9-]{1,100}", series[0]):
            raise HTTPException(422, "登录目标会议系列无效")
        return parsed.path + "?" + urlencode({"series": series[0]})
    return parsed.path


def oauth_cookie_name(state):
    return "wecom_oauth_" + hashlib.sha256(state.encode()).hexdigest()[:24]


def oauth_url(return_to="/tasks", mode="oauth"):
    settings = get_settings()
    return_to = oauth_destination(return_to)
    WeCom()
    if mode not in {"oauth", "qr"}:
        raise HTTPException(422, "不支持的企业微信登录方式")
    token = secrets.token_urlsafe(32)
    redis_client().setex("oauth:" + token, 600, json.dumps({"return_to": return_to}))
    if mode == "qr":
        return "https://login.work.weixin.qq.com/wwlogin/sso/login?" + urlencode({
            "login_type": "CorpApp",
            "appid": settings.wecom_corp_id,
            "agentid": settings.wecom_agent_id,
            "redirect_uri": settings.public_url.rstrip("/") + "/tasks",
            "state": token,
            "lang": "zh",
        }), token
    url = (
        "https://open.weixin.qq.com/connect/oauth2/authorize?"
        + urlencode(
            {
                "appid": settings.wecom_corp_id,
                "redirect_uri": settings.public_url.rstrip("/") + "/tasks",
                "response_type": "code",
                "scope": "snsapi_base",
                "state": token,
            }
        )
        + "#wechat_redirect"
    )
    return url, token


def oauth_user(db, code, state, cookie):
    if len(state) > 128:
        raise HTTPException(401, "登录链接已失效")
    if not cookie:
        raise HTTPException(401, "登录页面已过期或浏览器未保留校验信息，请在原浏览器返回系统重新登录")
    if not secrets.compare_digest(state.encode(), cookie.encode()):
        raise HTTPException(401, "登录校验未通过，请返回系统重新登录")
    stored = redis_client().getdel("oauth:" + state)
    if not stored:
        raise HTTPException(401, "登录链接已失效")
    try:
        value = json.loads(stored)
        return_to = (
            oauth_destination(value.get("return_to", "/tasks")) if isinstance(value, dict) else "/tasks"
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(401, "登录状态无效，请重新登录") from exc
    result = WeCom().get("auth/getuserinfo", code=code)
    member_id = result.get("userid")
    if not isinstance(member_id, str) or not member_id.strip():
        raise HTTPException(403, "请使用本企业成员的企业微信扫码登录")
    user = db.scalar(select(User).where(User.wecom_id == member_id, User.active.is_(True)))
    if not user:
        raise HTTPException(403, "当前成员尚未同步或不在授权范围")
    return user, return_to


def pending_verification_tasks(db, payload):
    """Resolve a queued digest against current permissions, task versions and reminder rules."""
    from datetime import timedelta, timezone

    from app.models import CompletionRequest, SystemSetting, now
    from app.services.business import task_wecom_enabled

    recipients = payload.get("user_ids", [])
    if len(recipients) != 1:
        return []
    setting = db.get(SystemSetting, "reminders")
    hours = setting.value["verification_hours"] if setting else 24
    cutoff = now() - timedelta(hours=hours)
    tasks = []
    for request, task in db.execute(
        select(CompletionRequest, Task)
        .join(Task, Task.id == CompletionRequest.task_id)
        .where(
            CompletionRequest.id.in_(payload.get("verification_request_ids", [])),
            CompletionRequest.state == "pending",
            CompletionRequest.task_version == Task.version,
            Task.closed_at.is_(None),
            Task.status.notin_(["关闭", "已完成", "不统计"]),
            Task.verifier_id == recipients[0],
        )
        .order_by(CompletionRequest.created_at, CompletionRequest.id)
    ):
        created = request.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created <= cutoff and task_wecom_enabled(db, task):
            tasks.append(task)
    return tasks


def send_wecom(db, event):
    from app.services.business import notification_enabled, task_wecom_enabled

    if not notification_enabled(db, "wecom", event.payload):
        return {"skipped": "该来源的外部提醒已关闭，未发送且不会补发"}
    p = event.payload
    if "verification_request_ids" in p:
        tasks = pending_verification_tasks(db, p)
        if not tasks:
            return {"skipped": "待验证申请已处理、调整或不再满足提醒条件"}
        lines = [f"{t.code} {t.content[:80]}（截止 {t.due_date or '未设置'}）" for t in tasks]
        p = {**p, "content": f"共 {len(tasks)} 项待验证，请进入应用查看全部。\n" + "\n".join(lines)}
    if p.get("reminder_task_ids"):
        from app.models import SystemSetting
        from app.services.business import owner_ids, today

        setting = db.get(SystemSetting, "reminders")
        config = setting.value if setting else {"before_days": 1, "overdue_enabled": True}

        items = []
        for identifier in p["reminder_task_ids"]:
            current = db.get(Task, identifier)
            if (
                current
                and task_wecom_enabled(db, current)
                and not current.closed_at
                and current.status not in {"关闭", "已完成", "不统计"}
                and current.due_date is not None
                and p["user_ids"][0] in owner_ids(db, current)
            ):
                delta = (current.due_date - today()).days
                if delta == config["before_days"] or (delta < 0 and config["overdue_enabled"]):
                    items.append(f"{current.code} {current.content[:80]}（截止 {current.due_date}）")
        if not items:
            return {"skipped": "到期事项已关闭或调整"}
        p = {**p, "content": "\n".join(items)}
    task = db.get(Task, p.get("task_id")) if p.get("task_id") else None
    if task and (task.version != p.get("task_version") or task.closed_at and not p.get("allow_closed")):
        return {"skipped": "任务已更新或关闭"}
    if p.get("native_confirmation"):
        from app.models import NativeTodoLink
        from app.services.access import can_manage_direct_task
        from app.services.app_native_todo import enabled, task_payload

        issuer = db.get(User, task.issuer_id) if task and task.issuer_id else None
        if (
            not enabled() or not task or task.source_meeting_id or task.closed_at or task.withdrawn_at
            or not issuer or not issuer.active or not issuer.wecom_id
            or p.get("user_ids") != [issuer.id] or not can_manage_direct_task(db, issuer, task)
            or db.scalar(select(NativeTodoLink.id).where(NativeTodoLink.task_id == task.id))
        ):
            return {"skipped": "原生待办已确认或派发确认已不适用"}
        try:
            task_payload(db, task)
        except ProviderRejected:
            return {"skipped": "责任人不满足原生待办条件，继续使用应用消息"}
    if p.get("meeting_edit_request_id"):
        from app.models import Meeting, MeetingEditRequest

        approval = db.get(MeetingEditRequest, p["meeting_edit_request_id"])
        meeting = db.get(Meeting, p.get("meeting_id"))
        if not approval or not meeting or approval.permission_generation != meeting.permission_generation:
            return {"skipped": "会议或审批申请已失效"}
        if p.get("meeting_edit_expected_state") and approval.state != p["meeting_edit_expected_state"]:
            return {"skipped": "编辑申请已处理"}
    if p.get("request_id"):
        from app.models import CompletionRequest

        request = db.get(CompletionRequest, p["request_id"])
        if not request or request.state != "pending":
            return {"skipped": "申请已处理或失效"}
    users = [db.get(User, i) for i in p.get("user_ids", [])]
    if not users or any(not u or not u.active or not u.wecom_id for u in users):
        raise NotConfigured("收件人未绑定企业微信或已停用，请同步并重新指派")
    if p.get("native_confirmation"):
        path = "/tasks?" + urlencode({"id": task.id, "native_confirm": "1"})
    elif p.get("meeting_edit_request_id"):
        path = "/meetings/" + p["meeting_id"]
    elif p.get("request_id"):
        path = "/verifications?" + urlencode({"request": p["request_id"]})
    elif "verification_request_ids" in p:
        path = "/verifications"
    else:
        path = "/tasks" + ("?" + urlencode({"id": task.id}) if task else "")
    url = get_settings().public_url.rstrip("/") + path
    source_title = None
    if task:
        from app.models import Meeting

        meeting = db.get(Meeting, task.source_meeting_id) if task.source_meeting_id else None
        source_title = meeting.title if meeting else "直接派发任务"
    content = wecom_text(
        p.get("title", "会议任务提醒"),
        p.get("content", ""),
        url,
        due_date=task.due_date if task else None,
        source_title=source_title,
    )
    if task and task.source_meeting_id is None and task.issuer_id:
        footer = "<br>截止日期：" + (task.due_date.isoformat() if task.due_date else "未设置")
        issuer = db.get(User, task.issuer_id)
        footer += "<br>派发人：" + truncate_escaped_utf8(issuer.name if issuer else "—", 120)
        footer += (
            "<br>打开后自动弹出企微确认窗，确认后责任人直接收到待办。"
            if p.get("native_confirmation") else "<br>请进入系统处理任务及完成验证。"
        )
        prefix = escape(task.code) + "<br>"
        raw = p.get("content", "").replace("\n", " ").replace("\r", " ")
        budget = 512 - len((prefix + footer).encode("utf-8"))
        description = prefix + truncate_escaped_utf8(raw, budget) + footer
        card = {
            "title": truncate_utf8(p.get("title", "直接派发任务提醒"), 120),
            "description": description, "url": url,
            "btntxt": "确认派发" if p.get("native_confirmation") else "查看任务",
        }
        return WeCom().send([u.wecom_id for u in users], content, card=card)
    return WeCom().send([u.wecom_id for u in users], content)


@contextmanager
def smtp_session(timeout=30):
    config = get_settings()
    if not config.smtp_host or not config.smtp_from:
        raise NotConfigured("SMTP 尚未配置主机或发件地址")
    if config.smtp_ssl:
        smtp = smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=timeout)
    try:
        if config.smtp_starttls and not config.smtp_ssl:
            smtp.starttls(context=ssl.create_default_context())
        if config.smtp_username:
            smtp.login(config.smtp_username, config.smtp_password)
        yield smtp
    finally:
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            try:
                smtp.close()
            except (OSError, smtplib.SMTPException):
                pass


def send_mail(db, event):
    from app.services.business import external_notifications_enabled

    if not external_notifications_enabled(db):
        return {"skipped": "成员提醒总开关已关闭，未发送且不会补发"}
    config = get_settings()
    if not config.smtp_host or not config.smtp_from:
        raise NotConfigured("SMTP 尚未配置")
    from app.models import Meeting, MeetingVersion

    p = event.payload
    meeting = db.get(Meeting, p["meeting_id"])
    if meeting.published_version != p["version"]:
        return {"skipped": "纪要已有更新版本，请发布人重新确认收件人"}
    version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == p["version"]
        )
    )
    recipients = [db.get(User, i) for i in set(p["recipient_ids"] + p["cc_ids"])]
    if any(not u or not u.active or not u.email for u in recipients):
        raise NotConfigured("收件人邮箱缺失或账号已停用")
    from app.services.access import meeting_access

    for user in recipients:
        meeting_access(db, user, meeting)
    message = EmailMessage()
    message["Subject"] = version.snapshot["title"] + f" · V{p['version']}"
    message["From"] = config.smtp_from
    message["To"] = ", ".join(db.get(User, i).email for i in p["recipient_ids"])
    message["Cc"] = ", ".join(db.get(User, i).email for i in p["cc_ids"])
    message["Message-ID"] = f"<{event.id}@meeting-actions.local>"
    message["Date"] = formatdate(localtime=False)
    message.set_content(
        "会议纪要及任务单见附件。\n" + config.public_url.rstrip("/") + "/meetings/" + meeting.id
    )
    for attachment in generate_exports(db, meeting.id, p["version"]):
        if attachment.kind not in {"minutes_pdf", "tasks_xlsx"}:
            continue
        major, minor = attachment.mime_type.split("/", 1)
        message.add_attachment(
            (config.storage_dir / attachment.storage_key).read_bytes(),
            maintype=major,
            subtype=minor,
            filename=attachment.filename,
        )
    with smtp_session() as smtp:
        try:
            refused = smtp.send_message(message, to_addrs=[u.email for u in recipients])
        except smtplib.SMTPRecipientsRefused as exc:
            raise ProviderRejected("所有邮件收件人被 SMTP 拒绝") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryUncertain("邮件发送结果不明确，请按 Message-ID 核实") from exc
        if refused:
            raise DeliveryUncertain("部分邮件收件人被拒绝，请核实收件记录")
        return {"message_id": message["Message-ID"]}
