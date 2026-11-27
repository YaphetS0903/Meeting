const fallbackMessage = "页面处理失败，请重试；如仍有问题，请联系管理员";
const unsafeText =
  /[\r\n]|Traceback|https?:\/\/|\[SQL:|(?:password|token|secret|api_key)\s*[=:]/i;

export function chineseError(
  value: unknown,
  fallback = fallbackMessage,
): string {
  const message = value instanceof Error ? value.message : value;
  if (typeof message !== "string") return fallback;
  const text = message.trim();
  return text.length <= 520 &&
    /[\u3400-\u9fff]/.test(text) &&
    !unsafeText.test(text)
    ? text
    : fallback;
}

export function responseMessage(
  detail: unknown,
  status: number,
  fallback: string,
) {
  if (Array.isArray(detail)) {
    if (
      detail.some(
        (issue) =>
          Array.isArray(issue?.loc) &&
          issue.loc.at(-1) === "title" &&
          ["missing", "string_too_short"].includes(issue.type),
      )
    )
      return "请填写会议名称后再保存草稿";
    return "填写内容有误，请检查后重新提交";
  }
  const message =
    detail && typeof detail === "object" && "message" in detail
      ? (detail as any).message
      : detail;
  const defaults: Record<number, string> = {
    401: "登录已过期，请重新登录",
    403: "没有权限执行此操作，请联系管理员",
    404: "找不到该功能或记录，请刷新后重试",
    422: "填写内容有误，请检查后重新提交",
    429: "操作过于频繁，请稍后重试",
  };
  return chineseError(message, defaults[status] || `${fallback}，请稍后重试`);
}

const lastReports = new Map<string, number>();
const pathPattern = /^\/[a-zA-Z0-9/_-]{0,180}$/;

export function reportClientError(
  code: string,
  path = "",
  method = "",
  status?: number,
) {
  const token = sessionStorage.getItem("token");
  if (!token) return;
  const page = window.location.pathname;
  if (!pathPattern.test(page)) return;
  const pathname = path.split("?", 1)[0];
  if (pathname === "/client-errors") return;
  const safePath = pathname && pathPattern.test(pathname) ? pathname : "";
  const key = JSON.stringify([code, page, safePath, method, status]);
  const now = Date.now();
  if (now - (lastReports.get(key) ?? 0) < 30_000) return;
  if (lastReports.size >= 100) lastReports.clear();
  lastReports.set(key, now);
  const payload = JSON.stringify({
    code,
    page,
    path: safePath,
    method,
    status,
  });
  void fetch("/api/client-errors", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: payload,
    keepalive: true,
  }).catch(() => {});
}
