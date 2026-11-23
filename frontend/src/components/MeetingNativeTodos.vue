<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api";
import { confirmAssignedTodo, type NativeResult } from "../nativeAssignment";
import { insideWeCom } from "../wecom";
import NativeTodoPanel from "./NativeTodoPanel.vue";

type MeetingTask = {
  id: string;
  version: number;
  code: string;
  content: string;
  due_date: string | null;
  owner_names: string[];
  native_todo: any;
};
type Plan = {
  meeting_id: string;
  published_version: number;
  enabled: boolean;
  can_confirm: boolean;
  reason: string;
  tasks: MeetingTask[];
  excluded_tasks: {
    id: string | null;
    content: string;
    owner_names: string[];
    reason: string;
  }[];
};
const props = defineProps<{
  meetingId: string;
  publishedVersion: number;
  autoConfirm?: boolean;
}>();
const emit = defineEmits<{ busyChange: [busy: boolean] }>();
const plan = ref<Plan | null>(null);
const loading = ref(false),
  busy = ref(false),
  error = ref("");
const detailBusy = ref(false);
const activeTask = ref("");
const results = ref<Record<string, NativeResult>>({});
const progress = ref("");
let generation = 0;
let disposed = false;
const readyTasks = computed(() =>
  (plan.value?.tasks || []).filter(
    (task) =>
      !task.native_todo.link &&
      !task.native_todo.pending_attempt &&
      task.native_todo.can_create &&
      !["created", "linked", "pending"].includes(
        results.value[task.id]?.state || "",
      ),
  ),
);
const linkedCount = computed(
  () =>
    (plan.value?.tasks || []).filter(
      (task) =>
        task.native_todo.link ||
        ["created", "linked"].includes(results.value[task.id]?.state || ""),
    ).length,
);
const pendingCount = computed(
  () =>
    (plan.value?.tasks || []).filter(
      (task) =>
        task.native_todo.pending_attempt ||
        results.value[task.id]?.state === "pending",
    ).length,
);

async function load() {
  const current = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const value = await api(`/meetings/${props.meetingId}/native-todos`);
    if (current === generation) plan.value = value;
  } catch (e: any) {
    if (current === generation) {
      plan.value = null;
      error.value = e.message || "无法读取待办状态，请重试";
    }
  } finally {
    if (current === generation) loading.value = false;
  }
}
function stateLabel(task: MeetingTask) {
  if (task.native_todo.link?.state === "finished") return "待办已完成";
  if (
    task.native_todo.link ||
    ["created", "linked"].includes(results.value[task.id]?.state || "")
  )
    return "已关联待办";
  if (
    task.native_todo.pending_attempt ||
    results.value[task.id]?.state === "pending"
  )
    return "创建结果待核实";
  if (results.value[task.id]?.state === "cancelled") return "已取消确认";
  if (!task.native_todo.can_create) return "暂不可派发";
  return "待确认派发";
}
function updateState(task: MeetingTask, value: any) {
  task.native_todo = value;
  if (value.link) delete results.value[task.id];
  else if (
    !value.pending_attempt &&
    results.value[task.id]?.state === "pending"
  )
    delete results.value[task.id];
}
async function dispatch() {
  if (
    busy.value ||
    detailBusy.value ||
    !insideWeCom() ||
    !plan.value?.can_confirm
  )
    return;
  busy.value = true;
  emit("busyChange", true);
  progress.value = "正在核对最新任务及已有待办…";
  try {
    await load();
    if (!plan.value?.can_confirm) return;
    const tasks = [...readyTasks.value];
    for (let index = 0; index < tasks.length; index++) {
      if (disposed) return;
      const task = tasks[index];
      progress.value = `正在确认第 ${index + 1} / ${tasks.length} 项：${task.content}`;
      const result = await confirmAssignedTodo(task);
      if (disposed) return;
      results.value[task.id] = result;
      if (result.state !== "created" && result.state !== "linked") {
        activeTask.value = task.id;
        progress.value = "派发已暂停，已创建的待办保留。请处理当前项后再继续。";
        return;
      }
    }
    progress.value = tasks.length
      ? "本轮确认已完成，请核对下方每项任务的待办状态。"
      : "没有需要新建的待办，请核对已有待办及待处理事项。";
  } catch (e: any) {
    progress.value = `会议已发布，待办派发中断：${e.message || "请刷新状态后继续"}`;
  } finally {
    if (!disposed) await load();
    busy.value = false;
    emit("busyChange", false);
  }
}
watch(
  () => [props.meetingId, props.publishedVersion],
  async () => {
    plan.value = null;
    results.value = {};
    progress.value = "";
    activeTask.value = "";
    await load();
    if (
      props.autoConfirm &&
      (plan.value as Plan | null)?.can_confirm &&
      insideWeCom() &&
      readyTasks.value.length
    )
      await dispatch();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  disposed = true;
  generation++;
});
</script>

<template>
  <section class="meeting-native-todos" aria-label="本次会议企微待办">
    <p>
      会议已发布。同一任务沿用原企微待办；尚未关联的任务各建一条，发给该项全部责任人。
    </p>
    <el-alert
      v-if="!insideWeCom()"
      type="info"
      :closable="false"
      show-icon
      title="请在企业微信中打开本会议，再点击“派发企微待办”完成确认。当前浏览器可查看状态。"
    />
    <el-alert
      v-if="error"
      type="warning"
      :closable="false"
      show-icon
      :title="'会议已发布；待办状态读取或派发失败：' + error"
    />
    <div v-if="loading && !plan" role="status" class="muted">
      正在读取本次会议的待办状态…
    </div>
    <template v-if="plan">
      <el-alert
        v-if="plan.reason"
        type="warning"
        :closable="false"
        show-icon
        :title="plan.reason"
      />
      <p class="native-summary" role="status">
        本次安排 {{ plan.tasks.length + (plan.excluded_tasks?.length || 0) }} 项
        · 已关联 {{ linkedCount }} 项 · 待确认 {{ readyTasks.length }} 项 ·
        待核实 {{ pendingCount }} 项
      </p>
      <el-alert
        v-if="linkedCount && !readyTasks.length && !pendingCount"
        type="success"
        :closable="false"
        show-icon
        title="已有企微待办已保留，无需再次弹窗创建。会议纪要通知按发布时的通知选项发送。"
      />
      <el-empty
        v-if="!plan.tasks.length && !plan.excluded_tasks?.length"
        description="本次会议没有需要派发的任务"
        :image-size="64"
      />
      <ol class="native-task-list">
        <li v-for="task in plan.tasks" :key="task.id" class="native-task-row">
          <div class="native-task-heading">
            <h3>{{ task.content }}</h3>
            <el-tag
              :type="
                task.native_todo.link ||
                ['created', 'linked'].includes(results[task.id]?.state || '')
                  ? 'success'
                  : 'info'
              "
              >{{ stateLabel(task) }}</el-tag
            >
          </div>
          <p class="muted small">
            责任人：{{ task.owner_names.join("、") }} · 计划完成：{{
              task.due_date || "未设置"
            }}
          </p>
          <p v-if="results[task.id]" class="small">
            {{ results[task.id].message }}
          </p>
          <p v-else-if="task.native_todo.reason" class="small">
            {{ task.native_todo.reason }}
          </p>
          <el-button
            link
            type="primary"
            :disabled="busy || detailBusy"
            :aria-expanded="activeTask === task.id"
            @click="activeTask = activeTask === task.id ? '' : task.id"
          >
            {{ activeTask === task.id ? "收起待办详情" : "查看 / 处理待办" }}
          </el-button>
          <NativeTodoPanel
            v-if="activeTask === task.id && !busy"
            :key="task.id"
            :task="task"
            issuer-context
            :result="results[task.id]"
            @state-change="(value) => updateState(task, value)"
            @busy-change="
              (value) => {
                detailBusy = value;
                emit('busyChange', value);
              }
            "
          />
        </li>
        <li
          v-for="(task, index) in plan.excluded_tasks"
          :key="task.id || `excluded-${index}`"
          class="native-task-row"
        >
          <div class="native-task-heading">
            <h3>{{ task.content }}</h3>
            <el-tag type="info">未派发</el-tag>
          </div>
          <p v-if="task.owner_names.length" class="muted small">
            责任人：{{ task.owner_names.join("、") }}
          </p>
          <p class="small">{{ task.reason }}</p>
        </li>
      </ol>
    </template>
    <p v-if="progress" role="status" class="small">{{ progress }}</p>
    <p class="muted small">
      取消确认不影响已发布会议和系统任务；已有待办不会重复创建。待核实的任务请先恢复关联，确认未创建后再重试。
    </p>
    <div class="actions native-dispatch-actions">
      <el-button
        type="primary"
        :loading="busy"
        :disabled="
          loading ||
          detailBusy ||
          !plan?.can_confirm ||
          !insideWeCom() ||
          !readyTasks.length
        "
        @click="dispatch"
        >确认派发全部待办</el-button
      >
      <el-button :disabled="busy || detailBusy || loading" @click="load"
        >刷新待办状态</el-button
      >
    </div>
  </section>
</template>

<style scoped>
.meeting-native-todos {
  display: grid;
  gap: 16px;
}
.meeting-native-todos p {
  margin-bottom: 0;
}
.native-summary {
  font-weight: 600;
}
.native-task-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.native-task-row {
  padding: 16px 0;
  border-top: 1px solid var(--line);
}
.native-task-heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 16px;
}
.native-task-heading h3 {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.native-task-heading :deep(.el-tag) {
  flex-shrink: 0;
}
.native-task-row > p {
  margin-top: 8px;
  overflow-wrap: anywhere;
}
.native-task-row > .el-button {
  margin-top: 8px;
}
.native-dispatch-actions {
  flex-wrap: wrap;
}
.native-dispatch-actions
  :deep(
    .el-button--primary.is-disabled:not(.is-link):not(.is-text):not(.is-plain)
  ) {
  color: var(--muted);
  background: #f4f6f9;
  border-color: var(--line);
  box-shadow: none;
  transform: none;
  cursor: not-allowed;
}
@media (max-width: 640px) {
  .native-task-heading {
    flex-direction: column;
    gap: 8px;
  }
  .native-dispatch-actions {
    align-items: stretch;
    flex-direction: column;
  }
  .native-dispatch-actions :deep(.el-button) {
    width: 100%;
    margin-left: 0;
  }
}
</style>
