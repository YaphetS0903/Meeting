<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, run } from "../api";
import {
  bindAssignedTodo,
  clearSavedNativeBinding,
  confirmAssignedTodo,
  savedNativeBinding,
  type NativeResult,
} from "../nativeAssignment";
import { insideWeCom, nativeError, viewWeComTodo } from "../wecom";

const props = defineProps<{
  task: { id: string; version: number; source_meeting_id?: string | null };
  issuerContext?: boolean;
  autoConfirm?: boolean;
  result?: NativeResult | null;
  compact?: boolean;
}>();
const emit = defineEmits<{
  stateChange: [value: any];
  busyChange: [busy: boolean];
}>();
const info = ref<any>(null),
  busy = ref(false),
  error = ref("");
const recoveryId = ref("");
const expanded = ref(false);
const hasRecoveryId = computed(() => !!recoveryId.value.trim());
const compactStatus = computed(() =>
  error.value ||
  info.value?.warning ||
  info.value?.pending_attempt ||
  ["blocked", "failed", "uncertain"].includes(info.value?.message_state)
    ? "需要处理"
    : info.value?.link
      ? info.value.link.state === "finished"
        ? "待办已完成"
        : "已关联待办"
      : info.value?.message_state === "sent"
        ? "应用消息已发送"
        : "查看状态",
);
function syncExpanded(event: Event) {
  if (props.compact) expanded.value = (event.target as HTMLDetailsElement).open;
}
watch(
  () => [props.task.id, props.autoConfirm],
  () => {
    expanded.value = !!props.autoConfirm;
  },
  { immediate: true },
);
watch(
  () => [
    error.value,
    info.value?.warning,
    info.value?.pending_attempt,
    info.value?.message_state,
  ],
  () => {
    if (compactStatus.value === "需要处理") expanded.value = true;
  },
);
let generation = 0;
let autoOpenedFor = "";
const messageLabel = computed(
  () =>
    ({
      pending: "应用消息排队发送中",
      sending: "应用消息发送中",
      sent: "应用消息已发送",
      skipped: "历史应用消息未发送，不会自动补发",
      blocked: "应用消息待管理员核对账号或配置",
      failed: "应用消息发送失败，请联系管理员",
      uncertain: "应用消息送达结果待核实",
    })[info.value?.message_state as string] || "",
);
async function load() {
  const current = ++generation;
  error.value = "";
  try {
    const result = await api(`/tasks/${props.task.id}/native-todo`);
    if (current === generation) {
      info.value = result;
      emit("stateChange", result);
      recoveryId.value =
        result.pending_attempt?.todo_id || savedBinding()?.todo_id || "";
    }
  } catch (e: any) {
    if (current === generation) error.value = chineseError(e);
  }
}
function savedBinding(): any {
  const cached = savedNativeBinding(
    props.task.id,
    info.value?.pending_attempt?.id,
  );
  return cached?.attempt_id === info.value?.pending_attempt?.id ? cached : null;
}
async function create() {
  if (busy.value || !props.issuerContext || !info.value?.is_issuer) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await confirmAssignedTodo(props.task);
    if (result.state === "created" || result.state === "linked")
      ElMessage.success(result.message);
    await load();
    if (result.state !== "created" && result.state !== "linked")
      error.value = chineseError(
        result.message,
        "企业微信待办操作失败，请稍后重试",
      );
  } finally {
    busy.value = false;
  }
}
async function recover() {
  if (busy.value) return;
  const cached = savedBinding(),
    attempt = info.value?.pending_attempt;
  const todoId = recoveryId.value.trim() || attempt?.todo_id || cached?.todo_id;
  if (!todoId || !attempt) {
    error.value =
      "当前没有保存到待办编号，暂时无法恢复。请核对企微里的原待办；已经创建时，请联系管理员处理，避免重复新建。";
    return;
  }
  busy.value = true;
  try {
    info.value = await bindAssignedTodo(props.task.id, attempt.id, todoId);
    emit("stateChange", info.value);
    error.value = "";
    ElMessage.success("原生待办已恢复关联");
  } catch (e: any) {
    error.value = chineseError(e);
  } finally {
    busy.value = false;
  }
}
async function reset() {
  await ElMessageBox.confirm(
    "请先在企业微信待办列表核实此任务确实没有创建。已有待办时请恢复关联，重新创建会导致重复。确定尚未创建吗？",
    "核实创建结果",
    {
      confirmButtonText: "确认未创建",
      cancelButtonText: "返回核对",
      type: "warning",
    },
  );
  busy.value = true;
  try {
    info.value = await api(
      `/tasks/${props.task.id}/native-todo/reset`,
      "POST",
      {
        attempt_id: info.value.pending_attempt.id,
        reason: "confirmed_not_created",
      },
    );
    clearSavedNativeBinding(props.task.id);
    emit("stateChange", info.value);
    error.value = "";
  } finally {
    busy.value = false;
  }
}
async function view() {
  const todoId =
    info.value?.link?.todo_id ||
    info.value?.pending_attempt?.todo_id ||
    savedBinding()?.todo_id;
  if (!todoId) {
    error.value = "当前页面没有保存待办编号，请从企业微信待办列表打开这项任务";
    return;
  }
  busy.value = true;
  try {
    await viewWeComTodo(todoId);
  } catch (e: any) {
    error.value = nativeError(e);
  } finally {
    busy.value = false;
  }
}
watch(
  () => [
    props.task.id,
    props.task.version,
    props.result?.state,
    props.autoConfirm,
  ],
  async () => {
    const taskId = props.task.id;
    info.value = null;
    error.value = "";
    recoveryId.value = "";
    await load();
    if (taskId !== props.task.id) return;
    if (
      props.autoConfirm &&
      props.issuerContext &&
      info.value?.is_issuer &&
      info.value?.can_create &&
      insideWeCom() &&
      autoOpenedFor !== taskId
    ) {
      autoOpenedFor = taskId;
      await create();
    }
  },
  { immediate: true },
);
watch(busy, (value) => emit("busyChange", value));
</script>

<template>
  <component
    :is="compact ? 'details' : 'section'"
    v-if="info?.enabled || info?.link || error"
    class="panel section-body native-panel"
    :class="{ 'native-panel-compact': compact }"
    :open="compact ? expanded : undefined"
    @toggle="syncExpanded"
  >
    <summary v-if="compact" class="native-summary">
      <strong>企业微信提醒</strong>
      <span class="muted small">{{ compactStatus }}</span>
    </summary>
    <div :class="{ 'native-details': compact }">
      <h3 v-if="!compact">企业微信提醒</h3>
      <p v-if="messageLabel" class="muted small">{{ messageLabel }}</p>
      <p class="muted small">
        原生待办由派发人确认后直接送达责任人；完成后仍需在系统提交完成申请。
      </p>
      <p class="muted small">
        补充说明和输出物随待办内容展示，内容较长时可从详情链接查看完整安排。
      </p>
      <el-alert
        v-if="error"
        :title="error"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="info?.warning"
        :title="info.warning"
        type="warning"
        :closable="false"
        show-icon
      />
      <template v-if="info?.link">
        <el-button
          v-if="info.link.provider === 'app'"
          :loading="busy"
          @click="view"
          >查看企微原生待办</el-button
        >
        <p v-else class="muted small">
          此任务已关联原机器人待办，继续按原通道处理。
        </p>
        <el-tag v-if="info.link.state === 'finished'" type="success"
          >原生待办已完成</el-tag
        >
      </template>
      <template v-else-if="info?.pending_attempt">
        <p v-if="hasRecoveryId" class="muted small">
          已保存待办编号，可以恢复关联，无需重新创建。
        </p>
        <el-alert
          v-else
          title="未保存到待办编号，暂时无法恢复关联。请先核对企微中的原待办；已经创建时，请联系管理员处理，避免重复新建。"
          type="warning"
          :closable="false"
          show-icon
        />
        <p v-if="issuerContext && info.is_issuer" class="muted small">
          请核对已创建待办的截止日期：{{
            info.pending_attempt.due_date || "未设置"
          }}。日期不一致时，先在企微补正，再恢复关联。
        </p>
        <p
          v-if="
            issuerContext &&
            info.is_issuer &&
            info.pending_attempt.owner_names?.length
          "
          class="muted small"
        >
          责任人：{{
            info.pending_attempt.owner_names.join("、")
          }}。恢复关联时会自动核对并补齐企微参与人。
        </p>
        <el-alert
          v-if="info.pending_attempt.verification_error"
          :title="info.pending_attempt.verification_error"
          type="warning"
          :closable="false"
          show-icon
        />
        <div
          v-if="
            issuerContext && info.is_issuer && info.pending_attempt.can_recover
          "
          class="recovery"
        >
          <el-input
            v-model="recoveryId"
            placeholder="管理员恢复：输入已核对的待办编号"
            maxlength="200"
          />
          <div class="actions">
            <el-button
              v-if="
                (info.pending_attempt.todo_id || savedBinding()?.todo_id) &&
                insideWeCom()
              "
              :disabled="busy"
              @click="view"
              >打开已创建待办</el-button
            >
            <el-button
              type="primary"
              :loading="busy"
              :disabled="!hasRecoveryId"
              @click="recover"
              >恢复待办关联</el-button
            >
            <el-button :disabled="busy" @click="run(reset)"
              >核实未创建</el-button
            >
          </div>
        </div>
      </template>
      <el-button
        v-else-if="issuerContext && info?.is_issuer && info?.can_create"
        type="primary"
        plain
        :loading="busy"
        @click="create"
        >{{
          info?.source === "meeting" ? "确认创建企微待办" : "重新打开企微确认窗"
        }}</el-button
      >
      <p v-else-if="issuerContext && info?.reason" class="muted small">
        {{ info.reason }}
      </p>
      <p v-else-if="!info?.link && !info?.pending_attempt" class="muted small">
        尚未确认原生待办，可通过应用消息处理系统任务。
      </p>
      <p
        v-if="issuerContext && info?.can_create && !insideWeCom()"
        class="muted small"
      >
        请从企业微信工作台打开系统创建原生待办。
      </p>
      <el-button link :disabled="busy" @click="load">刷新提醒状态</el-button>
    </div>
  </component>
</template>

<style scoped>
.native-panel {
  margin: 20px 0;
}
.native-panel :deep(.el-alert) {
  margin-bottom: 12px;
}
.native-panel-compact {
  margin: 16px 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  background: transparent;
}
.native-summary {
  cursor: pointer;
  padding: 10px 0;
}
.native-summary strong {
  margin-right: 12px;
}
.native-summary:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 3px;
}
.native-details {
  padding: 4px 0 12px;
}
.recovery {
  display: grid;
  gap: 12px;
}
</style>
