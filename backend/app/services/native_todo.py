"""Official WeCom CLI adapter for native todos.

Native todo is a projection of system state. Finishing it never changes a system task;
the system closes a task first, after verifier approval, and then finishes the projection.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import locked_get
from app.models import Meeting, NativeTodoLink, Task, User, now
from app.services.business import audit, owner_ids
from app.services.integrations import DeliveryUncertain, NotConfigured, ProviderRejected

_authorization_process: subprocess.Popen | None = None
_authorization_qr = Path("/tmp/meeting-actions-native-todo-auth.png")


class AuthorizationRequired(NotConfigured):
    def __init__(self, scope: str, help_message: str):
        super().__init__(f"企业微信{scope}使用权限已过期，请在系统管理中重新授权")
        self.authorization_url = ""
        match = re.search(r"\]\((https://[^\s)]+)\)", help_message)
        if match:
            parsed = urlsplit(match[1])
            if parsed.netloc == "work.weixin.qq.com" and parsed.path == "/ai/aiHelper/authorizationList":
                self.authorization_url = match[1]


def _decode_json(output: str) -> dict:
    output = output.strip()
    try:
        value = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DeliveryUncertain("原生待办返回内容无法确认，请核实企业微信中的实际状态") from exc
    if not isinstance(value, dict):
        raise DeliveryUncertain("原生待办返回格式异常，请核实企业微信中的实际状态")
    return value


def _run(*arguments: str, payload: dict | None = None, timeout: int = 45) -> dict:
    path = Path(get_settings().native_todo_cli_path)
    if not path.is_file():
        raise NotConfigured("原生待办 CLI 未安装")
    command = [str(path), *arguments]
    if payload is not None:
        command.extend(["--json", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))])
    environment = {**os.environ, "NO_COLOR": "1"}
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeliveryUncertain("原生待办调用超时，需核实企业微信中的实际状态") from exc
    except OSError as exc:
        raise NotConfigured("原生待办 CLI 无法启动") from exc
    output = (result.stdout or result.stderr).strip()
    value = _decode_json(output) if output.startswith("{") else {}
    error = value.get("error", {}) if isinstance(value.get("error"), dict) else {}
    message = str(error.get("message") or value.get("errmsg") or "")
    code = error.get("code") or value.get("errcode")
    if str(code) == "850003":
        scope = "通讯录" if arguments[0] == "contact" else "待办"
        raise AuthorizationRequired(scope, str(value.get("help_message") or ""))
    if result.returncode or error or code not in {None, 0, "0"}:
        if "需要授权" in message or "AuthError" in message or str(code) == "893201":
            raise NotConfigured("原生待办尚未完成扫码授权")
        message = re.sub(r"https?://\S+", "[链接已隐藏]", message)
        raise ProviderRejected("企业微信拒绝原生待办操作" + (f"（{message[:160]}）" if message else ""))
    if not value:
        raise DeliveryUncertain("原生待办未返回可确认结果，请核实企业微信中的实际状态")
    return value


def status() -> dict:
    path = Path(get_settings().native_todo_cli_path)
    if not path.is_file():
        return {"cli_available": False, "authorized": False}
    try:
        result = subprocess.run(
            [str(path), "auth", "show"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"cli_available": False, "authorized": False}
    output = (result.stdout + result.stderr).lower()
    value = {
        "cli_available": result.returncode == 0,
        "credentials_saved": result.returncode == 0 and "status: authorized" in output,
        "authorized": False,
        "permissions": [],
    }
    if value["credentials_saved"]:
        for scope, arguments, payload in [
            ("通讯录", ("contact", "users", "search"), {
                "keywords": ["__meeting_actions_permission_check__"], "search_mode": "list",
            }),
            ("待办", ("todo", "list"), {"limit": 1}),
        ]:
            permission = {"scope": scope, "authorized": False, "authorization_url": "", "error": ""}
            try:
                _run(*arguments, payload=payload, timeout=12)
                permission["authorized"] = True
            except AuthorizationRequired as exc:
                permission.update(error=str(exc), authorization_url=exc.authorization_url)
            except (NotConfigured, ProviderRejected, DeliveryUncertain) as exc:
                permission["error"] = str(exc)
            value["permissions"].append(permission)
        value["authorized"] = all(item["authorized"] for item in value["permissions"])
    return value


def start_authorization() -> dict:
    """Start one bounded CLI QR flow; the feature switch remains unchanged."""
    global _authorization_process
    current = status()
    if current["authorized"]:
        return {**current, "state": "authorized"}
    if any(item.get("authorization_url") for item in current.get("permissions", [])):
        return {**current, "state": "permission_required"}
    path = Path(get_settings().native_todo_cli_path)
    if not current["cli_available"]:
        raise NotConfigured("原生待办 CLI 未安装或无法启动")
    if _authorization_process and _authorization_process.poll() is None and _authorization_qr.is_file():
        return {**current, "state": "awaiting_scan"}
    if _authorization_process and _authorization_process.poll() is None:
        _authorization_process.terminate()
        _authorization_process.wait(timeout=5)
    _authorization_qr.unlink(missing_ok=True)
    try:
        _authorization_process = subprocess.Popen(
            [
                str(path),
                "auth",
                "init",
                "--noninteractive",
                "--no-browser",
                "--output-qrcode",
                str(_authorization_qr),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except OSError as exc:
        raise NotConfigured("原生待办 CLI 无法启动") from exc
    for _ in range(50):
        if _authorization_qr.is_file():
            return {**current, "state": "awaiting_scan"}
        if _authorization_process.poll() is not None:
            break
        time.sleep(0.1)
    raise ProviderRejected("未能生成原生待办授权二维码，请查看服务日志后重试")


def authorization_qr_path() -> Path:
    if not _authorization_qr.is_file():
        raise NotConfigured("授权二维码不存在或已过期，请重新生成")
    return _authorization_qr


def search_users(keywords: list[str]) -> list[dict]:
    value = _run("contact", "users", "search", payload={"keywords": keywords, "search_mode": "list"})
    return [
        {
            "userid": item.get("userid", ""),
            "name": item.get("name") or "",
            "email": item.get("email") or "",
            "position": item.get("position", ""),
            "departments": item.get("departments", []),
        }
        for item in value.get("users", [])
        if item.get("userid")
    ]


def match_member(member: User, candidates: list[dict]) -> dict:
    """Require a unique exact match; never guess from fuzzy search order."""
    candidates = list({item["userid"]: item for item in candidates}.values())
    email = (member.email or "").strip().casefold()
    matches = [item for item in candidates if email and item["email"].strip().casefold() == email]
    if not matches:
        matches = [item for item in candidates if item["name"].strip() == member.name.strip()]
        # A conflicting known email needs explicit directory correction.
        if email:
            matches = [item for item in matches if not item["email"].strip()]
    if len(matches) != 1:
        raise NotConfigured(f"{member.name}的企业微信成员无法唯一匹配，请在系统管理中核对姓名及邮箱")
    return matches[0]


def _allowed_owners(db, task: Task) -> list[User]:
    settings = get_settings()
    users = [locked_get(db, User, identifier) for identifier in sorted(owner_ids(db, task))]
    eligible = [
        user for user in users
        if user and settings.native_todo_member_allowed(user.wecom_id, user.active)
    ]
    if not eligible:
        return []
    if settings.native_todo_all_active_users:
        for member in eligible:
            if member.native_todo_userid:
                continue
            candidate = match_member(
                member, search_users([member.name] + ([member.email] if member.email else []))
            )
            duplicate = db.scalar(select(User.id).where(
                User.native_todo_userid == candidate["userid"], User.id != member.id,
            ))
            if duplicate:
                raise NotConfigured(f"{member.name}的企业微信成员已绑定其他账号，请管理员核对")
            member.native_todo_userid = candidate["userid"]
            audit(
                db, member, "native_todo.user.auto_bind", member,
                after={"native_todo_userid": member.native_todo_userid},
                reason="待办同步按企业微信通讯录唯一姓名或邮箱匹配",
            )
            db.flush()
    missing = [user.name for user in eligible if not user.native_todo_userid]
    if missing:
        raise NotConfigured("原生待办成员标识尚未绑定：" + "、".join(missing))
    return eligible


def _content(db, task: Task) -> tuple[str, str]:
    meeting = db.get(Meeting, task.source_meeting_id) if task.source_meeting_id else None
    title = f"{task.code} {task.content}"[:4000]
    link = get_settings().public_url.rstrip("/") + "/tasks?id=" + task.id
    description = (
        f"会议来源：{meeting.title if meeting else '直接派发任务'}\n"
        f"截止日期：{task.due_date.isoformat()}\n"
        "完成后请进入会议纪要系统提交已完成申请，由验证人确认关闭。\n" + link
    )[:4000]
    return title, description


def _result(value: dict) -> dict:
    items = value.get("items") or []
    if len(items) != 1 or not items[0].get("success"):
        message = str(items[0].get("errmsg", "")) if items else "未返回处理结果"
        raise ProviderRejected("企业微信拒绝原生待办操作" + (f"（{message[:160]}）" if message else ""))
    return items[0]


def send_native_todo(db, event) -> dict:
    settings = get_settings()
    if not settings.native_todo_enabled:
        return {"skipped": "原生待办开关已关闭"}
    payload = event.payload
    action = payload.get("action")
    if action not in {"create", "update", "finish"}:
        raise ProviderRejected("原生待办事件类型无效")
    task = locked_get(db, Task, payload.get("task_id"))
    if not task:
        return {"skipped": "任务已不存在"}
    source = "meeting" if task.source_meeting_id else "direct"
    if not settings.native_todo_source_enabled(source):
        return {"skipped": "该任务来源的原生待办同步已关闭"}
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    if action in {"create", "update"} and task.version != payload.get("task_version"):
        return {"skipped": "任务已产生新版本"}
    if link and link.provider == "app":
        from app.services.app_native_todo import send_app_native_todo

        return send_app_native_todo(db, task, link, action)
    if settings.native_todo_app_enabled and source == "direct" and not link:
        return {"skipped": "请由派发人在企业微信中确认创建应用待办"}
    if action == "finish":
        if not task.closed_at:
            return {"skipped": "系统任务尚未验证关闭"}
        if not link:
            return {"skipped": "该任务没有原生待办映射"}
        item = _result(
            _run("todo", "finish", payload={"items": [{"todo_id": link.todo_id, "finished_all": True}]})
        )
        link.state = "finished"
        link.last_synced_at = now()
        return {"todo_id": item.get("todo_id") or link.todo_id, "action": "finish"}

    if task.closed_at or task.withdrawn_at:
        return {"skipped": "任务已关闭或撤回"}

    if task.due_date is None:
        return {"skipped": "任务未设置截止日期，暂不创建或更新原生待办"}

    followers = _allowed_owners(db, task)
    if not followers:
        return {"skipped": "任务没有在同步范围内的在用企业微信责任人"}
    follower_ids = sorted({user.native_todo_userid for user in followers})
    title, description = _content(db, task)
    if action == "create" and not link:
        item = _result(
            _run(
                "todo",
                "create",
                payload={
                    "items": [
                        {
                            "title": title,
                            "description": description,
                            "follower_ids": follower_ids,
                            "deadline": {"type": "date", "value": task.due_date.isoformat()},
                        }
                    ]
                },
            )
        )
        todo_id = item.get("todo_id")
        if not todo_id:
            raise DeliveryUncertain("原生待办已返回成功但缺少待办编号，请人工核实")
        link = NativeTodoLink(
            task_id=task.id,
            todo_id=todo_id,
            task_version=task.version,
            follower_ids=follower_ids,
            state="active",
            last_synced_at=now(),
        )
        db.add(link)
        return {"todo_id": todo_id, "action": "create"}
    if not link:
        raise NotConfigured("任务尚未建立原生待办映射，请先重试创建事件")
    item = _result(
        _run(
            "todo",
            "update",
            payload={
                "items": [
                    {
                        "todo_id": link.todo_id,
                        "title": title,
                        "description": description,
                        "followers": [{"userid": identifier} for identifier in follower_ids],
                        "deadline": {"type": "date", "value": task.due_date.isoformat()},
                    }
                ]
            },
        )
    )
    link.task_version = task.version
    link.follower_ids = follower_ids
    link.state = "active"
    link.last_synced_at = now()
    return {"todo_id": item.get("todo_id") or link.todo_id, "action": "update"}
