<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  api,
  state,
  run,
  statuses,
  query,
  person,
  download,
  clientId,
  departmentPath,
} from "../api";
import StatusTag from "../components/StatusTag.vue";
import PeopleSelect from "../components/PeopleSelect.vue";
import RecurrencePanel from "../components/RecurrencePanel.vue";
import NativeTodoPanel from "../components/NativeTodoPanel.vue";
const route = useRoute(),
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
const transferDialog = ref(false),
  transferPeople = ref<any[]>([]);
const transferForm = ref({ issuer_id: "", reason: "" });
const withdrawalDialog = ref(false);
const withdrawalForm = ref({ reason: "", stop_recurrence: false });
let loadGeneration = 0;
let detailGeneration = 0;
async function load() {
  const generation = ++loadGeneration;
  busy.value = true;
  loadError.value = "";
  try {
    const result = await api(
      "/tasks?" + query({ mine: mine.value, state: status.value, q: q.value }),
    );
    if (generation === loadGeneration) rows.value = result;
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
async function open(id: string) {
  const generation = ++detailGeneration;
  let result;
  try {
    result = await api("/tasks/" + id);
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
}
onBeforeUnmount(() => {
  detailGeneration += 1;
});
const canApply = computed(
  () =>
    selected.value?.owner_ids.includes(state.me.id) &&
    !selected.value?.closed_at &&
    !selected.value?.requests.some((x) => x.state === "pending"),
);
async function apply() {
  sending.value = true;
  try {
    await api("/tasks/" + selected.value.id + "/completion", "POST", {
      expected_version: selected.value.version,
      idempotency_key: key.value,
      explanation: explanation.value,
      evidence_ids: evidence.value.map((x) => x.id),
    });
    ElMessage.success("完成申请已提交，等待验证");
    await open(selected.value.id);
    await load();
  } finally {
    sending.value = false;
  }
}
async function upload(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  evidence.value.push(
    await api("/files?task_id=" + selected.value.id, "POST", form),
  );
}
async function saveChange() {
  if (!change.value.due_date) {
    ElMessage.error("请选择计划完成时间");
    return;
  }
  sending.value = true;
  try {
    await api("/tasks/" + selected.value.id, "PUT", change.value);
    await open(selected.value.id);
    await load();
    ElMessage.success("任务已更新，历史考核保持不变");
  } finally {
    sending.value = false;
  }
}
async function openTransfer() {
  transferPeople.value = await api(
    `/tasks/${selected.value.id}/transfer-options`,
  );
  transferForm.value = { issuer_id: "", reason: "" };
  transferDialog.value = true;
}
async function saveTransfer() {
  if (!transferForm.value.issuer_id || !transferForm.value.reason.trim()) {
    ElMessage.error("请选择新的派发人并填写移交原因");
    return;
  }
  const task = selected.value;
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
    await open(task.id);
    await load();
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
    await open(task.id);
    await load();
    ElMessage.success("任务已撤回，记录已保留");
  } finally {
    sending.value = false;
  }
}
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
      <p class="eyebrow">MY ACTIONS</p>
      <h1>我的待办</h1>
      <p class="muted">提交完成申请后，由验证人确认关闭。</p>
    </div>
  </section>
  <section class="panel filters filter-row">
    <el-switch
      v-model="mine"
      active-text="只看我的"
      @change="run(load)"
    /><el-select
      v-model="status"
      placeholder="全部状态"
      clearable
      aria-label="任务状态"
      @change="run(load)"
      ><el-option
        v-for="s in statuses"
        :key="s"
        :value="s"
        :label="s" /></el-select
    ><el-input
      v-model="q"
      placeholder="搜索任务内容或编号"
      aria-label="搜索任务"
      @keyup.enter="run(load)"
    /><el-button @click="run(load)">查询</el-button>
  </section>
  <el-alert v-if="loadError" type="error" :closable="false" show-icon>
    <template #title>待办列表加载失败：{{ loadError }}</template>
    <el-button size="small" @click="run(load)">重试加载</el-button>
  </el-alert>
  <div v-loading="busy" class="task-grid">
    <button
      v-for="t in rows"
      :key="t.id"
      class="task-card"
      @click="run(() => open(t.id))"
    >
      <div class="task-card-top">
        <span class="muted small">{{ t.code }}</span
        ><el-tag v-if="t.withdrawn_at" type="info">已撤回</el-tag
        ><el-tag v-else-if="!t.due_date && t.status === '未到期'" type="info"
          >未设期限</el-tag
        >
        <StatusTag v-else :value="t.status" />
      </div>
      <h2>{{ t.content }}</h2>
      <p class="muted">
        {{
          t.issuer_name
            ? `由 ${t.issuer_name} 直接派发`
            : t.category || "会议工作事项"
        }}
      </p>
      <dl>
        <div>
          <dt>截止时间</dt>
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
      <div class="task-card-bottom">
        {{
          t.latest_request?.state === "pending"
            ? "已提交申请 · 待验证"
            : "查看详情与进展"
        }}
        <span>→</span>
      </div></button
    ><el-empty
      v-if="!busy && !rows.length"
      :description="loadError ? '加载失败，请重试' : '当前筛选下没有待办'"
    />
  </div>
  <el-drawer
    :model-value="!!selected"
    @close="closeDetail"
    title="任务详情"
    size="min(680px,100vw)"
    ><template v-if="selected"
      ><div class="task-card-top">
        <span class="muted">{{ selected.code }} · V{{ selected.version }}</span
        ><el-tag v-if="selected.withdrawn_at" type="info">已撤回</el-tag
        ><el-tag
          v-else-if="!selected.due_date && selected.status === '未到期'"
          type="info"
          >未设期限</el-tag
        >
        <StatusTag v-else :value="selected.status" />
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
        v-if="!selected.source_meeting_id"
        :key="selected.id"
        :task="selected"
        :issuer-context="selected.issuer_id === state.me?.id"
        :auto-confirm="
          route.query.native_confirm === '1' && route.query.id === selected.id
        "
      />
      <RecurrencePanel
        :task="selected"
        @changed="run(() => open(selected.id))"
      />
      <h3>申请与验证记录</h3>
      <el-empty
        v-if="!selected.requests.length"
        description="暂无申请记录"
        :image-size="60"
      /><el-timeline
        ><el-timeline-item
          v-for="r in selected.requests"
          :key="r.id"
          :timestamp="new Date(r.created_at).toLocaleString('zh-CN')"
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
          selected.can_edit &&
          !selected.source_meeting_id &&
          !selected.closed_at
        "
        type="danger"
        plain
        @click="
          withdrawalForm = { reason: '', stop_recurrence: false };
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
      <h3>版本记录</h3>
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
    <p class="muted">
      新派发人必须有权限管理当前责任人和验证人；存在周期规则时，后续周期也一并移交。已经生成的其他任务保留原派发人。
    </p>
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
      <el-checkbox
        v-if="selected?.recurrence_key && selected?.can_manage_recurrence"
        v-model="withdrawalForm.stop_recurrence"
        >同时停止后续周期任务（已生成的其他任务继续保留）</el-checkbox
      >
    </el-form>
    <template #footer
      ><el-button @click="withdrawalDialog = false">取消</el-button
      ><el-button type="danger" :loading="sending" @click="run(withdrawTask)"
        >确认撤回</el-button
      ></template
    >
  </el-dialog>
</template>
