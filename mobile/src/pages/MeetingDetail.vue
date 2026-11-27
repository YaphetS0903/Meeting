<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showConfirmDialog, showSuccessToast, showToast } from "vant";
import {
  addDays,
  api,
  ApiResponseError,
  clientId,
  departmentPath,
  download,
  fetchAuthBlob,
  kinds,
  localDate,
  person,
  previewFile,
  query,
  rate,
  run,
  state,
  statuses,
  thisFriday,
} from "../api/client";
import { chineseError } from "../api/errorMessages";
import {
  attendeeRecipientIds,
  confirmAssignedTodo,
  normalizeMailbox,
  syncAttendeeRecipients,
} from "../api/nativeAssignment";
import { sortReviewsByResult } from "../api/reportDepartments";
import { beijingTime } from "../api/time";
import { insideWeCom } from "../api/wecom";
import PdfViewer from "../components/PdfViewer.vue";
import PeoplePicker from "../components/PeoplePicker.vue";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();

const isNewRoute = computed(() => String(route.params.id) === "new");
const seriesParam = computed(() =>
  typeof route.query.series === "string" ? route.query.series : "",
);

const loading = ref(false);
const saving = ref(false);
const publishing = ref(false);
const meeting = ref<any>(null);
const draft = ref<any>(null);
const dirty = ref(false);
const activeTab = ref<"info" | "review" | "current" | "files">("info");
const reviewSearch = ref("");
const meetingUsers = ref<any[]>([]);
const meetingDepartments = ref<any[]>([]);
const seriesLocked = ref(false);

const setupWizardOpen = ref(false);
const actionSheetOpen = ref(false);
const versionSheetOpen = ref(false);
const selectedVersion = ref<number | null>(null);

// Work item full-screen editor sheet
const workSheetOpen = ref(false);
const editingWorkIdx = ref<number | null>(null);
const workForm = ref<any>(null);

// Postpone sheet
const postponeOpen = ref(false);
const postponeItem = ref<any>(null);
const postponeDate = ref("");

// Per-owner mixed results sheet
const ownerResultsOpen = ref(false);
const ownerResultsItem = ref<any>(null);
const ownerResultsMap = ref<Record<string, "已完成" | "未完成">>({});

// Preview & Publish sheet
const previewOpen = ref(false);
const previewBlob = ref<Blob | null>(null);
const previewLoading = ref(false);
const previewError = ref("");
const notifyEnabled = ref(true);
const ccEmailInput = ref("");
const publishWecomTodo = ref(true);
const previousAttendees = ref<string[]>([]);

// Standalone published PDF viewer
const pdfPopupOpen = ref(false);
const publishedPdfBlob = ref<Blob | null>(null);
const publishedPdfLoading = ref(false);

// Post-publish WeCom native todo confirmation sheet
const nativeTodoSheet = ref(false);
const nativeTodoTasks = ref<any[]>([]);
const nativeTodoStatus = ref<Record<string, string>>({});

const editable = computed(() => {
  if (selectedVersion.value) return false;
  if (isNewRoute.value && !meeting.value) return true;
  return Boolean(meeting.value?.has_draft && meeting.value?.can_edit);
});

const canAssess = computed(
  () => editable.value && (meeting.value?.can_assess ?? true),
);

const allUsers = computed(() =>
  meetingUsers.value.length ? meetingUsers.value : state.directory.users || [],
);
const allDepartments = computed(() =>
  meetingDepartments.value.length
    ? meetingDepartments.value
    : state.directory.departments || [],
);

const viewData = computed(() => {
  if (selectedVersion.value && meeting.value?.versions?.length) {
    const v = meeting.value.versions.find(
      (x: any) => x.version === selectedVersion.value,
    );
    if (v?.snapshot) return v.snapshot;
  }
  return draft.value;
});

const reviewItems = computed(() =>
  (viewData.value?.items || []).filter((it: any) => it.section === "review"),
);
const filteredReviewItems = computed(() => {
  const q = reviewSearch.value.trim().toLowerCase();
  if (!q) return reviewItems.value;
  return reviewItems.value.filter((it: any) => {
    const owners = (it.owner_ids || []).map(person).join("、");
    return (
      String(it.content || "").toLowerCase().includes(q) ||
      owners.toLowerCase().includes(q)
    );
  });
});

const currentItems = computed(() =>
  (viewData.value?.items || []).filter((it: any) => it.section === "new"),
);
const carryCurrentItems = computed(() =>
  currentItems.value.filter(
    (it: any) =>
      it.source?.carried_from_review_id ||
      it.source?.continuation_origin === "extension" ||
      (it.task_id &&
        reviewItems.value.some(
          (r: any) => r.task_id === it.task_id && r.status === "未完成",
        )),
  ),
);
const newCurrentItems = computed(() => {
  const carrySet = new Set(carryCurrentItems.value.map((x: any) => x.id));
  return currentItems.value.filter((it: any) => !carrySet.has(it.id));
});

const onsiteAttendees = computed(() => {
  const d = viewData.value;
  if (!d) return [];
  const online = new Set(d.online_attendee_ids || []);
  return (d.attendee_ids || []).filter((id: string) => !online.has(id));
});

const attendeeCount = computed(() => {
  const d = viewData.value;
  if (!d) return 0;
  return new Set([d.host_id, ...(d.attendee_ids || [])].filter(Boolean)).size;
});

function updateAttendees(mode: "onsite" | "online", ids: string[]) {
  if (!draft.value) return;
  const other =
    mode === "onsite"
      ? draft.value.online_attendee_ids || []
      : onsiteAttendees.value;
  const remaining = other.filter((id: string) => !ids.includes(id));
  const online = mode === "online" ? ids : remaining;
  const physical = mode === "onsite" ? ids : remaining;
  draft.value.attendee_ids = [...new Set([...physical, ...online])].filter(
    (id) => id !== draft.value.host_id,
  );
  draft.value.online_attendee_ids = online;
  markDirty();
}

function markDirty() {
  dirty.value = true;
}

function emptyDraft() {
  const seriesId = seriesParam.value.startsWith("series:")
    ? seriesParam.value.slice(7)
    : null;
  const seriesObj = seriesId
    ? (state.directory.series || []).find((s: any) => s.id === seriesId)
    : null;
  const kind = seriesObj?.kind === "special" ? "special" : "department";
  seriesLocked.value = Boolean(seriesObj?.kind);
  return {
    title: "",
    department_id:
      kind === "department"
        ? seriesObj?.department_id || state.me?.department_id || null
        : null,
    series_id: seriesId,
    kind,
    meeting_date: localDate(),
    meeting_time: "",
    location: "",
    host_id: state.me?.id || "",
    recorder_id: state.me?.id || null,
    reviewer_id: null,
    attendee_ids: [],
    online_attendee_ids: [],
    absentees: "",
    decisions: "",
    items: [],
    attachment_ids: [],
    recipient_ids: [],
    recipient_customized: false,
    cc_ids: [],
    cc_emails: [],
    history_backfill: false,
  };
}

async function loadPeopleOptions() {
  try {
    const res = await api(
      "/meeting-people?" +
        query({
          department_id: draft.value?.department_id || undefined,
          meeting_id: meeting.value?.id || undefined,
        }),
      "GET",
      undefined,
      { background: true },
    );
    if (res.users) meetingUsers.value = res.users;
    if (res.departments) meetingDepartments.value = res.departments;
  } catch {
    /* Fall back to global directory */
  }
}

async function loadMeeting() {
  loading.value = true;
  selectedVersion.value = null;
  dirty.value = false;
  try {
    if (isNewRoute.value) {
      meeting.value = null;
      draft.value = emptyDraft();
      previousAttendees.value = attendeeRecipientIds(draft.value);
      await loadPeopleOptions();
      setupWizardOpen.value = true;
      activeTab.value = "current";
      return;
    }
    const res = await api("/meetings/" + route.params.id);
    meeting.value = res;
    draft.value = JSON.parse(
      JSON.stringify(res.draft || res.published_snapshot || emptyDraft()),
    );
    if (!draft.value.online_attendee_ids) draft.value.online_attendee_ids = [];
    if (!draft.value.cc_emails) draft.value.cc_emails = [];
    if (!draft.value.recipient_ids) draft.value.recipient_ids = [];
    if (!draft.value.cc_ids) draft.value.cc_ids = [];
    previousAttendees.value = attendeeRecipientIds(draft.value);
    seriesLocked.value = Boolean(res.series_kind_locked);
    notifyEnabled.value = !draft.value.history_backfill;
    await loadPeopleOptions();
    if (reviewItems.value.length && activeTab.value === "info") {
      activeTab.value = res.has_draft ? "review" : "info";
    }
  } finally {
    loading.value = false;
  }
}

async function confirmNewWizard() {
  if (!draft.value.title?.trim()) {
    showToast("请填写会议名称");
    return;
  }
  if (!draft.value.meeting_date) {
    showToast("请选择会议日期");
    return;
  }
  if (draft.value.kind === "department" && !draft.value.department_id) {
    showToast("请选择所属部门");
    return;
  }
  if (!draft.value.host_id) {
    showToast("请选择主持人");
    return;
  }
  draft.value.title = draft.value.title.trim();
  const ok = await saveDraft(true);
  if (ok) setupWizardOpen.value = false;
}

async function saveDraft(manual = true): Promise<boolean> {
  if (!draft.value || !editable.value) return false;
  if (!draft.value.title?.trim()) {
    showToast("请填写会议名称后再保存草稿");
    activeTab.value = "info";
    return false;
  }
  if (draft.value.kind === "department" && !draft.value.department_id) {
    showToast("部门例会请选择所属部门");
    activeTab.value = "info";
    return false;
  }
  saving.value = true;
  try {
    const payload = JSON.parse(JSON.stringify(draft.value));
    const res = meeting.value?.id
      ? await api(`/meetings/${meeting.value.id}`, "PUT", {
          expected_version: meeting.value.draft_version,
          draft: payload,
        })
      : await api("/meetings", "POST", payload);
    meeting.value = {
      versions: [],
      attachments: [],
      ...meeting.value,
      ...res,
      can_edit: true,
    };
    dirty.value = false;
    if (isNewRoute.value && res.id) {
      await router.replace({
        path: `/meetings/${res.id}`,
        query: route.query,
      });
    }
    if (manual) showSuccessToast("草稿已保存");
    return true;
  } catch (e: any) {
    showToast(chineseError(e, "草稿保存失败"));
    return false;
  } finally {
    saving.value = false;
  }
}

// Review operations
function setReviewStatus(item: any, statusVal: string) {
  if (!canAssess.value) return;
  item.status = statusVal;
  item.owner_results = {};
  if (statusVal !== "未到期") item.postponed_due_date = null;
  markDirty();
}

function openOwnerResults(item: any) {
  ownerResultsItem.value = item;
  const map: Record<string, "已完成" | "未完成"> = {};
  for (const oid of item.owner_ids || []) {
    map[oid] = item.owner_results?.[oid] || "已完成";
  }
  ownerResultsMap.value = map;
  ownerResultsOpen.value = true;
}

function saveOwnerResults() {
  if (!ownerResultsItem.value) return;
  const vals = new Set(Object.values(ownerResultsMap.value));
  if (vals.size === 1) {
    ownerResultsItem.value.status = [...vals][0];
    ownerResultsItem.value.owner_results = {};
  } else {
    ownerResultsItem.value.status = "部分完成";
    ownerResultsItem.value.owner_results = { ...ownerResultsMap.value };
  }
  ownerResultsOpen.value = false;
  markDirty();
}

function hasCurrentCarry(reviewItem: any) {
  return currentItems.value.some(
    (c: any) =>
      c.source?.carried_from_review_id === reviewItem.id ||
      (reviewItem.task_id && c.task_id === reviewItem.task_id) ||
      (reviewItem.historical_work_id &&
        c.historical_work_id === reviewItem.historical_work_id),
  );
}

function openPostpone(item: any) {
  if (hasCurrentCarry(item)) {
    showToast("该事项已在本次安排中，请直接修改下方本次安排的截止日期");
    return;
  }
  postponeItem.value = item;
  postponeDate.value = item.postponed_due_date || addDays(7);
  postponeOpen.value = true;
}

function confirmPostpone() {
  const item = postponeItem.value;
  if (!item || !postponeDate.value) {
    showToast("请选择新的截止日期");
    return;
  }
  if (item.due_date && postponeDate.value <= item.due_date) {
    showToast("延期日期必须晚于原计划完成时间");
    return;
  }
  if (draft.value?.meeting_date && postponeDate.value < draft.value.meeting_date) {
    showToast("延期日期不能早于本次会议日期");
    return;
  }
  item.postponed_due_date = postponeDate.value;
  postponeOpen.value = false;
  markDirty();
  showSuccessToast("已设置延期日期");
}

function cancelPostpone(item: any) {
  item.postponed_due_date = null;
  markDirty();
}

function carryUnfinished(reviewItem: any) {
  if (hasCurrentCarry(reviewItem)) {
    activeTab.value = "current";
    showToast("该事项已在本次安排中");
    return;
  }
  const nextItem = {
    id: clientId(),
    section: "new",
    category: reviewItem.category || "",
    content: reviewItem.content || "",
    requirements: reviewItem.requirements || "",
    owner_ids: [...(reviewItem.owner_ids || [])],
    historical_owner_record: false,
    due_date: addDays(7),
    postponed_due_date: null,
    status: "未到期",
    owner_results: {},
    verifier_id: reviewItem.verifier_id || draft.value?.host_id || state.me?.id || null,
    deliverable: reviewItem.deliverable || "",
    notes: reviewItem.notes || "",
    exclusion_reason: "",
    task_id: reviewItem.task_id || null,
    task_version: reviewItem.task_version || null,
    change_reason: "上期未完成事项延续安排",
    source: {
      carried_from_review_id: reviewItem.id,
      continuation_origin: "extension",
    },
    historical_work_id: reviewItem.historical_work_id || null,
  };
  draft.value.items.push(nextItem);
  markDirty();
  activeTab.value = "current";
  openWorkEditor(draft.value.items.length - 1);
}

function sortReviewList() {
  if (!draft.value) return;
  const reviews = sortReviewsByResult(
    draft.value.items.filter((x: any) => x.section === "review"),
  );
  const others = draft.value.items.filter((x: any) => x.section !== "review");
  draft.value.items = [...reviews, ...others];
  markDirty();
  showSuccessToast("已按完成结果排序");
}

function moveSectionItem(section: "review" | "new", itemId: string, delta: number) {
  if (!draft.value) return;
  const sectionList = draft.value.items.filter(
    (x: any) => x.section === section,
  );
  const pos = sectionList.findIndex((x: any) => x.id === itemId);
  const target = pos + delta;
  if (pos < 0 || target < 0 || target >= sectionList.length) return;
  [sectionList[pos], sectionList[target]] = [
    sectionList[target],
    sectionList[pos],
  ];
  const otherList = draft.value.items.filter((x: any) => x.section !== section);
  draft.value.items =
    section === "review"
      ? [...sectionList, ...otherList]
      : [...otherList, ...sectionList];
  markDirty();
}

// Current work item editor
function openNewWork() {
  editingWorkIdx.value = null;
  workForm.value = {
    id: clientId(),
    section: "new",
    category: "",
    content: "",
    requirements: "",
    owner_ids: [],
    historical_owner_record: false,
    due_date: thisFriday(),
    postponed_due_date: null,
    status: "未到期",
    owner_results: {},
    verifier_id: draft.value?.host_id || state.me?.id || "",
    deliverable: "",
    notes: "",
    exclusion_reason: "",
    task_id: null,
    task_version: null,
    change_reason: "",
    source: {},
    historical_work_id: null,
  };
  workSheetOpen.value = true;
}

function openWorkEditor(idxOrId: number | string) {
  const idx =
    typeof idxOrId === "number"
      ? idxOrId
      : draft.value.items.findIndex((x: any) => x.id === idxOrId);
  if (idx < 0) return;
  editingWorkIdx.value = idx;
  workForm.value = JSON.parse(JSON.stringify(draft.value.items[idx]));
  if (!workForm.value.verifier_id) {
    workForm.value.verifier_id = draft.value?.host_id || "";
  }
  workSheetOpen.value = true;
}

function saveWorkForm(continueNext = false) {
  if (!workForm.value.content?.trim()) {
    showToast("请填写工作内容及要求");
    return;
  }
  if (!workForm.value.owner_ids?.length) {
    showToast("请选择至少一位责任人");
    return;
  }
  workForm.value.content = workForm.value.content.trim();
  if (editingWorkIdx.value === null) {
    draft.value.items.push(workForm.value);
  } else {
    draft.value.items[editingWorkIdx.value] = workForm.value;
  }
  markDirty();
  if (continueNext) {
    showSuccessToast("已保存，继续添加下一条");
    openNewWork();
  } else {
    workSheetOpen.value = false;
  }
}

async function removeWorkItem(itemId: string) {
  await showConfirmDialog({
    title: "删除任务条目",
    message: "确定从本次草稿中移除该任务条目吗？",
  });
  draft.value.items = draft.value.items.filter((x: any) => x.id !== itemId);
  markDirty();
}

// Preview & Publish
function addCcEmail() {
  const email = normalizeMailbox(ccEmailInput.value);
  if (!email) {
    showToast("请输入有效的邮箱地址");
    return;
  }
  if (!draft.value.cc_emails) draft.value.cc_emails = [];
  if (!draft.value.cc_emails.includes(email)) {
    draft.value.cc_emails.push(email);
    markDirty();
  }
  ccEmailInput.value = "";
}

const recipientEmailSummary = computed(() => {
  const ids = draft.value?.recipient_ids || [];
  const map = new Map(allUsers.value.map((u: any) => [u.id, u]));
  const emails: string[] = [];
  const missing: string[] = [];
  for (const id of ids) {
    const u: any = map.get(id);
    if (u?.email) emails.push(`${u.name} <${u.email}>`);
    else missing.push(u?.name || person(id));
  }
  return { emails, missing };
});

async function openPreviewPublish() {
  if (!draft.value) return;
  const missingReview = reviewItems.value.find((r: any) => !r.status);
  if (missingReview) {
    activeTab.value = "review";
    showToast("请先逐项选择上期任务的完成结果");
    return;
  }
  const synced = syncAttendeeRecipients(draft.value, previousAttendees.value);
  draft.value.recipient_ids = synced.recipients;
  draft.value.cc_ids = synced.cc;
  previousAttendees.value = synced.attendees;
  notifyEnabled.value = !draft.value.history_backfill;

  const saved = await saveDraft(false);
  if (!saved || !meeting.value?.id) return;

  previewOpen.value = true;
  await refreshPreviewPdf();
}

async function refreshPreviewPdf() {
  if (!meeting.value?.id) return;
  previewLoading.value = true;
  previewError.value = "";
  previewBlob.value = null;
  try {
    previewBlob.value = await previewFile(
      meeting.value.id,
      meeting.value.draft_version,
      "pdf",
    );
  } catch (e: any) {
    previewError.value = chineseError(e, "PDF 预览生成失败，请重试");
  } finally {
    previewLoading.value = false;
  }
}

async function confirmPublish() {
  if (publishing.value || !meeting.value?.id) return;
  if (dirty.value) {
    const saved = await saveDraft(false);
    if (!saved) return;
  }
  publishing.value = true;
  const meetingId = meeting.value.id;
  const expectedVersion = meeting.value.draft_version;
  const prevPub = meeting.value.published_version || 0;
  try {
    let published: any;
    try {
      published = await api(`/meetings/${meetingId}/publish`, "POST", {
        expected_version: expectedVersion,
        notify: draft.value.history_backfill ? false : notifyEnabled.value,
        shared_recipients: true,
        reason: prevPub ? "更新会议纪要" : "发布会议纪要",
      });
    } catch (error) {
      if (!(error instanceof ApiResponseError) || !error.outcomeUnknown)
        throw error;
      try {
        const actual = await api(`/meetings/${meetingId}`);
        if (
          actual.draft_version === expectedVersion &&
          !actual.has_draft &&
          actual.published_version === prevPub + 1
        )
          published = actual;
      } catch {
        /* do not auto-retry publish */
      }
      if (!published)
        throw new Error(
          "发布响应异常，暂未确认是否发布成功。请刷新核对会议状态，切勿重复发布。",
        );
    }
    previewOpen.value = false;
    showSuccessToast(
      notifyEnabled.value && !draft.value.history_backfill
        ? "会议纪要已正式发布"
        : "会议已发布（未发送通知）",
    );
    await loadMeeting();
    if (
      publishWecomTodo.value &&
      !draft.value?.history_backfill &&
      meeting.value?.tasks?.length
    ) {
      nativeTodoTasks.value = meeting.value.tasks.filter(
        (t: any) => !t.closed_at && !t.withdrawn_at,
      );
      if (nativeTodoTasks.value.length) nativeTodoSheet.value = true;
    }
  } finally {
    publishing.value = false;
  }
}

async function confirmOneNativeTodo(task: any) {
  nativeTodoStatus.value[task.id] = "确认中…";
  const res = await confirmAssignedTodo({ id: task.id, version: task.version });
  nativeTodoStatus.value[task.id] = res.message;
}

// Attachments & published PDF
async function onUploadAttachment(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !meeting.value?.id) return;
  const form = new FormData();
  form.append("file", file);
  const uploaded = await api(
    `/files?meeting_id=${meeting.value.id}`,
    "POST",
    form,
  );
  if (!draft.value.attachment_ids) draft.value.attachment_ids = [];
  draft.value.attachment_ids.push(uploaded.id);
  if (!meeting.value.attachments) meeting.value.attachments = [];
  meeting.value.attachments.push(uploaded);
  markDirty();
  showSuccessToast("附件已上传");
  input.value = "";
}

async function openPublishedPdf() {
  if (!meeting.value?.id) return;
  pdfPopupOpen.value = true;
  publishedPdfLoading.value = true;
  publishedPdfBlob.value = null;
  try {
    const version = selectedVersion.value || meeting.value.published_version;
    const path = version
      ? `/meetings/${meeting.value.id}/export/pdf?version=${version}`
      : `/meetings/${meeting.value.id}/export/pdf`;
    publishedPdfBlob.value = await fetchAuthBlob(path);
  } catch (e: any) {
    showToast(chineseError(e, "PDF 纪要加载失败"));
  } finally {
    publishedPdfLoading.value = false;
  }
}

// Top-right menu actions
const menuActions = computed(() => {
  const m = meeting.value;
  if (!m) return [];
  const list: any[] = [];
  if (m.published_version && !m.has_draft && m.can_edit)
    list.push({ name: "创建修订版本", key: "revision" });
  if (m.published_version && m.has_draft && m.can_edit)
    list.push({ name: "取消当前修订", key: "cancel_revision", color: "#a16207" });
  if (m.published_version && m.can_continue)
    list.push({ name: "接续新建下期会议", key: "continue" });
  if (m.published_version && !m.can_continue && !m.can_edit)
    list.push({ name: "申请编辑与接续权限", key: "request_edit" });
  if (m.versions?.length > 1)
    list.push({ name: "切换历史版本", key: "versions" });
  if (m.can_delete)
    list.push({ name: "删除会议", key: "delete", color: "#b91c1c" });
  return list;
});

async function onSelectMenu(action: any) {
  actionSheetOpen.value = false;
  const m = meeting.value;
  if (!m) return;
  if (action.key === "revision") {
    await api(`/meetings/${m.id}/revision`, "POST", {
      expected_version: m.published_version,
    });
    showSuccessToast("已进入修订草稿模式");
    await loadMeeting();
  } else if (action.key === "cancel_revision") {
    await showConfirmDialog({
      title: "取消修订",
      message: "确定放弃当前未发布的修订草稿吗？已发布版本不受影响。",
    });
    await api(`/meetings/${m.id}/revision/cancel`, "POST", {
      expected_version: m.draft_version,
    });
    showSuccessToast("已取消修订草稿");
    await loadMeeting();
  } else if (action.key === "continue") {
    const created = await api(`/meetings/${m.id}/continue`, "POST");
    showSuccessToast("已创建接续会议草稿");
    void router.push({ path: `/meetings/${created.id}`, query: route.query });
  } else if (action.key === "request_edit") {
    await api(`/meetings/${m.id}/edit-requests`, "POST");
    showSuccessToast("已向主持人提交权限申请");
  } else if (action.key === "versions") {
    versionSheetOpen.value = true;
  } else if (action.key === "delete") {
    await showConfirmDialog({
      title: "删除会议",
      message: `确定删除「${m.title}」吗？`,
    });
    await api(`/meetings/${m.id}`, "DELETE", { confirm_title: m.title });
    showSuccessToast("会议已删除");
    void router.replace({
      path: "/meetings",
      query: seriesParam.value ? { series: seriesParam.value } : {},
    });
  }
}

function goBack() {
  if (seriesParam.value)
    void router.push({ path: "/meetings", query: { series: seriesParam.value } });
  else void router.back();
}

watch(
  () => route.params.id,
  () => {
    void run(loadMeeting);
  },
);

onMounted(() => run(loadMeeting));
</script>

<template>
  <div class="m-page-sub">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <button
          type="button"
          class="m-btn m-btn-sm"
          style="border: none; padding: 0 6px"
          @click="goBack"
        >
          ‹ 返回
        </button>
        <div style="text-align: center; min-width: 0; flex: 1">
          <div class="m-header-title" style="font-size: 15px">
            {{ viewData?.title || "新建会议" }}
          </div>
          <div class="m-muted" style="font-size: 11px">
            <template v-if="selectedVersion">
              历史版本 V{{ selectedVersion }}（只读）
            </template>
            <template v-else-if="editable">
              {{ meeting?.published_version ? "修订草稿" : "草稿" }}
              {{ meeting?.draft_version ? `V${meeting.draft_version}` : "" }} ·
              {{ dirty ? "有未保存修改" : "已保存" }}
            </template>
            <template v-else-if="meeting?.published_version">
              已发布 V{{ meeting.published_version }}
            </template>
          </div>
        </div>
        <button
          v-if="menuActions.length"
          type="button"
          class="m-btn m-btn-sm"
          style="border: none; padding: 0 8px; font-size: 16px"
          @click="actionSheetOpen = true"
        >
          ⋯
        </button>
        <span v-else style="width: 36px" />
      </div>

      <div class="m-tabs-bar">
        <button
          type="button"
          :class="{ active: activeTab === 'info' }"
          @click="activeTab = 'info'"
        >
          基本信息
        </button>
        <button
          type="button"
          :class="{ active: activeTab === 'review' }"
          @click="activeTab = 'review'"
        >
          上期跟踪 ({{ reviewItems.length }})
        </button>
        <button
          type="button"
          :class="{ active: activeTab === 'current' }"
          @click="activeTab = 'current'"
        >
          本次安排 ({{ currentItems.length }})
        </button>
        <button
          type="button"
          :class="{ active: activeTab === 'files' }"
          @click="activeTab = 'files'"
        >
          纪要附件
        </button>
      </div>
    </header>

    <div v-if="loading" class="m-empty">
      <van-loading size="22">正在读取会议内容…</van-loading>
    </div>

    <section v-else-if="viewData" class="m-section">
      <div v-if="selectedVersion" class="m-alert m-alert-warning">
        当前正在查看历史版本 V{{ selectedVersion }}（只读）。
        <button
          type="button"
          class="m-btn m-btn-sm"
          style="margin-left: 8px"
          @click="selectedVersion = null"
        >
          返回当前版本
        </button>
      </div>

      <!-- TAB 1: Basic Info & Decisions -->
      <template v-if="activeTab === 'info'">
        <div class="m-card" style="display: flex; flex-direction: column; gap: 12px">
          <div style="font-size: 14px; font-weight: 700">会议基本信息</div>

          <div>
            <label class="m-label m-label-req">会议名称</label>
            <input
              v-if="editable"
              v-model="draft.title"
              class="m-input"
              placeholder="请输入会议名称"
              @input="markDirty"
            />
            <div v-else style="font-weight: 600">{{ viewData.title }}</div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label m-label-req">会议日期</label>
              <input
                v-if="editable"
                v-model="draft.meeting_date"
                type="date"
                class="m-input"
                @change="markDirty"
              />
              <div v-else class="tabular">{{ viewData.meeting_date }}</div>
            </div>
            <div>
              <label class="m-label">会议时间</label>
              <input
                v-if="editable"
                v-model="draft.meeting_time"
                class="m-input"
                placeholder="如 09:00-10:00"
                @input="markDirty"
              />
              <div v-else>{{ viewData.meeting_time || "—" }}</div>
            </div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label">会议类型</label>
              <select
                v-if="editable"
                v-model="draft.kind"
                class="m-select"
                :disabled="seriesLocked"
                @change="markDirty"
              >
                <option value="department">部门例会</option>
                <option value="special">专项会议</option>
              </select>
              <StatusTag
                v-else
                :value="kinds[viewData.kind] || viewData.kind"
                tone="blue"
              />
            </div>
            <div>
              <label class="m-label">会议地点</label>
              <input
                v-if="editable"
                v-model="draft.location"
                class="m-input"
                placeholder="选填"
                @input="markDirty"
              />
              <div v-else>{{ viewData.location || "—" }}</div>
            </div>
          </div>

          <div v-if="viewData.kind === 'department'">
            <label class="m-label m-label-req">所属部门</label>
            <select
              v-if="editable"
              v-model="draft.department_id"
              class="m-select"
              @change="
                markDirty();
                loadPeopleOptions();
              "
            >
              <option value="">请选择所属部门</option>
              <option
                v-for="d in allDepartments"
                :key="d.id"
                :value="d.id"
              >
                {{ departmentPath(d.id, allDepartments) }}
              </option>
            </select>
            <div v-else>
              {{ departmentPath(viewData.department_id, allDepartments) }}
            </div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label m-label-req">主持人</label>
              <PeoplePicker
                v-if="editable"
                v-model="draft.host_id"
                :users="allUsers"
                title="选择主持人"
                :clearable="false"
                @change="markDirty"
              />
              <div v-else>{{ person(viewData.host_id) }}</div>
            </div>
            <div>
              <label class="m-label">编制人</label>
              <PeoplePicker
                v-if="editable"
                v-model="draft.recorder_id"
                :users="allUsers"
                title="选择编制人"
                @change="markDirty"
              />
              <div v-else>{{ viewData.recorder_id ? person(viewData.recorder_id) : "—" }}</div>
            </div>
          </div>

          <label
            v-if="editable && !meeting?.published_version"
            style="display: flex; align-items: center; gap: 8px; font-size: 13px"
          >
            <input
              v-model="draft.history_backfill"
              type="checkbox"
              @change="markDirty"
            />
            <span>补录历史会议（不生成实时待办、不发通知）</span>
          </label>
        </div>

        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div style="font-size: 14px; font-weight: 700">
            参会人员（共 {{ attendeeCount }} 人，自动计入主持人）
          </div>
          <div>
            <label class="m-label">现场参会人员</label>
            <PeoplePicker
              v-if="editable"
              :model-value="onsiteAttendees"
              :users="allUsers"
              multiple
              title="选择现场参会人员"
              @update:model-value="updateAttendees('onsite', $event)"
            />
            <div v-else class="m-work-text">
              {{ onsiteAttendees.map(person).join("、") || "仅主持人" }}
            </div>
          </div>
          <div>
            <label class="m-label">线上参会人员</label>
            <PeoplePicker
              v-if="editable"
              :model-value="draft.online_attendee_ids"
              :users="allUsers"
              multiple
              title="选择线上参会人员"
              @update:model-value="updateAttendees('online', $event)"
            />
            <div v-else class="m-work-text">
              {{ (viewData.online_attendee_ids || []).map(person).join("、") || "无" }}
            </div>
          </div>
          <div>
            <label class="m-label">缺席人员及原因</label>
            <textarea
              v-if="editable"
              v-model="draft.absentees"
              class="m-textarea"
              rows="2"
              placeholder="选填"
              @input="markDirty"
            />
            <div v-else class="m-muted">{{ viewData.absentees || "无" }}</div>
          </div>
        </div>

        <div class="m-card" style="display: flex; flex-direction: column; gap: 8px">
          <div style="font-size: 14px; font-weight: 700">本次会议议定事项</div>
          <textarea
            v-if="editable"
            v-model="draft.decisions"
            class="m-textarea"
            rows="4"
            placeholder="记录会议核心结论、决议事项…"
            @input="markDirty"
          />
          <div v-else class="m-work-text" style="font-size: 13px">
            {{ viewData.decisions || "暂无会议议定事项" }}
          </div>
        </div>
      </template>

      <!-- TAB 2: Previous Task Review -->
      <template v-else-if="activeTab === 'review'">
        <div class="review-tools">
          <input
            v-model="reviewSearch"
            class="m-input"
            style="padding: 8px 10px; font-size: 13px"
            placeholder="搜索任务内容或责任人…"
          />
          <button
            v-if="editable && reviewItems.length > 1"
            type="button"
            class="m-btn m-btn-sm"
            style="flex-shrink: 0"
            @click="sortReviewList"
          >
            一键排序
          </button>
        </div>

        <template v-if="filteredReviewItems.length">
          <article
            v-for="(item, idx) in filteredReviewItems"
            :key="item.id"
            class="m-card r-card"
          >
            <div class="r-head">
              <span class="seq-badge">{{ Number(idx) + 1 }}</span>
              <p class="r-content m-work-text">{{ item.content }}</p>
              <div
                v-if="editable && filteredReviewItems.length > 1"
                class="move-capsule"
                role="group"
                aria-label="调整顺序"
              >
                <button
                  type="button"
                  class="move-seg"
                  aria-label="上移"
                  :disabled="Number(idx) === 0"
                  @click="moveSectionItem('review', item.id, -1)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="18 15 12 9 6 15" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="move-seg"
                  aria-label="下移"
                  :disabled="Number(idx) === filteredReviewItems.length - 1"
                  @click="moveSectionItem('review', item.id, 1)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="r-meta">
              <span>责任人：{{ (item.owner_ids || []).map(person).join("、") || "—" }}</span>
              <div class="tabular" style="text-align: right; flex-shrink: 0">
                <template v-if="item.postponed_due_date">
                  <div style="text-decoration: line-through; color: #a1a1aa">
                    {{ item.due_date || "—" }}
                  </div>
                  <div style="color: var(--m-primary); font-weight: 600">
                    {{ item.postponed_due_date }}
                  </div>
                </template>
                <span v-else>期限 {{ item.due_date || "未设置" }}</span>
              </div>
            </div>

            <!-- One-tap 5-status pills in a single responsive row -->
            <div v-if="canAssess" class="status-pills">
              <button
                v-for="st in statuses"
                :key="st"
                type="button"
                class="st-pill"
                :class="[
                  item.status === st ? 'selected' : '',
                  item.status === st ? `st-${st}` : '',
                ]"
                @click="setReviewStatus(item, st)"
              >
                {{ st }}
              </button>
              <button
                v-if="(item.owner_ids || []).length > 1"
                type="button"
                class="st-pill st-pill-wide"
                :class="{ 'selected st-部分完成': item.status === '部分完成' }"
                @click="openOwnerResults(item)"
              >
                按责任人分别评定
              </button>
            </div>
            <div v-else>
              <StatusTag :value="item.status || '未评定'" />
            </div>

            <!-- Conditional actions for editable draft -->
            <div v-if="editable" class="r-actions">
              <input
                v-model="item.notes"
                class="m-input"
                style="padding: 7px 10px; font-size: 12px; flex: 1"
                placeholder="补充备注…"
                @input="markDirty"
              />
              <button
                v-if="item.status === '未到期' && !item.postponed_due_date"
                type="button"
                class="m-btn m-btn-sm m-btn-soft"
                @click="openPostpone(item)"
              >
                延期
              </button>
              <button
                v-if="item.status === '未到期' && item.postponed_due_date"
                type="button"
                class="m-btn m-btn-sm"
                @click="cancelPostpone(item)"
              >
                取消延期
              </button>
              <button
                v-if="item.status === '未完成'"
                type="button"
                class="m-btn m-btn-sm m-btn-danger-soft"
                @click="carryUnfinished(item)"
              >
                延续安排 →
              </button>
            </div>
            <div v-else-if="item.notes" class="m-muted m-work-text">
              备注：{{ item.notes }}
            </div>
          </article>
        </template>

        <div v-else class="m-empty">暂无上期跟踪任务</div>
      </template>

      <!-- TAB 3: Current Arrangements -->
      <template v-else-if="activeTab === 'current'">
        <div
          v-if="editable"
          style="display: flex; justify-content: space-between; align-items: center"
        >
          <span class="m-muted">共 {{ currentItems.length }} 项本次安排</span>
          <button
            type="button"
            class="m-btn m-btn-sm m-btn-primary"
            @click="openNewWork"
          >
            + 新增工作
          </button>
        </div>

        <template v-if="carryCurrentItems.length">
          <div class="group-label">
            上次未完成延续事项（{{ carryCurrentItems.length }}）
          </div>
          <article
            v-for="(item, idx) in carryCurrentItems"
            :key="item.id"
            class="m-card c-card"
          >
            <div class="r-head">
              <span class="seq-badge blue">{{ Number(idx) + 1 }}</span>
              <p class="r-content m-work-text">{{ item.content }}</p>
              <div
                v-if="editable && carryCurrentItems.length > 1"
                class="move-capsule"
                role="group"
                aria-label="调整顺序"
              >
                <button
                  type="button"
                  class="move-seg"
                  aria-label="上移"
                  :disabled="Number(idx) === 0"
                  @click="moveSectionItem('new', item.id, -1)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="18 15 12 9 6 15" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="move-seg"
                  aria-label="下移"
                  :disabled="Number(idx) === carryCurrentItems.length - 1"
                  @click="moveSectionItem('new', item.id, 1)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
              </div>
            </div>
            <div class="c-fields">
              <div><span class="m-muted">责任人：</span>{{ (item.owner_ids || []).map(person).join("、") || "未选" }}</div>
              <div class="tabular"><span class="m-muted">截止：</span>{{ item.due_date || "未设" }}</div>
              <div><span class="m-muted">验证人：</span>{{ (item.verifier_id || viewData.host_id) ? person(item.verifier_id || viewData.host_id) : "未选" }}</div>
              <div><span class="m-muted">输出物：</span>{{ item.deliverable || "—" }}</div>
              <div v-if="item.notes" style="grid-column: 1 / -1">
                <span class="m-muted">备注：</span>{{ item.notes }}
              </div>
            </div>
            <div v-if="editable" class="c-foot">
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-soft"
                @click="openWorkEditor(item.id)"
              >
                编辑条目
              </button>
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-danger-soft"
                @click="run(() => removeWorkItem(item.id))"
              >
                删除
              </button>
            </div>
          </article>
        </template>

        <template v-if="newCurrentItems.length">
          <div class="group-label">
            本次新建工作（{{ newCurrentItems.length }}）
          </div>
          <article
            v-for="(item, idx) in newCurrentItems"
            :key="item.id"
            class="m-card c-card"
          >
            <div class="r-head">
              <span class="seq-badge blue">
                {{ carryCurrentItems.length + Number(idx) + 1 }}
              </span>
              <p class="r-content m-work-text">{{ item.content }}</p>
              <div
                v-if="editable && newCurrentItems.length > 1"
                class="move-capsule"
                role="group"
                aria-label="调整顺序"
              >
                <button
                  type="button"
                  class="move-seg"
                  aria-label="上移"
                  :disabled="Number(idx) === 0"
                  @click="moveSectionItem('new', item.id, -1)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="18 15 12 9 6 15" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="move-seg"
                  aria-label="下移"
                  :disabled="Number(idx) === newCurrentItems.length - 1"
                  @click="moveSectionItem('new', item.id, 1)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
              </div>
            </div>
            <div class="c-fields">
              <div><span class="m-muted">责任人：</span>{{ (item.owner_ids || []).map(person).join("、") || "未选" }}</div>
              <div class="tabular"><span class="m-muted">截止：</span>{{ item.due_date || "未设" }}</div>
              <div><span class="m-muted">验证人：</span>{{ (item.verifier_id || viewData.host_id) ? person(item.verifier_id || viewData.host_id) : "未选" }}</div>
              <div><span class="m-muted">输出物：</span>{{ item.deliverable || "—" }}</div>
              <div v-if="item.notes" style="grid-column: 1 / -1">
                <span class="m-muted">备注：</span>{{ item.notes }}
              </div>
            </div>
            <div v-if="editable" class="c-foot">
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-soft"
                @click="openWorkEditor(item.id)"
              >
                编辑条目
              </button>
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-danger-soft"
                @click="run(() => removeWorkItem(item.id))"
              >
                删除
              </button>
            </div>
          </article>
        </template>

        <div v-if="!currentItems.length" class="m-empty">
          暂无本次工作安排
          <div v-if="editable" style="margin-top: 12px">
            <button
              type="button"
              class="m-btn m-btn-primary"
              @click="openNewWork"
            >
              + 添加第一条工作安排
            </button>
          </div>
        </div>
      </template>

      <!-- TAB 4: Files, PDF & Assessment Trace -->
      <template v-else>
        <div
          v-if="meeting?.published_version"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 10px"
        >
          <div style="font-size: 14px; font-weight: 700">正式会议纪要文件</div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px">
            <button
              type="button"
              class="m-btn m-btn-soft"
              @click="run(openPublishedPdf)"
            >
              在线看 PDF / 长图
            </button>
            <button
              type="button"
              class="m-btn"
              @click="
                run(() =>
                  download(
                    selectedVersion
                      ? `/meetings/${meeting.id}/export/xlsx?version=${selectedVersion}`
                      : `/meetings/${meeting.id}/export/xlsx`,
                    `${meeting.title}-纪要.xlsx`,
                  ),
                )
              "
            >
              下载 Excel 纪要
            </button>
          </div>
        </div>

        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div
            style="
              display: flex;
              align-items: center;
              justify-content: space-between;
            "
          >
            <span style="font-size: 14px; font-weight: 700">会议附件</span>
            <label v-if="editable && meeting?.id" class="m-btn m-btn-sm m-btn-soft">
              <input
                type="file"
                style="display: none"
                @change="run(() => onUploadAttachment($event))"
              />
              + 上传/拍照
            </label>
          </div>
          <div v-if="meeting?.attachments?.length" style="display: flex; flex-direction: column; gap: 8px">
            <div
              v-for="att in meeting.attachments"
              :key="att.id"
              class="att-row"
            >
              <span style="font-size: 13px; overflow: hidden; text-overflow: ellipsis">
                {{ att.filename || "会议附件" }}
              </span>
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-soft"
                @click="
                  run(() =>
                    download('/files/' + att.id, att.filename || '会议附件'),
                  )
                "
              >
                下载
              </button>
            </div>
          </div>
          <div v-else class="m-muted">暂无用户上传附件</div>
        </div>

        <div
          v-if="meeting?.assessment_summary"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 6px"
        >
          <div style="font-size: 14px; font-weight: 700">考核达成汇总</div>
          <div class="m-muted">
            回顾 {{ meeting.assessment_summary.total_items ?? 0 }} 项 · 达成率
            <strong style="color: var(--m-ink)">
              {{ rate(meeting.assessment_summary.achievement_rate) }}
            </strong>
          </div>
        </div>

        <div
          v-if="meeting?.mail_logs?.length"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 8px"
        >
          <div style="font-size: 14px; font-weight: 700">纪要邮件发送记录</div>
          <div
            v-for="log in meeting.mail_logs.slice(0, 20)"
            :key="log.id"
            class="att-row"
          >
            <div style="min-width: 0">
              <div style="font-size: 12px; font-weight: 600">
                {{ log.recipient || log.to }}
              </div>
              <div class="m-muted tabular">
                {{ beijingTime(log.sent_at || log.created_at) }}
              </div>
            </div>
            <StatusTag :value="log.status === 'sent' ? '已发送' : log.status" />
          </div>
        </div>
      </template>
    </section>

    <!-- Sticky Bottom Action Bar for Editable Draft -->
    <div v-if="editable" class="m-sticky-footer">
      <button
        type="button"
        class="m-btn"
        style="flex: 1"
        :disabled="saving"
        @click="run(() => saveDraft(true))"
      >
        {{ saving ? "保存中…" : "保存草稿" }}
      </button>
      <button
        type="button"
        class="m-btn m-btn-primary"
        style="flex: 1.4"
        :disabled="saving || publishing"
        @click="run(openPreviewPublish)"
      >
        预览并发布
      </button>
    </div>

    <!-- Sticky Bottom Action Bar for Published Meeting (Screen 07 -> 26) -->
    <div v-else-if="meeting?.published_version" class="m-sticky-footer">
      <button
        type="button"
        class="m-btn"
        style="flex: 1"
        @click="router.push(`/meetings/${meeting.id}/native-todos`)"
      >
        派发企微待办
      </button>
      <button
        v-if="meeting.can_continue"
        type="button"
        class="m-btn m-btn-primary"
        style="flex: 1.2"
        @click="run(() => onSelectMenu({ key: 'continue' }))"
      >
        接续新建
      </button>
    </div>

    <!-- New Meeting Setup Wizard Popup -->
    <van-popup
      v-model:show="setupWizardOpen"
      position="bottom"
      round
      :close-on-click-overlay="false"
      :style="{ height: '90vh', display: 'flex', flexDirection: 'column' }"
    >
      <div v-if="draft" class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">新建会议</span>
          <button type="button" class="sheet-close" @click="goBack">取消</button>
        </div>
        <div class="sheet-body">
          <div class="m-alert m-alert-info">
            先填写会议基本信息与参会名单，确认后即可进入任务单编辑与发布。
          </div>
          <div>
            <label class="m-label m-label-req">会议名称</label>
            <input
              v-model="draft.title"
              class="m-input"
              placeholder="例如：办公室周会（11月第4周）"
            />
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label m-label-req">会议日期</label>
              <input v-model="draft.meeting_date" type="date" class="m-input" />
            </div>
            <div>
              <label class="m-label">会议时间（选填）</label>
              <input
                v-model="draft.meeting_time"
                class="m-input"
                placeholder="09:00-10:00"
              />
            </div>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label">会议类型</label>
              <select
                v-model="draft.kind"
                class="m-select"
                :disabled="seriesLocked"
              >
                <option value="department">部门例会</option>
                <option value="special">专项会议</option>
              </select>
            </div>
            <div>
              <label class="m-label">会议地点（选填）</label>
              <input v-model="draft.location" class="m-input" placeholder="会议室" />
            </div>
          </div>
          <div v-if="draft.kind === 'department'">
            <label class="m-label m-label-req">所属部门</label>
            <select v-model="draft.department_id" class="m-select">
              <option value="">请选择所属部门</option>
              <option v-for="d in allDepartments" :key="d.id" :value="d.id">
                {{ departmentPath(d.id, allDepartments) }}
              </option>
            </select>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label m-label-req">主持人</label>
              <PeoplePicker
                v-model="draft.host_id"
                :users="allUsers"
                title="选择主持人"
                :clearable="false"
              />
            </div>
            <div>
              <label class="m-label">编制人（选填）</label>
              <PeoplePicker
                v-model="draft.recorder_id"
                :users="allUsers"
                title="选择编制人"
              />
            </div>
          </div>
          <div>
            <label class="m-label">
              现场参会人员（共 {{ attendeeCount }} 人，主持人自动计入）
            </label>
            <PeoplePicker
              :model-value="onsiteAttendees"
              :users="allUsers"
              multiple
              title="选择现场参会人员"
              @update:model-value="updateAttendees('onsite', $event)"
            />
          </div>
          <div>
            <label class="m-label">线上参会人员</label>
            <PeoplePicker
              :model-value="draft.online_attendee_ids"
              :users="allUsers"
              multiple
              title="选择线上参会人员"
              @update:model-value="updateAttendees('online', $event)"
            />
          </div>
          <label style="display: flex; align-items: center; gap: 8px; font-size: 13px">
            <input v-model="draft.history_backfill" type="checkbox" />
            <span>补录历史会议（不生成待办、不发送通知）</span>
          </label>
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            :disabled="saving"
            @click="run(confirmNewWizard)"
          >
            {{ saving ? "正在创建…" : "确认并进入任务单" }}
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Single Work Item Full-Screen Editor Sheet -->
    <van-popup
      v-model:show="workSheetOpen"
      position="bottom"
      round
      :style="{ height: '88vh', display: 'flex', flexDirection: 'column' }"
    >
      <div v-if="workForm" class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">
            {{ editingWorkIdx === null ? "新增本次工作" : "编辑本次工作" }}
          </span>
          <button
            type="button"
            class="sheet-close"
            @click="workSheetOpen = false"
          >
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div>
            <label class="m-label m-label-req">工作内容及要求</label>
            <textarea
              v-model="workForm.content"
              class="m-textarea"
              rows="4"
              placeholder="请清晰描述具体工作事项及验收要求…"
            />
          </div>
          <div>
            <label class="m-label m-label-req">责任人（支持粘贴多人）</label>
            <PeoplePicker
              v-model="workForm.owner_ids"
              :users="allUsers"
              multiple
              :allow-inactive="Boolean(draft?.history_backfill)"
              title="选择责任人"
            />
          </div>
          <div>
            <label class="m-label">计划完成时间</label>
            <div class="m-chip-row" style="margin-bottom: 8px">
              <button
                type="button"
                class="m-chip m-chip-soft"
                :class="{ active: workForm.due_date === thisFriday() }"
                @click="workForm.due_date = thisFriday()"
              >
                本周五 {{ thisFriday().slice(5) }}
              </button>
              <button
                type="button"
                class="m-chip m-chip-soft"
                :class="{ active: workForm.due_date === addDays(7) }"
                @click="workForm.due_date = addDays(7)"
              >
                一周后 {{ addDays(7).slice(5) }}
              </button>
              <button
                type="button"
                class="m-chip m-chip-soft"
                :class="{ active: workForm.due_date === addDays(14) }"
                @click="workForm.due_date = addDays(14)"
              >
                两周后 {{ addDays(14).slice(5) }}
              </button>
            </div>
            <input v-model="workForm.due_date" type="date" class="m-input" />
          </div>
          <div>
            <label class="m-label">验证人</label>
            <PeoplePicker
              v-model="workForm.verifier_id"
              :users="allUsers"
              title="选择验证人"
            />
          </div>
          <div>
            <label class="m-label">输出物（选填）</label>
            <input
              v-model="workForm.deliverable"
              class="m-input"
              placeholder="例如：整改报告、对账单、验收表"
            />
          </div>
          <div>
            <label class="m-label">备注（选填）</label>
            <textarea v-model="workForm.notes" class="m-textarea" rows="2" />
          </div>
        </div>
        <div class="sheet-foot" style="display: flex; gap: 10px">
          <button
            v-if="editingWorkIdx === null"
            type="button"
            class="m-btn m-btn-soft"
            style="flex: 1"
            @click="saveWorkForm(true)"
          >
            保存并再添一条
          </button>
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="flex: 1"
            @click="saveWorkForm(false)"
          >
            保存返回
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Postpone Date Sheet -->
    <van-popup v-model:show="postponeOpen" position="bottom" round>
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">设置延期日期</span>
          <button
            type="button"
            class="sheet-close"
            @click="postponeOpen = false"
          >
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div class="m-muted">
            原计划完成时间：{{ postponeItem?.due_date || "未设" }}；延期日期须晚于原日期且不早于会议日期。
          </div>
          <input v-model="postponeDate" type="date" class="m-input" />
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            @click="confirmPostpone"
          >
            确认延期
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Per-owner Mixed Results Sheet -->
    <van-popup v-model:show="ownerResultsOpen" position="bottom" round>
      <div v-if="ownerResultsItem" class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">按责任人分别评定</span>
          <button
            type="button"
            class="sheet-close"
            @click="ownerResultsOpen = false"
          >
            取消
          </button>
        </div>
        <div class="sheet-body">
          <div
            v-for="oid in ownerResultsItem.owner_ids"
            :key="oid"
            style="
              display: flex;
              align-items: center;
              justify-content: space-between;
              padding: 8px 0;
              border-bottom: 1px solid #f4f4f5;
            "
          >
            <span style="font-weight: 600">{{ person(oid) }}</span>
            <div style="display: flex; gap: 8px">
              <button
                type="button"
                class="st-pill"
                :class="{ 'selected st-已完成': ownerResultsMap[oid] === '已完成' }"
                @click="ownerResultsMap[oid] = '已完成'"
              >
                已完成
              </button>
              <button
                type="button"
                class="st-pill"
                :class="{ 'selected st-未完成': ownerResultsMap[oid] === '未完成' }"
                @click="ownerResultsMap[oid] = '未完成'"
              >
                未完成
              </button>
            </div>
          </div>
        </div>
        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            @click="saveOwnerResults"
          >
            确认评定结果
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Preview & Publish Full Sheet -->
    <van-popup
      v-model:show="previewOpen"
      position="bottom"
      round
      :style="{ height: '92vh', display: 'flex', flexDirection: 'column' }"
    >
      <div v-if="draft" class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">预览并发布会议纪要</span>
          <button
            type="button"
            class="sheet-close"
            @click="previewOpen = false"
          >
            返回修改
          </button>
        </div>
        <div class="sheet-body">
          <div v-if="previewLoading" class="m-empty" style="padding: 20px">
            <van-loading size="20">正在生成实时 PDF 纪要预览…</van-loading>
          </div>
          <div v-else-if="previewError" class="m-alert m-alert-error">
            {{ previewError }}
            <button
              type="button"
              class="m-btn m-btn-sm"
              style="margin-left: 8px"
              @click="run(refreshPreviewPdf)"
            >
              重试预览
            </button>
          </div>
          <PdfViewer
            v-else
            :blob="previewBlob"
            :filename="`${draft.title}-预览.pdf`"
          />

          <div class="m-divider" />

          <div v-if="!draft.history_backfill">
            <label class="m-label">发布通知设置</label>
            <div class="m-segmented" style="grid-template-columns: 1fr 1fr">
              <button
                type="button"
                :class="{ active: notifyEnabled }"
                @click="notifyEnabled = true"
              >
                发送通知
              </button>
              <button
                type="button"
                :class="{ active: !notifyEnabled }"
                @click="notifyEnabled = false"
              >
                不通知
              </button>
            </div>
          </div>
          <div v-else class="m-alert m-alert-info">
            补录历史会议固定为「不通知」，仅生成并留存历史纪要文件。
          </div>

          <template v-if="notifyEnabled && !draft.history_backfill">
            <div>
              <label class="m-label">
                通知人员名单（{{ (draft.recipient_ids || []).length }} 人）
              </label>
              <PeoplePicker
                v-model="draft.recipient_ids"
                :users="allUsers"
                multiple
                title="编辑通知接收人"
                @change="
                  draft.recipient_customized = true;
                  markDirty();
                "
              />
            </div>

            <div class="m-card" style="padding: 10px; background: #fafafa">
              <div class="m-muted" style="margin-bottom: 4px">
                匹配邮件收件人（{{ recipientEmailSummary.emails.length }}）
              </div>
              <div style="font-size: 12px; word-break: break-all; line-height: 1.6">
                {{ recipientEmailSummary.emails.join("；") || "无有效邮箱" }}
              </div>
              <div
                v-if="recipientEmailSummary.missing.length"
                class="m-alert m-alert-warning"
                style="margin-top: 8px"
              >
                以下人员未配置邮箱（将仅通过企微接收）：{{
                  recipientEmailSummary.missing.join("、")
                }}
              </div>
            </div>

            <div>
              <label class="m-label">附加邮件抄送（选填）</label>
              <div style="display: flex; gap: 6px">
                <input
                  v-model="ccEmailInput"
                  class="m-input"
                  placeholder="输入邮箱地址后点添加"
                  @keyup.enter="addCcEmail"
                />
                <button
                  type="button"
                  class="m-btn m-btn-sm m-btn-soft"
                  @click="addCcEmail"
                >
                  添加
                </button>
              </div>
              <div
                v-if="draft.cc_emails?.length"
                style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px"
              >
                <span
                  v-for="(mail, idx) in draft.cc_emails"
                  :key="mail"
                  class="m-chip"
                >
                  {{ mail }}
                  <button
                    type="button"
                    style="border: none; background: transparent; margin-left: 4px"
                    @click="
                      draft.cc_emails.splice(idx, 1);
                      markDirty();
                    "
                  >
                    ×
                  </button>
                </span>
              </div>
            </div>
          </template>

          <label
            v-if="!draft.history_backfill"
            style="display: flex; align-items: center; gap: 8px; font-size: 13px"
          >
            <input v-model="publishWecomTodo" type="checkbox" />
            <span>发布后在企业微信内派发原生待办</span>
          </label>
        </div>

        <div class="sheet-foot">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            :disabled="publishing || previewLoading"
            @click="run(confirmPublish)"
          >
            {{ publishing ? "正在发布…" : "确认正式发布" }}
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Published PDF Viewer Popup -->
    <van-popup
      v-model:show="pdfPopupOpen"
      position="bottom"
      round
      :style="{ height: '92vh', display: 'flex', flexDirection: 'column' }"
    >
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">会议纪要 PDF 预览</span>
          <button
            type="button"
            class="sheet-close"
            @click="pdfPopupOpen = false"
          >
            关闭
          </button>
        </div>
        <div class="sheet-body">
          <div v-if="publishedPdfLoading" class="m-empty">
            <van-loading size="22">正在加载正式纪要…</van-loading>
          </div>
          <PdfViewer
            v-else
            :blob="publishedPdfBlob"
            :filename="`${meeting?.title || '会议纪要'}.pdf`"
          />
        </div>
      </div>
    </van-popup>

    <!-- Post-publish WeCom Native Todo Sheet -->
    <van-popup
      v-model:show="nativeTodoSheet"
      position="bottom"
      round
      :style="{ maxHeight: '78vh', display: 'flex', flexDirection: 'column' }"
    >
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">派发企业微信原生待办</span>
          <button
            type="button"
            class="sheet-close"
            @click="nativeTodoSheet = false"
          >
            完成
          </button>
        </div>
        <div class="sheet-body">
          <div v-if="!insideWeCom()" class="m-alert m-alert-warning">
            当前不在企业微信客户端内。会议已正式发布，您可稍后在手机企业微信中打开本会议继续确认派发原生待办。
          </div>
          <div
            v-for="t in nativeTodoTasks"
            :key="t.id"
            class="m-card"
            style="padding: 10px"
          >
            <div style="font-size: 13px; font-weight: 600; margin-bottom: 4px">
              {{ t.content }}
            </div>
            <div
              style="
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
              "
            >
              <span class="m-muted">
                {{ nativeTodoStatus[t.id] || `截止 ${t.due_date || "未设"}` }}
              </span>
              <button
                v-if="insideWeCom()"
                type="button"
                class="m-btn m-btn-sm m-btn-primary"
                @click="run(() => confirmOneNativeTodo(t))"
              >
                确认派发
              </button>
            </div>
          </div>
        </div>
      </div>
    </van-popup>

    <van-action-sheet
      v-model:show="actionSheetOpen"
      :actions="menuActions"
      cancel-text="取消"
      @select="run(() => onSelectMenu($event))"
    />

    <van-popup v-model:show="versionSheetOpen" position="bottom" round>
      <div class="sheet-wrap">
        <div class="sheet-head">
          <span class="sheet-title">切换历史版本</span>
          <button
            type="button"
            class="sheet-close"
            @click="versionSheetOpen = false"
          >
            关闭
          </button>
        </div>
        <div class="sheet-body">
          <button
            type="button"
            class="m-btn"
            :class="!selectedVersion ? 'm-btn-soft' : ''"
            @click="
              selectedVersion = null;
              versionSheetOpen = false;
            "
          >
            当前最新版本
          </button>
          <button
            v-for="v in meeting?.versions || []"
            :key="v.version"
            type="button"
            class="m-btn"
            :class="selectedVersion === v.version ? 'm-btn-soft' : ''"
            @click="
              selectedVersion = v.version;
              versionSheetOpen = false;
            "
          >
            V{{ v.version }} · {{ beijingTime(v.published_at) }}
          </button>
        </div>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.m-tabs-bar {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border-top: 1px solid #f4f4f5;
}
.m-tabs-bar button {
  border: none;
  background: transparent;
  padding: 10px 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--m-muted);
  position: relative;
  cursor: pointer;
}
.m-tabs-bar button.active {
  color: var(--m-primary);
  font-weight: 700;
}
.m-tabs-bar button.active::after {
  content: "";
  position: absolute;
  left: 16%;
  right: 16%;
  bottom: 0;
  height: 2.5px;
  border-radius: 99px;
  background: var(--m-primary);
}
.review-tools {
  display: flex;
  gap: 8px;
  align-items: center;
}
.r-card,
.c-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.r-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.seq-badge {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: #f4f4f5;
  color: #52525b;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.seq-badge.blue {
  background: #eff6ff;
  color: #1d4ed8;
}
.r-content {
  margin: 0;
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: var(--m-ink);
  line-height: 1.45;
}
.move-capsule {
  display: inline-flex;
  align-items: center;
  height: 26px;
  border: 1px solid var(--m-line);
  border-radius: 999px;
  background: #f8fafc;
  overflow: hidden;
  flex-shrink: 0;
}
.move-seg {
  width: 28px;
  height: 100%;
  border: none;
  background: transparent;
  color: #475569;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease;
}
.move-seg + .move-seg {
  border-left: 1px solid var(--m-line);
}
.move-seg:active:not(:disabled) {
  background: #eaf2ff;
  color: var(--m-primary);
}
.move-seg:disabled {
  opacity: 0.32;
  cursor: default;
}
.r-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--m-muted);
}
.status-pills {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 5px;
}
.st-pill {
  border: 1px solid #ebeff5;
  background: #f5f7fb;
  color: #4b5563;
  font-size: clamp(11px, 2.9vw, 12.5px);
  font-weight: 500;
  padding: 7px 2px;
  border-radius: 8px;
  cursor: pointer;
  white-space: nowrap;
  text-align: center;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  transition: all 0.14s ease;
}
.st-pill-wide {
  grid-column: 1 / -1;
  padding: 6px 10px;
}
.st-pill.selected {
  font-weight: 700;
}
.st-已完成 {
  background: #f0fdf4;
  color: #166534;
  border-color: #86efac;
}
.st-未完成 {
  background: #fef2f2;
  color: #991b1b;
  border-color: #fca5a5;
}
.st-未到期 {
  background: #eff6ff;
  color: #1d4ed8;
  border-color: #93c5fd;
}
.st-不统计,
.st-关闭 {
  background: #e4e4e7;
  color: #27272a;
  border-color: #a1a1aa;
}
.st-部分完成 {
  background: #fffbeb;
  color: #a16207;
  border-color: #fcd34d;
}
.r-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-top: 8px;
  border-top: 1px solid #f4f4f5;
}
.group-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--m-text-secondary);
  padding: 4px 2px 0;
}
.c-fields {
  background: #fafafa;
  border: 1px solid var(--m-line);
  border-radius: 8px;
  padding: 10px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
  font-size: 12px;
}
.c-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.att-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--m-line);
  border-radius: 8px;
  background: #fafafa;
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
</style>
