<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, clientId, run, state } from "../api";
import { isEmployeeAccount } from "../people";
import PeopleSelect from "../components/PeopleSelect.vue";
import IssuedTaskRecords from "../components/IssuedTaskRecords.vue";
import NativeTodoPanel from "../components/NativeTodoPanel.vue";
import {
  Calendar,
  Checked,
  Document,
  Promotion,
  User,
  Bell,
  ArrowRight,
} from "@element-plus/icons-vue";
import { confirmAssignedTodo, type NativeResult } from "../nativeAssignment";
import { insideWeCom } from "../wecom";
import { assignmentDeliveryMessage } from "../assignmentDelivery";

type Person = {
  id: string;
  name: string;
  department_id: string | null;
  active?: boolean;
  wecom_bound?: boolean;
  position?: string;
};
const options = ref<{
  can_assign: boolean;
  scope: string;
  native_todo_auto_confirm: boolean;
  wecom_bound: boolean;
  wecom_notifications_enabled: boolean;
  users: Person[];
} | null>(null);
const rows = ref<any[]>([]);
const busy = ref(false);
const loadError = ref("");
const formError = ref("");
const form = ref({
  content: "",
  requirements: "",
  deliverable: "",
  due_date: null as string | null,
  owner_ids: [] as string[],
  verifier_id: state.me?.id || "",
});
const draftStorageKey = `direct-assignment-attempt:${state.me?.id}`;
let attemptKey = clientId();
let attemptPayload = "";
const restoredAttempt = ref(false);
const savedTask = ref<any>(null);
const nativeResult = ref<NativeResult | null>(null);
const phase = ref("");
const recordsVisible = ref(false);
const previewExpanded = ref(false);
const ownerNames = computed(() =>
  form.value.owner_ids.map((id) => people.value.get(id) || "未找到人员"),
);
const verifierName = computed(
  () =>
    people.value.get(form.value.verifier_id) ||
    (form.value.verifier_id === state.me?.id ? state.me.name : "待选择"),
);
const scopeLabel = computed(() =>
  options.value?.scope === "all" ? "全部在用员工" : "本部门及子部门",
);
const nativeReady = computed(
  () =>
    options.value?.native_todo_auto_confirm &&
    options.value?.wecom_bound &&
    insideWeCom(),
);
const deliveryHint = computed(() => {
  if (!options.value) return "正在读取通知设置…";
  const notification = options.value.wecom_notifications_enabled
    ? "任务建立后将排队发送应用消息，实际送达请查看通知记录。"
    : "应用消息通知已关闭，责任人可登录系统查看任务。";
  if (nativeReady.value)
    return notification + "派发后会弹出企微确认窗，可确认创建原生待办。";
  if (options.value.native_todo_auto_confirm)
    return notification + "使用已绑定的本人企微身份，可确认创建原生待办。";
  return notification;
});
const openCount = computed(
  () => rows.value.filter((row) => !row.closed_at && !row.withdrawn_at).length,
);
const fieldErrors = ref({
  content: "",
  owner_ids: "",
  verifier_id: "",
  due_date: "",
});
try {
  const previous = JSON.parse(
    sessionStorage.getItem(draftStorageKey) || "null",
  );
  if (
    previous?.key &&
    previous?.payload &&
    Array.isArray(previous.payload.owner_ids)
  ) {
    form.value = { ...form.value, ...previous.payload };
    attemptKey = previous.key;
    attemptPayload = JSON.stringify(previous.payload);
    restoredAttempt.value = true;
  }
} catch {
  sessionStorage.removeItem(draftStorageKey);
}
const allowed = computed(() => {
  const directory = new Map<string, any>(
    state.directory.users.map((person: any) => [person.id, person]),
  );
  return (options.value?.users || [])
    .map((person) => ({
      ...directory.get(person.id),
      ...person,
      // This endpoint returns only active employees within dispatch scope.
      active: person.active !== false,
    }))
    .filter((person) => person.active && isEmployeeAccount(person));
});
const verifiers = computed(() => {
  if (!state.me || allowed.value.some((person) => person.id === state.me.id))
    return allowed.value;
  // The dispatcher may verify personally, as on the previous form.
  return [...allowed.value, { ...state.me, active: true }];
});
const verifierWarning = computed(() => {
  const selected = verifiers.value.find(
    (p: any) => p.id === form.value.verifier_id,
  );
  const bound =
    selected?.wecom_bound ??
    (selected?.id === state.me?.id ? options.value?.wecom_bound : undefined);
  return bound === false
    ? "该验证人未绑定企业微信，无法收到验证提醒；可在系统验证待办中处理，或改选已绑定人员。"
    : "";
});
const people = computed(
  () => new Map(allowed.value.map((person) => [person.id, person.name])),
);
async function load(background = false) {
  loadError.value = "";
  try {
    const [scope, issued] = await Promise.all([
      api("/direct-assignments/options"),
      api("/direct-assignments", "GET", undefined, { background }),
    ]);
    options.value = scope;
    rows.value = issued;
  } catch (error: any) {
    loadError.value = chineseError(error, "服务暂时不可用");
    rows.value = [];
    throw error;
  }
}
function openRecords() {
  recordsVisible.value = true;
}
async function assign() {
  formError.value = "";
  fieldErrors.value = {
    content: form.value.content.trim() ? "" : "请填写任务内容",
    owner_ids: !form.value.owner_ids.length
      ? "请选择责任人"
      : form.value.owner_ids.some(
            (id) => !allowed.value.some((person) => person.id === id),
          )
        ? "责任人不在当前可选员工范围，请重新选择"
        : "",
    verifier_id: !form.value.verifier_id
      ? "请选择验证人"
      : !verifiers.value.some((person) => person.id === form.value.verifier_id)
        ? "验证人不在当前可选员工范围，请重新选择"
        : "",
    due_date: form.value.due_date ? "" : "请选择计划完成时间",
  };
  if (
    !form.value.content.trim() ||
    fieldErrors.value.owner_ids ||
    fieldErrors.value.verifier_id ||
    !form.value.due_date
  ) {
    formError.value = "请填写任务内容、计划完成时间，并选择责任人和验证人";
    return;
  }
  const names = form.value.owner_ids
    .map((id) => people.value.get(id) || "未找到人员")
    .join("、");
  if (busy.value) return;
  busy.value = true;
  phase.value = "确认派发";
  const payload = {
    ...form.value,
    content: form.value.content.trim(),
    requirements: form.value.requirements.trim(),
    deliverable: form.value.deliverable.trim(),
    owner_ids: [...form.value.owner_ids].sort(),
    due_date: form.value.due_date || null,
  };
  try {
    await ElMessageBox.confirm(
      `确定向 ${names} 派发任务吗？截止日期：${form.value.due_date}。${verifierWarning.value}`,
      "确认派发任务",
      {
        confirmButtonText: "确认派发",
        cancelButtonText: "再检查一下",
        type: "warning",
      },
    );
    const serialized = JSON.stringify(payload);
    if (serialized !== attemptPayload) attemptKey = clientId();
    attemptPayload = serialized;
    sessionStorage.setItem(
      draftStorageKey,
      JSON.stringify({ key: attemptKey, payload }),
    );
    phase.value = "正在派发任务";
    const task = await api("/direct-assignments", "POST", {
      ...payload,
      idempotency_key: attemptKey,
    });
    sessionStorage.removeItem(draftStorageKey);
    restoredAttempt.value = false;
    attemptKey = clientId();
    attemptPayload = "";
    savedTask.value = task;
    nativeResult.value = null;
    ElMessage.success(
      "任务已派发。" + assignmentDeliveryMessage(task.wecom_notification_state),
    );
    form.value = {
      content: "",
      requirements: "",
      deliverable: "",
      due_date: null,
      owner_ids: [],
      verifier_id: state.me?.id || "",
    };
    if (options.value?.native_todo_auto_confirm) {
      phase.value = nativeReady.value ? "请在企微弹窗中确认" : "任务已派发";
      try {
        nativeResult.value = options.value.wecom_bound
          ? await confirmAssignedTodo(task)
          : {
              state: "unavailable",
              message:
                "当前账号未绑定企业微信。使用本人企微账号派发时，可确认原生待办。",
            };
      } catch (error: any) {
        nativeResult.value = {
          state: "pending",
          message: `系统任务已派发，企微确认结果待核实：${chineseError(error, "请稍后刷新")}`,
        };
      }
      if (nativeResult.value.state === "created")
        ElMessage.success("原生待办已送达责任人");
    }
    await run(() => load(true));
  } catch (error: any) {
    if (error !== "cancel" && error !== "close")
      formError.value = chineseError(error, "派发失败，请检查后重试");
  } finally {
    busy.value = false;
    phase.value = "";
  }
}
onMounted(() => run(load));
</script>

<template>
  <div class="dispatch-page">
    <section class="dispatch-heading">
      <div>
        <h1>直接派发任务</h1>
      </div>
      <div class="dispatch-heading-actions" v-if="options?.can_assign">
        <el-button
          class="dispatch-preview-toggle"
          :aria-expanded="previewExpanded"
          aria-controls="dispatch-preview-panel"
          @click="previewExpanded = !previewExpanded"
          >{{ previewExpanded ? "收起预览" : "派发预览" }}</el-button
        >
        <el-button
          class="dispatch-records-mobile"
          :icon="Document"
          aria-label="我派发的任务"
          aria-haspopup="dialog"
          :aria-expanded="recordsVisible"
          @click="openRecords"
          >我派发的任务</el-button
        >
        <RouterLink
          :to="{
            path: '/task-overview',
            query: { source: 'direct', issued_by_me: '1' },
          }"
        >
          <el-button :icon="Document">任务总览</el-button>
        </RouterLink>
      </div>
    </section>
    <el-alert
      v-if="loadError"
      :title="`派发记录加载失败：${loadError}`"
      type="error"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="options && !options.can_assign"
      title="当前账号没有派发权限，请联系系统管理员核对岗位或授权角色。"
      type="info"
      :closable="false"
      show-icon
    />
    <template v-if="options?.can_assign">
      <el-alert
        v-if="restoredAttempt"
        title="已恢复上次未确认结果的任务。重新确认会核对原派发记录，避免重复派发。"
        type="info"
        :closable="false"
        show-icon
      />
      <div class="dispatch-layout">
        <section class="panel dispatch-form-card">
          <header class="dispatch-card-heading">
            <div class="dispatch-title">
              <span class="dispatch-icon"
                ><el-icon><Promotion /></el-icon
              ></span>
              <div>
                <h2>新建任务</h2>
              </div>
            </div>
            <span class="dispatch-scope"
              ><el-icon><User /></el-icon>{{ scopeLabel }}</span
            >
          </header>
          <el-form
            :disabled="busy"
            label-position="top"
            @submit.prevent="run(assign)"
            class="dispatch-form"
          >
            <el-form-item
              label="任务内容"
              required
              :error="fieldErrors.content"
            >
              <el-input
                v-model="form.content"
                type="textarea"
                :rows="3"
                maxlength="20000"
                show-word-limit
                placeholder="例如：整理本月采购计划，并提交部门审核"
                aria-label="任务内容"
              />
            </el-form-item>
            <div class="dispatch-fields">
              <el-form-item
                label="责任人"
                required
                :error="fieldErrors.owner_ids"
              >
                <PeopleSelect
                  v-model="form.owner_ids"
                  multiple
                  search-only
                  copyable
                  :users="allowed"
                  placeholder="输入或粘贴姓名，多个用顿号分隔"
                  aria-label="选择责任人"
                />
              </el-form-item>
              <el-form-item
                label="验证人"
                required
                :error="fieldErrors.verifier_id"
              >
                <PeopleSelect
                  v-model="form.verifier_id"
                  search-only
                  copyable
                  :users="verifiers"
                  placeholder="输入或粘贴验证人姓名"
                  aria-label="选择验证人"
                />
                <p v-if="verifierWarning" class="muted small" role="status">
                  {{ verifierWarning }}
                </p>
              </el-form-item>
              <el-form-item
                label="计划完成时间"
                required
                :error="fieldErrors.due_date"
              >
                <el-date-picker
                  v-model="form.due_date"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="请选择计划完成时间"
                  :clearable="false"
                  aria-label="计划完成时间"
                  aria-required="true"
                />
              </el-form-item>
              <el-form-item label="输出物">
                <el-input
                  v-model="form.deliverable"
                  placeholder="选填，例如：方案、清单、报告"
                  aria-label="输出物"
                />
              </el-form-item>
            </div>
            <el-form-item label="补充说明">
              <el-input
                v-model="form.requirements"
                type="textarea"
                :rows="2"
                placeholder="选填，补充验收标准或注意事项"
                aria-label="补充说明"
              />
            </el-form-item>
            <div class="dispatch-delivery">
              <el-icon><Bell /></el-icon>
              <div>
                <strong>{{
                  nativeReady ? "应用消息 + 企微原生待办" : "企业微信应用消息"
                }}</strong>
                <p>{{ deliveryHint }}</p>
              </div>
            </div>
            <el-alert
              v-if="formError"
              :title="formError"
              type="error"
              :closable="false"
              show-icon
            />
            <footer class="dispatch-submit">
              <el-button
                type="primary"
                native-type="submit"
                size="large"
                :icon="Promotion"
                :loading="busy"
                >{{ busy ? phase : "确认派发任务" }}</el-button
              >
            </footer>
          </el-form>
        </section>
        <div
          id="dispatch-preview-panel"
          class="dispatch-sidebar"
          :class="{ 'is-open': previewExpanded }"
        >
          <aside class="dispatch-preview panel" aria-label="派发预览">
            <div class="dispatch-preview-heading">
              <h2>派发预览</h2>
            </div>
            <div class="dispatch-preview-body">
              <span class="dispatch-preview-label">任务安排</span>
              <p
                class="dispatch-preview-content"
                :class="{ 'is-empty': !form.content.trim() }"
              >
                {{ form.content.trim() || "待填写任务内容" }}
              </p>
              <div class="dispatch-preview-item">
                <el-icon><User /></el-icon>
                <div>
                  <span>责任人</span>
                  <div v-if="ownerNames.length" class="dispatch-recipient-list">
                    <span
                      v-for="(name, index) in ownerNames.slice(0, 3)"
                      :key="index"
                      class="dispatch-recipient"
                      >{{ name }}</span
                    ><span v-if="ownerNames.length > 3" class="dispatch-more"
                      >+{{ ownerNames.length - 3 }} 人</span
                    >
                  </div>
                  <p v-else>待选择</p>
                </div>
              </div>
              <div class="dispatch-preview-item">
                <el-icon><Calendar /></el-icon>
                <div>
                  <span>计划完成</span>
                  <p>{{ form.due_date || "待选择计划完成时间" }}</p>
                </div>
              </div>
              <div class="dispatch-preview-item">
                <el-icon><Checked /></el-icon>
                <div>
                  <span>验证人</span>
                  <p>{{ verifierName }}</p>
                </div>
              </div>
              <div v-if="form.deliverable.trim()" class="dispatch-preview-item">
                <el-icon><Document /></el-icon>
                <div>
                  <span>输出物</span>
                  <p>{{ form.deliverable }}</p>
                </div>
              </div>
            </div>
          </aside>
          <button
            type="button"
            class="panel dispatch-records-trigger"
            aria-label="我派发的任务"
            aria-haspopup="dialog"
            :aria-expanded="recordsVisible"
            @click="openRecords"
          >
            <span class="dispatch-records-trigger-title">
              <strong>我派发的任务</strong>
              <span class="count">{{ rows.length }}</span>
            </span>
            <span class="dispatch-records-trigger-summary"
              >{{ openCount }} 项正在跟进</span
            >
            <span class="dispatch-records-trigger-action"
              >查看全部派发记录<el-icon><ArrowRight /></el-icon
            ></span>
          </button>
        </div>
      </div>
      <section v-if="savedTask" class="dispatch-result" aria-live="polite">
        <div class="dispatch-result-heading">
          <el-icon><Checked /></el-icon>
          <div>
            <strong>任务已派发</strong>
            <p>
              {{
                assignmentDeliveryMessage(savedTask.wecom_notification_state)
              }}
            </p>
            <p v-if="nativeResult?.message">{{ nativeResult.message }}</p>
          </div>
          <RouterLink :to="{ path: '/tasks', query: { id: savedTask.id } }"
            ><el-button>查看任务</el-button></RouterLink
          >
        </div>
        <NativeTodoPanel
          :task="savedTask"
          issuer-context
          :result="nativeResult"
        />
      </section>
      <el-dialog
        v-model="recordsVisible"
        title="我派发的任务"
        width="min(1180px, 94vw)"
        top="5vh"
        append-to-body
        destroy-on-close
      >
        <IssuedTaskRecords
          v-if="recordsVisible"
          dialog
          @loaded="rows = $event"
          @close="recordsVisible = false"
        />
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.dispatch-page {
  --dispatch-text: #233858;
  --dispatch-muted: #64758d;
  --dispatch-line: #e8eef7;
  --dispatch-tint: #f5f8fd;
  --dispatch-blue: #2878ef;
}
.dispatch-page {
  max-width: 1480px;
  margin: 0 auto;
}
.dispatch-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}
.dispatch-heading h1 {
  font-size: 26px;
  letter-spacing: 0;
  margin: 0;
}
.dispatch-heading-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.dispatch-heading-actions .el-button {
  margin: 0;
}
.dispatch-records-mobile {
  display: none;
}
.dispatch-preview-toggle {
  display: none;
}
.dispatch-page > .el-alert {
  margin-bottom: 18px;
}
.dispatch-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 24px;
  align-items: start;
}
.dispatch-form-card,
.dispatch-preview {
  border-radius: 14px;
  overflow: hidden;
  margin-bottom: 0;
}
.dispatch-card-heading {
  padding: 24px 28px 22px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  border-bottom: 1px solid var(--dispatch-line);
}
.dispatch-title {
  display: flex;
  align-items: center;
  gap: 12px;
}
.dispatch-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: #edf4ff;
  color: var(--dispatch-blue);
  font-size: 22px;
  flex-shrink: 0;
}
.dispatch-title h2,
.dispatch-preview-heading h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 650;
  color: var(--dispatch-text);
}
.dispatch-scope {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--dispatch-tint);
  border: 1px solid var(--dispatch-line);
  color: #486284;
  border-radius: 6px;
  font-size: 12px;
  white-space: nowrap;
}
.dispatch-form {
  padding: 24px 28px 0;
}
.dispatch-form :deep(.el-form-item) {
  margin-bottom: 22px;
  min-width: 0;
}
.dispatch-form :deep(.el-form-item__label) {
  font-size: 13px;
  line-height: 22px;
  margin-bottom: 8px;
  color: #445773;
}
.dispatch-form :deep(.el-input__wrapper),
.dispatch-form :deep(.el-select__wrapper) {
  min-height: 42px;
  background: #fff;
}
.dispatch-form :deep(.el-textarea__inner) {
  padding: 12px 14px 22px;
  line-height: 1.7;
  font-size: 14px;
  resize: vertical;
}
.dispatch-form :deep(.el-select),
.dispatch-form :deep(.el-date-editor) {
  width: 100%;
}
.dispatch-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
}
.dispatch-delivery {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: var(--dispatch-tint);
  border: 1px solid var(--dispatch-line);
  border-radius: 9px;
  padding: 14px 16px;
  margin: 2px 0 22px;
}
.dispatch-delivery > .el-icon {
  color: var(--dispatch-blue);
  font-size: 18px;
  margin-top: 2px;
  flex-shrink: 0;
}
.dispatch-delivery strong {
  font-size: 13px;
  color: #395777;
  font-weight: 600;
}
.dispatch-delivery p {
  color: var(--dispatch-muted);
  font-size: 12px;
  line-height: 1.65;
  margin: 5px 0 0;
}
.dispatch-submit {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  width: auto;
  padding: 20px 0 24px;
  border-top: 1px solid var(--dispatch-line);
  margin-top: 22px;
}
.dispatch-submit .el-button {
  min-width: 162px;
  min-height: 44px;
  margin: 0;
}
.dispatch-sidebar {
  position: sticky;
  top: 24px;
  display: grid;
  gap: 20px;
  min-width: 0;
}
.dispatch-records-trigger {
  width: 100%;
  padding: 22px;
  text-align: left;
  color: var(--dispatch-text);
  display: grid;
  gap: 8px;
}
.dispatch-records-trigger:hover {
  border-color: var(--dispatch-blue);
}
.dispatch-records-trigger:focus-visible {
  outline: 2px solid var(--dispatch-blue);
  outline-offset: 3px;
}
.dispatch-records-trigger-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
}
.dispatch-records-trigger-summary {
  font-size: 13px;
  color: var(--dispatch-muted);
}
.dispatch-records-trigger-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--dispatch-blue);
  font-size: 13px;
  margin-top: 8px;
}
.dispatch-preview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 22px;
  border-bottom: 1px solid var(--dispatch-line);
}
.dispatch-preview-heading h2 {
  font-size: 16px;
}
.dispatch-preview-heading > span {
  color: var(--dispatch-muted);
  font-size: 11px;
}
.dispatch-preview-body {
  padding: 22px;
}
.dispatch-preview-label {
  color: var(--dispatch-muted);
  font-size: 12px;
}
.dispatch-preview-content {
  color: var(--dispatch-text);
  margin: 10px 0 22px;
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 155px;
  overflow: auto;
}
.dispatch-preview-content.is-empty {
  color: #8593a8;
}
.dispatch-preview-item {
  display: flex;
  gap: 12px;
  padding: 16px 0;
  border-top: 1px solid var(--dispatch-line);
}
.dispatch-preview-item > .el-icon {
  color: #8194ae;
  font-size: 17px;
  margin-top: 2px;
  flex-shrink: 0;
}
.dispatch-preview-item > div {
  min-width: 0;
}
.dispatch-preview-item > div > span {
  color: var(--dispatch-muted);
  font-size: 12px;
}
.dispatch-preview-item p {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--dispatch-text);
  overflow-wrap: anywhere;
}
.dispatch-recipient-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.dispatch-recipient {
  font-size: 12px;
  border-radius: 5px;
  background: #edf4ff;
  color: #426ba4;
  padding: 4px 8px;
}
.dispatch-more {
  color: var(--dispatch-muted);
  font-size: 12px;
  align-self: center;
}
.dispatch-result {
  margin-top: 24px;
  border: 1px solid #d9e9e1;
  border-radius: 12px;
  background: #fff;
  padding: 20px 24px;
}
.dispatch-result-heading {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.dispatch-result-heading > .el-icon {
  color: #398269;
  font-size: 22px;
  margin-top: 2px;
}
.dispatch-result-heading > div {
  flex: 1;
  min-width: 0;
}
.dispatch-result-heading strong {
  color: #2c6753;
  font-size: 15px;
}
.dispatch-result-heading p {
  color: var(--dispatch-muted);
  font-size: 13px;
  margin: 5px 0 0;
}
.dispatch-result :deep(.native-panel) {
  box-shadow: none;
  border: 0;
  padding: 16px 0 0;
  margin-bottom: 0;
}
@media (max-width: 1440px) {
  .dispatch-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .dispatch-sidebar {
    display: none;
  }
  .dispatch-sidebar.is-open {
    display: block;
    grid-row: 1;
    position: static;
  }
  .dispatch-sidebar .dispatch-records-trigger {
    display: none;
  }
  .dispatch-preview-toggle {
    display: inline-flex;
  }
  .dispatch-records-mobile {
    display: inline-flex;
  }
}
@media (min-width: 768px) and (max-width: 1440px) {
  .dispatch-heading {
    margin-bottom: 12px;
  }
  .dispatch-heading h1 {
    font-size: 22px;
  }
  .dispatch-form-card {
    overflow: visible;
  }
  .dispatch-card-heading {
    padding: 14px 20px;
  }
  .dispatch-form {
    padding: 16px 20px 0;
  }
  .dispatch-form :deep(.el-form-item) {
    margin-bottom: 14px;
  }
  .dispatch-form :deep(.el-form-item__label) {
    margin-bottom: 5px;
  }
  .dispatch-form :deep(.el-input__wrapper),
  .dispatch-form :deep(.el-select__wrapper) {
    min-height: 36px;
  }
  .dispatch-form :deep(.el-textarea__inner) {
    padding: 8px 12px 20px;
  }
  .dispatch-delivery {
    padding: 10px 12px;
    margin-bottom: 12px;
  }
  .dispatch-submit {
    position: sticky;
    bottom: 0;
    z-index: 5;
    padding: 10px 0;
    margin-top: 12px;
    background: #fff;
  }
  .dispatch-preview-body {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }
  .dispatch-preview-label,
  .dispatch-preview-content {
    grid-column: 1 / -1;
  }
  .dispatch-preview-item {
    margin: 0;
    padding-top: 12px;
  }
}
@media (max-width: 680px) {
  .dispatch-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
  .dispatch-heading h1 {
    font-size: 23px;
  }
  .dispatch-heading > div > p:last-child {
    font-size: 13px;
  }
  .dispatch-card-heading {
    padding: 18px;
    flex-wrap: wrap;
  }
  .dispatch-form {
    padding: 20px 18px 0;
  }
  .dispatch-fields {
    grid-template-columns: 1fr;
  }
  .dispatch-submit {
    flex-direction: column;
    align-items: stretch;
    padding-bottom: 20px;
  }
  .dispatch-submit .el-button {
    width: 100%;
  }
  .dispatch-result {
    padding: 18px;
  }
  .dispatch-result-heading {
    flex-wrap: wrap;
  }
}
</style>
