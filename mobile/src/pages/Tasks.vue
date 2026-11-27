<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  showConfirmDialog,
  showImagePreview,
  showSuccessToast,
  showToast,
} from "vant";
import {
  api,
  clientId,
  download,
  fetchAuthBlob,
  localDate,
  person,
  query,
  run,
  state,
} from "../api/client";
import { chineseError } from "../api/errorMessages";
import { confirmAssignedTodo } from "../api/nativeAssignment";
import { isEmployeeAccount, withDirectoryDetails } from "../api/people";
import { beijingTime } from "../api/time";
import { insideWeCom, viewWeComTodo } from "../api/wecom";
import PeoplePicker from "../components/PeoplePicker.vue";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();

const rows = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 20;
const busy = ref(false);
const refreshing = ref(false);
const loadError = ref("");
const mine = ref(true);
const status = ref("");
const q = ref("");

const selected = ref<any>(null);
const detailOpen = ref(false);
const applyOpen = ref(false);
const explanation = ref("");
const evidence = ref<any[]>([]);
const uploading = ref(false);
const sending = ref(false);
const idempotencyKey = ref(clientId());
const nativeTodo = ref<any>(null);

const editOpen = ref(false);
const editForm = ref<any>(null);
const transferOpen = ref(false);
const transferPeople = ref<any[]>([]);
const transferForm = ref({ issuer_id: "", reason: "" });
const withdrawOpen = ref(false);
const withdrawReason = ref("");
const actionSheetOpen = ref(false);

let loadGen = 0;
let detailGen = 0;
let disposed = false;

const reminderDay = computed(() =>
  typeof route.query.reminder_day === "string" ? route.query.reminder_day : "",
);
const historyView = computed(
  () => !reminderDay.value && route.query.view === "history",
);

const inboxStatuses = [
  "到期未验证",
  "未到期验证",
  "未完成",
  "已完成",
  "未到期",
  "不统计",
];
const statusChips = computed(() => {
  const list = historyView.value
    ? [...inboxStatuses, "关闭", "已撤回", "部分完成"]
    : inboxStatuses;
  return [
    { value: "", label: "全部" },
    ...list.map((v) => ({ value: v, label: v === "关闭" ? "已关闭" : v })),
  ];
});

const taskStatus = (task: any) => {
  const value = task.todo_status || task.status;
  return historyView.value && value === "关闭" ? "已关闭" : value;
};

const meetingTitle = (task: any) =>
  task.source_meeting_id
    ? task.inbox_meeting_title || task.source_meeting_title || "来源会议不可见"
    : "直接派发";

const accentBarColor = (task: any) => {
  const s = taskStatus(task);
  if (s === "未完成") return "#b91c1c";
  if (s === "已完成" || s === "已关闭") return "#15803d";
  if (task.has_pending_request || s === "待验证") return "#d97706";
  if (s === "到期未验证") return "#2563eb";
  if (s === "未到期" || s === "未到期验证") return "#60a5fa";
  return "#a1a1aa";
};

const isOverdue = (due?: string | null) => Boolean(due && due < localDate());

const isPriorityTask = (task: any) =>
  !historyView.value &&
  (taskStatus(task) === "未完成" ||
    (!task.closed_at && isOverdue(task.due_date)));

const priorityRows = computed(() =>
  historyView.value ? [] : rows.value.filter(isPriorityTask),
);

const regularRows = computed(() =>
  historyView.value ? rows.value : rows.value.filter((t) => !isPriorityTask(t)),
);

async function switchTaskView(mode: "current" | "history") {
  await router.replace({
    path: "/tasks",
    query: mode === "history" ? { view: "history" } : {},
  });
}

async function load(append = false, background = false) {
  const gen = ++loadGen;
  if (!append) {
    busy.value = true;
    loadError.value = "";
  }
  try {
    const result = await api(
      (historyView.value ? "/task-history?" : "/tasks?") +
        query({
          mine: reminderDay.value ? true : mine.value,
          state: status.value,
          q: q.value.trim(),
          current: historyView.value ? undefined : !reminderDay.value,
          reminder_day: reminderDay.value,
          offset: (page.value - 1) * pageSize,
          limit: pageSize,
        }),
      "GET",
      undefined,
      { background },
    );
    if (gen !== loadGen) return;
    const rawItems = Array.isArray(result) ? result : result.items || [];
    const filtered = historyView.value
      ? rawItems
      : rawItems.filter(
          (t: any) =>
            !t.closed_at && !t.withdrawn_at && taskStatus(t) !== "关闭",
        );
    total.value = Array.isArray(result) ? filtered.length : result.total || 0;
    rows.value = append ? [...rows.value, ...filtered] : filtered;
  } catch (error: any) {
    if (gen === loadGen) {
      if (!append) rows.value = [];
      loadError.value = chineseError(error, "待办列表加载失败");
    }
    throw error;
  } finally {
    if (gen === loadGen) {
      busy.value = false;
      refreshing.value = false;
    }
  }
}

function search() {
  page.value = 1;
  return load(false);
}

function loadMore() {
  if (busy.value || rows.value.length >= total.value) return;
  page.value += 1;
  void run(() => load(true));
}

async function openDetail(id: string, background = false) {
  const gen = ++detailGen;
  const archived = historyView.value
    ? rows.value.find((r) => r.id === id)
    : null;
  let result: any;
  try {
    result = archived?.history_only
      ? archived
      : await api("/tasks/" + id, "GET", undefined, { background });
  } catch (e) {
    if (gen === detailGen) throw e;
    return;
  }
  if (gen !== detailGen) return;
  selected.value = archived
    ? {
        ...result,
        todo_status: archived.todo_status,
        recorded_status: archived.recorded_status,
        notes: archived.notes,
        inbox_meeting_date: archived.inbox_meeting_date,
        inbox_meeting_title: archived.inbox_meeting_title,
        source_meeting_title: archived.source_meeting_title,
        owner_names: archived.owner_names,
        verifier_name: archived.verifier_name,
      }
    : result;
  explanation.value = "";
  evidence.value = [];
  idempotencyKey.value = clientId();
  nativeTodo.value = null;
  detailOpen.value = true;
  if (!archived?.history_only) {
    api(`/tasks/${id}/native-todo`, "GET", undefined, { background: true })
      .then((info) => {
        if (gen === detailGen) nativeTodo.value = info;
      })
      .catch(() => {});
  }
}

function closeDetail() {
  detailGen += 1;
  detailOpen.value = false;
  applyOpen.value = false;
  selected.value = null;
  if (route.query.id || route.query.native_confirm) {
    const { id, native_confirm, ...rest } = route.query;
    void router.replace({ path: route.path, query: rest });
  }
}

const canApply = computed(
  () =>
    !historyView.value &&
    selected.value &&
    !selected.value.history_only &&
    selected.value.owner_ids?.includes(state.me?.id) &&
    !selected.value.closed_at &&
    !selected.value.withdrawn_at &&
    !(selected.value.requests || []).some((x: any) => x.state === "pending"),
);

const moreActions = computed(() => {
  const t = selected.value;
  if (!t || historyView.value || t.history_only || t.closed_at || t.withdrawn_at)
    return [];
  const actions: any[] = [];
  if (t.can_edit) actions.push({ name: "调整期限 / 责任人", key: "edit" });
  if (t.can_transfer) actions.push({ name: "移交派发人", key: "transfer" });
  if (t.can_withdraw)
    actions.push({ name: "撤回任务", key: "withdraw", color: "#b91c1c" });
  return actions;
});

async function onSelectMoreAction(action: any) {
  actionSheetOpen.value = false;
  const t = selected.value;
  if (!t) return;
  if (action.key === "edit") {
    editForm.value = {
      expected_version: t.version,
      content: t.content,
      due_date: t.due_date || "",
      owner_ids: [...(t.owner_ids || [])],
      verifier_id: t.verifier_id || "",
      deliverable: t.deliverable || "",
      reason: "",
    };
    editOpen.value = true;
  } else if (action.key === "transfer") {
    const people = await api(`/tasks/${t.id}/transfer-options`);
    transferPeople.value = withDirectoryDetails(
      people,
      state.directory.users,
    ).filter(isEmployeeAccount);
    transferForm.value = { issuer_id: "", reason: "" };
    transferOpen.value = true;
  } else if (action.key === "withdraw") {
    withdrawReason.value = "";
    withdrawOpen.value = true;
  }
}

async function onUploadEvidence(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  if (!files.length || !selected.value) return;
  const taskId = selected.value.id;
  uploading.value = true;
  try {
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      const uploaded = await api("/files?task_id=" + taskId, "POST", form);
      evidence.value.push(uploaded);
    }
    showSuccessToast("证据已上传");
  } finally {
    uploading.value = false;
    input.value = "";
  }
}

async function submitCompletion() {
  const t = selected.value;
  if (!t) return;
  if (!explanation.value.trim()) {
    showToast("请填写完成情况说明");
    return;
  }
  sending.value = true;
  try {
    await api(`/tasks/${t.id}/completion`, "POST", {
      expected_version: t.version,
      idempotency_key: idempotencyKey.value,
      explanation: explanation.value.trim(),
      evidence_ids: evidence.value.map((x) => x.id),
    });
    applyOpen.value = false;
    showSuccessToast("完成申请已提交，等待验证");
    await openDetail(t.id, true);
    await load(false, true);
  } finally {
    sending.value = false;
  }
}

async function saveEdit() {
  const t = selected.value;
  if (!t || !editForm.value) return;
  if (!editForm.value.due_date) {
    showToast("请选择计划完成时间");
    return;
  }
  sending.value = true;
  try {
    await api(`/tasks/${t.id}`, "PUT", editForm.value);
    editOpen.value = false;
    showSuccessToast("任务已更新");
    await openDetail(t.id, true);
    await load(false, true);
  } finally {
    sending.value = false;
  }
}

async function saveTransfer() {
  const t = selected.value;
  if (!t) return;
  if (!transferForm.value.issuer_id || !transferForm.value.reason.trim()) {
    showToast("请选择新派发人并填写原因");
    return;
  }
  await showConfirmDialog({
    title: "确认移交",
    message: "确定移交此任务吗？当前待验证申请将失效。",
  });
  sending.value = true;
  try {
    await api(`/tasks/${t.id}/transfer`, "POST", {
      ...transferForm.value,
      expected_version: t.version,
    });
    transferOpen.value = false;
    showSuccessToast("任务已移交");
    await openDetail(t.id, true);
    await load(false, true);
  } finally {
    sending.value = false;
  }
}

async function confirmWithdraw() {
  const t = selected.value;
  if (!t) return;
  if (!withdrawReason.value.trim()) {
    showToast("请填写撤回原因");
    return;
  }
  await showConfirmDialog({
    title: "确认撤回",
    message: "撤回后责任人将停止执行，操作记录会保留。",
  });
  sending.value = true;
  try {
    await api(`/tasks/${t.id}/withdraw`, "POST", {
      reason: withdrawReason.value.trim(),
      expected_version: t.version,
    });
    withdrawOpen.value = false;
    showSuccessToast("任务已撤回");
    await openDetail(t.id, true);
    await load(false, true);
  } finally {
    sending.value = false;
  }
}

async function previewEvidenceFile(id: string) {
  const blob = await fetchAuthBlob("/files/" + id);
  if (blob.type.startsWith("image/")) {
    const url = URL.createObjectURL(blob);
    showImagePreview({
      images: [url],
      closeable: true,
      onClose: () => URL.revokeObjectURL(url),
    });
  } else {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "完成证据";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
}

async function triggerNativeTodo() {
  const t = selected.value;
  if (!t) return;
  if (nativeTodo.value?.link?.todo_id && insideWeCom()) {
    await viewWeComTodo(nativeTodo.value.link.todo_id);
    return;
  }
  const result = await confirmAssignedTodo({ id: t.id, version: t.version });
  showToast(result.message);
  await openDetail(t.id, true);
}

watch([reminderDay, historyView], () => {
  if (reminderDay.value) mine.value = true;
  q.value = "";
  status.value = "";
  rows.value = [];
  total.value = 0;
  closeDetail();
  void run(search);
});

watch(
  () => route.query.id,
  (id) => {
    if (id) void run(() => openDetail(String(id)));
  },
);

onBeforeUnmount(() => {
  disposed = true;
  loadGen += 1;
  detailGen += 1;
});

onMounted(() =>
  run(async () => {
    await load();
    if (route.query.id && !disposed) await openDetail(String(route.query.id));
  }),
);
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <h1 class="m-header-title-lg" style="flex: 1; text-align: center">
          {{ reminderDay ? `${reminderDay} 到期提醒` : "我的待办" }}
        </h1>
        <button
          v-if="reminderDay"
          type="button"
          class="m-btn m-btn-sm"
          style="position: absolute; right: 16px"
          @click="router.replace('/tasks')"
        >
          全部待办
        </button>
      </div>

      <div class="tasks-filters">
        <div
          v-if="!reminderDay"
          class="m-segmented"
          style="grid-template-columns: 1fr 1fr"
        >
          <button
            type="button"
            :class="{ active: !historyView }"
            @click="switchTaskView('current')"
          >
            当前待办
          </button>
          <button
            type="button"
            :class="{ active: historyView }"
            @click="switchTaskView('history')"
          >
            历史任务单
          </button>
        </div>

        <div class="m-search-box">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="7" />
            <line x1="20" y1="20" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="q"
            placeholder="搜索会议或任务内容"
            @keyup.enter="run(search)"
          />
          <button
            v-if="q"
            type="button"
            class="m-link-btn"
            style="font-size: 12px"
            @click="run(search)"
          >
            搜索
          </button>
        </div>

        <div class="tasks-bar-row">
          <select
            v-model="status"
            class="m-select tasks-status-select"
            aria-label="筛选状态"
            @change="run(search)"
          >
            <option
              v-for="chip in statusChips"
              :key="chip.value"
              :value="chip.value"
            >
              {{ chip.value === "" ? "全部状态" : chip.label }}
            </option>
          </select>

          <label
            v-if="!reminderDay"
            class="tasks-mine-toggle"
            @click.prevent="
              mine = !mine;
              run(search);
            "
          >
            <span>只看我的</span>
            <button
              type="button"
              role="switch"
              :aria-checked="mine"
              class="m-switch"
              :class="{ on: mine }"
            />
          </label>
        </div>
      </div>
    </header>

    <van-pull-refresh v-model="refreshing" @refresh="run(search)">
      <section class="m-section">
        <div v-if="loadError" class="m-alert m-alert-error">
          {{ loadError }}
          <button
            type="button"
            class="m-btn m-btn-sm"
            style="margin-left: 8px"
            @click="run(() => load(false))"
          >
            重试
          </button>
        </div>

        <div v-if="busy && !rows.length" class="m-empty">
          <van-loading size="22">正在加载任务…</van-loading>
        </div>

        <template v-else-if="rows.length">
          <!-- 需要优先处理 (Unfinished / Overdue) -->
          <template v-if="priorityRows.length">
            <div class="m-section-heading">
              <h2>需要优先处理</h2>
              <span class="m-muted">共 {{ priorityRows.length }} 条</span>
            </div>
            <article
              v-for="t in priorityRows"
              :key="t.id"
              class="task-card task-card-priority"
              @click="run(() => openDetail(t.id))"
            >
              <div class="task-bar" style="background: #e11d48" />
              <div class="task-body">
                <div class="task-top">
                  <p class="task-content m-work-text">{{ t.content }}</p>
                  <StatusTag v-if="t.withdrawn_at" value="已撤回" tone="gray" />
                  <StatusTag v-else :value="taskStatus(t)" />
                </div>
                <div class="task-meta">
                  <span class="task-meta-line tabular">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                      <rect x="3" y="4" width="18" height="17" rx="3" />
                      <line x1="8" y1="2" x2="8" y2="6" />
                      <line x1="16" y1="2" x2="16" y2="6" />
                      <line x1="3" y1="10" x2="21" y2="10" />
                    </svg>
                    截止 {{ t.due_date || "未设置" }} · {{ meetingTitle(t) }}
                  </span>
                  <svg class="m-chevron" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </div>
              </div>
            </article>
          </template>

          <!-- 其他待办 / 历史任务单 -->
          <template v-if="regularRows.length">
            <div class="m-section-heading">
              <h2>{{ historyView ? "历史任务单" : "其他待办" }}</h2>
              <span class="m-muted">
                {{ historyView ? "仅供查阅" : `共 ${regularRows.length} 条` }}
              </span>
            </div>
            <article
              v-for="t in regularRows"
              :key="t.id"
              class="task-card"
              @click="run(() => openDetail(t.id))"
            >
              <div class="task-body">
                <div class="task-top">
                  <p class="task-content m-work-text">{{ t.content }}</p>
                  <StatusTag v-if="t.withdrawn_at" value="已撤回" tone="gray" />
                  <StatusTag v-else-if="t.has_pending_request" value="待验证" tone="amber" />
                  <StatusTag v-else :value="taskStatus(t)" />
                </div>
                <div class="task-meta">
                  <span class="task-meta-line tabular">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                      <rect x="3" y="4" width="18" height="17" rx="3" />
                      <line x1="8" y1="2" x2="8" y2="6" />
                      <line x1="16" y1="2" x2="16" y2="6" />
                      <line x1="3" y1="10" x2="21" y2="10" />
                    </svg>
                    截止 {{ t.due_date || "未设置" }} · {{ meetingTitle(t) }}
                  </span>
                  <svg class="m-chevron" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </div>
              </div>
            </article>
          </template>

          <button
            v-if="rows.length < total"
            type="button"
            class="m-btn"
            style="width: 100%"
            :disabled="busy"
            @click="loadMore"
          >
            {{ busy ? "加载中…" : `加载更多（已显示 ${rows.length}/${total}）` }}
          </button>
        </template>

        <div v-else class="m-empty">
          {{ historyView ? "当前筛选条件下暂无历史任务单" : "太棒了，当前没有待办任务" }}
        </div>
      </section>
    </van-pull-refresh>

    <!-- Task Detail Full Sheet -->
    <van-popup
      v-model:show="detailOpen"
      position="bottom"
      round
      :style="{ height: '90vh', display: 'flex', flexDirection: 'column' }"
      @closed="closeDetail"
    >
      <div v-if="selected" class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">任务详情</span>
          <div style="display: flex; gap: 10px; align-items: center">
            <button
              v-if="moreActions.length"
              type="button"
              class="m-btn m-btn-sm"
              @click="actionSheetOpen = true"
            >
              管理 ⋯
            </button>
            <button type="button" class="sheet-close" @click="closeDetail">
              关闭
            </button>
          </div>
        </div>

        <div class="sheet-body">
          <div style="display: flex; flex-wrap: wrap; gap: 6px">
            <StatusTag :value="meetingTitle(selected)" tone="blue" />
            <StatusTag
              v-if="selected.withdrawn_at"
              value="已撤回"
              tone="gray"
            />
            <StatusTag v-else :value="taskStatus(selected)" />
          </div>

          <h2 class="detail-title m-work-text">{{ selected.content }}</h2>

          <div class="detail-grid">
            <div>
              <div class="m-muted">责任人</div>
              <div class="detail-val">
                {{
                  selected.owner_names?.join("、") ||
                  (selected.owner_ids || []).map(person).join("、") ||
                  "—"
                }}
              </div>
            </div>
            <div>
              <div class="m-muted">验证人</div>
              <div class="detail-val">
                {{ selected.verifier_name || person(selected.verifier_id) }}
              </div>
            </div>
            <div>
              <div class="m-muted">计划完成时间</div>
              <div class="detail-val tabular">
                {{ selected.due_date || "未设置" }}
              </div>
            </div>
            <div>
              <div class="m-muted">来源</div>
              <div class="detail-val">
                <a
                  v-if="selected.source_meeting_id"
                  href="javascript:void(0)"
                  style="color: var(--m-primary); text-decoration: none"
                  @click="
                    closeDetail();
                    router.push('/meetings/' + selected.source_meeting_id);
                  "
                >
                  查看来源会议 ›
                </a>
                <span v-else>{{
                  selected.issuer_name
                    ? `${selected.issuer_name} 直接派发`
                    : "直接派发"
                }}</span>
              </div>
            </div>
            <div v-if="selected.deliverable" style="grid-column: 1 / -1">
              <div class="m-muted">输出物要求</div>
              <div class="detail-val m-work-text">{{ selected.deliverable }}</div>
            </div>
            <div v-if="selected.requirements" style="grid-column: 1 / -1">
              <div class="m-muted">补充说明</div>
              <div class="detail-val m-work-text">
                {{ selected.requirements }}
              </div>
            </div>
            <div v-if="selected.notes" style="grid-column: 1 / -1">
              <div class="m-muted">会议备注</div>
              <div class="detail-val m-work-text">{{ selected.notes }}</div>
            </div>
          </div>

          <!-- WeCom Native Todo -->
          <div
            v-if="nativeTodo && (nativeTodo.link || nativeTodo.can_create)"
            class="m-card"
            style="padding: 12px"
          >
            <div
              style="
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
              "
            >
              <div>
                <div style="font-size: 13px; font-weight: 600">
                  企业微信原生待办
                </div>
                <div class="m-muted">
                  {{
                    nativeTodo.link
                      ? "已关联企业微信待办"
                      : "可在企业微信中确认创建原生待办"
                  }}
                </div>
              </div>
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-soft"
                @click="run(triggerNativeTodo)"
              >
                {{ nativeTodo.link ? "查看待办" : "创建企微待办" }}
              </button>
            </div>
          </div>

          <!-- Completion & Verification Timeline -->
          <div v-if="selected.requests?.length" class="m-card" style="padding: 12px">
            <div style="font-size: 13px; font-weight: 700; margin-bottom: 10px">
              申请与验证记录
            </div>
            <div
              v-for="req in selected.requests"
              :key="req.id"
              class="req-item"
            >
              <div
                style="
                  display: flex;
                  justify-content: space-between;
                  align-items: center;
                  margin-bottom: 6px;
                "
              >
                <StatusTag
                  :value="
                    ({
                      pending: '待验证',
                      approved: '已通过',
                      rejected: '已驳回',
                      invalidated: '已失效',
                    } as any)[req.state] || req.state
                  "
                />
                <span class="m-muted tabular">{{
                  beijingTime(req.created_at)
                }}</span>
              </div>
              <p class="m-work-text" style="margin: 0 0 6px; font-size: 13px">
                {{ req.explanation }}
              </p>
              <div
                v-if="req.evidence_ids?.length"
                style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px"
              >
                <button
                  v-for="(fid, idx) in req.evidence_ids"
                  :key="fid"
                  type="button"
                  class="m-btn m-btn-sm m-btn-soft"
                  @click="run(() => previewEvidenceFile(fid))"
                >
                  查看证据 {{ Number(idx) + 1 }}
                </button>
              </div>
              <div
                v-if="req.opinion"
                class="m-alert m-alert-info"
                style="padding: 8px 10px"
              >
                验证意见：{{ req.opinion }}
              </div>
            </div>
          </div>

          <!-- Version History -->
          <div v-if="selected.versions?.length" class="m-card" style="padding: 12px">
            <div style="font-size: 13px; font-weight: 700; margin-bottom: 8px">
              版本记录
            </div>
            <div
              v-for="v in selected.versions"
              :key="v.version"
              class="ver-row"
            >
              <span class="tabular" style="font-weight: 600"
                >V{{ v.version }}</span
              >
              <span class="m-muted tabular">{{ v.due_date || "无期限" }}</span>
              <span class="m-muted">{{ v.reason || "初始建立" }}</span>
            </div>
          </div>
        </div>

        <div v-if="canApply" class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            @click="applyOpen = true"
          >
            提交完成申请（拍照 / 传凭证）
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Submit Completion Application Sheet -->
    <van-popup
      v-model:show="applyOpen"
      position="bottom"
      round
      :style="{ maxHeight: '82vh', display: 'flex', flexDirection: 'column' }"
    >
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">提交完成申请</span>
          <button type="button" class="sheet-close" @click="applyOpen = false">
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div>
            <label class="m-label m-label-req">完成情况说明</label>
            <textarea
              v-model="explanation"
              class="m-textarea"
              rows="4"
              placeholder="请填写完成进展、结果数据或交付物说明…"
            />
          </div>
          <div>
            <label class="m-label">完成证据（拍照 / 相册 / 文档）</label>
            <label class="upload-trigger">
              <input
                type="file"
                multiple
                accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.zip"
                style="display: none"
                @change="run(() => onUploadEvidence($event))"
              />
              <span>{{ uploading ? "正在上传…" : "+ 拍照或选择文件上传" }}</span>
            </label>
            <div
              v-if="evidence.length"
              style="
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                margin-top: 8px;
              "
            >
              <span
                v-for="(item, idx) in evidence"
                :key="item.id"
                class="m-chip m-chip-soft active"
              >
                {{ item.filename || `证据${idx + 1}` }}
                <button
                  type="button"
                  style="
                    border: none;
                    background: transparent;
                    color: inherit;
                    margin-left: 4px;
                  "
                  @click="evidence.splice(idx, 1)"
                >
                  ×
                </button>
              </span>
            </div>
          </div>
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            :disabled="sending || uploading"
            @click="run(submitCompletion)"
          >
            {{ sending ? "正在提交…" : "确认提交完成申请" }}
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Admin/Issuer Action Sheet -->
    <van-action-sheet
      v-model:show="actionSheetOpen"
      :actions="moreActions"
      cancel-text="取消"
      @select="run(() => onSelectMoreAction($event))"
    />

    <!-- Edit Task Sheet -->
    <van-popup
      v-model:show="editOpen"
      position="bottom"
      round
      :style="{ height: '82vh', display: 'flex', flexDirection: 'column' }"
    >
      <div v-if="editForm" class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">调整任务</span>
          <button type="button" class="sheet-close" @click="editOpen = false">
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div>
            <label class="m-label m-label-req">任务内容</label>
            <textarea v-model="editForm.content" class="m-textarea" rows="3" />
          </div>
          <div>
            <label class="m-label m-label-req">责任人</label>
            <PeoplePicker v-model="editForm.owner_ids" multiple title="选择责任人" />
          </div>
          <div>
            <label class="m-label m-label-req">计划完成时间</label>
            <input v-model="editForm.due_date" type="date" class="m-input" />
          </div>
          <div>
            <label class="m-label m-label-req">验证人</label>
            <PeoplePicker v-model="editForm.verifier_id" title="选择验证人" />
          </div>
          <div>
            <label class="m-label">输出物</label>
            <input v-model="editForm.deliverable" class="m-input" />
          </div>
          <div>
            <label class="m-label">调整原因</label>
            <input
              v-model="editForm.reason"
              class="m-input"
              placeholder="选填，记录于版本历史"
            />
          </div>
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            :disabled="sending"
            @click="run(saveEdit)"
          >
            {{ sending ? "保存中…" : "保存调整" }}
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Transfer Sheet -->
    <van-popup v-model:show="transferOpen" position="bottom" round>
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">移交派发人</span>
          <button type="button" class="sheet-close" @click="transferOpen = false">
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div>
            <label class="m-label m-label-req">新派发人</label>
            <PeoplePicker
              v-model="transferForm.issuer_id"
              :users="transferPeople"
              title="选择新派发人"
            />
          </div>
          <div>
            <label class="m-label m-label-req">移交原因</label>
            <textarea
              v-model="transferForm.reason"
              class="m-textarea"
              rows="3"
              placeholder="请填写移交原因"
            />
          </div>
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            :disabled="sending"
            @click="run(saveTransfer)"
          >
            确认移交
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Withdraw Sheet -->
    <van-popup v-model:show="withdrawOpen" position="bottom" round>
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">撤回任务</span>
          <button type="button" class="sheet-close" @click="withdrawOpen = false">
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div>
            <label class="m-label m-label-req">撤回原因</label>
            <textarea
              v-model="withdrawReason"
              class="m-textarea"
              rows="3"
              placeholder="请说明撤回原因"
            />
          </div>
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-danger"
            style="width: 100%"
            :disabled="sending"
            @click="run(confirmWithdraw)"
          >
            确认撤回任务
          </button>
        </div>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.tasks-filters {
  padding: 0 16px 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tasks-bar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.tasks-status-select {
  width: auto;
  min-width: 112px;
  padding: 6px 10px;
  font-size: 13px;
  border-radius: 10px;
}
.tasks-mine-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--m-text);
  cursor: pointer;
}
.task-card {
  background: var(--m-surface);
  border: 1px solid var(--m-line);
  border-radius: 14px;
  overflow: hidden;
  display: flex;
  cursor: pointer;
  box-shadow: var(--m-shadow-card);
}
.task-card-priority {
  background: var(--m-danger-soft);
  border-color: #f8d0d4;
}
.task-bar {
  width: 4px;
  flex-shrink: 0;
}
.task-body {
  flex: 1;
  min-width: 0;
  padding: 13px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.task-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.task-content {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--m-ink);
  line-height: 1.4;
  flex: 1;
}
.task-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--m-muted);
}
.task-meta-line {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sheet-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--m-line);
  flex-shrink: 0;
}
.sheet-title {
  font-size: 16px;
  font-weight: 700;
}
.sheet-close {
  border: none;
  background: transparent;
  font-size: 14px;
  color: var(--m-muted);
  cursor: pointer;
}
.sheet-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sheet-foot {
  padding: 10px 16px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--m-line);
  background: var(--m-surface);
  flex-shrink: 0;
}
.detail-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.45;
}
.detail-grid {
  background: #fafafa;
  border: 1px solid var(--m-line);
  border-radius: 10px;
  padding: 12px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.detail-val {
  font-size: 13px;
  font-weight: 600;
  color: var(--m-ink);
  margin-top: 2px;
}
.req-item {
  padding: 10px 0;
  border-top: 1px solid #f4f4f5;
}
.req-item:first-of-type {
  border-top: none;
  padding-top: 0;
}
.ver-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  border-top: 1px solid #f4f4f5;
  font-size: 12px;
}
.ver-row:first-of-type {
  border-top: none;
}
.upload-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 52px;
  border: 1.5px dashed var(--m-input-line);
  border-radius: 10px;
  background: #fafafa;
  color: var(--m-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
</style>
