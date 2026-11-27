import { reactive } from "vue";
import { showFailToast } from "vant";
import {
  chineseError,
  reportClientError,
  responseMessage,
} from "./errorMessages";

export const state = reactive({
  me: null as any,
  directory: { users: [], departments: [], series: [] } as any,
  pendingVerifications: 0,
});

export const statuses = ["已完成", "未完成", "未到期", "不统计", "关闭"];
export const kinds: Record<string, string> = {
  department: "部门例会",
  special: "专项会议",
};

export function clientId() {
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function")
    globalThis.crypto.getRandomValues(bytes);
  else
    for (let i = 0; i < bytes.length; i += 1)
      bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((item) => item.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export function query(data: Record<string, any>) {
  return new URLSearchParams(
    Object.entries(data)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}

export class ApiResponseError extends Error {
  constructor(
    message: string,
    public outcomeUnknown = false,
  ) {
    super(message);
    this.name = "ApiResponseError";
  }
}

function expireSession(status: number) {
  if (status === 401) {
    sessionStorage.removeItem("token");
    state.me = null;
  }
}

async function responseError(
  res: Response,
  fallback: string,
  path: string,
  method: string,
) {
  let detail: unknown;
  try {
    const text = await res.text();
    detail = text.trim() ? JSON.parse(text).detail : null;
  } catch {
    /* ignore non-JSON gateway bodies */
  }
  if (!detail && res.status >= 500)
    reportClientError("gateway", path, method, res.status);
  expireSession(res.status);
  return new ApiResponseError(responseMessage(detail, res.status, fallback));
}

async function request(
  path: string,
  init: RequestInit = {},
  outcomeUnknown = false,
) {
  try {
    const headers = new Headers(init.headers);
    headers.set("X-Client-Page", window.location.pathname);
    return await fetch("/api" + path, { ...init, headers });
  } catch {
    reportClientError(
      outcomeUnknown ? "network_write" : "network",
      path,
      init.method || "GET",
    );
    throw new ApiResponseError(
      outcomeUnknown
        ? "网络连接中断，操作结果尚未确认，请先核对结果再重试"
        : "网络连接中断，请稍后重试",
      outcomeUnknown,
    );
  }
}

export async function api(
  path: string,
  method = "GET",
  body?: any,
  options: { background?: boolean } = {},
): Promise<any> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${sessionStorage.getItem("token") || ""}`,
  };
  if (body && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const mutating = !["GET", "HEAD"].includes(method.toUpperCase());
  if (options.background === true && method.toUpperCase() === "GET")
    headers["X-Operation-Context"] = "background";
  const res = await request(
    path,
    {
      method,
      headers,
      cache: mutating ? undefined : "no-store",
      body:
        body instanceof FormData
          ? body
          : body
            ? JSON.stringify(body)
            : undefined,
    },
    mutating,
  );
  if (!res.ok) {
    const error = await responseError(res, "服务器处理失败", path, method);
    error.outcomeUnknown = mutating && res.status >= 500;
    throw error;
  }
  if (res.status === 204) return null;
  try {
    const text = await res.text();
    if (!text.trim()) throw new Error("empty response");
    return JSON.parse(text);
  } catch {
    reportClientError(mutating ? "response_write" : "response", path, method);
    throw new ApiResponseError(
      mutating
        ? "服务器未返回完整数据，操作结果尚未确认，请先核对结果再重试"
        : "服务器返回的数据不完整，请稍后重新加载",
      mutating,
    );
  }
}

export async function run<T = any>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (e: any) {
    if (e === "cancel" || e === "close") return null;
    if (!(e instanceof ApiResponseError)) reportClientError("browser");
    showFailToast({ message: chineseError(e), duration: 3500 });
    return null;
  }
}

let directoryLoadedAt = 0;
let directoryRequest: Promise<any> | null = null;

export async function refreshDirectory(force = false) {
  if (!force && Date.now() - directoryLoadedAt < 60000) return state.directory;
  if (directoryRequest) return directoryRequest;
  const token = sessionStorage.getItem("token");
  directoryRequest = api("/directory", "GET", undefined, { background: true })
    .then((directory) => {
      if (token === sessionStorage.getItem("token")) {
        state.directory = directory;
        directoryLoadedAt = Date.now();
      }
      return directory;
    })
    .finally(() => {
      directoryRequest = null;
    });
  return directoryRequest;
}

export async function refreshPendingVerifications() {
  try {
    const rows = await api("/verifications", "GET", undefined, {
      background: true,
    });
    state.pendingVerifications = Array.isArray(rows)
      ? rows.filter((x: any) => x.state === "pending").length
      : 0;
  } catch {
    /* Badge count is auxiliary */
  }
}

export async function loadSession() {
  const [me, directory] = await Promise.all([api("/me"), api("/directory")]);
  state.directory = directory;
  directoryLoadedAt = Date.now();
  state.me = me;
  void refreshPendingVerifications();
}

export async function fetchAuthBlob(path: string): Promise<Blob> {
  const res = await request(path, {
    headers: { Authorization: `Bearer ${sessionStorage.getItem("token") || ""}` },
  });
  if (!res.ok) throw await responseError(res, "文件读取失败", path, "GET");
  return res.blob();
}

export async function download(path: string, name: string) {
  const blob = await fetchAuthBlob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export async function previewFile(
  meetingId: string,
  version: number,
  format = "pdf",
) {
  const path = `/meetings/${meetingId}/preview`;
  const res = await request(path, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionStorage.getItem("token") || ""}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ expected_version: version, format }),
  });
  if (!res.ok) throw await responseError(res, "预览生成失败", path, "POST");
  const blob = await res.blob();
  if (!blob.size) {
    reportClientError("response", path, "POST");
    throw new ApiResponseError("预览服务返回空文件，请重新生成预览");
  }
  return blob;
}

export const person = (id: string) =>
  state.directory.users.find((x: any) => x.id === id)?.name || "未选择";

export function departmentPath(
  id: string | null | undefined,
  options: any[] = state.directory.departments,
) {
  if (!id) return "专项会议（无所属部门）";
  const departments = new Map(options.map((item: any) => [item.id, item]));
  const names: string[] = [];
  const visited = new Set<string>();
  let current: any = departments.get(id);
  while (current && !visited.has(current.id) && names.length < 20) {
    visited.add(current.id);
    names.unshift(current.name);
    current = current.parent_id ? departments.get(current.parent_id) : null;
  }
  return names.join(" / ") || "未找到部门";
}

export function departmentLeaf(
  id: string | null | undefined,
  options: any[] = state.directory.departments,
) {
  if (!id) return "专项会议";
  const found = options.find((d: any) => d.id === id);
  return found?.name || departmentPath(id, options).split(" / ").at(-1) || "—";
}

export const rate = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

export const isLowReportRate = (value: number | null | undefined) =>
  value != null && value < 0.8;

export function localDate(value = new Date()) {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(
    value,
  );
}

export function addDays(days: number) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return localDate(d);
}

export function thisFriday() {
  const d = new Date();
  const day = d.getDay();
  const diff = day <= 5 ? 5 - day : 5 + (7 - day);
  d.setDate(d.getDate() + diff);
  return localDate(d);
}

export function hasBusinessPermission(
  module: string,
  action: string,
  departmentId?: string | null,
  related = false,
) {
  if (state.me?.is_admin) return true;
  const scope = state.me?.business_permissions?.[module]?.[action];
  if (!scope) return !state.me?.business_permissions;
  return !!(
    scope.all ||
    (related && scope.self) ||
    (departmentId
      ? scope.department_ids.includes(departmentId)
      : scope.department_ids.length || scope.self)
  );
}
