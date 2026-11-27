import * as ww from "@wecom/jssdk";
import { api } from "./client";

export const insideWeCom = () => /wxwork/i.test(navigator.userAgent);
let registered = "";

async function register() {
  if (!insideWeCom())
    throw new Error("请在企业微信中打开系统，再创建或查看原生待办");
  const page = window.location.href.split("#")[0];
  if (registered === page) return;
  const identity = await api("/wecom/sdk");
  ww.register({
    ...identity,
    jsApiList: ["createTodo", "viewTodo"],
    getConfigSignature: (url: string) =>
      api("/wecom/sdk/signature", "POST", { url, kind: "corp" }),
    getAgentConfigSignature: (url: string) =>
      api("/wecom/sdk/signature", "POST", { url, kind: "agent" }),
    onConfigFail: () => {
      registered = "";
    },
    onAgentConfigFail: () => {
      registered = "";
    },
  });
  registered = page;
}

type NativeTodoPayload = {
  content: string;
  attendees: string[];
  endTime?: number;
};

export async function createWeComTodo(payload: NativeTodoPayload) {
  await register();
  const params = {
    ...payload,
    ...(payload.endTime == null ? {} : { end_time: payload.endTime }),
  };
  const result = await ww.createTodo(params);
  const raw = result as typeof result & { todo_id?: unknown };
  const ids = [raw.todoId, raw.todo_id].filter(
    (value): value is string => typeof value === "string" && !!value.trim(),
  );
  if (new Set(ids).size !== 1 || ids[0].length > 200)
    throw new Error(
      "企微创建结果没有明确的待办编号，请先核实已创建的待办，避免重复",
    );
  return { ...result, todoId: ids[0] };
}

export async function ensureWeComTodo() {
  await register();
  const result = await ww.checkJsApi({ jsApiList: ["createTodo"] });
  if (!result.checkResult.createTodo)
    throw new Error(
      "此客户端暂不支持原生待办创建，请将企业微信更新至5.0.9或以上版本",
    );
}

export async function viewWeComTodo(todoId: string) {
  await register();
  return ww.viewTodo({ todoId });
}

export function nativeError(error: any) {
  const value =
    error?.errMsg || error?.err_msg || error?.message || String(error);
  if (/cancel/.test(value)) return "";
  if (/no permission|invalid signature/.test(value))
    return "企微应用权限或签名校验未通过，请检查应用全员可见、可信域名和当前登录账号";
  if (/not exist|not support/.test(value))
    return "请将企业微信更新至5.0.9或以上版本";
  if (/no session|expired/.test(value))
    return "企微登录已过期，请重新从工作台打开系统";
  return value;
}
