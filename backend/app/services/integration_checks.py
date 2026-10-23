"""Administrator-initiated connection checks; no messages, emails or directory writes."""

import smtplib
import ssl

from app.core.config import get_settings
from app.models import now
from app.services.integrations import NotConfigured, ProviderRejected, WeCom, smtp_session


def failure_message(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "TLS 证书验证失败，请检查证书链、主机名及企业信任根证书"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "SMTP 身份验证失败，请核对账号与凭据"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "连接失败或超时，请检查地址、端口和网络访问规则"
    if isinstance(exc, NotConfigured):
        return "配置不完整，请检查企业环境参数"
    if isinstance(exc, ProviderRejected):
        return "企业接口拒绝请求，请检查应用标识、凭据、通讯录权限及可信 IP"
    return "检查失败，请核对企业配置及服务可用性"


def check_integration(target):
    settings = get_settings()
    result = {"target": target, "checked_at": now().isoformat(), "state": "ready", "checks": []}
    configured = (
        bool(settings.wecom_corp_id and settings.wecom_agent_id and settings.wecom_secret)
        if target == "wecom"
        else bool(settings.smtp_host and settings.smtp_from)
    )
    if not configured:
        result.update(
            state="not_configured",
            checks=[
                {
                    "label": "必要参数",
                    "state": "not_configured",
                    "message": "企业微信 CorpID、AgentID、Secret 尚未配置完整"
                    if target == "wecom"
                    else "SMTP 主机或发件地址尚未配置",
                }
            ],
        )
        return result
    label = "应用访问权限" if target == "wecom" else "SMTP 连接与身份验证"
    try:
        if target == "wecom":
            provider = WeCom()
            app = provider.get("agent/get", agentid=settings.wecom_agent_id)
            if str(app.get("agentid")) != str(settings.wecom_agent_id):
                raise ProviderRejected("应用标识不一致")
            result["checks"].append({"label": label, "state": "ready", "message": "已确认配置应用的访问权限"})
            label = "通讯录读取权限"
            departments = provider.get("department/list")["department"]
            if not isinstance(departments, list):
                raise ValueError("Invalid department response")
            if not departments:
                direct_users = len((app.get("allow_userinfos") or {}).get("user") or [])
                result["state"] = "limited" if direct_users else "failed"
                result["checks"].append(
                    {
                        "label": label,
                        "state": "limited" if direct_users else "failed",
                        "message": (
                            f"接口可用，当前直接授权 {direct_users} 名成员且未授权部门；可先同步这些成员，开放部门后再次同步"
                            if direct_users
                            else "接口访问成功，但应用可见范围未包含成员或部门，请在企业微信后台设置可见范围后重试"
                        ),
                    }
                )
                return result
            result["checks"].append(
                {
                    "label": label,
                    "state": "ready",
                    "message": f"接口返回 {len(departments)} 个可见部门，未同步到系统",
                }
            )
        else:
            with smtp_session(timeout=10) as smtp:
                code, _ = smtp.noop()
                if not 200 <= code < 300:
                    raise smtplib.SMTPResponseException(code, b"NOOP rejected")
            result["checks"].append(
                {
                    "label": label,
                    "state": "ready",
                    "message": "连接和已配置的 TLS／身份验证检查通过，未发送邮件",
                }
            )
    except Exception as exc:
        result["state"] = "failed"
        result["checks"].append({"label": label, "state": "failed", "message": failure_message(exc)})
    return result
