<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { ArrowLeft } from "@element-plus/icons-vue";
import { beijingTime } from "../time";
import {
  api,
  state,
  run,
  query,
  person,
  download,
  clientId,
  departmentPath,
} from "../api";
import StatusTag from "../components/StatusTag.vue";
import PeopleSelect from "../components/PeopleSelect.vue";
import { isEmployeeAccount, withDirectoryDetails } from "../people";
import NativeTodoPanel from "../components/NativeTodoPanel.vue";
const route = useRoute(),
  router = useRouter(),
  rows = ref<any[]>([]),
  busy = ref(false),
  loadError = ref(""),
  mine = ref(true),
  status = ref(""),
  q = ref(""),
  selected = ref<any>(null),
  explanation = ref(""),
  evidence = ref<any[]>([]),
  sending = ref(false),
  change = ref<any>(null),
  key = ref(clientId());
const reminderDay = computed(() =>
  typeof route.query.reminder_day === "string" ? route.query.reminder_day : "",
);
async function showAllTasks() {
  await router.replace({ path: "/tasks" });
}
const transferDialog = ref(false),
  transferPeople = ref<any[]>([]);
const transferForm = ref({ issuer_id: "", reason: "" });
const withdrawalDialog = ref(false);
const withdrawalForm = ref({ reason: "" });
const page = ref(1),
  pageSize = ref(20),
  total = ref(0);
let loadGeneration = 0;
let detailGeneration = 0;
let disposed = false;
const inboxStatuses = [
  "到期未验证",
  "未到期验证",
  "未完成",
  "已完成",
  "未到期",
  "不统计",
];
const taskStatus = (task: any) => task.todo_status || task.status;
const meetingTitle = (task: any) =>
  task.source_meeting_id
    ? task.inbox_meeting_title || task.source_meeting_title || "来源会议不可见"
    : "直接派发";
function search() {
  page.value = 1;
  return load();
}
async function load(background = false) {
  const generation = ++loadGeneration;
  busy.value = true;
  loadError.value = "";
  try {
    const result = await api(
      "/tasks?" +
        query({
          mine: reminderDay.value ? true : mine.value,
          state: status.value,
          q: q.value,
          current: !reminderDay.value,
          reminder_day: reminderDay.value,
          offset: (page.value - 1) * pageSize.value,
          limit: pageSize.value,
        }),
      "GET",
      undefined,
      { background },
    );
    if (generation === loadGeneration) {
      total.value = Array.isArray(result) ? result.length : result.total;
      rows.value = (Array.isArray(result) ? result : result.items).filter(
        (task: any) =>
          !task.closed_at && !task.withdrawn_at && taskStatus(task) !== "关闭",
      );
    }
  } catch (error: any) {
    if (generation === loadGeneration) {
      rows.value = [];
      loadError.value = error.message || "服务暂时不可用";
    }
    throw error;
  } finally {
    if (generation === loadGeneration) busy.value = false;
  }
}
async function open(id: string, background = false) {
  const generation = ++detailGeneration;
  let result;
  try {
    result = await api("/tasks/" + id, "GET", undefined, { background });
  } catch (error) {
    if (generation === detailGeneration) throw error;
    return;
  }
  if (generation !== detailGeneration) return;
  selected.value = result;
  explanation.value = "";
  evidence.value = [];
  change.value = null;
  key.value = clientId();
}
function closeDetail() {
  detailGeneration += 1;
  selected.value = null;
  transferDialog.value = withdrawalDialog.value = false;
  change.value = null;
  explanation.value = "";
  evidence.value = [];
  if (route.query.id || route.query.native_confirm) {
    const { id, native_confirm, ...rest } = route.query;
    run(() => router.replace({ path: route.path, query: rest }));
  }
}
onBeforeUnmount(() => {
  disposed = true;
  loadGeneration += 1;
  detailGeneration += 1;
});
async function refreshAfterAction(id: string, generation: number) {
  if (disposed) return;
  if (generation === detailGeneration) await open(id, true);
  if (!disposed) await load(true);
}
const canApply = computed(
  () =>
    selected.value?.owner_ids.includes(state.me.id) &&
    !selected.value?.closed_at &&
    !selected.value?.requests.some((x) => x.state === "pending"),
);
async function apply() {
  const task = selected.value;
  const generation = detailGeneration;
  sending.value = true;
  try {
    await api("/tasks/" + task.id + "/completion", "POST", {
      expected_version: task.version,
      idempotency_key: key.value,
      explanation: explanation.value,
      evidence_ids: evidence.value.map((x) => x.id),
    });
    ElMessage.success("完成申请已提交，等待验证");
    await refreshAfterAction(task.id, generation);
  } finally {
    sending.value = false;
  }
}
async function upload(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (!f) return;
  const task = selected.value;
  const generation = detailGeneration;
  const form = new FormData();
  form.append("file", f);
  const uploaded = await api("/files?task_id=" + task.id, "POST", form);
  if (!disposed && generation === detailGeneration)
    evidence.value.push(uploaded);
}
async function saveChange() {
  if (!change.value.due_date) {
    ElMessage.error("请选择计划完成时间");
    return;
  }
  const task = selected.value;
  const generation = detailGeneration;
  sending.value = true;
  try {
    await api("/tasks/" + task.id, "PUT", change.value);
    await refreshAfterAction(task.id, generation);
    ElMessage.success("任务已更新，历史考核保持不变");
  } finally {
    sending.value = false;
  }
}
async function openTransfer() {
  const task = selected.value;
  const generation = detailGeneration;
  const people = await api(`/tasks/${task.id}/transfer-options`);
  if (disposed || generation !== detailGeneration) return;
  transferPeople.value = withDirectoryDetails(
    people,
    state.directory.users,
  ).filter(isEmployeeAccount);
  transferForm.value = { issuer_id: "", reason: "" };
  transferDialog.value = true;
}
async function saveTransfer() {
  if (!transferForm.value.issuer_id || !transferForm.value.reason.trim()) {
    ElMessage.error("请选择新的派发人并填写移交原因");
    return;
  }
  const task = selected.value;
  const generation = detailGeneration;
  await ElMessageBox.confirm(
    "确定移交此任务吗？当前待验证申请将失效，需要重新提交。",
    "确认移交",
    { type: "warning" },
  );
  sending.value = true;
  try {
    await api(`/tasks/${task.id}/transfer`, "POST", {
      ...transferForm.value,
      expected_version: task.version,
    });
    transferDialog.value = false;
    await refreshAfterAction(task.id, generation);
    ElMessage.success("任务已移交");
  } finally {
    sending.value = false;
  }
}
async function withdrawTask() {
  if (!withdrawalForm.value.reason.trim()) {
    ElMessage.error("请填写撤回原因");
    return;
  }
  const task = selected.value;
  const generation = detailGeneration;
  await ElMessageBox.confirm(
    "确定撤回此任务吗？责任人将停止执行，待验证申请将失效，操作记录会保留。",
    "确认撤回",
    { type: "warning" },
  );
  sending.value = true;
  try {
    await api(`/tasks/${task.id}/withdraw`, "POST", {
      ...withdrawalForm.value,
      expected_version: task.version,
    });
    withdrawalDialog.value = false;
    await refreshAfterAction(task.id, generation);
    ElMessage.success("任务已撤回，记录已保留");
  } finally {
    sending.value = false;
  }
}
watch(reminderDay, () => {
  mine.value = true;
  q.value = status.value = "";
  closeDetail();
  run(search);
});
watch(
  () => route.query.id,
  (id) => {
    if (id) run(() => open(String(id)));
  },
);
onMounted(() =>
  run(async () => {
    await load();
    if (route.query.id) await open(String(route.query.id));
  }),
);
</script>
<template>
  <section class="page-heading">
    <div>
      <h1>{{ reminderDay ? `${reminderDay} 到期提醒` : "我的待办" }}</h1>
    </div>
    <el-button v-if="reminderDay" @click="run(showAllTasks)"
      >全部待办</el-button
    >
  </section>
  <section class="panel filters filter-row">
    <el-switch
      v-if="!reminderDay"
      v-model="mine"
      active-text="只看我的"
      @change="run(search)"
    /><el-select
      v-model="status"
      placeholder="全部状态"
      clearable
      aria-label="任务状态"
      @change="run(search)"
      ><el-option
        v-for="s in inboxStatuses"
        :key="s"
        :value="s"
        :label="s" /></el-select
    ><el-input
      v-model="q"
      placeholder="搜索会议日期、名称或任务内容"
      aria-label="搜索任务"
      @keyup.enter="run(search)"
    /><el-button @click="run(search)">查询</el-button>
  </section>
  <el-alert v-if="loadError" type="error" :closable="false" show-icon>
    <template #title>待办列表加载失败：{{ loadError }}</template>
    <el-button size="small" @click="run(load)">重试加载</el-button>
  </el-alert>
  <section
    v-loading="busy"
    class="panel task-list-panel"
    aria-label="待办任务列表"
  >
    <div class="task-list-heading">
      <div>
        <h2>任务列表</h2>
      </div>
      <span class="muted small" role="status">{{
        busy
          ? "正在加载任务…"
          : loadError
            ? "任务读取失败"
            : `共 ${total} 条任务`
      }}</span>
    </div>
    <div
      v-if="rows.length"
      class="task-table-scroll"
      role="region"
      aria-label="待办任务数据"
      tabindex="0"
    >
      <table class="task-table">
        <caption class="task-table-caption">
          待办任务列表，可点击任务内容或查看详情办理任务
        </caption>
        <colgroup>
          <col class="task-meeting-date-column" />
          <col class="task-subject-column" />
          <col class="task-content-column" />
          <col class="task-date-column" />
          <col class="task-people-column" />
          <col class="task-verifier-column" />
          <col class="task-status-column" />
          <col class="task-action-column" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">会议日期</th>
            <th scope="col">会议名称</th>
            <th scope="col">任务内容</th>
            <th scope="col">截止日期</th>
            <th scope="col">责任人</th>
            <th scope="col">验证人</th>
            <th scope="col">状态</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in rows" :key="t.id" class="task-row">
            <td class="task-date">{{ t.inbox_meeting_date || "—" }}</td>
            <td class="task-subject">
              {{ meetingTitle(t) }}
              <span v-if="t.issuer_name" class="task-source"
                >由 {{ t.issuer_name }} 派发</span
              >
            </td>
            <td>
              <button
                class="task-title-button"
                type="button"
                @click="run(() => open(t.id))"
              >
                {{ t.content }}
              </button>
            </td>
            <td class="task-date">{{ t.due_date || "未设置" }}</td>
            <td>{{ t.owner_names.join("、") }}</td>
            <td>{{ t.verifier_name }}</td>
            <td>
              <el-tag v-if="t.withdrawn_at" type="info">已撤回</el-tag>
              <el-tag
                v-else-if="!t.due_date && taskStatus(t) === '未到期'"
                type="info"
                >未设期限</el-tag
              >
              <StatusTag v-else :value="taskStatus(t)" />
              <span
                v-if="t.latest_request?.state === 'pending'"
                class="task-pending"
                >已提交申请 · 待验证</span
              >
            </td>
            <td>
              <el-button
                link
                type="primary"
                :aria-label="`查看详情：${t.content}`"
                @click="run(() => open(t.id))"
                >查看详情</el-button
              >
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <ul v-if="rows.length" class="task-mobile-list" aria-label="待办任务">
      <li v-for="t in rows" :key="t.id" class="task-mobile-row">
        <div class="task-mobile-heading">
          <button
            class="task-title-button"
            type="button"
            @click="run(() => open(t.id))"
          >
            {{ t.content }}
          </button>
          <el-tag v-if="t.withdrawn_at" type="info">已撤回</el-tag>
          <el-tag
            v-else-if="!t.due_date && taskStatus(t) === '未到期'"
            type="info"
            >未设期限</el-tag
          >
          <StatusTag v-else :value="taskStatus(t)" />
        </div>
        <p v-if="t.issuer_name" class="task-source">
          由 {{ t.issuer_name }} 派发
        </p>
        <dl class="task-mobile-meta">
          <div>
            <dt>会议日期</dt>
            <dd>{{ t.inbox_meeting_date || "—" }}</dd>
          </div>
          <div>
            <dt>会议名称</dt>
            <dd>{{ meetingTitle(t) }}</dd>
          </div>
          <div>
            <dt>截止日期</dt>
            <dd>{{ t.due_date || "未设置" }}</dd>
          </div>
          <div>
            <dt>责任人</dt>
            <dd>{{ t.owner_names.join("、") }}</dd>
          </div>
          <div>
            <dt>验证人</dt>
            <dd>{{ t.verifier_name }}</dd>
          </div>
        </dl>
        <div class="task-mobile-footer">
          <span
            v-if="t.latest_request?.state === 'pending'"
            class="task-pending"
            >已提交申请 · 待验证</span
          >
          <el-button
            link
            type="primary"
            :aria-label="`查看详情：${t.content}`"
            @click="run(() => open(t.id))"
            >查看详情</el-button
          >
        </div>
      </li>
    </ul>
    <el-empty
      v-if="!busy && !rows.length"
      :description="loadError ? '加载失败，请重试' : '当前筛选下没有待办'"
    />
    <el-pagination
      v-if="total"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :page-sizes="[20, 50, 100]"
      :total="total"
      layout="total, sizes, prev, pager, next"
      aria-label="待办分页"
      @current-change="run(() => load())"
      @size-change="
        page = 1;
        run(() => load());
      "
    />
  </section>
  <el-drawer
    :model-value="!!selected"
    @close="closeDetail"
    title="任务详情"
    size="min(680px,100vw)"
  >
    <template #header="{ titleId, titleClass }">
      <div class="task-detail-heading">
        <h2 :id="titleId" :class="titleClass">任务详情</h2>
        <el-button size="small" :icon="ArrowLeft" @click="closeDetail"
          >返回上一页</el-button
        >
      </div>
    </template>
    <template v-if="selected"
      ><div class="task-card-top">
        <span class="muted"
          >{{ selected.inbox_meeting_date || "" }}
          {{ meetingTitle(selected) }}</span
        ><el-tag v-if="selected.withdrawn_at" type="info">已撤回</el-tag
        ><el-tag
          v-else-if="!selected.due_date && taskStatus(selected) === '未到期'"
          type="info"
          >未设期限</el-tag
        >
        <StatusTag v-else :value="taskStatus(selected)" />
      </div>
      <h2>{{ selected.content }}</h2>
      <section v-if="selected.requirements" class="panel section-body">
        <h3>补充说明</h3>
        <p style="white-space: pre-wrap; overflow-wrap: anywhere">
          {{ selected.requirements }}
        </p>
      </section>
      <el-alert
        v-if="selected.withdrawn_at"
        :title="'撤回原因：' + selected.withdrawal_reason"
        type="info"
        :closable="false"
      />
      <el-descriptions :column="1" border
        ><el-descriptions-item label="责任人">{{
          selected.owner_ids.map(person).join("、")
        }}</el-descriptions-item
        ><el-descriptions-item label="验证人">{{
          person(selected.verifier_id)
        }}</el-descriptions-item
        ><el-descriptions-item label="截止日期">{{
          selected.due_date || "未设置"
        }}</el-descriptions-item
        ><el-descriptions-item label="输出物">{{
          selected.deliverable || "—"
        }}</el-descriptions-item></el-descriptions
      ><RouterLink
        v-if="selected.source_meeting_id && selected.can_view_source"
        class="back-link"
        :to="'/meetings/' + selected.source_meeting_id"
        >查看来源会议 →</RouterLink
      >
      <p v-else-if="!selected.source_meeting_id" class="muted">
        {{ selected.issuer_name }}直接派发
      </p>
      <section v-if="canApply" class="panel section-body">
        <h3>已完成申请</h3>
        <p class="muted small">提交后由验证人确认关闭任务。</p>
        <label for="explanation">完成说明</label
        ><el-input
          id="explanation"
          v-model="explanation"
          type="textarea"
          :rows="4"
          placeholder="说明已完成的工作和相关依据"
        /><label class="upload-button"
          >添加完成证据<input type="file" @change="(e) => run(() => upload(e))"
        /></label>
        <p v-for="f in evidence" :key="f.id">{{ f.filename }}</p>
        <el-button
          type="primary"
          :loading="sending"
          :disabled="!explanation.trim()"
          @click="run(apply)"
          >提交完成申请</el-button
        >
      </section>
      <NativeTodoPanel
        :key="selected.id"
        compact
        :task="selected"
        :issuer-context="
          selected.source_meeting_id
            ? selected.can_edit
            : selected.issuer_id === state.me?.id
        "
        :auto-confirm="
          route.query.native_confirm === '1' && route.query.id === selected.id
        "
      />
      <h3>申请与验证记录</h3>
      <p v-if="!selected.requests.length" class="muted small">暂无申请记录</p>
      <el-timeline
        ><el-timeline-item
          v-for="r in selected.requests"
          :key="r.id"
          :timestamp="beijingTime(r.created_at)"
          ><strong>{{
            {
              pending: "待验证",
              approved: "验证通过",
              rejected: "已驳回",
              invalidated: "申请失效",
            }[r.state]
          }}</strong>
          <p>{{ r.explanation }}</p>
          <p class="muted">{{ r.opinion }}</p>
          <el-button
            v-for="id in r.evidence_ids"
            :key="id"
            link
            @click="run(() => download('/files/' + id, '完成证据'))"
            >下载完成证据</el-button
          ></el-timeline-item
        ></el-timeline
      ><el-button
        v-if="selected.can_edit && !selected.closed_at && !change"
        @click="
          change = {
            ...selected,
            expected_version: selected.version,
            reason: '',
          }
        "
        >调整期限 / 责任人</el-button
      ><el-button v-if="selected.can_transfer" @click="run(openTransfer)"
        >移交派发人</el-button
      >
      <el-button
        v-if="
          selected.can_withdraw &&
          !selected.source_meeting_id &&
          !selected.closed_at
        "
        type="danger"
        plain
        @click="
          withdrawalForm = { reason: '' };
          withdrawalDialog = true;
        "
        >撤回任务</el-button
      >
      <el-form v-if="change" label-position="top" class="panel section-body"
        ><el-alert
          title="调整任务内容、责任人或期限后，待验证申请将失效，责任人需要重新提交。"
          type="info"
          :closable="false"
        />
        <el-form-item label="任务内容"
          ><el-input v-model="change.content" type="textarea" /></el-form-item
        ><el-form-item label="补充说明"
          ><el-input
            v-model="change.requirements"
            type="textarea"
            :rows="3"
            placeholder="补充验收标准或注意事项" /></el-form-item
        ><el-form-item label="输出物"
          ><el-input v-model="change.deliverable" /></el-form-item
        ><el-form-item label="计划完成时间" required
          ><el-date-picker
            v-model="change.due_date"
            value-format="YYYY-MM-DD"
            :clearable="false"
            aria-required="true"
            placeholder="请选择计划完成时间" /></el-form-item
        ><el-form-item label="责任人"
          ><PeopleSelect
            v-model="change.owner_ids"
            multiple
            :allowed-ids="
              selected.source_meeting_id
                ? undefined
                : selected.allowed_owner_ids
            " /></el-form-item
        ><el-form-item label="验证人"
          ><PeopleSelect
            v-model="change.verifier_id"
            :allowed-ids="
              selected.source_meeting_id
                ? undefined
                : [...selected.allowed_owner_ids, state.me.id]
            " /></el-form-item
        ><el-form-item label="变更原因"
          ><el-input v-model="change.reason" /></el-form-item
        ><el-button @click="change = null">取消</el-button
        ><el-button type="primary" :loading="sending" @click="run(saveChange)"
          >保存变更</el-button
        ></el-form
      >
      <h3 v-if="selected.versions?.length">版本记录</h3>
      <p v-for="v in selected.versions" :key="v.id" class="muted">
        V{{ v.number }} · {{ v.snapshot.due_date }} · {{ v.reason }}
      </p></template
    ></el-drawer
  >
  <el-dialog
    v-model="transferDialog"
    title="移交派发人"
    width="min(520px,calc(100vw - 32px))"
  >
    <p class="muted">新派发人必须有权限管理当前责任人和验证人。</p>
    <el-form label-position="top" :disabled="sending">
      <el-form-item label="新派发人"
        ><el-select
          v-model="transferForm.issuer_id"
          filterable
          placeholder="选择人员"
          style="width: 100%"
          ><el-option
            v-for="p in transferPeople"
            :key="p.id"
            :value="p.id"
            :label="`${p.name} · ${p.position || '管理员'} · ${p.department_id ? departmentPath(p.department_id) : '未分配部门'}`" /></el-select
      ></el-form-item>
      <el-form-item label="移交原因"
        ><el-input
          v-model="transferForm.reason"
          type="textarea"
          maxlength="1000"
      /></el-form-item>
    </el-form>
    <template #footer
      ><el-button @click="transferDialog = false">取消</el-button
      ><el-button type="primary" :loading="sending" @click="run(saveTransfer)"
        >确认移交</el-button
      ></template
    >
  </el-dialog>
  <el-dialog
    v-model="withdrawalDialog"
    title="撤回任务"
    width="min(520px,calc(100vw - 32px))"
  >
    <el-form label-position="top" :disabled="sending">
      <el-form-item label="撤回原因"
        ><el-input
          v-model="withdrawalForm.reason"
          type="textarea"
          maxlength="1000"
          placeholder="填写误派或取消安排的原因"
      /></el-form-item>
    </el-form>
    <template #footer
      ><el-button @click="withdrawalDialog = false">取消</el-button
      ><el-button type="danger" :loading="sending" @click="run(withdrawTask)"
        >确认撤回</el-button
      ></template
    >
  </el-dialog>
</template>

<style scoped>
.task-detail-heading {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
}
.task-detail-heading h2 {
  margin: 0;
  font-size: 18px;
}
.task-list-panel {
  overflow: hidden;
}
.task-list-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 24px;
  border-bottom: 1px solid var(--line);
}
.task-list-heading h2 {
  margin: 0;
  font-size: 16px;
}
.task-list-heading p {
  margin: 6px 0 0;
}
.task-table-scroll {
  overflow-x: auto;
}
.task-table-scroll:focus-visible,
.task-title-button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.task-table {
  width: 100%;
  min-width: 980px;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 14px;
  line-height: 1.6;
}
.task-table-caption {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.task-content-column {
  width: auto;
}
.task-subject-column {
  width: 18%;
}
.task-date-column {
  width: 106px;
}
.task-meeting-date-column {
  width: 120px;
}
.task-people-column {
  width: 8%;
}
.task-verifier-column {
  width: 6%;
}
.task-status-column {
  width: 100px;
}
.task-action-column {
  width: 86px;
}
.task-table th,
.task-table td {
  padding: 16px 14px;
  text-align: left;
  vertical-align: middle;
  overflow-wrap: anywhere;
}
.task-table th {
  padding-block: 12px;
  background: #f5f8fc;
  color: #52657d;
  font-size: 13px;
  font-weight: 600;
}
.task-table th:first-child,
.task-table td:first-child {
  padding-left: 24px;
}
.task-table td {
  border-top: 1px solid var(--line);
}
.task-row:hover,
.task-row:focus-within {
  background: #f7faff;
}
.task-source {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.task-date {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.task-title-button {
  display: block;
  width: 100%;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  font-weight: 600;
  line-height: 1.6;
  text-align: left;
  overflow-wrap: anywhere;
}
.task-title-button:hover {
  color: var(--el-color-primary);
}
.task-pending {
  display: block;
  margin-top: 5px;
  color: #8a5817;
  font-size: 12px;
  line-height: 1.5;
}
.task-mobile-list {
  display: none;
}
@media (max-width: 760px) {
  .task-list-heading {
    padding: 16px;
  }
  .task-table-scroll {
    display: none;
  }
  .task-mobile-list {
    display: block;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .task-mobile-row {
    padding: 16px;
  }
  .task-mobile-row + .task-mobile-row {
    border-top: 1px solid var(--line);
  }
  .task-mobile-heading {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }
  .task-mobile-heading > .task-title-button {
    flex: 1;
    min-width: 0;
  }
  .task-mobile-heading > :last-child {
    flex-shrink: 0;
  }
  .task-mobile-row p {
    margin: 5px 0 0;
    font-size: 12px;
  }
  .task-mobile-meta {
    display: grid;
    gap: 5px;
    margin: 12px 0 0;
    font-size: 13px;
  }
  .task-mobile-meta > div {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr);
    gap: 10px;
  }
  .task-mobile-meta dt {
    color: var(--muted);
  }
  .task-mobile-meta dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .task-mobile-footer {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
  }
  .task-mobile-footer .task-pending {
    flex: 1;
    margin-top: 0;
  }
}
</style>
