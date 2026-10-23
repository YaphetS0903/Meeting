<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, clientId, departmentPath, run, state } from "../api";
import StatusTag from "../components/StatusTag.vue";
import NativeTodoPanel from "../components/NativeTodoPanel.vue";
import {
  Calendar,
  Checked,
  Document,
  Promotion,
  Refresh,
  User,
  Bell,
  ArrowRight,
} from "@element-plus/icons-vue";
import { confirmAssignedTodo, type NativeResult } from "../nativeAssignment";
import { insideWeCom } from "../wecom";

type Person = { id: string; name: string; department_id: string | null };
const options = ref<{
  can_assign: boolean;
  scope: string;
  native_todo_auto_confirm: boolean;
  wecom_bound: boolean;
  users: Person[];
} | null>(null);
const rows = ref<any[]>([]);
const busy = ref(false);
const loading = ref(false);
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
const search = ref("");
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
const deliveryHint = computed(() =>
  nativeReady.value
    ? "派发后自动弹出企微确认窗，确认后责任人直接收到原生待办。"
    : insideWeCom() && !options.value?.wecom_bound
      ? "当前账号未绑定企微。使用企业微信身份登录后派发，可自动弹窗确认原生待办。"
      : options.value?.wecom_bound && options.value?.native_todo_auto_confirm
        ? "当前浏览器派发后，责任人自动收到应用消息；你会收到企微确认卡片，打开确认后为责任人生成原生待办。"
        : "从企业微信工作台用本人账号派发，可自动弹窗确认原生待办。当前浏览器派发将自动发送应用消息。",
);
const filteredRows = computed(() =>
  rows.value.filter(
    (row) =>
      !search.value.trim() ||
      `${row.content} ${row.code} ${row.owner_names.join(" ")}`
        .toLowerCase()
        .includes(search.value.trim().toLowerCase()),
  ),
);
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
const allowed = computed(() => options.value?.users || []);
const people = computed(
  () => new Map(allowed.value.map((person) => [person.id, person.name])),
);
async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const [scope, issued] = await Promise.all([
      api("/direct-assignments/options"),
      api("/direct-assignments"),
    ]);
    options.value = scope;
    rows.value = issued;
  } catch (error: any) {
    loadError.value = error.message || "服务暂时不可用";
    rows.value = [];
    throw error;
  } finally {
    loading.value = false;
  }
}
async function assign() {
  formError.value = "";
  fieldErrors.value = {
    content: form.value.content.trim() ? "" : "请填写任务内容",
    owner_ids: form.value.owner_ids.length ? "" : "请选择责任人",
    verifier_id: form.value.verifier_id ? "" : "请选择验证人",
    due_date: form.value.due_date ? "" : "请选择计划完成时间",
  };
  if (
    !form.value.content.trim() ||
    !form.value.owner_ids.length ||
    !form.value.verifier_id ||
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
      `确定向 ${names} 派发任务吗？截止日期：${form.value.due_date}。`,
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
    ElMessage.success("任务已派发，应用消息排队发送中");
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
                "当前账号未绑定企业微信，已使用责任人应用消息提醒。使用本人企微账号派发时，可确认原生待办。",
            };
      } catch (error: any) {
        nativeResult.value = {
          state: "pending",
          message: `系统任务已派发，企微确认结果待核实：${error.message || "请稍后刷新"}`,
        };
      }
      if (nativeResult.value.state === "created")
        ElMessage.success("原生待办已送达责任人");
    }
    await run(load);
  } catch (error: any) {
    if (error !== "cancel" && error !== "close")
      formError.value = error.message || "派发失败，请检查后重试";
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
        <p class="dispatch-kicker">任务协作 / 直接派发</p>
        <h1>直接派发任务</h1>
        <p>明确工作、指定责任人，让每一项安排都有跟进。</p>
      </div>
      <RouterLink
        v-if="options?.can_assign"
        :to="{
          path: '/task-overview',
          query: { source: 'direct', issued_by_me: '1' },
        }"
      >
        <el-button :icon="Document">任务总览</el-button>
      </RouterLink>
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
      title="当前岗位没有派发权限，如岗位信息有误，请联系管理员同步企业微信通讯录。"
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
                <p>填写安排，确认后通知责任人</p>
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
                :rows="4"
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
                <el-select
                  v-model="form.owner_ids"
                  multiple
                  filterable
                  collapse-tags
                  collapse-tags-tooltip
                  :max-collapse-tags="2"
                  placeholder="搜索姓名，可选择多人"
                  aria-label="选择责任人"
                >
                  <el-option
                    v-for="person in allowed"
                    :key="person.id"
                    :value="person.id"
                    :label="
                      person.name +
                      (person.department_id
                        ? ' · ' + departmentPath(person.department_id)
                        : '')
                    "
                  />
                </el-select>
              </el-form-item>
              <el-form-item
                label="验证人"
                required
                :error="fieldErrors.verifier_id"
              >
                <el-select
                  v-model="form.verifier_id"
                  filterable
                  placeholder="选择完成后的验证人"
                  aria-label="选择验证人"
                >
                  <el-option
                    v-if="state.me && !people.has(state.me.id)"
                    :value="state.me.id"
                    :label="state.me.name + ' · 派发人'"
                  />
                  <el-option
                    v-for="person in allowed"
                    :key="person.id"
                    :value="person.id"
                    :label="
                      person.name +
                      (person.department_id
                        ? ' · ' + departmentPath(person.department_id)
                        : '')
                    "
                  />
                </el-select>
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
              <p>
                <el-icon><Checked /></el-icon>完成后由验证人确认关闭
              </p>
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
        <aside class="dispatch-preview panel" aria-label="派发预览">
          <div class="dispatch-preview-heading">
            <h2>派发预览</h2>
            <span>实时预览</span>
          </div>
          <div class="dispatch-preview-body">
            <span class="dispatch-preview-label">任务安排</span>
            <p
              class="dispatch-preview-content"
              :class="{ 'is-empty': !form.content.trim() }"
            >
              {{
                form.content.trim() ||
                "填写任务内容后，这里会显示本次派发的安排。"
              }}
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
          <div class="dispatch-preview-footer">
            <span>派发</span><el-icon><ArrowRight /></el-icon><span>执行</span
            ><el-icon><ArrowRight /></el-icon><span>验证关闭</span>
          </div>
        </aside>
      </div>
      <section v-if="savedTask" class="dispatch-result" aria-live="polite">
        <div class="dispatch-result-heading">
          <el-icon><Checked /></el-icon>
          <div>
            <strong>任务已派发</strong>
            <p>
              {{
                nativeResult?.message ||
                "应用消息排队发送中，责任人可在系统查看任务。"
              }}
            </p>
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
      <section class="panel dispatch-records">
        <header class="dispatch-records-heading">
          <div>
            <h2>
              我派发的任务 <span class="count">{{ rows.length }}</span>
            </h2>
            <p>{{ openCount }} 项正在跟进</p>
          </div>
          <div class="dispatch-records-actions">
            <el-input
              v-model="search"
              placeholder="搜索任务、编号或责任人"
              clearable
              aria-label="搜索派发记录"
            /><el-button :icon="Refresh" :loading="loading" @click="run(load)"
              >刷新</el-button
            >
          </div>
        </header>
        <el-table
          v-loading="loading"
          :data="filteredRows"
          :empty-text="
            loadError
              ? '加载失败，请重试'
              : search
                ? '没有匹配的任务'
                : '还没有派发记录，从上方创建第一项任务'
          "
        >
          <el-table-column label="任务内容" min-width="300"
            ><template #default="{ row }"
              ><RouterLink
                class="table-link dispatch-task-link"
                :to="{ path: '/tasks', query: { id: row.id } }"
                >{{ row.content }}</RouterLink
              >
              <div class="dispatch-task-code">{{ row.code }}</div></template
            ></el-table-column
          >
          <el-table-column label="责任人" min-width="160"
            ><template #default="{ row }">{{
              row.owner_names.join("、")
            }}</template></el-table-column
          >
          <el-table-column label="计划完成" width="145"
            ><template #default="{ row }"
              ><span :class="{ muted: !row.due_date }">{{
                row.due_date || "未设期限"
              }}</span></template
            ></el-table-column
          >
          <el-table-column label="状态" width="110"
            ><template #default="{ row }"
              ><el-tag v-if="row.withdrawn_at" type="info">已撤回</el-tag
              ><StatusTag v-else :value="row.status" /></template
          ></el-table-column>
          <el-table-column label="操作" width="175"
            ><template #default="{ row }"
              ><div class="dispatch-row-actions">
                <RouterLink
                  class="dispatch-detail-link"
                  :to="{ path: '/tasks', query: { id: row.id } }"
                  >查看详情</RouterLink
                ><RouterLink
                  v-if="
                    options?.wecom_bound &&
                    options?.native_todo_auto_confirm &&
                    !row.closed_at &&
                    !row.withdrawn_at
                  "
                  class="dispatch-detail-link"
                  :to="{
                    path: '/tasks',
                    query: { id: row.id, native_confirm: '1' },
                  }"
                  >企微待办</RouterLink
                >
              </div></template
            ></el-table-column
          >
        </el-table>
      </section>
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
.dispatch-kicker {
  color: var(--dispatch-muted);
  font-size: 12px;
  margin: 0 0 8px;
}
.dispatch-heading h1 {
  font-size: 26px;
  letter-spacing: 0;
  margin: 0 0 8px;
}
.dispatch-heading > div > p:last-child {
  color: var(--dispatch-muted);
  font-size: 14px;
  margin: 0;
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
.dispatch-preview,
.dispatch-records {
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
.dispatch-preview-heading h2,
.dispatch-records-heading h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 650;
  color: var(--dispatch-text);
}
.dispatch-title p,
.dispatch-records-heading p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--dispatch-muted);
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
  justify-content: space-between;
  gap: 16px;
  width: auto;
  padding: 20px 0 24px;
  border-top: 1px solid var(--dispatch-line);
  margin-top: 22px;
}
.dispatch-submit p {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--dispatch-muted);
  margin: 0;
  min-width: 0;
}
.dispatch-submit p .el-icon {
  color: #42826d;
}
.dispatch-submit .el-button {
  min-width: 162px;
  min-height: 44px;
  margin: 0;
}
.dispatch-preview {
  position: sticky;
  top: 24px;
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
.dispatch-preview-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 16px;
  background: var(--dispatch-tint);
  border-top: 1px solid var(--dispatch-line);
  color: #617a9c;
  font-size: 12px;
}
.dispatch-preview-footer .el-icon {
  color: #a2b4cc;
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
.dispatch-records {
  margin-top: 24px;
}
.dispatch-records-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 22px 28px;
}
.dispatch-records-heading h2 {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dispatch-records-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.dispatch-records-actions .el-input {
  width: 255px;
}
.dispatch-records-actions .el-button {
  margin: 0;
}
.dispatch-records :deep(.el-table__cell) {
  padding-left: 18px;
  padding-right: 8px;
}
.dispatch-task-link {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  line-height: 1.65;
  font-weight: 500;
}
.dispatch-task-code {
  color: #8b99ac;
  font-size: 11px;
  margin-top: 5px;
  font-variant-numeric: tabular-nums;
}
.dispatch-detail-link {
  color: var(--dispatch-blue);
  font-size: 13px;
}
.dispatch-row-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
@media (max-width: 1150px) {
  .dispatch-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .dispatch-preview {
    display: none;
  }
}
@media (max-width: 680px) {
  .dispatch-heading {
    align-items: flex-start;
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
  .dispatch-records-heading {
    padding: 18px;
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
  }
  .dispatch-records-actions .el-input {
    width: 100%;
  }
  .dispatch-records-actions {
    min-width: 0;
  }
  .dispatch-result {
    padding: 18px;
  }
  .dispatch-result-heading {
    flex-wrap: wrap;
  }
}
</style>
