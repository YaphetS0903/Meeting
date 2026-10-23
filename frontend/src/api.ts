import { reactive } from "vue";
import { ElMessage } from "element-plus";
export const state = reactive({
  me: null as any,
  directory: { users: [], departments: [], series: [] } as any,
});
export const statuses = ["已完成", "未完成", "未到期", "不统计", "关闭"];
export function importIssueMessage(issue: { message?: string }) {
  const message = issue.message || "需要检查原文件";
  if (/BOF not workbook\/worksheet.*strm=0x0*6\b/i.test(message))
    return "原文件中的这个页是内部模块，不是普通会议工作表，系统无法读取它。其他已识别的工作表不受影响；请核对原件，如这页没有会议内容，可在历史导入页记录检查结果。";
  return message;
}
export const kinds = { department: "部门例会", special: "专项会议" };
export function clientId() {
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function")
    globalThis.crypto.getRandomValues(bytes);
  else
    for (let index = 0; index < bytes.length; index += 1)
      bytes[index] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = [...bytes].map((item) => item.toString(16).padStart(2, "0"));
  return `${value.slice(0, 4).join("")}-${value.slice(4, 6).join("")}-${value.slice(6, 8).join("")}-${value.slice(8, 10).join("")}-${value.slice(10).join("")}`;
}
export function query(data: Record<string, any>) {
  return new URLSearchParams(
    Object.entries(data)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}
export async function api(
  path: string,
  method = "GET",
  body?: any,
): Promise<any> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${sessionStorage.getItem("token") || ""}`,
  };
  if (body && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const res = await fetch("/api" + path, {
    method,
    headers,
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const value = await res.json().catch(() => ({ detail: "服务暂时不可用" }));
    if (res.status === 401) {
      sessionStorage.removeItem("token");
      state.me = null;
    }
    const detail = value.detail;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return res.status === 204 ? null : res.json();
}
export async function run(fn: () => Promise<any>) {
  try {
    return await fn();
  } catch (e: any) {
    if (e === "cancel" || e === "close") return null;
    ElMessage.error({ message: e.message, duration: 6500 });
    return null;
  }
}
export async function loadSession() {
  const [me, directory] = await Promise.all([api("/me"), api("/directory")]);
  state.directory = directory;
  state.me = me;
}
export async function download(path: string, name: string) {
  const res = await fetch("/api" + path, {
    headers: { Authorization: `Bearer ${sessionStorage.getItem("token")}` },
  });
  if (!res.ok) throw new Error((await res.json()).detail || "下载失败");
  const url = URL.createObjectURL(await res.blob());
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
  const res = await fetch(`/api/meetings/${meetingId}/preview`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionStorage.getItem("token")}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ expected_version: version, format }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "预览生成失败");
  return res.blob();
}
export const person = (id: string) =>
  state.directory.users.find((x) => x.id === id)?.name || "未选择";
export function departmentPath(id: string | null | undefined) {
  if (!id) return "专项会议（无所属部门）";
  const departments = new Map(
    state.directory.departments.map((item: any) => [item.id, item]),
  );
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
export const rate = (value: number | null) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
export function localDate(value = new Date()) {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(
    value,
  );
}
