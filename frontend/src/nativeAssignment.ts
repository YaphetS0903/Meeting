import { api, state } from "./api";
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
type AssignedTask = { id: string; version: number };

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
      /* One unavailable browser store must not hide the other recovery copy. */
    }
  }
  return null;
}

export function clearSavedNativeBinding(taskId: string) {
  for (const kind of ["sessionStorage", "localStorage"] as const) {
    try {
      window[kind].removeItem(nativeBindingKey(taskId));
    } catch {
      /* Old hints are also checked against the server's unique attempt ID. */
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
      /* Restricted browser storage must not prevent saving the callback on the server. */
    }
  }
  const payload = {
    attempt_id: attemptId,
    todo_id: todoId,
  };
  await api(`/tasks/${taskId}/native-todo/record`, "POST", payload);
  const result = await api(
    `/tasks/${taskId}/native-todo/bind`,
    "POST",
    payload,
  );
  clearSavedNativeBinding(taskId);
  return result;
}

export async function confirmAssignedTodo(
  task: AssignedTask,
): Promise<NativeResult> {
  if (!insideWeCom())
    return {
      state: "unavailable",
      message: "任务已派发。在企业微信中打开任务，可查看原生待办确认入口。",
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
        message:
          "上次企微确认结果尚未核实，已保留创建记录，请先恢复关联，避免重复创建。",
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
      message: "原生待办已派发给责任人，责任人可直接在企业微信待办中查看。",
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
        /* Leave the attempt pending when cancellation could not be saved. */
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
