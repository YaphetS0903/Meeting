"""Chinese error messages shared by API responses and operation records."""
import re

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

STATUS_MESSAGES = {
    400: "请求内容有误，请检查后重试",
    401: "登录已失效，请重新登录",
    403: "没有权限执行此操作，请联系管理员",
    404: "找不到该功能或记录，请刷新后重试",
    405: "当前功能不支持此操作",
    409: "资料已变更，请刷新核对后重试",
    413: "文件或提交内容过大，请缩小后重试",
    415: "文件格式不受支持，请检查后重试",
    422: "填写内容有误，请检查后重新提交",
    429: "操作过于频繁，请稍后重试",
    502: "相关服务暂时不可用，请稍后重试",
    503: "系统暂时不可用，请稍后重试",
    504: "服务器响应超时，请稍后重试",
}
SERVER_ERROR = "系统处理失败，请稍后重试。如仍有问题，请联系管理员"
FIELDS = {
    "title": "会议名称", "meeting_date": "会议日期", "meeting_time": "会议时间",
    "location": "会议地点", "host_id": "主持人", "recorder_id": "编制人",
    "department_id": "所属部门", "series_id": "会议系列", "kind": "会议类型",
    "attendee_ids": "参会人员", "online_attendee_ids": "线上参会人员",
    "items": "任务单", "content": "工作内容", "due_date": "计划完成时间",
    "owner_ids": "责任人", "verifier_id": "验证人", "status": "状态",
    "username": "账号", "password": "密码", "new_password": "新密码", "email": "邮箱",
    "start": "开始日期", "end": "结束日期", "page": "页码", "page_size": "每页条数",
    "expected_version": "草稿版本", "keyword": "搜索关键词", "file": "文件",
}


def chinese_message(value, fallback):
    if not isinstance(value, str):
        return fallback
    message = value.strip()
    if (not re.search(r"[\u3400-\u9fff]", message) or len(message) > 360
            or re.search(r"[\r\n]|Traceback|https?://|\[SQL:|(?:password|token|secret|api_key)\s*[=:]", message, re.I)):
        return fallback
    return message


def validation_message(errors, path):
    messages = []
    for error in errors[:8]:
        loc = error.get("loc", ())
        kind = error.get("type", "")
        field = next((FIELDS[key] for key in reversed(loc) if key in FIELDS), "填写内容")
        if loc and loc[-1] == "title" and path.startswith("/api/meetings"):
            if kind in {"missing", "string_too_short"}:
                message = "请填写会议名称后再保存草稿"
            else:
                message = "请检查会议名称，最多可填写 500 个字符"
        elif kind == "json_invalid":
            message = "提交的数据格式无效，请刷新页面后重试"
        elif kind in {"missing", "string_too_short", "too_short"}:
            message = f"请填写或补全{field}"
        elif kind in {"string_too_long", "too_long"}:
            message = f"{field}超出长度限制，请缩短后重试"
        elif "date" in kind:
            message = f"请检查{field}的日期格式"
        else:
            message = f"请检查{field}的格式或取值"
        if "items" in loc:
            index = next((key for key in loc[loc.index("items") + 1:] if isinstance(key, int)), None)
            if index is not None:
                message = f"第 {index + 1} 条任务：{message}"
        if message not in messages:
            messages.append(message)
    return "；".join(messages[:3]) or STATUS_MESSAGES[422]


def error_message(error, path):
    if isinstance(error, RequestValidationError):
        return validation_message(error.errors(), path)
    status = getattr(error, "status_code", 500)
    detail = getattr(error, "detail", None)
    fallback = STATUS_MESSAGES.get(status, SERVER_ERROR)
    return chinese_message(detail.get("message") if isinstance(detail, dict) else detail, fallback)


async def validation_response(request: Request, error: RequestValidationError):
    return JSONResponse({"detail": error_message(error, request.url.path)}, status_code=422)


async def http_error_response(request: Request, error: HTTPException):
    if error.status_code in (204, 304) or error.status_code < 200:
        from starlette.responses import Response
        return Response(status_code=error.status_code, headers=error.headers)
    message = error_message(error, request.url.path)
    detail = {**error.detail, "message": message} if isinstance(error.detail, dict) else message
    return JSONResponse({"detail": detail}, status_code=error.status_code, headers=error.headers)


async def unexpected_error_response(request: Request, error: Exception):
    return JSONResponse({"detail": SERVER_ERROR}, status_code=500)
