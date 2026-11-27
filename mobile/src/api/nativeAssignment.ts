import { api, state } from "./client";
import {
  createWeComTodo,
  ensureWeComTodo,
  insideWeCom,
  nativeError,
} from "./wecom";

export type NativeResult = {
  state: "created" | "cancelled" | "unavailable" | "pending" | "linked";
  message: string;
};

export const nativeBindingKey = (taskId: string) =>
  `native-todo-bind:${state.me?.id}:${taskId}`;

export function savedNativeBinding(taskId: string, attemptId?: string): any {
  const key = nativeBindingKey(taskId);
  for (const kind of ["sessionStorage", "localStorage"] as const) {
    try {
      const cached = JSON.parse(window[kind].getItem(key) || "null");
      if (
        typeof cached?.attempt_id === "string" &&
        typeof cached?.todo_id === "string" &&
        (!attemptId || cached.attempt_id === attemptId)
      )
        return cached;
    } catch {
      /* ignore */
    }
  }
  return null;
}

export function clearSavedNativeBinding(taskId: string) {
  for (const kind of ["sessionStorage", "localStorage"] as const) {
    try {
      window[kind].removeItem(nativeBindingKey(taskId));
    } catch {
      /* ignore */
    }
  }
}

export async function bindAssignedTodo(
  taskId: string,
  attemptId: string,
  todoId: string,
) {
  const key = nativeBindingKey(taskId);
  for (const kind of ["sessionStorage", "localStorage"] as const) {
    try {
      window[kind].setItem(
        key,
        JSON.stringify({ attempt_id: attemptId, todo_id: todoId }),
      );
    } catch {
      /* ignore */
    }
  }
  const payload = { attempt_id: attemptId, todo_id: todoId };
  await api(`/tasks/${taskId}/native-todo/record`, "POST", payload);
  const result = await api(
    `/tasks/${taskId}/native-todo/bind`,
    "POST",
    payload,
  );
  clearSavedNativeBinding(taskId);
  return result;
}

export async function confirmAssignedTodo(task: {
  id: string;
  version: number;
}): Promise<NativeResult> {
  if (!insideWeCom())
    return {
      state: "unavailable",
      message: "任务已派发。在企业微信中打开系统，可确认创建原生待办。",
    };
  let attemptId = "";
  let created = false;
  try {
    const info = await api(`/tasks/${task.id}/native-todo`);
    if (info.link)
      return { state: "linked", message: "此任务已有原生待办，未重复创建。" };
    if (info.pending_attempt) {
      const cached = savedNativeBinding(task.id, info.pending_attempt.id);
      const todoId =
        info.pending_attempt.todo_id ||
        (cached?.attempt_id === info.pending_attempt.id ? cached.todo_id : "");
      if (todoId) {
        attemptId = info.pending_attempt.id;
        created = true;
        await bindAssignedTodo(task.id, info.pending_attempt.id, todoId);
        return {
          state: "created",
          message: "原生待办已恢复关联，责任人可在企业微信待办中查看。",
        };
      }
      return {
        state: "pending",
        message: "上次企微确认结果尚未核实，请先恢复关联避免重复创建。",
      };
    }
    if (!info.enabled || !info.can_create)
      return {
        state: "unavailable",
        message: info.reason || "任务已派发；当前无法确认原生待办。",
      };
    await ensureWeComTodo();
    const prepared = await api(
      `/tasks/${task.id}/native-todo/prepare`,
      "POST",
      { expected_version: task.version },
    );
    attemptId = prepared.attempt_id;
    const result = await createWeComTodo(prepared.payload);
    created = true;
    await bindAssignedTodo(task.id, attemptId, result.todoId);
    return {
      state: "created",
      message: "原生待办已派发给责任人，可在企业微信待办中查看。",
    };
  } catch (error: any) {
    const raw = String(
      error?.errMsg || error?.err_msg || error?.message || error || "",
    ).toLowerCase();
    const cancelled = !created && /cancel/.test(raw);
    const notCreated =
      !created &&
      /no permission|permission denied|no session|expired|function not exist|not support|invalid signature/.test(
        raw,
      );
    if (attemptId && (cancelled || notCreated)) {
      try {
        await api(`/tasks/${task.id}/native-todo/reset`, "POST", {
          attempt_id: attemptId,
          reason: cancelled ? "cancelled" : "failed_before_create",
        });
      } catch {
        /* ignore */
      }
    }
    if (cancelled)
      return {
        state: "cancelled",
        message: "已取消原生待办确认。系统任务已派发，应用消息仍会自动发送。",
      };
    return {
      state: attemptId && !notCreated ? "pending" : "unavailable",
      message: `系统任务已派发。${nativeError(error) || "企微确认结果待核实"}；应用消息独立发送。`,
    };
  }
}

export function assignmentDeliveryMessage(state: string | null | undefined) {
  switch (state) {
    case "skipped":
      return "应用消息未发送，责任人可登录系统查看任务。";
    case "pending":
      return "应用消息排队发送中，责任人可在系统查看任务。";
    case "sending":
      return "应用消息正在发送，责任人可在系统查看任务。";
    case "sent":
      return "应用消息已发送，责任人可在企业微信或系统中查看任务。";
    case "failed":
    case "blocked":
      return "应用消息发送失败，责任人仍可登录系统查看任务。";
    default:
      return "应用消息送达状态待核实，责任人可登录系统查看任务。";
  }
}

export function normalizeMailbox(raw: string): string | null {
  if (/[\u0000-\u001f\u007f]/.test(raw)) return null;
  let value = raw.normalize("NFKC").trim();
  const named = value.match(/^[^<>]*<([^<>]+)>$/u);
  if (named) value = named[1].trim();
  value = value.replace(/^mailto:/i, "");
  if (
    value.length > 254 ||
    !/^[^\s@<>,;:"\\]+@[^\s@<>,;:"\\]+\.[^\s@<>,;:"\\]+$/u.test(value)
  )
    return null;
  const [local, domain] = value.split("@");
  if (
    domain
      .split(".")
      .some((part) => !part || part.startsWith("-") || part.endsWith("-"))
  )
    return null;
  return `${local}@${domain.toLowerCase()}`;
}

export function attendeeRecipientIds(draft: {
  host_id?: string;
  attendee_ids?: string[];
}): string[] {
  return [...new Set([draft.host_id, ...(draft.attendee_ids || [])])].filter(
    (id): id is string => Boolean(id),
  );
}

export function syncAttendeeRecipients(
  draft: {
    host_id?: string;
    attendee_ids?: string[];
    recipient_ids?: string[];
    recipient_customized?: boolean;
    cc_ids?: string[];
  },
  previousAttendees: string[],
  reset = false,
) {
  const attendees = attendeeRecipientIds(draft);
  const inherited = new Set([...previousAttendees, ...attendees]);
  const extras = reset
    ? []
    : (draft.recipient_ids || []).filter((id) => !inherited.has(id));
  const chosen = new Set(draft.recipient_ids || []);
  const previous = new Set(previousAttendees);
  const defaults =
    reset || !draft.recipient_customized
      ? attendees
      : attendees.filter((id) => chosen.has(id) || !previous.has(id));
  const recipients = [...new Set([...defaults, ...extras])];
  return {
    attendees,
    recipients,
    cc: [...new Set(draft.cc_ids || [])].filter(
      (id) => !recipients.includes(id),
    ),
  };
}
