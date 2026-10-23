<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from "vue";
import {
  useRoute,
  useRouter,
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
} from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  Calendar,
  Edit,
  List,
  Memo,
  Tickets,
  UserFilled,
} from "@element-plus/icons-vue";
import {
  api,
  state,
  person,
  localDate,
  run,
  download,
  statuses,
  rate,
  previewFile,
  departmentPath,
  clientId,
} from "../api";
import PeopleSelect from "../components/PeopleSelect.vue";
import StatusTag from "../components/StatusTag.vue";
import { mergedWorkContent, updateWorkContent } from "../workContent";
const activation = ref<any>(null),
  activating = ref(false),
  linkItem = ref<any>(null),
  suggestions = ref<any[]>([]);
async function activate() {
  if (!activation.value.due_date) {
    ElMessage.error("请选择计划完成时间");
    return;
  }
  activating.value = true;
  try {
    const t = await api("/meetings/" + meeting.value.id + "/activate", "POST", {
      ...activation.value,
      expected_version: meeting.value.published_version,
    });
    activation.value = null;
    ElMessage.success("事项已激活为当前待办");
    await router.push("/tasks?id=" + t.id);
  } finally {
    activating.value = false;
  }
}
async function suggest(item: any) {
  linkItem.value = item;
  const params = new URLSearchParams({
    q: item.content,
    series_id: draft.value.series_id || "",
  });
  for (const id of item.owner_ids) params.append("owner_ids", id);
  if (item.due_date) params.set("due_date", item.due_date);
  suggestions.value = await api("/task-suggestions?" + params);
}
function linkTask(t: any) {
  Object.assign(linkItem.value, {
    task_id: t.id,
    task_version: t.version,
    content: t.content,
    category: t.category,
    requirements: t.requirements,
    deliverable: t.deliverable,
    due_date: t.due_date,
    owner_ids: t.owner_ids,
    verifier_id: t.verifier_id,
  });
  linkItem.value = null;
}
const route = useRoute(),
  router = useRouter(),
  meeting = ref<any>(null),
  draft = ref<any>(null),
  busy = ref(false),
  saving = ref(false),
  preview = ref(false),
  dirty = ref(false),
  saveError = ref(""),
  selectedVersion = ref<number | null>(null),
  publishReason = ref("首次发布"),
  notify = ref(true),
  loading = ref(false),
  editing = ref(false),
  basicInfoEditing = ref(false),
  participantsExpanded = ref(false),
  participantDialogVisible = ref(false),
  participantDraftIds = ref<string[]>([]),
  archiveSeriesDialogVisible = ref(false),
  archiveSeriesDraftId = ref(""),
  archiveSeriesSaving = ref(false),
  absenteesDraft = ref(""),
  reviewSearch = ref(""),
  arrangementSearch = ref("");
const archiveSeriesKey = computed(() => {
  if (meeting.value)
    return meeting.value.series_id
      ? `series:${meeting.value.series_id}`
      : "unassigned";
  return typeof route.query.series === "string" ? route.query.series : "";
});
const archiveSeriesName = computed(() => {
  if (!archiveSeriesKey.value) return "";
  if (
    archiveSeriesKey.value === "unassigned" ||
    archiveSeriesKey.value.startsWith("unassigned:")
  )
    return "未归入系列";
  return (
    state.directory.series.find(
      (series: any) => series.id === archiveSeriesKey.value.slice(7),
    )?.name || "会议系列"
  );
});
const previewUrl = ref(""),
  previewLoading = ref(false);
const mailStates: Record<string, string> = {
  pending: "待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "发送失败",
  blocked: "配置待完善",
  uncertain: "送达结果待核实",
  skipped: "已跳过",
};
const recipientCandidates = computed(() => {
  if (!draft.value) return [];
  const ids = new Set([
    draft.value.host_id,
    draft.value.recorder_id,
    ...draft.value.attendee_ids,
  ]);
  for (const item of draft.value.items) {
    item.owner_ids.forEach((id: string) => ids.add(id));
    ids.add(item.verifier_id || draft.value.host_id);
  }
  return state.directory.users.filter(
    (u) => ids.has(u.id) && u.active && u.email,
  );
});
function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = "";
}
async function refreshPreview() {
  previewLoading.value = true;
  try {
    if (!(await save(false)))
      throw new Error(saveError.value || "请先保存草稿");
    const blob = await previewFile(
      meeting.value.id,
      meeting.value.draft_version,
    );
    clearPreview();
    previewUrl.value = URL.createObjectURL(blob);
  } finally {
    previewLoading.value = false;
  }
}
async function previewTasks() {
  if (!(await save(false))) return;
  const blob = await previewFile(
    meeting.value.id,
    meeting.value.draft_version,
    "tasks",
  );
  const url = URL.createObjectURL(blob),
    link = document.createElement("a");
  link.href = url;
  link.download = "草稿预览-任务单.xlsx";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
watch(preview, (value) => {
  if (!value) clearPreview();
});
onBeforeUnmount(clearPreview);
const editable = computed(
  () =>
    route.params.id === "new" ||
    (editing.value && meeting.value?.can_edit && meeting.value?.has_draft),
);
const assessmentVersion = computed(
  () => selectedVersion.value || meeting.value?.published_version,
);
const assessment = computed(
  () => meeting.value?.assessments?.[String(assessmentVersion.value)],
);
const beijingTime = (value: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })
    : "—";
const show = computed(() =>
  selectedVersion.value || (!editing.value && meeting.value?.published_version)
    ? meeting.value?.versions.find(
        (x) =>
          x.number ===
          (selectedVersion.value || meeting.value?.published_version),
      )?.snapshot
    : draft.value,
);
const readOnlyView = computed(() => !editable.value || !!selectedVersion.value);
const participants = computed(() => {
  if (!show.value) return [];
  const people = new Map<string, { id: string; name: string; role: string }>();
  const add = (id: string | null, role: string) => {
    if (!id || people.has(id)) return;
    people.set(id, { id, name: person(id), role });
  };
  add(show.value.host_id, "主持人");
  add(show.value.recorder_id, "记录人");
  for (const id of show.value.attendee_ids || []) add(id, "参会人员");
  return [...people.values()];
});
const visibleParticipants = computed(() =>
  participantsExpanded.value || participants.value.length <= 5
    ? participants.value
    : participants.value.slice(0, 4),
);
const review = computed(
    () => show.value?.items.filter((x) => x.section === "review") || [],
  ),
  arrangements = computed(
    () => show.value?.items.filter((x) => x.section === "new") || [],
  );
const visibleReview = computed(() =>
  review.value.filter((item: any) =>
    `${item.category || ""} ${item.content || ""} ${item.owner_names?.join("、") || item.owner_ids?.map(person).join("、") || ""}`
      .toLowerCase()
      .includes(reviewSearch.value.trim().toLowerCase()),
  ),
);
const visibleArrangements = computed(() =>
  arrangements.value.filter((item: any) =>
    `${item.category || ""} ${item.content || ""} ${item.owner_names?.join("、") || item.owner_ids?.map(person).join("、") || ""}`
      .toLowerCase()
      .includes(arrangementSearch.value.trim().toLowerCase()),
  ),
);
let timer: ReturnType<typeof setTimeout> | undefined;
let createdRouteId: string | null = null;
let pageGeneration = 0;
let activeSave: Promise<boolean> | null = null;
function cancelAutosave() {
  if (timer) clearTimeout(timer);
  timer = undefined;
}
function scheduleAutosave() {
  cancelAutosave();
  const generation = pageGeneration;
  timer = setTimeout(() => {
    timer = undefined;
    if (generation === pageGeneration && editable.value) void save(false);
  }, 1600);
}
async function load() {
  const generation = ++pageGeneration;
  const routeId = String(route.params.id);
  cancelAutosave();
  activeSave = null;
  saving.value = false;
  loading.value = true;
  selectedVersion.value = null;
  basicInfoEditing.value = false;
  participantsExpanded.value = false;
  participantDialogVisible.value = false;
  try {
    if (routeId === "new") {
      editing.value = true;
      meeting.value = null;
      draft.value = {
        title: "",
        department_id: state.me.department_id || "",
        series_id:
          typeof route.query.series === "string" &&
          route.query.series.startsWith("series:")
            ? route.query.series.slice(7)
            : null,
        kind: "department",
        meeting_date: localDate(),
        meeting_time: "",
        location: "",
        host_id: state.me.id,
        recorder_id: state.me.id,
        reviewer_id: null,
        attendee_ids: [],
        absentees: "",
        decisions: "",
        items: [],
        attachment_ids: [],
        recipient_ids: [],
        cc_ids: [],
      };
    } else {
      const result = await api("/meetings/" + routeId);
      if (generation !== pageGeneration || route.params.id !== routeId) return;
      meeting.value = result;
      editing.value =
        !meeting.value.published_version &&
        meeting.value.has_draft &&
        meeting.value.can_edit;
      draft.value = JSON.parse(
        JSON.stringify(
          meeting.value.draft || meeting.value.versions[0]?.snapshot,
        ),
      );
      draft.value.reviewer_id = null;
      if (meeting.value.published_version)
        selectedVersion.value = meeting.value.published_version;
    }
    dirty.value = false;
    saveError.value = "";
  } finally {
    if (generation === pageGeneration) loading.value = false;
  }
}
watch(
  () => route.params.id,
  (id, previousId) => {
    // The first save only changes the address; keep edits made while it was in flight.
    if (previousId === "new" && id === createdRouteId) return;
    run(load);
  },
  { immediate: true },
);
watch(
  draft,
  () => {
    if (loading.value) return;
    dirty.value = true;
    cancelAutosave();
    if (meeting.value && editable.value) scheduleAutosave();
  },
  { deep: true, flush: "sync" },
);
async function save(manual = true) {
  const generation = pageGeneration;
  while (activeSave) {
    const saved = await activeSave;
    if (generation !== pageGeneration || !saved) return false;
  }
  if (generation !== pageGeneration || loading.value || !draft.value)
    return false;
  if (!dirty.value && meeting.value) return true;
  const operation = persistDraft(manual, generation);
  activeSave = operation;
  try {
    return await operation;
  } finally {
    if (activeSave === operation) activeSave = null;
  }
}
async function persistDraft(manual: boolean, generation: number) {
  cancelAutosave();
  saving.value = true;
  saveError.value = "";
  const routeId = String(route.params.id);
  const meetingId = meeting.value?.id || null;
  const expectedVersion = meeting.value?.draft_version;
  const data = JSON.parse(JSON.stringify(draft.value));
  let saved = false;
  try {
    const result = meetingId
      ? await api("/meetings/" + meetingId, "PUT", {
          expected_version: expectedVersion,
          draft: data,
        })
      : await api("/meetings", "POST", data);
    if (
      generation !== pageGeneration ||
      String(route.params.id) !== routeId ||
      (meeting.value?.id || null) !== meetingId
    )
      return false;
    meeting.value = {
      versions: [],
      attachments: [],
      pending_edit_requests: [],
      can_continue: false,
      ...meeting.value,
      ...result,
      can_edit: true,
      can_delete: state.me.is_admin || state.me.id === result.host_id,
    };
    dirty.value = JSON.stringify(data) !== JSON.stringify(draft.value);
    if (routeId === "new") {
      createdRouteId = result.id;
      try {
        await router.replace({
          path: "/meetings/" + result.id,
          query: route.query,
        });
      } finally {
        createdRouteId = null;
      }
    }
    saved = true;
    if (manual)
      ElMessage.success(
        dirty.value ? "本次草稿已保存，新增修改将继续保存" : "草稿已保存",
      );
    return true;
  } catch (e: any) {
    if (generation === pageGeneration) saveError.value = e.message;
    return false;
  } finally {
    if (generation === pageGeneration) {
      saving.value = false;
      if (saved && dirty.value && editable.value) scheduleAutosave();
    }
  }
}
function add() {
  draft.value.items.push({
    id: clientId(),
    section: "new",
    category: "",
    content: "",
    owner_ids: [],
    due_date: null,
    status: "未到期",
    verifier_id: null,
    deliverable: "",
    notes: "",
    requirements: "",
    exclusion_reason: "",
    task_id: null,
    task_version: null,
    change_reason: "",
  });
}
function meetingKindChanged(value: string) {
  if (value === "special") {
    draft.value.department_id = null;
  }
}
async function carry() {
  if (!draft.value.series_id) throw new Error("请先选择会议系列");
  const requestedSeries = draft.value.series_id;
  const items = await api("/series/" + requestedSeries + "/carry-forward");
  if (draft.value.series_id !== requestedSeries) return;
  const known = new Set(
    draft.value.items
      .filter((x) => x.section === "review")
      .map((x) => x.task_id),
  );
  draft.value.items.push(...items.filter((x) => !known.has(x.task_id)));
  ElMessage.success("已带入尚未回顾的任务");
}
async function newSeries() {
  const { value } = await ElMessageBox.prompt(
    "为连续会议设置一个名称，例如：质量部周例会",
    "新建会议系列",
    { inputValidator: (v) => !!v?.trim() || "请输入名称" },
  );
  const s = await api("/series", "POST", { name: value });
  if (!state.directory.series.some((item: any) => item.id === s.id))
    state.directory.series.push(s);
  draft.value.series_id = s.id;
}
async function openArchiveSeriesDialog() {
  if (dirty.value && !(await save(false)))
    throw new Error(saveError.value || "请先保存当前修改");
  archiveSeriesDraftId.value = meeting.value.series_id || "";
  archiveSeriesDialogVisible.value = true;
}
async function moveArchiveSeries() {
  if (!archiveSeriesDraftId.value) throw new Error("请选择会议系列");
  archiveSeriesSaving.value = true;
  try {
    await api("/meetings/" + meeting.value.id + "/archive-series", "PUT", {
      series_id: archiveSeriesDraftId.value,
      expected_draft_version: meeting.value.draft_version,
    });
    archiveSeriesDialogVisible.value = false;
    dirty.value = false;
    await router.replace({
      path: "/meetings/" + meeting.value.id,
      query: { series: `series:${archiveSeriesDraftId.value}` },
    });
    await load();
    ElMessage.success("会议已归入所选系列");
  } finally {
    archiveSeriesSaving.value = false;
  }
}
async function requestEdit() {
  if (
    !meeting.value ||
    meeting.value.can_edit ||
    meeting.value.my_edit_request?.state === "pending"
  )
    return;
  await ElMessageBox.confirm(
    "确定向会议主持人申请此会议的编辑与接续新建权限吗？主持人审批通过后才可操作。",
    "申请编辑会议",
    { type: "info", confirmButtonText: "确定申请" },
  );
  await api(`/meetings/${meeting.value.id}/edit-requests`, "POST");
  await load();
  ElMessage.success("申请已提交，等待主持人审批");
}
async function decideEdit(request: any, decision: "approved" | "rejected") {
  await ElMessageBox.confirm(
    `确定${decision === "approved" ? "通过" : "拒绝"}${request.applicant_name}的编辑与接续新建申请吗？`,
    "审批编辑权限",
    { type: decision === "rejected" ? "warning" : "info" },
  );
  await api(
    `/meetings/${meeting.value.id}/edit-requests/${request.id}/decision`,
    "POST",
    { decision },
  );
  await load();
  ElMessage.success(decision === "approved" ? "已授权" : "已拒绝申请");
}
async function continueMeeting() {
  if (!meeting.value?.can_continue) return;
  await ElMessageBox.confirm(
    `确定以“${meeting.value.title}”为基础接续新建会议吗？`,
    "接续新建",
    { type: "info" },
  );
  if ((dirty.value || saving.value) && !(await save(false)))
    throw new Error(saveError.value || "请先保存当前会议修改，再接续新建");
  const created = await api(`/meetings/${meeting.value.id}/continue`, "POST");
  ElMessage.success("已建立接续会议草稿，请核对带入事项和日期");
  await router.push(`/meetings/${created.id}`);
}
async function removeMeeting() {
  if (!meeting.value?.can_delete) return;
  const impact = await api(
    "/meetings/" + meeting.value.id + "/deletion-impact",
  );
  if (impact.blocking_meeting_count) {
    const titles = (impact.blocking_meetings || [])
      .map((item: any) => `“${item.title}”`)
      .join("、");
    const hidden = impact.hidden_blocking_meeting_count
      ? `另有 ${impact.hidden_blocking_meeting_count} 场关联会议不在您的查看范围，请联系管理员处理。`
      : "";
    await ElMessageBox.alert(
      `${titles ? `${titles}仍引用本会议事项。` : "其他会议仍引用本会议事项。"}${hidden}请先处理关联会议，再删除本会议。`,
      "暂时无法删除会议",
      { type: "warning", confirmButtonText: "知道了" },
    );
    return;
  }
  if (impact.active_native_todos)
    throw new Error("这场会议还有未结束的企业微信待办，请先处理");
  const consequences = impact.published
    ? `此会议已发布。删除后，其 ${impact.task_count} 条关联正式任务和会议附件也会删除；原始导入文件保留。`
    : "这条草稿及其附件将被删除。";
  await ElMessageBox.confirm(
    `确定删除“${impact.title}”吗？${consequences}删除后无法恢复。`,
    "删除会议",
    {
      type: "warning",
      confirmButtonText: "确认删除",
      confirmButtonClass: "el-button--danger",
    },
  );
  const target = archiveSeriesKey.value;
  await api("/meetings/" + meeting.value.id, "DELETE", {
    expected_draft_version: impact.draft_version,
  });
  dirty.value = false;
  ElMessage.success("会议已删除");
  await router.push({
    path: "/meetings",
    query: target ? { series: target } : {},
  });
}
async function openPreview() {
  if (await save()) {
    preview.value = true;
    await refreshPreview();
  }
}
async function publish() {
  busy.value = true;
  try {
    await api("/meetings/" + meeting.value.id + "/publish", "POST", {
      expected_version: meeting.value.draft_version,
      notify: notify.value,
      reason: publishReason.value,
    });
    preview.value = false;
    await load();
    ElMessage.success("会议已发布，任务已生成");
  } finally {
    busy.value = false;
  }
}
async function revise() {
  const { value } = await ElMessageBox.prompt("请说明修订原因", "创建修订版本");
  if (!value?.trim()) return;
  await api("/meetings/" + meeting.value.id + "/revision", "POST", {
    reason: value,
  });
  publishReason.value = value;
  await load();
  openEditor();
}
function openEditor() {
  editing.value = true;
  selectedVersion.value = null;
  basicInfoEditing.value = false;
}
async function ensureEditableDraft() {
  if (!editable.value || selectedVersion.value) {
    if (meeting.value?.has_draft) openEditor();
    else await revise();
  }
  return editable.value && !selectedVersion.value;
}
async function editCard() {
  if (await ensureEditableDraft()) basicInfoEditing.value = true;
}
async function openParticipantsDialog() {
  if (!(await ensureEditableDraft())) return;
  participantDraftIds.value = [...(draft.value.attendee_ids || [])];
  absenteesDraft.value = draft.value.absentees || "";
  participantDialogVisible.value = true;
}
function saveParticipantsDialog() {
  draft.value.attendee_ids = [...new Set(participantDraftIds.value)];
  draft.value.absentees = absenteesDraft.value;
  participantDialogVisible.value = false;
}
async function closeEditor() {
  if (dirty.value && !(await save(false))) return;
  editing.value = false;
  selectedVersion.value = meeting.value?.published_version || null;
  basicInfoEditing.value = false;
  participantDialogVisible.value = false;
}
async function upload(e: Event) {
  const input = e.target as HTMLInputElement;
  const f = input.files?.[0];
  if (!f) return;
  try {
    if (!meeting.value && !(await save())) return;
    const form = new FormData();
    form.append("file", f);
    const a = await api("/files?meeting_id=" + meeting.value.id, "POST", form);
    draft.value.attachment_ids.push(a.id);
    meeting.value.attachments = [...(meeting.value.attachments || []), a];
    ElMessage.success("附件已上传");
  } finally {
    input.value = "";
  }
}
async function extend(item: any) {
  if (
    !editable.value ||
    selectedVersion.value ||
    ["已完成", "关闭", "不统计"].includes(item.status)
  )
    return;
  await ElMessageBox.confirm("是否确定将此事项添加到本周任务单？", "延续安排", {
    confirmButtonText: "确定添加",
    cancelButtonText: "取消",
    type: "warning",
  });
  if (
    !editable.value ||
    selectedVersion.value ||
    ["已完成", "关闭", "不统计"].includes(item.status)
  )
    return;
  const next = JSON.parse(JSON.stringify(item));
  next.id = clientId();
  next.section = "new";
  next.status = "未到期";
  draft.value.items.push(next);
}
function beforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value) {
    e.preventDefault();
    e.returnValue = "";
  }
}
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => {
  pageGeneration += 1;
  activeSave = null;
  cancelAutosave();
  window.removeEventListener("beforeunload", beforeUnload);
});
async function beforeNavigation() {
  // A route parameter change reuses this component, so settle the old save first.
  if (activeSave) await activeSave;
  cancelAutosave();
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        "存在尚未保存的内容，确认离开？",
        "未保存的修改",
        { type: "warning" },
      );
      return true;
    } catch {
      if (meeting.value && editable.value && !saveError.value)
        scheduleAutosave();
      return false;
    }
  }
  return true;
}
onBeforeRouteLeave(beforeNavigation);
onBeforeRouteUpdate((to, from) => {
  if (to.params.id === from.params.id) return true;
  // The first successful POST only replaces /new with the new ID.
  if (from.params.id === "new" && to.params.id === createdRouteId) return true;
  return beforeNavigation();
});
</script>
<template>
  <div v-loading="loading">
    <section class="page-heading">
      <div>
        <h1>{{ editable ? "在线会议编辑" : "会议纪要" }}</h1>
        <div class="meeting-breadcrumb">
          <RouterLink to="/meetings">会议系列</RouterLink>
          <template v-if="archiveSeriesKey">
            <span>›</span>
            <RouterLink
              :to="{ path: '/meetings', query: { series: archiveSeriesKey } }"
              >{{ archiveSeriesName }}</RouterLink
            >
          </template>
          <span>›</span>
          <span>{{ editable ? "在线会议编辑" : "会议纪要详情" }}</span>
        </div>
      </div>
      <div class="actions">
        <span class="muted small" role="status">{{
          saving
            ? "正在保存…"
            : dirty
              ? "有未保存内容"
              : meeting
                ? "已保存"
                : ""
        }}</span
        ><template v-if="editable"
          ><el-button
            v-if="meeting?.published_version"
            @click="run(closeEditor)"
            >返回详情</el-button
          ><el-button :loading="saving" @click="save()">保存草稿</el-button
          ><el-button type="primary" @click="run(openPreview)"
            >预览并发布</el-button
          ></template
        ><template v-else
          ><el-button
            v-if="meeting?.can_edit && meeting?.has_draft"
            type="primary"
            @click="openEditor"
            >编辑会议</el-button
          ><el-button v-else-if="meeting?.can_edit" @click="run(revise)"
            >创建修订版本</el-button
          ><el-button
            v-else-if="meeting && meeting.my_edit_request?.state !== 'pending'"
            type="primary"
            @click="run(requestEdit)"
            >申请编辑会议</el-button
          ><span
            v-if="
              !meeting?.can_edit &&
              meeting?.my_edit_request?.state === 'rejected'
            "
            class="muted small"
            >上次申请未通过，可重新申请</span
          ><el-tag
            v-if="
              !meeting?.can_edit &&
              meeting?.my_edit_request?.state === 'pending'
            "
            type="warning"
            >编辑申请待主持人审批</el-tag
          ></template
        ><el-button v-if="meeting?.can_continue" @click="run(continueMeeting)"
          >接续新建</el-button
        ><el-button
          v-if="meeting?.can_edit && meeting?.published_version"
          @click="run(openArchiveSeriesDialog)"
          >调整归档系列</el-button
        ><el-button
          v-if="meeting?.can_delete"
          type="danger"
          plain
          @click="run(removeMeeting)"
          >删除会议</el-button
        >
      </div>
    </section>
    <section
      v-if="meeting?.pending_edit_requests?.length"
      class="panel section-body"
    >
      <h2>待审批的编辑申请</h2>
      <div
        v-for="request in meeting.pending_edit_requests"
        :key="request.id"
        class="meeting-approval-row"
      >
        <span>{{ request.applicant_name }}申请编辑及接续新建本场会议</span>
        <span class="actions">
          <el-button
            type="primary"
            size="small"
            @click="run(() => decideEdit(request, 'approved'))"
            >通过</el-button
          >
          <el-button
            size="small"
            @click="run(() => decideEdit(request, 'rejected'))"
            >拒绝</el-button
          >
        </span>
      </div>
    </section>
    <el-alert
      v-if="saveError"
      :title="'草稿未保存：' + saveError"
      type="error"
      :closable="false"
    />
    <div v-if="meeting?.published_version" class="panel toolbar">
      <el-select
        v-model="selectedVersion"
        placeholder="当前草稿"
        clearable
        aria-label="查看版本"
        ><el-option
          v-for="v in meeting.versions"
          :key="v.number"
          :label="'发布版本 V' + v.number"
          :value="v.number" /></el-select
      ><el-button
        v-for="[fmt, label] in [
          ['xlsx', 'Excel 纪要'],
          ['pdf', 'PDF 纪要'],
          ['tasks', 'Excel 任务单'],
        ]"
        :key="fmt"
        @click="
          run(() =>
            download(
              '/meetings/' +
                meeting.id +
                '/export/' +
                fmt +
                '?version=' +
                (selectedVersion || meeting.published_version),
              draft.title + (fmt === 'pdf' ? '.pdf' : '.xlsx'),
            ),
          )
        "
        >{{ label }}</el-button
      >
    </div>
    <article
      v-if="show"
      class="meeting-editor"
      :class="{ 'meeting-readonly': readOnlyView }"
    >
      <el-form label-position="top">
        <div class="meeting-overview-grid">
          <section
            v-if="readOnlyView || !basicInfoEditing"
            class="panel meeting-info-card meeting-display-card"
          >
            <header class="section-card-heading">
              <div class="section-title">
                <span class="section-icon"
                  ><el-icon><Calendar /></el-icon
                ></span>
                <h2>会议基本信息</h2>
              </div>
              <div class="meeting-card-actions">
                <el-button
                  v-if="route.params.id === 'new' || meeting?.can_edit"
                  size="small"
                  @click="run(editCard)"
                  ><el-icon><Edit /></el-icon> 编辑</el-button
                >
                <span class="meeting-version-badge">{{
                  meeting?.published_version
                    ? meeting.historical
                      ? "历史会议"
                      : "已发布"
                    : "草稿"
                }}</span>
              </div>
            </header>
            <dl class="meeting-facts">
              <div>
                <dt>会议名称</dt>
                <dd>{{ show.title || "—" }}</dd>
              </div>
              <div>
                <dt>会议类型</dt>
                <dd>
                  <span class="meeting-kind-badge">{{
                    show.kind === "special" ? "专项会议" : "部门例会"
                  }}</span>
                </dd>
              </div>
              <div>
                <dt>会议时间</dt>
                <dd>
                  {{ show.meeting_date || "—" }} {{ show.meeting_time || "" }}
                </dd>
              </div>
              <div>
                <dt>主持人</dt>
                <dd>{{ person(show.host_id) }}</dd>
              </div>
              <div>
                <dt>会议地点</dt>
                <dd>{{ show.location || "—" }}</dd>
              </div>
              <div>
                <dt>编制人</dt>
                <dd>{{ show.recorder_id ? person(show.recorder_id) : "—" }}</dd>
              </div>
              <div v-if="show.kind === 'department'">
                <dt>所属部门</dt>
                <dd>{{ departmentPath(show.department_id) || "—" }}</dd>
              </div>
              <div
                v-if="
                  meeting?.published_version
                    ? meeting?.series_id
                    : show.series_id
                "
              >
                <dt>会议系列</dt>
                <dd>
                  {{
                    state.directory.series.find(
                      (s) =>
                        s.id ===
                        (meeting?.published_version
                          ? meeting.series_id
                          : show.series_id),
                    )?.name || "—"
                  }}
                </dd>
              </div>
              <div>
                <dt>会议状态</dt>
                <dd>
                  <span class="meeting-complete-badge">{{
                    meeting?.published_version ? "已发布" : "草稿"
                  }}</span>
                </dd>
              </div>
            </dl>
          </section>
          <section class="panel attendee-card meeting-display-card">
            <header class="section-card-heading">
              <div class="section-title">
                <span class="section-icon"
                  ><el-icon><UserFilled /></el-icon
                ></span>
                <h2>参会人员</h2>
              </div>
              <div class="meeting-card-actions">
                <el-button
                  v-if="route.params.id === 'new' || meeting?.can_edit"
                  size="small"
                  type="primary"
                  plain
                  @click="run(openParticipantsDialog)"
                  >＋ 新增</el-button
                >
                <span class="count">{{ participants.length }} 人</span>
              </div>
            </header>
            <div class="attendee-card-body">
              <div v-if="participants.length" class="attendee-people">
                <div
                  v-for="participant in visibleParticipants"
                  :key="participant.id"
                  class="attendee-person"
                >
                  <span class="person-avatar">{{
                    participant.name.slice(0, 1)
                  }}</span>
                  <strong>{{ participant.name }}</strong>
                  <small>{{ participant.role }}</small>
                </div>
                <button
                  v-if="participants.length > 5"
                  type="button"
                  class="attendee-more"
                  :aria-label="
                    participantsExpanded
                      ? '收起参会人员'
                      : `查看更多参会人员，另有 ${participants.length - 4} 人`
                  "
                  @click="participantsExpanded = !participantsExpanded"
                >
                  <span class="person-avatar">{{
                    participantsExpanded ? "⌃" : `+${participants.length - 4}`
                  }}</span>
                  <strong>{{ participantsExpanded ? "收起" : "更多" }}</strong>
                  <small>{{
                    participantsExpanded
                      ? "仅显示前 4 位"
                      : `另有 ${participants.length - 4} 人`
                  }}</small>
                </button>
              </div>
              <p v-else class="muted">暂无参会人员</p>
              <p v-if="show.absentees" class="meeting-absentees">
                缺席人员及原因：{{ show.absentees }}
              </p>
            </div>
          </section>
          <section
            v-if="!readOnlyView && basicInfoEditing"
            class="panel meeting-info-card"
          >
            <header class="section-card-heading">
              <div class="section-title">
                <span class="section-icon"
                  ><el-icon><Calendar /></el-icon
                ></span>
                <h2>会议基本信息</h2>
              </div>
              <el-button size="small" @click="basicInfoEditing = false"
                >完成</el-button
              >
            </header>
            <div class="meeting-meta-grid">
              <el-form-item class="span-2" label="会议名称">
                <el-input
                  v-model="show.title"
                  placeholder="请输入会议名称"
                  aria-label="会议名称"
                />
              </el-form-item>
              <el-form-item label="会议类型">
                <el-select v-model="show.kind" @change="meetingKindChanged">
                  <el-option label="部门例会" value="department" />
                  <el-option label="专项会议" value="special" />
                </el-select>
              </el-form-item>
              <el-form-item v-if="show.kind === 'department'" label="所属部门">
                <el-select
                  v-model="show.department_id"
                  filterable
                  placeholder="请选择所属部门"
                >
                  <el-option
                    v-for="d in state.directory.departments"
                    :key="d.id"
                    :label="departmentPath(d.id)"
                    :value="d.id"
                  />
                </el-select>
              </el-form-item>
              <el-form-item v-else label="所属部门">
                <el-input model-value="专项会议不需要填写部门" disabled />
              </el-form-item>
              <el-form-item label="会议日期">
                <el-date-picker
                  v-model="show.meeting_date"
                  value-format="YYYY-MM-DD"
                  type="date"
                />
              </el-form-item>
              <el-form-item label="时间">
                <el-input
                  v-model="show.meeting_time"
                  placeholder="如 09:00–10:00"
                />
              </el-form-item>
              <el-form-item label="地点">
                <el-input
                  v-model="show.location"
                  placeholder="请输入会议地点"
                />
              </el-form-item>
              <el-form-item label="会议系列">
                <el-select
                  v-model="show.series_id"
                  clearable
                  @change="
                    (value) => {
                      if (value && !meeting?.published_version) run(carry);
                    }
                  "
                >
                  <el-option
                    v-for="s in state.directory.series"
                    :key="s.id"
                    :label="s.name"
                    :value="s.id"
                  />
                </el-select>
                <el-button
                  v-if="editable"
                  link
                  type="primary"
                  @click="run(newSeries)"
                  >新建系列</el-button
                >
              </el-form-item>
              <el-form-item label="主持人">
                <PeopleSelect v-model="show.host_id" />
              </el-form-item>
              <el-form-item label="编制人（选填）">
                <PeopleSelect
                  v-model="show.recorder_id"
                  placeholder="可留空，输入姓名搜索人员"
                />
              </el-form-item>
            </div>
          </section>
        </div>

        <section class="panel document-section meeting-data-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><List /></el-icon
              ></span>
              <h2>上期会议任务跟踪情况</h2>
            </div>
            <el-button v-if="editable && !selectedVersion" @click="run(carry)"
              >带入上期任务</el-button
            >
            <el-input
              v-else
              v-model="reviewSearch"
              class="meeting-table-search"
              clearable
              placeholder="搜索任务名称、责任人…"
              aria-label="搜索上期任务"
            />
          </header>
          <p class="section-note">
            只有此分区纳入本次达成率。多人共同任务，每位责任人各计一次。
          </p>
          <div class="table-scroll">
            <table class="minutes-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>项目 / 工作内容及要求</th>
                  <th>责任人</th>
                  <th>原计划完成时间</th>
                  <th>完成结果</th>
                  <th>备注</th>
                  <th v-if="editable">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, i) in visibleReview" :key="item.id">
                  <td>{{ Number(i) + 1 }}</td>
                  <td class="content-cell">
                    <p>{{ mergedWorkContent(item) }}</p>
                  </td>
                  <td>
                    {{
                      (item.owner_names || item.owner_ids.map(person)).join(
                        "、",
                      )
                    }}
                  </td>
                  <td>{{ item.due_date || "—" }}</td>
                  <td>
                    <el-select
                      v-if="editable && !selectedVersion"
                      v-model="item.status"
                    >
                      <el-option
                        v-for="s in statuses"
                        :key="s"
                        :label="s"
                        :value="s"
                      />
                    </el-select>
                    <StatusTag v-else :value="item.status" />
                  </td>
                  <td>
                    <el-input
                      v-if="editable && !selectedVersion"
                      v-model="item.notes"
                      type="textarea"
                      autosize
                    />
                    <span v-else>{{ item.notes }}</span>
                  </td>
                  <td v-if="editable">
                    <el-button
                      v-if="!['已完成', '关闭', '不统计'].includes(item.status)"
                      link
                      type="primary"
                      @click="run(() => extend(item))"
                      >延续安排</el-button
                    >
                  </td>
                </tr>
                <tr v-if="!visibleReview.length">
                  <td colspan="7" class="empty-cell">
                    {{
                      reviewSearch
                        ? "没有匹配的上期任务"
                        : "暂无上期任务，选择会议系列后可带入。"
                    }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel document-section meeting-data-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Memo /></el-icon
              ></span>
              <h2>本次会议议定事项</h2>
            </div>
          </header>
          <div class="section-body">
            <p v-if="readOnlyView" class="meeting-decision-text">
              {{ show.decisions || "暂无会议议定事项" }}
            </p>
            <el-input
              v-else
              v-model="show.decisions"
              type="textarea"
              :autosize="{ minRows: 3 }"
              placeholder="记录会议形成的正式决议"
            />
          </div>
        </section>

        <section class="panel document-section meeting-data-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Tickets /></el-icon
              ></span>
              <h2>本次会议任务部署</h2>
            </div>
            <el-button
              v-if="editable && !selectedVersion"
              type="primary"
              @click="add"
              >＋ 新增工作</el-button
            >
            <el-input
              v-else
              v-model="arrangementSearch"
              class="meeting-table-search"
              clearable
              placeholder="搜索工作内容、责任人…"
              aria-label="搜索本次工作"
            />
          </header>
          <p class="section-note">
            本次安排不计入本次达成率。新任务必须填写计划完成时间，草稿可先保存。验证人未填时默认为
            {{ person(show.host_id) }}。
          </p>
          <div class="table-scroll">
            <table class="minutes-table meeting-plan-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>工作内容及要求</th>
                  <th>责任人</th>
                  <th>
                    计划完成时间<span
                      v-if="editable && !selectedVersion"
                      class="required-mark"
                    >
                      *</span
                    >
                  </th>
                  <th>验证人</th>
                  <th>输出物</th>
                  <th>备注</th>
                  <th v-if="editable && !selectedVersion">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, i) in visibleArrangements" :key="item.id">
                  <td>{{ Number(i) + 1 }}</td>
                  <td class="content-cell">
                    <el-input
                      v-if="editable && !selectedVersion"
                      :model-value="mergedWorkContent(item)"
                      @update:model-value="updateWorkContent(item, $event)"
                      type="textarea"
                      autosize
                      aria-label="工作内容及要求"
                    />
                    <p v-else>{{ mergedWorkContent(item) }}</p>
                    <el-input
                      v-if="editable && !selectedVersion && item.task_id"
                      v-model="item.change_reason"
                      placeholder="关联任务变更原因"
                      aria-label="关联任务变更原因"
                    />
                  </td>
                  <td>
                    <PeopleSelect
                      v-if="editable && !selectedVersion"
                      v-model="item.owner_ids"
                      multiple
                    />
                    <span v-else>{{
                      (item.owner_names || item.owner_ids.map(person)).join(
                        "、",
                      )
                    }}</span>
                  </td>
                  <td>
                    <el-date-picker
                      v-if="editable && !selectedVersion"
                      v-model="item.due_date"
                      type="date"
                      value-format="YYYY-MM-DD"
                      placeholder="请选择计划完成时间"
                      :clearable="false"
                      aria-label="计划完成时间"
                      aria-required="true"
                    />
                    <span v-if="!editable || selectedVersion">{{
                      item.due_date || "—"
                    }}</span>
                  </td>
                  <td>
                    <PeopleSelect
                      v-if="editable && !selectedVersion"
                      v-model="item.verifier_id"
                      :placeholder="'默认：' + person(show.host_id)"
                    />
                    <span v-else>{{
                      item.verifier_name ||
                      person(item.verifier_id || show.host_id)
                    }}</span>
                  </td>
                  <td>
                    <el-input
                      v-if="editable && !selectedVersion"
                      v-model="item.deliverable"
                      aria-label="输出物"
                    />
                    <span v-else>{{ item.deliverable || "—" }}</span>
                  </td>
                  <td>
                    <el-input
                      v-if="editable && !selectedVersion"
                      v-model="item.notes"
                      type="textarea"
                      autosize
                      aria-label="备注"
                    />
                    <span v-else>{{ item.notes || "—" }}</span>
                  </td>
                  <td v-if="editable && !selectedVersion">
                    <el-button
                      link
                      type="primary"
                      @click="run(() => suggest(item))"
                      >{{
                        item.task_id ? "重新关联" : "关联已有任务"
                      }}</el-button
                    >
                    <el-button
                      link
                      type="danger"
                      aria-label="删除此工作行"
                      @click="
                        draft.items = draft.items.filter(
                          (x) => x.id !== item.id,
                        )
                      "
                      >删除</el-button
                    >
                  </td>
                </tr>
                <tr v-if="!visibleArrangements.length">
                  <td
                    :colspan="editable && !selectedVersion ? 8 : 7"
                    class="empty-cell"
                  >
                    {{
                      arrangementSearch
                        ? "没有匹配的本次工作"
                        : "暂无本次工作安排"
                    }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel meeting-signoff-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Memo /></el-icon
              ></span>
              <h2>会议附件</h2>
            </div>
          </header>
          <div class="attachment-panel-body">
            <div class="attachment-actions">
              <label v-if="editable" class="upload-button"
                >上传会议附件<input
                  type="file"
                  @change="(e) => run(() => upload(e))"
              /></label>
              <el-button
                v-for="a in meeting?.attachments || []"
                :key="a.id"
                link
                type="primary"
                @click="run(() => download('/files/' + a.id, a.filename))"
                >{{ a.filename }}</el-button
              >
            </div>
          </div>
        </section>
      </el-form>
    </article>
    <section v-if="meeting?.published_version" class="panel section-body">
      <h2>考核与任务追溯 · V{{ assessmentVersion }}</h2>
      <p v-if="assessment" class="muted">
        {{
          assessment.effective
            ? "当前有效发布版本"
            : "历史版本，仅供追溯，不计当前报表"
        }}；回顾 {{ assessment.items }} 项、{{
          assessment.person_count
        }}
        人次，达成率 {{ rate(assessment.rate) }}。本次新安排不计本次达成率。
      </p>
      <el-collapse>
        <el-collapse-item title="本会议考核明细" name="assessments">
          <el-table
            :data="assessment?.details || []"
            empty-text="本版本没有上期回顾考核"
            max-height="480"
          >
            <el-table-column
              prop="user_name"
              label="责任人"
              width="110"
            /><el-table-column
              prop="person_department"
              label="考核时主部门"
              min-width="130"
            />
            <el-table-column
              prop="content"
              label="工作内容"
              min-width="280"
            /><el-table-column prop="due_date" label="原期限" width="125" />
            <el-table-column label="考核结果" width="115"
              ><template #default="{ row }"
                ><StatusTag :value="row.status" /></template></el-table-column
            ><el-table-column prop="notes" label="备注" min-width="160" />
          </el-table>
        </el-collapse-item>
        <el-collapse-item
          v-if="meeting.can_edit"
          title="相关任务通知（最近 200 条）"
          name="task-notifications"
        >
          <el-table
            :data="meeting.task_notifications || []"
            empty-text="暂无关联任务通知"
          >
            <el-table-column
              prop="title"
              label="通知"
              min-width="210"
            /><el-table-column label="任务编号" min-width="200"
              ><template #default="{ row }">{{
                row.task_codes.join("、")
              }}</template></el-table-column
            >
            <el-table-column label="接收人" min-width="150"
              ><template #default="{ row }">{{
                row.user_ids.map(person).join("、")
              }}</template></el-table-column
            >
            <el-table-column label="状态" width="150"
              ><template #default="{ row }">{{
                mailStates[row.state] || row.state
              }}</template></el-table-column
            >
            <el-table-column label="发送时间（北京时间）" min-width="190"
              ><template #default="{ row }">{{
                beijingTime(row.sent_at)
              }}</template></el-table-column
            ><el-table-column prop="last_error" label="原因" min-width="220" />
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </section>
    <section
      v-if="meeting?.can_edit && meeting?.published_version"
      class="panel section-body"
    >
      <h2>纪要邮件发送记录</h2>
      <el-table
        :data="meeting.notifications || []"
        empty-text="暂无邮件发送记录（历史导入和未勾选通知的发布不会发送邮件）"
      >
        <el-table-column prop="version" label="发布版本" width="100" />
        <el-table-column label="收件人／抄送人" min-width="200"
          ><template #default="{ row }"
            >{{ row.recipient_ids.map(person).join("、") || "—" }}
            <p class="muted small">
              抄送：{{ row.cc_ids.map(person).join("、") || "—" }}
            </p></template
          ></el-table-column
        >
        <el-table-column label="状态" width="150"
          ><template #default="{ row }">{{
            mailStates[row.state] || row.state
          }}</template></el-table-column
        >
        <el-table-column label="发送时间（北京时间）" min-width="190"
          ><template #default="{ row }">{{
            beijingTime(row.sent_at)
          }}</template></el-table-column
        >
        <el-table-column prop="last_error" label="失败原因" min-width="220" />
      </el-table>
    </section>
    <section
      v-if="
        meeting?.historical && meeting?.can_edit && meeting?.published_version
      "
      class="panel section-body"
    >
      <h2>继续跟进历史事项</h2>
      <p class="muted">
        仅在确认仍需跟进后激活。请重新核对期限、责任人和验证人。
      </p>
      <el-button
        @click="
          activation = {
            item_id: '',
            due_date: localDate(),
            owner_ids: [],
            verifier_id: draft.host_id,
            reason: '',
          }
        "
        >选择事项并激活</el-button
      >
    </section>
    <el-dialog
      v-model="participantDialogVisible"
      title="新增或调整参会人员"
      width="min(560px, 94vw)"
    >
      <el-form label-position="top">
        <el-form-item label="参会人员（可多选）">
          <PeopleSelect
            v-model="participantDraftIds"
            multiple
            popper-class="meeting-participants-popper"
            placeholder="输入姓名搜索并选择人员"
          />
        </el-form-item>
        <el-form-item label="缺席人员及原因">
          <el-input
            v-model="absenteesDraft"
            type="textarea"
            :autosize="{ minRows: 2 }"
            placeholder="如有缺席，请填写人员与原因"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="participantDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveParticipantsDialog"
          >确认</el-button
        >
      </template>
    </el-dialog>
    <el-dialog
      :model-value="!!activation"
      @close="activation = null"
      title="激活历史事项"
      width="min(600px,94vw)"
      ><el-form v-if="activation" label-position="top"
        ><el-form-item label="选择待跟进事项"
          ><el-select
            v-model="activation.item_id"
            @change="
              (id) => {
                const item = meeting.versions[0].snapshot.items.find(
                  (x) => x.id === id,
                );
                activation.owner_ids = item.owner_ids;
                activation.verifier_id = item.verifier_id || draft.host_id;
              }
            "
            ><el-option
              v-for="item in meeting.versions[0].snapshot.items.filter(
                (x) => !['已完成', '关闭', '不统计'].includes(x.status),
              )"
              :key="item.id"
              :label="item.content"
              :value="item.id" /></el-select></el-form-item
        ><el-form-item label="计划完成时间" required
          ><el-date-picker
            v-model="activation.due_date"
            value-format="YYYY-MM-DD"
            :clearable="false"
            aria-required="true"
            placeholder="请选择计划完成时间" /></el-form-item
        ><el-form-item label="责任人"
          ><PeopleSelect
            v-model="activation.owner_ids"
            multiple /></el-form-item
        ><el-form-item label="验证人"
          ><PeopleSelect v-model="activation.verifier_id" /></el-form-item
        ><el-form-item label="激活原因"
          ><el-input
            v-model="activation.reason"
            type="textarea" /></el-form-item></el-form
      ><template #footer
        ><el-button @click="activation = null">取消</el-button
        ><el-button type="primary" :loading="activating" @click="run(activate)"
          >激活并通知责任人</el-button
        ></template
      ></el-dialog
    ><el-dialog
      :model-value="!!linkItem"
      @close="linkItem = null"
      title="人工确认任务关联"
      width="min(850px,94vw)"
      ><p class="muted">
        根据内容、会议系列、责任人和期限排序，仅提供建议。确认后将更新原任务，不新建重复待办。
      </p>
      <el-table :data="suggestions"
        ><el-table-column
          prop="content"
          label="任务内容"
          min-width="250"
        /><el-table-column
          prop="due_date"
          label="原期限"
          width="120"
        /><el-table-column label="责任人" width="140"
          ><template #default="{ row }">{{
            row.owner_names.join("、")
          }}</template></el-table-column
        ><el-table-column label="关联依据" min-width="170"
          ><template #default="{ row }">{{
            row.match_reasons.join("；") || "工作内容相似"
          }}</template></el-table-column
        ><el-table-column label="操作" width="120"
          ><template #default="{ row }"
            ><el-button type="primary" link @click="linkTask(row)"
              >确认关联</el-button
            ></template
          ></el-table-column
        ></el-table
      ></el-dialog
    ><el-dialog
      v-model="archiveSeriesDialogVisible"
      title="调整归档系列"
      width="min(440px,92vw)"
      ><p class="muted">选择归档分组，不改动已发布的纪要正文和任务内容。</p>
      <el-select
        v-model="archiveSeriesDraftId"
        filterable
        placeholder="请选择会议系列"
        style="width: 100%"
      >
        <el-option
          v-for="series in state.directory.series"
          :key="series.id"
          :label="series.name"
          :value="series.id"
        />
      </el-select>
      <template #footer>
        <el-button @click="archiveSeriesDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="archiveSeriesSaving"
          @click="run(moveArchiveSeries)"
          >确认调整</el-button
        >
      </template></el-dialog
    ><el-dialog
      v-model="preview"
      class="publication-dialog"
      top="4vh"
      title="确认发布会议纪要"
      width="min(1080px,94vw)"
      ><p>
        发布后保留不可覆盖的版本。本次工作将下发给所选责任人，历史考核单独保存。
      </p>
      <el-descriptions v-if="draft" :column="2" border
        ><el-descriptions-item label="会议">{{
          draft.title
        }}</el-descriptions-item
        ><el-descriptions-item label="日期">{{
          draft.meeting_date
        }}</el-descriptions-item
        ><el-descriptions-item label="回顾事项">{{
          review.length
        }}</el-descriptions-item
        ><el-descriptions-item label="本次安排">{{
          arrangements.length
        }}</el-descriptions-item></el-descriptions
      >
      <section class="section-body" v-loading="previewLoading">
        <div class="filter-row">
          <el-button @click="run(refreshPreview)">刷新 PDF 预览</el-button
          ><el-button @click="run(previewTasks)"
            >下载草稿任务单（Excel）</el-button
          >
        </div>
        <iframe
          v-if="previewUrl"
          :src="previewUrl"
          title="发布前 PDF 纪要预览"
          style="
            width: 100%;
            height: 460px;
            border: 1px solid #d8e0eb;
            margin-top: 12px;
          "
        />
        <p v-else class="muted">
          补全会议信息和工作行后可生成预览；预览不会创建任务或发送通知。
        </p>
      </section>
      <el-form label-position="top" class="section-body"
        ><el-form-item label="发布 / 修订说明"
          ><el-input v-model="publishReason" /></el-form-item
        ><el-form-item label="邮件收件人"
          ><PeopleSelect v-model="draft.recipient_ids" multiple /><el-button
            link
            type="primary"
            @click="
              draft.recipient_ids = recipientCandidates
                .map((u) => u.id)
                .filter((id) => !draft.cc_ids.includes(id))
            "
            >采用建议名单（{{ recipientCandidates.length }} 人）</el-button
          >
          <p class="muted small">
            建议包含已配置邮箱的参会人员、责任人、验证人和签署人，请确认后发布。
          </p></el-form-item
        ><el-form-item label="抄送人"
          ><PeopleSelect v-model="draft.cc_ids" multiple /></el-form-item
        ><el-checkbox v-model="notify" :disabled="meeting?.historical"
          >发送企业微信通知及纪要邮件</el-checkbox
        >
        <p class="muted small">
          附件：PDF 会议纪要、Excel 任务单。发送结果可在会议详情查看。
        </p></el-form
      ><template #footer
        ><el-button @click="preview = false">返回编辑</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="previewLoading || !previewUrl"
          @click="
            run(async () => {
              if (await save(false)) await publish();
            })
          "
          >确认发布</el-button
        ></template
      ></el-dialog
    >
  </div>
</template>

<style scoped>
.meeting-approval-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}
@media (max-width: 640px) {
  .meeting-approval-row {
    flex-wrap: wrap;
  }
}
</style>
