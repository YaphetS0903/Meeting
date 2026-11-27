<script setup lang="ts">
import { chineseError, reportClientError } from "../errorMessages";
import NewMeetingSetup from "../components/NewMeetingSetup.vue";
import {
  ref,
  computed,
  watch,
  onBeforeUnmount,
  nextTick,
  defineAsyncComponent,
} from "vue";
import {
  useRoute,
  useRouter,
  onBeforeRouteLeave,
  onBeforeRouteUpdate,
} from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { beijingTime } from "../time";
import {
  Calendar,
  Edit,
  List,
  Memo,
  Tickets,
  UserFilled,
  Plus,
  Sort,
} from "@element-plus/icons-vue";
import Sortable from "sortablejs";
import { orderReviewByOwners } from "../reviewSorting";
import {
  api,
  state,
  person as directoryPerson,
  localDate,
  run,
  download,
  statuses,
  rate,
  previewFile,
  departmentPath as directoryDepartmentPath,
  clientId,
  ApiResponseError,
} from "../api";
import PeopleSelect from "../components/PeopleSelect.vue";
import {
  attendeeRecipientIds,
  syncAttendeeRecipients,
} from "../notificationRecipients";
import TaskSheetReview from "../components/TaskSheetReview.vue";
import HistoricalTaskLink from "../components/HistoricalTaskLink.vue";
import MailCcSelect from "../components/MailCcSelect.vue";
const PdfPreview = defineAsyncComponent(
  () => import("../components/PdfPreview.vue"),
);
import StatusTag from "../components/StatusTag.vue";
import MeetingNativeTodos from "../components/MeetingNativeTodos.vue";
import OwnerResultsDialog from "../components/OwnerResultsDialog.vue";
import { mergedWorkContent, updateWorkContent } from "../workContent";
const meetingDepartments = ref<any[] | null>(null);
const newMeetingSetupOpen = ref(false);
const departmentOptions = computed(
  () => meetingDepartments.value ?? state.directory.departments,
);
function departmentPath(id: string | null | undefined) {
  return directoryDepartmentPath(id, [
    ...state.directory.departments,
    ...(meetingDepartments.value || []),
  ]);
}
function allowedMeeting(action: string) {
  return (
    route.params.id === "new" ||
    !!(meeting.value?.permissions?.[action] ?? meeting.value?.can_edit)
  );
}
function allowedFile(action: string) {
  return (
    route.params.id === "new" ||
    !!(meeting.value?.file_permissions?.[action] ?? meeting.value?.can_edit)
  );
}
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
  notify = ref(true),
  loading = ref(false),
  editing = ref(false),
  basicInfoEditing = ref(false),
  participantsExpanded = ref(false),
  participantDialogVisible = ref(false),
  participantDraftIds = ref<string[]>([]),
  onlineParticipantDraftIds = ref<string[]>([]),
  archiveSeriesDialogVisible = ref(false),
  archiveSeriesDraftId = ref(""),
  archiveSeriesSaving = ref(false),
  absenteesDraft = ref(""),
  reviewSearch = ref(""),
  arrangementSearch = ref("");
const carrying = ref(false),
  carryMessage = ref(""),
  carryError = ref(false);
const reviewResultsRequired = ref(false);
const missingReviewResults = computed(() =>
  (draft.value?.items || []).filter(
    (item: any) => item.section === "review" && !item.status,
  ),
);
const continuing = ref(false);
const cancellingRevision = ref(false);
const extension = ref<{
  mode: "extend" | "postpone";
  itemId: string;
  date: string;
  error: string;
  generation: number;
} | null>(null);
const extensionSource = computed(() =>
  draft.value?.items.find((item: any) => item.id === extension.value?.itemId),
);
const ownerAssessment = ref<{
  itemId: string;
  generation: number;
  owners: string;
  result: string;
} | null>(null);
const ownerAssessmentItem = computed(
  () =>
    show.value?.items.find(
      (item: any) => item.id === ownerAssessment.value?.itemId,
    ) || null,
);
function partialResultLabel(item: any) {
  const owners = item.owner_ids || [];
  const done = owners.filter(
    (id: string) => item.owner_results?.[id] === "已完成",
  ).length;
  return `部分完成（${done}/${owners.length}）`;
}
function openOwnerAssessment(item: any) {
  ownerAssessment.value = {
    itemId: item.id,
    generation: pageGeneration,
    owners: JSON.stringify(item.owner_ids),
    result: JSON.stringify([item.status, item.owner_results || {}]),
  };
}
function setReviewStatus(item: any, status: string) {
  if (status === "部分完成") {
    openOwnerAssessment(item);
    return;
  }
  item.status = status;
  item.owner_results = {};
  reviewStatusChanged(item);
}
function confirmOwnerAssessment(results: Record<string, string>) {
  const context = ownerAssessment.value,
    item = ownerAssessmentItem.value;
  if (
    !context ||
    context.generation !== pageGeneration ||
    !item ||
    !editable.value ||
    selectedVersion.value ||
    !allowedMeeting("assess") ||
    JSON.stringify(item.owner_ids) !== context.owners ||
    JSON.stringify([item.status, item.owner_results || {}]) !== context.result
  ) {
    ownerAssessment.value = null;
    ElMessage.info("当前事项已变化，请重新选择完成结果");
    return;
  }
  const values = new Set(Object.values(results));
  item.status = values.size === 1 ? Object.values(results)[0] : "部分完成";
  item.owner_results = item.status === "部分完成" ? results : {};
  reviewStatusChanged(item);
  ownerAssessment.value = null;
}
watch(selectedVersion, () => {
  ownerAssessment.value = null;
});
const nativeDialog = ref(false),
  nativeDispatchBusy = ref(false);
const participantPeople = ref<any[]>([]);
const meetingUsers = computed(() => {
  const users = new Map<string, any>(
    state.directory.users.map((user: any) => [user.id, user]),
  );
  for (const user of participantPeople.value)
    users.set(user.id, { ...users.get(user.id), ...user });
  return [...users.values()];
});
const mailCcUsers = computed(() => {
  const users = new Map<string, any>(
    participantPeople.value.map((user: any) => [user.id, user]),
  );
  for (const id of draft.value?.cc_ids || []) {
    if (users.has(id)) continue;
    const previous = meetingUsers.value.find((user) => user.id === id);
    if (previous) users.set(id, { ...previous, active: false });
  }
  return [...users.values()];
});
function person(id: string) {
  return (
    meetingUsers.value.find((user: any) => user.id === id)?.name ||
    directoryPerson(id)
  );
}
function inactiveOwnerNames(item: any) {
  const selected = new Set<string>(item.owner_ids || []);
  return meetingUsers.value
    .filter((user) => selected.has(user.id) && !user.active)
    .map((user) => user.name);
}
function canRecordHistoricalOwners(item: any) {
  return (
    item.section === "new" &&
    !item.task_id &&
    !item.historical_work_id &&
    inactiveOwnerNames(item).length > 0
  );
}
function updateArrangementOwners(item: any, value: string[] | null) {
  const next = [...new Set(value || [])];
  const removed = inactiveOwnerNames({
    owner_ids: (item.owner_ids || []).filter(
      (id: string) => !next.includes(id),
    ),
  });
  if (removed.length) {
    const history = `原责任人：${removed.join("、")}（已停用）`;
    const notes = item.notes || "";
    if (!notes.split("\n").some((line: string) => line.trim() === history))
      item.notes = notes ? `${notes}\n${history}` : history;
  }
  item.owner_ids = next;
}
function openNativeDispatch() {
  nativeDialog.value = true;
}
const visibleAttachments = computed(() => {
  const version = meeting.value?.published_version;
  const attachments = (meeting.value?.attachments || []).filter(
    (a: any) => !a.meeting_version || a.meeting_version === version,
  );
  const generatedKinds = new Set([
    "minutes_xlsx",
    "tasks_xlsx",
    "minutes_pdf",
    "minutes_png",
  ]);
  return [
    attachments.find((a: any) => a.kind === "minutes_xlsx") ||
      attachments.find((a: any) => a.kind === "tasks_xlsx"),
    attachments.find((a: any) => a.kind === "minutes_pdf"),
    ...attachments.filter((a: any) => !generatedKinds.has(a.kind)),
  ].filter(Boolean);
});
function attachmentName(attachment: any) {
  const snapshot = meeting.value?.versions.find(
    (version) => version.number === attachment.meeting_version,
  )?.snapshot;
  const extension = {
    minutes_xlsx: "xlsx",
    tasks_xlsx: "xlsx",
    minutes_pdf: "pdf",
    minutes_png: "png",
  }[attachment.kind];
  if (snapshot && extension) {
    const title = [...snapshot.title].slice(0, 120).join("");
    const label = attachment.kind === "tasks_xlsx" ? "任务单" : "纪要";
    if (
      attachment.filename ===
      `${title}-V${attachment.meeting_version}-${label}.${extension}`
    )
      return `${title}-${label}.${extension}`;
  }
  return attachment.filename;
}
const archiveSeriesKey = computed(() => {
  if (meeting.value)
    return meeting.value.series_id
      ? `series:${meeting.value.series_id}`
      : "unassigned";
  return typeof route.query.series === "string" ? route.query.series : "";
});
const historyReturnLocation = computed(() => {
  const department = route.query.history_department;
  return typeof department === "string"
    ? {
        path: "/history-meetings",
        query: {
          ...(department === "all" ? {} : { department }),
          ...(typeof route.query.history_series === "string"
            ? { series: route.query.history_series }
            : {}),
        },
      }
    : null;
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
let previewRequest = 0;
const mailStates: Record<string, string> = {
  pending: "待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "发送失败",
  blocked: "配置待完善",
  uncertain: "送达结果待核实",
  skipped: "已跳过",
};
const notificationRelatedIds = computed(() => {
  if (!draft.value) return new Set<string>();
  const ids = new Set([
    draft.value.host_id,
    draft.value.recorder_id,
    draft.value.reviewer_id,
    ...draft.value.attendee_ids,
  ]);
  for (const item of draft.value.items) {
    item.owner_ids.forEach((id: string) => ids.add(id));
    ids.add(item.verifier_id || draft.value.host_id);
  }
  return ids;
});
const notificationAllowedIds = computed(() =>
  meetingUsers.value
    .filter(
      (u: any) =>
        u.active &&
        (notificationRelatedIds.value.has(u.id) ||
          u.is_admin ||
          (draft.value?.department_id &&
            (u.department_id === draft.value.department_id ||
              u.department_ids?.includes(draft.value.department_id)))),
    )
    .map((u) => u.id),
);
const recipientCandidates = computed(() =>
  draft.value ? attendeeRecipientIds(draft.value) : [],
);
const mailRecipients = computed(() =>
  (draft.value?.recipient_ids || []).map((id: string) => ({
    id,
    name: person(id),
    email: meetingUsers.value.find((user) => user.id === id)?.email || "",
  })),
);
const notificationIssues = computed(() => {
  if (!draft.value || !notify.value || meeting.value?.historical) return [];
  const issues: string[] = [];
  const recipients = new Set<string>(draft.value.recipient_ids);
  if (!recipients.size)
    issues.push("请选择至少一位通知接收人，或取消发送通知后仅发布。");
  for (const id of new Set<string>([...recipients, ...draft.value.cc_ids])) {
    const user = (
      recipients.has(id) ? meetingUsers.value : mailCcUsers.value
    ).find((u) => u.id === id);
    if (!user) {
      issues.push("有接收人已不在通讯录中，请重新选择。");
      continue;
    }
    if (!user.active) {
      issues.push(`${user.name}已停用，请移出通知名单。`);
      continue;
    }
    if (recipients.has(id) && !notificationAllowedIds.value.includes(id)) {
      issues.push(`${user.name}无权查看该会议，请移出通知名单。`);
      continue;
    }
    const missing: string[] = [];
    if (!user.email) missing.push("邮箱");
    if (recipients.has(id) && !user.wecom_id) missing.push("企业微信绑定");
    if (missing.length)
      issues.push(`${user.name}尚未配置${missing.join("和")}。`);
  }
  return issues;
});
let recipientsInitialized = false;
let previousRecipientAttendees: string[] = [];
function syncRecipients(reset = false) {
  if (!draft.value || meeting.value?.historical) return;
  const next = syncAttendeeRecipients(
    draft.value,
    previousRecipientAttendees,
    reset,
  );
  previousRecipientAttendees = next.attendees;
  if (
    JSON.stringify(draft.value.recipient_ids) !==
    JSON.stringify(next.recipients)
  )
    draft.value.recipient_ids = next.recipients;
  if (JSON.stringify(draft.value.cc_ids) !== JSON.stringify(next.cc))
    draft.value.cc_ids = next.cc;
}
function useSuggestedRecipients() {
  draft.value.recipient_customized = false;
  syncRecipients(true);
}
function updateNotificationRecipients(ids: string[]) {
  const originalOrder = JSON.stringify(draft.value.attendee_ids);
  const previous = draft.value.recipient_ids || [];
  const reordering =
    ids.length === previous.length && ids.every((id) => previous.includes(id));
  const attendees = new Set(recipientCandidates.value);
  const ordered = ids.filter(
    (id) => attendees.has(id) && id !== draft.value.host_id,
  );
  if (reordering)
    draft.value.attendee_ids = [
      ...new Set([
        ...ordered,
        ...draft.value.attendee_ids.filter(
          (id: string) => id !== draft.value.host_id,
        ),
      ]),
    ];
  const online = new Set(draft.value.online_attendee_ids || []);
  draft.value.online_attendee_ids = attendeeRecipientIds(draft.value).filter(
    (id) => online.has(id),
  );
  draft.value.recipient_ids = ids;
  draft.value.recipient_customized = true;
  syncRecipients();
  if (
    preview.value &&
    originalOrder !== JSON.stringify(draft.value.attendee_ids)
  )
    void run(refreshPreview);
}
function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = "";
}
const previewError = ref("");
function inactiveVerifier(item: any) {
  const id = item.verifier_id || draft.value?.host_id;
  return meetingUsers.value.find(
    (person: any) => person.id === id && person.active === false,
  );
}
function locatePreviewIssue() {
  preview.value = false;
  const number = previewError.value.match(/上期任务第\s*(\d+)/)?.[1];
  const item = number
    ? review.value[Number(number) - 1]
    : draft.value.items.find(
        (row: any) =>
          !row.content?.trim() ||
          !row.owner_ids?.length ||
          (row.section === "new" && !row.due_date) ||
          inactiveVerifier(row),
      );
  nextTick(() => {
    const target = document.getElementById(
      item ? `work-row-${item.id}` : "meeting-basic",
    );
    target?.scrollIntoView({ block: "center", behavior: "smooth" });
    target
      ?.querySelector<HTMLElement>("input,textarea,button")
      ?.focus({ preventScroll: true });
  });
}
async function refreshPreview() {
  const generation = pageGeneration;
  const request = ++previewRequest;
  previewLoading.value = true;
  previewError.value = "";
  clearPreview();
  try {
    const saved = editable.value ? await save(false) : !!meeting.value;
    if (generation !== pageGeneration || request !== previewRequest) return;
    if (!saved) throw new Error(saveError.value || "请先保存草稿");
    const meetingId = meeting.value.id;
    const blob = await previewFile(meetingId, meeting.value.draft_version);
    if (
      generation !== pageGeneration ||
      request !== previewRequest ||
      meeting.value?.id !== meetingId ||
      !preview.value
    )
      return;
    clearPreview();
    previewUrl.value = URL.createObjectURL(blob);
  } catch (error: any) {
    if (generation === pageGeneration && request === previewRequest)
      previewError.value = chineseError(
        error,
        "预览生成失败，请检查会议信息和任务后重试",
      );
  } finally {
    if (generation === pageGeneration && request === previewRequest)
      previewLoading.value = false;
  }
}
async function previewTasks() {
  if (!allowedFile("download")) return;
  if (editable.value && !(await save(false))) return;
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
const sheetReviewActive = computed(
  () =>
    !!draft.value?.sheet_import_id && editable.value && !selectedVersion.value,
);
const sheetSource = ref<any>(null),
  sheetSourceError = ref(""),
  sheetSourceLoading = ref(false);
let sheetSourceGeneration = 0;
const sheetIssues = computed(() => {
  const source = sheetSource.value;
  return (source?.candidates[source.selected_index]?.issues || []).filter(
    (issue: any) =>
      !issue.item_id ||
      draft.value.items.some(
        (item: any) => item.source?.sheet_import_item_id === issue.item_id,
      ),
  );
});
const sheetPending = computed(() =>
  sheetIssues.value.filter(
    (issue: any) =>
      !(draft.value.import_confirmed_issues || []).includes(issue.id),
  ),
);
function sheetIssuesFor(item?: any) {
  if (
    item &&
    (!item.source?.sheet_import_item_id ||
      item.source?.sheet_import_id !== draft.value?.sheet_import_id)
  )
    return [];
  return sheetIssues.value.filter((issue: any) =>
    item ? issue.item_id === item.source?.sheet_import_item_id : !issue.item_id,
  );
}
async function loadSheetSource() {
  const generation = ++sheetSourceGeneration;
  sheetSource.value = null;
  sheetSourceError.value = "";
  sheetSourceLoading.value = false;
  if (!sheetReviewActive.value) return;
  sheetSourceLoading.value = true;
  try {
    const source = await api(
      `/meeting-sheet-imports/${draft.value.sheet_import_id}`,
    );
    if (generation === sheetSourceGeneration) sheetSource.value = source;
  } catch (error: any) {
    if (generation === sheetSourceGeneration)
      sheetSourceError.value = chineseError(error);
  } finally {
    if (generation === sheetSourceGeneration) sheetSourceLoading.value = false;
  }
}
watch(
  () => [sheetReviewActive.value, draft.value?.sheet_import_id],
  loadSheetSource,
);
const assessmentVersion = computed(
  () => selectedVersion.value || meeting.value?.published_version,
);
const assessment = computed(
  () => meeting.value?.assessments?.[String(assessmentVersion.value)],
);
const show = computed(() => {
  const version =
    selectedVersion.value ||
    (!editing.value && meeting.value?.published_version);
  if (!version) return draft.value;
  const snapshot = meeting.value?.versions.find(
    (x) => x.number === version,
  )?.snapshot;
  if (!snapshot) return snapshot;
  return version === meeting.value?.published_version
    ? { ...snapshot, title: meeting.value.title || snapshot.title }
    : snapshot;
});
const readOnlyView = computed(() => !editable.value || !!selectedVersion.value);
const participants = computed(() => {
  if (!show.value) return [];
  const people = new Map<string, { id: string; name: string; role: string }>();
  const add = (id: string | null, role: string) => {
    if (!id || people.has(id)) return;
    const online = show.value.online_attendee_ids?.includes(id);
    people.set(id, {
      id,
      name: person(id),
      role: online ? `${role} · 线上` : role,
    });
  };
  add(show.value.host_id, "主持人");
  for (const id of show.value.attendee_ids || []) add(id, "参会人员");
  return [...people.values()];
});
const visibleParticipants = computed(() =>
  participantsExpanded.value || participants.value.length <= 5
    ? participants.value
    : participants.value.slice(0, 4),
);
const participantDialogCount = computed(
  () =>
    new Set(
      [
        draft.value?.host_id,
        ...participantDraftIds.value,
        ...onlineParticipantDraftIds.value,
      ].filter(Boolean),
    ).size,
);
function setOnsiteParticipants(ids: string[]) {
  const selected = new Set(ids);
  participantDraftIds.value = [...selected].filter(
    (id) => id !== draft.value?.host_id,
  );
  onlineParticipantDraftIds.value = onlineParticipantDraftIds.value.filter(
    (id) => !selected.has(id),
  );
}
function setOnlineParticipants(ids: string[]) {
  onlineParticipantDraftIds.value = [...new Set(ids)];
  participantDraftIds.value = participantDraftIds.value.filter(
    (id) => !ids.includes(id),
  );
}
watch(
  () => [draft.value?.host_id, ...(draft.value?.attendee_ids || [])],
  (ids) => {
    if (!draft.value?.online_attendee_ids) return;
    const retained = draft.value.online_attendee_ids.filter((id: string) =>
      ids.includes(id),
    );
    if (retained.length !== draft.value.online_attendee_ids.length)
      draft.value.online_attendee_ids = retained;
    if (
      !loading.value &&
      editable.value &&
      (recipientsInitialized || draft.value.recipient_ids?.length)
    )
      syncRecipients();
  },
);
const review = computed(
    () => show.value?.items.filter((x) => x.section === "review") || [],
  ),
  arrangements = computed(() => {
    const items = show.value?.items || [];
    const rows = items.filter((x) => x.section === "new");
    return [
      ...rows.filter((item) => !isUnfinishedExtension(item, items)),
      ...rows.filter((item) => isUnfinishedExtension(item, items)),
    ];
  });
function isUnfinishedExtension(item: any, items = show.value?.items || []) {
  return (
    item.section === "new" &&
    (!!item.source?.extended_from_item_id ||
      items.some(
        (row: any) =>
          row.section === "review" &&
          ["未完成", "部分完成"].includes(row.status) &&
          ((item.task_id && item.task_id === row.task_id) ||
            (item.historical_work_id &&
              item.historical_work_id === row.historical_work_id)),
      ))
  );
}
function arrangementGroupHeading(item: any, index: number) {
  if (!arrangements.value.some((row) => isUnfinishedExtension(row))) return "";
  const extended = isUnfinishedExtension(item);
  if (
    index &&
    extended === isUnfinishedExtension(visibleArrangements.value[index - 1])
  )
    return "";
  return extended ? "上次未完成延续事项" : "本次新建工作";
}
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
const reviewSortOpen = ref(false);
const reviewSortMode = ref<"result" | "owners">("owners");
function sortReview() {
  if (
    !editable.value ||
    selectedVersion.value ||
    loading.value ||
    carrying.value
  )
    return;
  const resultRank = (item: any) => {
    const rank = ["已完成", "部分完成", ...statuses.slice(1)].indexOf(
      item.status === "已关闭" ? "关闭" : item.status,
    );
    return rank < 0 ? statuses.length + 1 : rank;
  };
  const ordered =
    reviewSortMode.value === "owners"
      ? orderReviewByOwners(review.value, meeting.value?.continuation_origins)
      : [...review.value].sort(
          (left, right) => resultRank(left) - resultRank(right),
        );
  const label = reviewSortMode.value === "owners" ? "责任人分类" : "完成结果";
  reviewSortOpen.value = false;
  if (ordered.every((item, index) => item === review.value[index])) {
    ElMessage.info(`当前已按${label}排序`);
    return;
  }
  let index = 0;
  draft.value.items = draft.value.items.map((item: any) =>
    item.section === "review" ? ordered[index++] : item,
  );
  ElMessage.success(`已按${label}排序`);
}
const saveActions = ref<HTMLElement | null>(null);
const addWorkOffset = ref(132);
watch(
  saveActions,
  (bar, _, onCleanup) => {
    const header = document.querySelector<HTMLElement>(".topbar");
    if (!bar || !header) return;
    const updateOffset = () => {
      bar.style.setProperty(
        "--meeting-header-height",
        `${header.getBoundingClientRect().height}px`,
      );
      addWorkOffset.value = Math.ceil(
        header.getBoundingClientRect().height +
          bar.getBoundingClientRect().height,
      );
    };
    const observer = new ResizeObserver(updateOffset);
    observer.observe(header);
    observer.observe(bar);
    updateOffset();
    onCleanup(() => observer.disconnect());
  },
  { flush: "post" },
);
const reviewBody = ref<HTMLTableSectionElement | null>(null);
const reviewOrderNotice = ref("");
function reorderReview(id: string, target: number) {
  if (readOnlyView.value || loading.value || carrying.value) return;
  const ordered = [...review.value];
  const current = ordered.findIndex((item) => item.id === id);
  const destination = ordered.findIndex(
    (item) => item.id === visibleReview.value[target]?.id,
  );
  if (current < 0 || destination < 0 || current === destination) return;
  ordered.splice(destination, 0, ordered.splice(current, 1)[0]);
  let index = 0;
  draft.value.items = draft.value.items.map((item: any) =>
    item.section === "review" ? ordered[index++] : item,
  );
  reviewOrderNotice.value = `已将第${current + 1}项上期任务移至第${destination + 1}项`;
}
function orderReviewByKeyboard(event: KeyboardEvent, id: string) {
  if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
  event.preventDefault();
  const index = visibleReview.value.findIndex((item) => item.id === id);
  reorderReview(id, index + (event.key === "ArrowUp" ? -1 : 1));
}
watch(
  [reviewBody, readOnlyView, loading, carrying],
  ([body, readOnly, busy, importing], _, onCleanup) => {
    if (!body || readOnly || busy || importing) return;
    let originalNextSibling: ChildNode | null = null;
    const sorter = Sortable.create(body, {
      handle: ".task-drag-handle",
      draggable: "tr[data-review-id]",
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 0
        : 150,
      ghostClass: "task-order-ghost",
      chosenClass: "task-order-chosen",
      dragClass: "task-order-drag",
      delay: 120,
      delayOnTouchOnly: true,
      touchStartThreshold: 4,
      fallbackTolerance: 4,
      scrollSensitivity: 70,
      scrollSpeed: 12,
      onStart(event) {
        originalNextSibling = event.item.nextSibling;
      },
      onEnd(event) {
        const { item, oldDraggableIndex: from, newDraggableIndex: to } = event;
        if (from === undefined || to === undefined || from === to) return;
        // Restore the DOM so Vue moves each keyed row with its own inputs.
        body.insertBefore(
          item,
          originalNextSibling?.parentNode === body ? originalNextSibling : null,
        );
        originalNextSibling = null;
        const id = item.dataset.reviewId;
        if (id) reorderReview(id, to);
      },
    });
    onCleanup(() => sorter.destroy());
  },
  { flush: "post" },
);
const arrangementBody = ref<HTMLTableSectionElement | null>(null);
const taskOrderNotice = ref("");
function reorderArrangement(id: string, target: number) {
  if (!editable.value || selectedVersion.value || loading.value) return;
  const items = [...draft.value.items];
  const ordered = [...arrangements.value];
  const current = ordered.findIndex((item) => item.id === id);
  if (
    current < 0 ||
    target < 0 ||
    target >= ordered.length ||
    current === target
  )
    return;
  const group = ordered
    .map((item, index) => ({ item, index }))
    .filter(
      ({ item }) =>
        isUnfinishedExtension(item) === isUnfinishedExtension(ordered[current]),
    );
  target = Math.max(
    group[0].index,
    Math.min(group[group.length - 1].index, target),
  );
  if (current === target) return;
  ordered.splice(target, 0, ordered.splice(current, 1)[0]);
  let index = 0;
  draft.value.items = items.map((item) =>
    item.section === "new" ? ordered[index++] : item,
  );
  taskOrderNotice.value = `已将第${current + 1}项任务移至第${target + 1}项`;
}
function orderByKeyboard(event: KeyboardEvent, id: string) {
  if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
  event.preventDefault();
  const index = arrangements.value.findIndex((item) => item.id === id);
  reorderArrangement(id, index + (event.key === "ArrowUp" ? -1 : 1));
}
watch(
  [arrangementBody, readOnlyView, loading],
  ([body, readOnly, busy], _, onCleanup) => {
    if (!body || readOnly || busy) return;
    let originalNextSibling: ChildNode | null = null;
    const sorter = Sortable.create(body, {
      handle: ".task-drag-handle",
      draggable: "tr[data-arrangement-id]",
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 0
        : 150,
      ghostClass: "task-order-ghost",
      chosenClass: "task-order-chosen",
      dragClass: "task-order-drag",
      delay: 120,
      delayOnTouchOnly: true,
      touchStartThreshold: 4,
      fallbackTolerance: 4,
      scrollSensitivity: 70,
      scrollSpeed: 12,
      onStart(event) {
        originalNextSibling = event.item.nextSibling;
      },
      onMove(event) {
        const moved = arrangements.value.find(
          (row) => row.id === event.dragged.dataset.arrangementId,
        );
        const related = arrangements.value.find(
          (row) => row.id === event.related.dataset.arrangementId,
        );
        return (
          !!moved &&
          !!related &&
          isUnfinishedExtension(moved) === isUnfinishedExtension(related)
        );
      },
      onEnd(event) {
        const { item, oldDraggableIndex: from, newDraggableIndex: to } = event;
        if (from === undefined || to === undefined || from === to) return;
        // Restore the DOM before updating Vue's keyed rows, so task inputs stay
        // attached to their own IDs when the framework applies the new order.
        body.insertBefore(
          item,
          originalNextSibling?.parentNode === body ? originalNextSibling : null,
        );
        originalNextSibling = null;
        const id = item.dataset.arrangementId;
        if (id) reorderArrangement(id, to);
      },
    });
    onCleanup(() => sorter.destroy());
  },
  { flush: "post" },
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
  if (cancellingRevision.value) return;
  const generation = pageGeneration;
  timer = setTimeout(() => {
    timer = undefined;
    if (generation === pageGeneration && editable.value) void save(false);
  }, 1600);
}
const minuteAutosave = setInterval(() => {
  if (
    loading.value ||
    saving.value ||
    busy.value ||
    cancellingRevision.value ||
    !editable.value ||
    selectedVersion.value ||
    !dirty.value ||
    saveError.value ||
    !draft.value
  )
    return;
  // A new meeting can be saved automatically once its basic information is
  // present. Do not create blank meetings or repeatedly submit an incomplete form.
  if (
    !meeting.value &&
    (!draft.value.title?.trim() ||
      !draft.value.host_id ||
      !draft.value.meeting_date ||
      (draft.value.kind === "department" && !draft.value.department_id))
  )
    return;
  void save(false);
}, 60_000);
async function load(background = false) {
  reviewSortOpen.value = false;
  reviewResultsRequired.value = false;
  const generation = ++pageGeneration;
  extension.value = null;
  ownerAssessment.value = null;
  carrying.value = false;
  carryMessage.value = "";
  carryError.value = false;
  const routeId = String(route.params.id);
  if (routeId === "new" || meeting.value?.id !== routeId) {
    participantPeople.value = [];
    meetingDepartments.value = null;
    // Publication choices belong to one meeting, even when the router reuses this page.
    notify.value = true;
    recipientsInitialized = false;
    nativeDialog.value = false;
    nativeDispatchBusy.value = false;
    preview.value = false;
    previewLoading.value = false;
    clearPreview();
  }
  cancelAutosave();
  activeSave = null;
  saving.value = false;
  loading.value = true;
  selectedVersion.value = null;
  basicInfoEditing.value = false;
  newMeetingSetupOpen.value = routeId === "new";
  participantsExpanded.value = false;
  participantDialogVisible.value = false;
  try {
    if (routeId === "new") {
      editing.value = true;
      meeting.value = null;
      const initialSeriesId =
        typeof route.query.series === "string" &&
        route.query.series.startsWith("series:")
          ? route.query.series.slice(7)
          : null;
      const initialSeries = state.directory.series.find(
        (s: any) => s.id === initialSeriesId,
      );
      const initialKind =
        initialSeries?.kind === "special" ||
        (!initialSeries && !state.me.department_id)
          ? "special"
          : "department";
      draft.value = {
        title: "",
        department_id:
          initialKind === "special"
            ? null
            : initialSeries?.department_id || state.me.department_id || "",
        series_id: initialSeriesId,
        kind: initialKind,
        history_backfill: false,
        meeting_date: localDate(),
        meeting_time: "",
        location: "",
        host_id: state.me.id,
        recorder_id: state.me.id,
        reviewer_id: null,
        attendee_ids: [],
        online_attendee_ids: [],
        absentees: "",
        decisions: "",
        items: [],
        attachment_ids: [],
        recipient_ids: [],
        cc_ids: [],
        cc_emails: [],
      };
    } else {
      const result = await api("/meetings/" + routeId, "GET", undefined, {
        background,
      });
      if (generation !== pageGeneration || route.params.id !== routeId) return;
      meeting.value = result;
      if (meeting.value.historical) notify.value = false;
      editing.value =
        (!meeting.value.published_version || route.query.carry === "1") &&
        meeting.value.has_draft &&
        meeting.value.can_edit;
      draft.value = JSON.parse(
        JSON.stringify(
          meeting.value.draft || meeting.value.versions[0]?.snapshot,
        ),
      );
      draft.value.reviewer_id = null;
      draft.value.cc_emails ||= [];
      if (meeting.value.published_version && !editing.value)
        selectedVersion.value = meeting.value.published_version;
    }
    previousRecipientAttendees = attendeeRecipientIds(draft.value);
    dirty.value = false;
    saveError.value = "";
    // Hosts and task owners can be any active employee, including people outside the
    // normal directory scope. Load the editor roster before editing tasks.
    if (
      routeId === "new" ||
      meeting.value?.can_edit ||
      allowedMeeting("preview") ||
      allowedMeeting("publish")
    )
      await refreshMeetingPeople();
  } finally {
    if (generation === pageGeneration) loading.value = false;
  }
  if (
    generation === pageGeneration &&
    route.query.carry === "1" &&
    editable.value
  ) {
    const query = { ...route.query };
    delete query.carry;
    await router.replace({ path: route.path, query });
    await carry();
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
    if (loading.value || !editable.value || selectedVersion.value) return;
    dirty.value = true;
    saveError.value = "";
    cancelAutosave();
    if (meeting.value && editable.value) scheduleAutosave();
  },
  { deep: true, flush: "sync" },
);
async function save(manual = true) {
  const generation = pageGeneration;
  while (activeSave) {
    const saved = await activeSave;
    if (generation !== pageGeneration) return false;
    if (!saved) {
      if (manual)
        ElMessage.error("草稿保存失败：" + (saveError.value || "请重试"));
      return false;
    }
  }
  if (
    generation !== pageGeneration ||
    cancellingRevision.value ||
    newMeetingSetupOpen.value ||
    loading.value ||
    !draft.value ||
    !editable.value ||
    selectedVersion.value
  )
    return false;
  if (!draft.value.title?.trim()) {
    saveError.value = "请填写会议名称后再保存草稿";
    if (manual) {
      reportClientError(
        "title_required",
        meeting.value?.id ? `/meetings/${meeting.value.id}` : "/meetings",
        meeting.value?.id ? "PUT" : "POST",
      );
      basicInfoEditing.value = true;
      ElMessage.error(saveError.value);
      await nextTick();
      const input = document.querySelector<HTMLInputElement>(
        ".meeting-info-card input[aria-label='会议名称']",
      );
      input?.focus();
      input?.scrollIntoView({ block: "center" });
    }
    return false;
  }
  for (const item of draft.value.items) {
    if (item.section === "new" && !item.task_id && !item.historical_work_id)
      item.historical_owner_record = canRecordHistoricalOwners(item);
  }
  if (!dirty.value && meeting.value) {
    if (manual) ElMessage.success("草稿已保存");
    return true;
  }
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
      can_delete:
        result.can_delete ??
        (state.me.is_admin || state.me.id === result.host_id),
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
    if (generation === pageGeneration) {
      saveError.value = chineseError(e);
      if (manual) ElMessage.error("草稿保存失败：" + chineseError(e));
    }
    return false;
  } finally {
    if (generation === pageGeneration) {
      saving.value = false;
      if (saved && dirty.value && editable.value) scheduleAutosave();
    }
  }
}
function add() {
  const id = clientId();
  draft.value.items.push({
    id,
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
function addHistoricalReview() {
  draft.value.items.push({
    id: clientId(),
    section: "review",
    content: "",
    category: "",
    requirements: "",
    owner_ids: [],
    due_date: null,
    status: "",
    verifier_id: null,
    deliverable: "",
    notes: "",
    exclusion_reason: "",
    task_id: null,
    task_version: null,
  });
}
function historyBackfillChanged(value: any) {
  if (
    draft.value.items.some(
      (item: any) => item.task_id || item.backfill_reference,
    )
  ) {
    draft.value.history_backfill = !value;
    ElMessage.error("请先移除已带入任务或取消关联，再切换补录模式");
    return;
  }
  if (value) notify.value = false;
}
function meetingKindChanged(value: string) {
  if (value === "special") {
    draft.value.department_id = null;
  }
}
const selectedMeetingSeries = computed(() =>
  state.directory.series.find(
    (series: any) => series.id === draft.value?.series_id,
  ),
);
const seriesKindLocked = computed(() =>
  ["department", "special"].includes(selectedMeetingSeries.value?.kind),
);
function meetingSeriesChanged(value: string) {
  const series = state.directory.series.find((item: any) => item.id === value);
  if (series && ["department", "special"].includes(series.kind)) {
    const existing = meeting.value?.series_id === value;
    draft.value.kind = existing
      ? meeting.value.draft?.kind || meeting.value.kind || series.kind
      : series.kind;
    if (draft.value.kind === "special") draft.value.department_id = null;
    else if (!draft.value.department_id)
      draft.value.department_id = series.department_id;
  }
  if (value && !meeting.value?.published_version) run(carry);
}
async function carry() {
  if (carrying.value) return;
  if (draft.value.history_backfill) return;
  if (!draft.value.series_id) throw new Error("请先选择会议系列");
  const generation = pageGeneration;
  const requestedSeries = draft.value.series_id;
  const requestedDate = draft.value.meeting_date;
  carrying.value = true;
  carryMessage.value = "";
  carryError.value = false;
  try {
    const params = new URLSearchParams({ meeting_date: requestedDate });
    if (meeting.value?.id) params.set("exclude_meeting_id", meeting.value.id);
    const items = await api(
      `/series/${requestedSeries}/carry-forward?${params}`,
    );
    if (
      generation !== pageGeneration ||
      draft.value.series_id !== requestedSeries ||
      draft.value.meeting_date !== requestedDate
    )
      return;
    const key = (item: any) =>
      item.historical_work_id
        ? `history:${item.historical_work_id}`
        : `task:${item.task_id}`;
    const known = new Set(
      draft.value.items.filter((x) => x.section === "review").map(key),
    );
    const additions = items.filter((item: any) => {
      const identity = key(item);
      if (known.has(identity)) return false;
      known.add(identity);
      return true;
    });
    draft.value.items.push(
      ...additions.map((item: any) => {
        const status =
          item.status === "未到期" &&
          item.due_date &&
          item.due_date >= requestedDate
            ? "未到期"
            : "";
        const source = { ...(item.source || {}) };
        delete source.continuation_default_status;
        if (status) source.continuation_default_status = status;
        return { ...item, status, source };
      }),
    );
    carryMessage.value = additions.length
      ? `已带入 ${additions.length} 项上期任务，请核对完成结果。`
      : items.length
        ? "上期任务已全部带入，无需重复添加。"
        : "该系列暂无需要继续跟进的上期任务。";
    if (additions.length) ElMessage.success(carryMessage.value);
    else ElMessage.info(carryMessage.value);
  } catch (error: any) {
    if (
      generation === pageGeneration &&
      draft.value.series_id === requestedSeries
    ) {
      carryError.value = true;
      carryMessage.value = `上期任务加载失败：${chineseError(error)}。请重试带入。`;
    }
    throw error;
  } finally {
    if (generation === pageGeneration) carrying.value = false;
  }
}
async function newSeries() {
  const { value } = await ElMessageBox.prompt(
    "为连续会议设置一个名称，例如：质量部周例会",
    "新建会议系列",
    { inputValidator: (v) => !!v?.trim() || "请输入名称" },
  );
  const s = await api("/series", "POST", {
    name: value,
    kind: draft.value.kind,
    department_id: draft.value.department_id,
  });
  const known = state.directory.series.find((item: any) => item.id === s.id);
  if (known) Object.assign(known, s);
  else state.directory.series.push(s);
  if (s.can_start_meeting === false)
    throw new Error(
      "请从系列内已发布的会议申请接续权限，或联系系列创建人建立首场会议",
    );
  draft.value.series_id = s.id;
  meetingSeriesChanged(s.id);
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
      query: { ...route.query, series: `series:${archiveSeriesDraftId.value}` },
    });
    await load(true);
    ElMessage.success("会议已归入所选系列");
  } finally {
    archiveSeriesSaving.value = false;
  }
}
async function requestEdit(forContinuation = false) {
  if (
    !meeting.value ||
    (meeting.value.can_edit && !meeting.value.can_request_continue) ||
    meeting.value.my_edit_request?.state === "pending"
  )
    return;
  await ElMessageBox.confirm(
    "确定向会议主持人申请此会议的编辑与持续新建权限吗？主持人审批通过后才可操作。",
    forContinuation ? "申请接续权限" : "申请编辑会议",
    { type: "info", confirmButtonText: "确定申请" },
  );
  await api(`/meetings/${meeting.value.id}/edit-requests`, "POST");
  await load(true);
  ElMessage.success("申请已提交，等待主持人审批");
}
async function decideEdit(request: any, decision: "approved" | "rejected") {
  await ElMessageBox.confirm(
    `确定${decision === "approved" ? "通过" : "拒绝"}${request.applicant_name}的编辑与持续新建申请吗？`,
    "审批编辑权限",
    { type: decision === "rejected" ? "warning" : "info" },
  );
  await api(
    `/meetings/${meeting.value.id}/edit-requests/${request.id}/decision`,
    "POST",
    { decision },
  );
  await load(true);
  ElMessage.success(decision === "approved" ? "已授权" : "已拒绝申请");
}
async function continueMeeting() {
  if (!meeting.value?.can_continue || continuing.value) return;
  const sourceId = meeting.value.id;
  const generation = pageGeneration;
  const series = archiveSeriesKey.value;
  continuing.value = true;
  try {
    await ElMessageBox.confirm("是否确认新建会议？", "持续新建", {
      type: "info",
      confirmButtonText: "确认新建",
      cancelButtonText: "取消",
    });
    if (meeting.value?.id !== sourceId || generation !== pageGeneration) return;
    if ((dirty.value || saving.value) && !(await save(false)))
      throw new Error(saveError.value || "请先保存当前会议修改，再持续新建");
    if (generation !== pageGeneration) return;
    const created = await api(`/meetings/${sourceId}/continue`, "POST");
    if (generation !== pageGeneration) return;
    ElMessage.success("已建立下一场会议草稿，请核对带入事项和日期");
    await router.push({
      path: `/meetings/${created.id}`,
      query: series.startsWith("series:") ? { series } : {},
    });
  } finally {
    continuing.value = false;
  }
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
  await router.push(
    historyReturnLocation.value || {
      path: "/meetings",
      query: target ? { series: target } : {},
    },
  );
}
async function openPreview() {
  if (!allowedMeeting("preview")) return;
  if (!editable.value) {
    preview.value = true;
    await refreshPreview();
    return;
  }
  if (sheetReviewActive.value) {
    if (!sheetSource.value) {
      ElMessage.error(
        sheetSourceError.value
          ? "导入原文读取失败，请在会议信息中重新读取后核对"
          : "正在读取导入原文，请稍候再预览",
      );
      return;
    }
    if (sheetPending.value.length) {
      ElMessage.error("请在会议信息或对应任务行核对确认后再预览发布");
      const issue = sheetPending.value[0];
      const item = draft.value.items.find(
        (row: any) => row.source?.sheet_import_item_id === issue.item_id,
      );
      const target = document.getElementById(
        item ? `sheet-check-${item.id}` : "sheet-check-meeting",
      );
      target?.scrollIntoView({ block: "center", behavior: "smooth" });
      target?.querySelector("input")?.focus({ preventScroll: true });
      return;
    }
  }
  reviewResultsRequired.value = true;
  if (missingReviewResults.value.length) {
    ElMessage.error("请逐项选择上期任务的完成结果后再预览发布");
    await nextTick();
    const field = document.getElementById(
      `review-result-${missingReviewResults.value[0].id}`,
    );
    field?.scrollIntoView({ block: "center", behavior: "smooth" });
    field?.querySelector("input")?.focus({ preventScroll: true });
    return;
  }
  if (!(await refreshMeetingPeople())) return;
  recipientsInitialized = true;
  syncRecipients();
  if (await save()) {
    preview.value = true;
    await refreshPreview();
  }
}
async function publish() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (notificationIssues.value.length)
      throw new Error(notificationIssues.value.join("\n"));
    const meetingId = meeting.value.id;
    const generation = pageGeneration;
    const expectedVersion = meeting.value.draft_version;
    const previousPublication = meeting.value.published_version || 0;
    let published;
    try {
      published = await api("/meetings/" + meetingId + "/publish", "POST", {
        expected_version: expectedVersion,
        notify: notify.value,
        shared_recipients: true,
        reason: previousPublication ? "更新会议纪要" : "发布会议纪要",
      });
    } catch (error) {
      if (!(error instanceof ApiResponseError) || !error.outcomeUnknown)
        throw error;
      try {
        const actual = await api("/meetings/" + meetingId);
        if (
          actual.draft_version === expectedVersion &&
          !actual.has_draft &&
          actual.published_version === previousPublication + 1
        )
          published = actual;
      } catch {
        // Never retry a publish request just because its response was lost.
      }
      if (!published)
        throw new Error(
          "发布响应异常，暂未确认本次是否发布成功。请先核对会议状态，勿重复发布；当前草稿内容保留。",
        );
    }
    if (
      generation !== pageGeneration ||
      String(route.params.id) !== meetingId ||
      meeting.value?.id !== meetingId
    )
      return;
    meeting.value = { ...meeting.value, ...published };
    preview.value = false;
    const sync = published.continuation_sync;
    const supplement = published.history_supplement;
    const publicationMessage =
      notify.value && !meeting.value.historical
        ? "发布成功"
        : "会议已发布，本次未发送邮件及企业微信通知";
    ElMessage.success(
      publicationMessage +
        (sync?.updated ? `；已同步 ${sync.updated} 场下期草稿` : ""),
    );
    if (supplement?.added)
      ElMessage.success(
        `已补齐 ${supplement.target_date} 会议的 ${supplement.added} 项上期任务，请到${supplement.revision ? "修订" : "会议"}草稿核对后发布`,
      );
    if (supplement?.skipped)
      ElMessage.warning(
        "部分上期任务未自动补齐，请由下期会议编制人核对权限或重复事项",
      );
    if (sync?.skipped)
      ElMessage.warning(
        `有 ${sync.skipped} 场下期草稿未同步，请由有权限的编制人核对`,
      );
    const refreshGeneration = pageGeneration + 1;
    try {
      await load(true);
    } catch (e: any) {
      if (pageGeneration === refreshGeneration)
        ElMessage.warning(
          `会议已发布；详情暂未刷新，请稍后刷新核对。企微待办可从“派发企微待办”继续：${chineseError(e)}`,
        );
    }
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
  notify.value = true;
  await load(true);
  openEditor();
}
async function cancelRevision() {
  if (
    cancellingRevision.value ||
    busy.value ||
    nativeDispatchBusy.value ||
    !allowedMeeting("cancel_revision") ||
    !meeting.value?.published_version ||
    !meeting.value?.has_draft
  )
    return;
  const id = meeting.value.id;
  const generation = pageGeneration;
  cancellingRevision.value = true;
  cancelAutosave();
  try {
    if (activeSave && !(await activeSave))
      throw new Error("草稿保存状态未确认，请刷新核对后再取消修订");
    await ElMessageBox.confirm(
      "取消后将放弃本次所有未发布的修改，恢复已发布内容。已发布文件和任务保留，不会重新发送通知。",
      "取消修订",
      {
        type: "warning",
        confirmButtonText: "确认取消修订",
        cancelButtonText: "继续修订",
      },
    );
    if (generation !== pageGeneration || meeting.value?.id !== id) return;
    const result = await api(`/meetings/${id}/revision/cancel`, "POST", {
      expected_version: meeting.value.draft_version,
    });
    if (generation !== pageGeneration || meeting.value?.id !== id) return;
    editing.value = false;
    selectedVersion.value = result.published_version;
    meeting.value = { ...meeting.value, ...result };
    draft.value = JSON.parse(JSON.stringify(result.draft));
    dirty.value = false;
    saveError.value = "";
    preview.value = false;
    clearPreview();
    ElMessage.success("已取消修订，会议恢复为已发布状态");
    try {
      await load(true);
    } catch {
      ElMessage.warning("修订已取消，详情暂未刷新，请稍后刷新页面");
    }
  } catch (error: any) {
    if (error !== "cancel" && error !== "close")
      saveError.value = chineseError(error, "取消修订失败，请刷新核对后重试");
    throw error;
  } finally {
    cancellingRevision.value = false;
    if (dirty.value && editable.value && !saveError.value) scheduleAutosave();
  }
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
function confirmNewMeetingSetup() {
  newMeetingSetupOpen.value = false;
}
async function cancelNewMeetingSetup() {
  dirty.value = false;
  await router.replace({
    path: "/meetings",
    query: archiveSeriesKey.value ? { series: archiveSeriesKey.value } : {},
  });
}
async function editCard() {
  if (await ensureEditableDraft()) basicInfoEditing.value = true;
}
async function refreshMeetingPeople() {
  const generation = pageGeneration;
  const params = new URLSearchParams({ include_inactive: "true" });
  if (meeting.value?.id) params.set("meeting_id", meeting.value.id);
  const directory = await api("/meeting-people?" + params);
  if (generation !== pageGeneration) return false;
  participantPeople.value = directory.users;
  meetingDepartments.value = directory.departments ?? null;
  return true;
}
async function openParticipantsDialog() {
  if (!(await ensureEditableDraft())) return;
  if (!(await refreshMeetingPeople())) return;
  const attending = new Set([
    draft.value.host_id,
    ...(draft.value.attendee_ids || []),
  ]);
  onlineParticipantDraftIds.value = [
    ...new Set<string>(draft.value.online_attendee_ids || []),
  ].filter((id) => attending.has(id));
  participantDraftIds.value = [
    ...new Set<string>(draft.value.attendee_ids || []),
  ].filter(
    (id) =>
      id !== draft.value.host_id &&
      !onlineParticipantDraftIds.value.includes(id),
  );
  absenteesDraft.value = draft.value.absentees || "";
  participantDialogVisible.value = true;
}
function saveParticipantsDialog() {
  draft.value.attendee_ids = [
    ...new Set([
      ...participantDraftIds.value,
      ...onlineParticipantDraftIds.value,
    ]),
  ].filter((id) => id !== draft.value.host_id);
  const attending = new Set([draft.value.host_id, ...draft.value.attendee_ids]);
  draft.value.online_attendee_ids = [
    ...new Set(
      onlineParticipantDraftIds.value.filter((id) => attending.has(id)),
    ),
  ];
  draft.value.absentees = absenteesDraft.value;
  participantDialogVisible.value = false;
}
async function closeEditor() {
  if (dirty.value && !(await save(false))) return;
  editing.value = false;
  selectedVersion.value = meeting.value?.published_version || null;
  basicInfoEditing.value = false;
  participantDialogVisible.value = false;
  extension.value = null;
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
function hasExtension(item: any) {
  return (draft.value?.items || []).some(
    (next: any) =>
      next.section === "new" &&
      (next.source?.extended_from_item_id === item.id ||
        (item.task_id && next.task_id === item.task_id) ||
        (item.historical_work_id &&
          next.historical_work_id === item.historical_work_id)),
  );
}
function extend(item: any) {
  if (
    !editable.value ||
    selectedVersion.value ||
    item.section !== "review" ||
    !["未完成", "部分完成"].includes(item.status) ||
    hasExtension(item)
  )
    return;
  extension.value = {
    mode: "extend",
    itemId: item.id,
    date: "",
    error: "",
    generation: pageGeneration,
  };
}
function publishedPostponement(item: any) {
  return meeting.value?.versions
    ?.find((version: any) => version.number === meeting.value.published_version)
    ?.snapshot.items.find((row: any) => row.id === item.id)?.postponed_due_date;
}
function canCancelPostponement(item: any) {
  return (
    editable.value &&
    !selectedVersion.value &&
    item.section === "review" &&
    item.postponed_due_date &&
    item.postponed_due_date !== publishedPostponement(item)
  );
}
function cancelPostponement(item: any) {
  if (!canCancelPostponement(item)) return;
  item.postponed_due_date = publishedPostponement(item) || null;
  if (extension.value?.itemId === item.id) extension.value = null;
}
function reviewStatusChanged(item: any) {
  if (item.source) delete item.source.continuation_default_status;
  if (item.status !== "未到期")
    item.postponed_due_date = publishedPostponement(item) || null;
}
function postpone(item: any) {
  if (
    !editable.value ||
    selectedVersion.value ||
    item.section !== "review" ||
    item.status !== "未到期" ||
    hasExtension(item)
  )
    return;
  extension.value = {
    mode: "postpone",
    itemId: item.id,
    date: "",
    error: "",
    generation: pageGeneration,
  };
}
function unavailablePostponementDate(value: Date) {
  const item = extensionSource.value;
  if (extension.value?.mode !== "postpone" || !item) return false;
  const date = `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
  const minimum = publishedPostponement(item) || item.due_date;
  return (minimum && date <= minimum) || date < draft.value.meeting_date;
}
function confirmExtension() {
  if (!extension.value) return;
  const item = extensionSource.value;
  const isPostponement = extension.value.mode === "postpone";
  if (
    extension.value.generation !== pageGeneration ||
    !item ||
    item.section !== "review" ||
    !editable.value ||
    selectedVersion.value ||
    !(isPostponement
      ? item.status === "未到期"
      : ["未完成", "部分完成"].includes(item.status)) ||
    hasExtension(item)
  ) {
    extension.value = null;
    ElMessage.info("当前事项已变化，请重新选择操作");
    return;
  }
  if (!extension.value.date) {
    extension.value.error = isPostponement
      ? "请选择延期日期"
      : "请选择延续日期";
    return;
  }
  if (isPostponement) {
    const minimum = publishedPostponement(item) || item.due_date;
    if (
      !minimum ||
      extension.value.date <= minimum ||
      extension.value.date < draft.value.meeting_date
    ) {
      extension.value.error = "延期日期须晚于计划完成日期，且不能早于会议日期";
      return;
    }
    item.postponed_due_date = extension.value.date;
    extension.value = null;
    return;
  }
  const next = JSON.parse(JSON.stringify(item));
  next.id = clientId();
  next.section = "new";
  next.historical_owner_record = false;
  next.status = "未到期";
  if (item.status === "部分完成")
    next.owner_ids = item.owner_ids.filter(
      (id: string) => item.owner_results?.[id] === "未完成",
    );
  next.owner_results = {};
  delete next.owner_names;
  next.due_date = extension.value.date;
  next.postponed_due_date = null;
  next.source = { ...next.source, extended_from_item_id: item.id };
  next.change_reason = "上期未完成事项延续安排";
  draft.value.items.push(next);
  extension.value = null;
}
function beforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value || nativeDispatchBusy.value) {
    e.preventDefault();
    e.returnValue = "";
  }
}
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => {
  pageGeneration += 1;
  activeSave = null;
  cancelAutosave();
  clearInterval(minuteAutosave);
  window.removeEventListener("beforeunload", beforeUnload);
});
async function beforeNavigation() {
  if (cancellingRevision.value) return false;
  if (nativeDispatchBusy.value) {
    ElMessage.info("正在确认企微待办，请完成当前确认后再离开");
    return false;
  }
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
  <div v-loading="loading || cancellingRevision">
    <NewMeetingSetup
      v-if="newMeetingSetupOpen && draft && !loading"
      :draft="draft"
      :users="meetingUsers"
      :departments="departmentOptions"
      :series-name="archiveSeriesName"
      :series-locked="seriesKindLocked"
      @confirm="confirmNewMeetingSetup"
      @cancel="run(cancelNewMeetingSetup)"
      @kind-change="meetingKindChanged"
    />
    <section class="page-heading">
      <div>
        <h1>{{ editable ? "在线会议编辑" : "会议纪要" }}</h1>
        <div class="meeting-breadcrumb">
          <RouterLink :to="historyReturnLocation || '/meetings'">{{
            historyReturnLocation ? "历史会议" : "会议系列"
          }}</RouterLink>
          <template v-if="archiveSeriesKey && !historyReturnLocation">
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
        <span v-if="!editable" class="muted small" role="status">{{
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
          ></template
        ><template v-else
          ><el-button
            v-if="meeting?.can_edit && meeting?.has_draft"
            type="primary"
            @click="openEditor"
            >编辑会议</el-button
          ><el-button
            v-else-if="allowedMeeting('revise')"
            class="meeting-revise-action"
            :icon="Edit"
            @click="run(revise)"
            >创建修订版本</el-button
          ><el-button
            v-else-if="
              meeting &&
              !meeting.can_edit &&
              meeting.my_edit_request?.state !== 'pending' &&
              !(
                meeting.can_request_continue &&
                route.query.request === 'continue'
              )
            "
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
        ><el-button
          v-if="
            allowedMeeting('cancel_revision') &&
            meeting?.published_version &&
            meeting?.has_draft
          "
          :loading="cancellingRevision"
          :disabled="busy || nativeDispatchBusy"
          @click="run(cancelRevision)"
          >取消修订</el-button
        ><el-button
          v-if="
            !editable &&
            !selectedVersion &&
            meeting?.has_draft &&
            allowedMeeting('preview')
          "
          @click="run(openPreview)"
          >{{
            allowedMeeting("publish") ? "预览并发布" : "预览任务单"
          }}</el-button
        ><el-button
          v-if="
            meeting?.can_request_continue &&
            (meeting.can_edit || route.query.request === 'continue') &&
            meeting.my_edit_request?.state !== 'pending'
          "
          type="primary"
          @click="run(() => requestEdit(true))"
          >申请接续权限</el-button
        ><el-tag
          v-if="
            meeting?.can_request_continue &&
            meeting.can_edit &&
            meeting.my_edit_request?.state === 'pending'
          "
          type="warning"
          >接续申请待主持人审批</el-tag
        ><el-button
          v-if="meeting?.can_continue"
          class="meeting-continue-action"
          :icon="Plus"
          :loading="continuing"
          :disabled="busy || nativeDispatchBusy"
          @click="run(continueMeeting)"
          >持续新建</el-button
        ><el-button
          v-if="allowedMeeting('publish') && meeting?.published_version"
          @click="openNativeDispatch"
          >派发企微待办</el-button
        ><el-button
          v-if="allowedMeeting('move') && meeting?.published_version"
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
        <span>{{ request.applicant_name }}申请编辑及持续新建本场会议</span>
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
    <div
      v-if="editable"
      ref="saveActions"
      class="actions meeting-save-actions"
      role="region"
      aria-label="会议编辑操作"
    >
      <span class="muted small" role="status">{{
        saving ? "正在保存…" : dirty ? "有未保存内容" : meeting ? "已保存" : ""
      }}</span>
      <el-button :loading="saving" @click="save()">保存草稿</el-button>
      <el-button
        v-if="allowedMeeting('preview')"
        type="primary"
        @click="run(openPreview)"
        >{{
          allowedMeeting("publish") ? "预览并发布" : "预览任务单"
        }}</el-button
      >
    </div>
    <el-alert
      v-if="saveError"
      :title="'草稿未保存：' + saveError"
      type="error"
      :closable="false"
    />
    <div
      v-if="meeting?.published_version && allowedFile('download')"
      class="panel toolbar"
    >
      <el-button
        v-for="[fmt, label] in [
          ['xlsx', 'Excel 纪要'],
          ['pdf', 'PDF 纪要'],
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
                meeting.published_version,
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
        <div
          v-if="
            editable &&
            !selectedVersion &&
            !meeting?.published_version &&
            (!meeting?.historical || draft.history_backfill)
          "
          class="section-body historical-mode-toggle"
        >
          <el-checkbox
            v-model="draft.history_backfill"
            @change="historyBackfillChanged"
            >补录历史会议</el-checkbox
          >
          <span v-if="draft.history_backfill" class="muted small"
            >发布后自动补齐下期会议的上期任务，核对草稿后再发布。</span
          >
        </div>
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
            <div v-if="sheetReviewActive" class="sheet-meeting-note">
              <p v-if="sheetSourceLoading" role="status">正在读取导入原文…</p>
              <p
                v-else-if="sheetSourceError"
                class="result-required"
                role="alert"
              >
                导入原文读取失败：{{ sheetSourceError }}
                <el-button link type="primary" @click="loadSheetSource"
                  >重新读取</el-button
                >
              </p>
              <p v-else-if="sheetSource" role="status">
                {{ sheetSource.filename }} ·
                {{
                  sheetPending.length
                    ? `还有 ${sheetPending.length} 项待核对，请在对应任务行处理。`
                    : "导入提示已核对，可继续检查下方任务内容。"
                }}
              </p>
              <TaskSheetReview
                :draft="draft"
                :issues="sheetIssuesFor()"
                :users="meetingUsers"
              />
            </div>
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
                <el-select
                  v-model="show.kind"
                  :disabled="seriesKindLocked"
                  aria-label="会议类型"
                  @change="meetingKindChanged"
                >
                  <el-option label="部门例会" value="department" />
                  <el-option label="专项会议" value="special" />
                </el-select>
              </el-form-item>
              <el-form-item v-if="show.kind === 'department'" label="所属部门">
                <el-select
                  v-model="show.department_id"
                  filterable
                  placeholder="请选择所属部门"
                  aria-label="会议所属部门"
                >
                  <el-option
                    v-for="d in departmentOptions"
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
                  @change="meetingSeriesChanged"
                >
                  <el-option
                    v-for="s in state.directory.series"
                    :key="s.id"
                    :label="s.name"
                    :value="s.id"
                    :disabled="
                      s.can_start_meeting === false &&
                      s.id !== meeting?.series_id
                    "
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
                <PeopleSelect
                  v-model="show.host_id"
                  :users="meetingUsers"
                  aria-label="主持人"
                />
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
            <div
              v-if="editable && !selectedVersion"
              class="actions review-source-actions"
            >
              <el-button
                type="primary"
                plain
                :icon="Sort"
                :disabled="carrying || loading || review.length < 2"
                @click="reviewSortOpen = true"
                >一键排序</el-button
              >
              <el-button
                v-if="draft.history_backfill"
                @click="addHistoricalReview"
                >新增历史事项</el-button
              >
              <el-button v-else :loading="carrying" @click="run(carry)"
                >带入上期任务</el-button
              >
            </div>
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
            只有此分区纳入本次达成率。多人共同任务，每位责任人各计一次。主持人、编制人或获授权人员将完成结果设为“已完成”后，发布时即确认任务完成。
          </p>
          <p
            v-if="!readOnlyView"
            id="review-order-help"
            class="section-note task-order-hint"
          >
            按住序号旁的拖动图标可调整上期任务顺序。
            <span class="task-order-notice" role="status">{{
              reviewOrderNotice
            }}</span>
          </p>
          <p
            v-if="editable && !selectedVersion && carryMessage"
            class="section-note review-source-message"
            :class="{ 'is-error': carryError }"
            role="status"
          >
            {{ carryMessage }}
          </p>
          <div class="table-scroll">
            <table
              class="minutes-table meeting-review-table"
              data-mobile-editor="true"
            >
              <thead>
                <tr>
                  <th>#</th>
                  <th>项目 / 工作内容及要求</th>
                  <th>责任人</th>
                  <th>原计划完成时间</th>
                  <th>
                    完成结果<span
                      v-if="editable && !selectedVersion"
                      class="result-required"
                      aria-hidden="true"
                    >
                      *</span
                    >
                  </th>
                  <th>备注</th>
                  <th v-if="editable">操作</th>
                </tr>
              </thead>
              <tbody ref="reviewBody">
                <tr
                  v-for="(item, i) in visibleReview"
                  :key="item.id"
                  :id="`work-row-${item.id}`"
                  :data-review-id="item.id"
                >
                  <td data-label="序号">
                    <div class="task-order-index">
                      <button
                        v-if="!readOnlyView"
                        type="button"
                        class="task-drag-handle"
                        :disabled="loading || carrying"
                        :aria-label="`拖动第${Number(i) + 1}项上期任务调整顺序`"
                        aria-describedby="review-order-help"
                        title="按住拖动调整顺序，也可使用键盘上下方向键"
                        @keydown="orderReviewByKeyboard($event, item.id)"
                      >
                        <svg
                          class="task-grip"
                          viewBox="0 0 16 24"
                          aria-hidden="true"
                        >
                          <circle
                            v-for="point in 6"
                            :key="point"
                            :cx="point % 2 ? 5 : 11"
                            :cy="Math.ceil(point / 2) * 6"
                            r="1.5"
                            fill="currentColor"
                          />
                        </svg>
                      </button>
                      <span>{{ Number(i) + 1 }}</span>
                    </div>
                  </td>
                  <td data-label="工作内容及要求" class="content-cell">
                    <el-input
                      v-if="
                        editable &&
                        !selectedVersion &&
                        (item.source?.sheet_import_id ||
                          draft.history_backfill) &&
                        !item.task_id
                      "
                      v-model="item.content"
                      type="textarea"
                      autosize
                      aria-label="上期工作内容"
                    />
                    <p v-else>{{ mergedWorkContent(item) }}</p>
                    <HistoricalTaskLink
                      v-if="show.history_backfill"
                      :item="item"
                      :draft="show"
                      :readonly="!editable || !!selectedVersion"
                      @change="item.backfill_reference = $event"
                    />
                    <TaskSheetReview
                      v-if="sheetReviewActive"
                      :draft="draft"
                      :item="item"
                      :issues="sheetIssuesFor(item)"
                      :users="meetingUsers"
                    />
                    <p
                      v-if="!readOnlyView && inactiveOwnerNames(item).length"
                      class="muted small"
                    >
                      保留已停用原责任人的上期记录；延续到本次安排时请交接给在用员工。
                    </p>
                    <div
                      v-if="!readOnlyView && inactiveVerifier(item)"
                      class="review-person-warning"
                      role="alert"
                    >
                      <p>
                        验证人
                        {{ inactiveVerifier(item)?.name }}
                        已停用，请更换后发布。
                      </p>
                      <PeopleSelect
                        v-model="item.verifier_id"
                        :users="meetingUsers"
                        search-only
                        copyable
                        aria-label="替换已停用验证人"
                        placeholder="选择新的验证人"
                      />
                    </div>
                  </td>
                  <td data-label="责任人">
                    <PeopleSelect
                      v-if="
                        editable &&
                        !selectedVersion &&
                        (item.source?.sheet_import_id ||
                          draft.history_backfill) &&
                        !item.task_id
                      "
                      v-model="item.owner_ids"
                      :users="meetingUsers"
                      multiple
                      search-only
                      copyable
                      aria-label="上期责任人"
                      include-inactive
                    />
                    <span v-else>{{
                      (item.owner_names || item.owner_ids.map(person)).join(
                        "、",
                      )
                    }}</span>
                  </td>
                  <td data-label="计划完成时间" class="review-due-date">
                    <div
                      v-for="(date, index) in item.source?.date_history || []"
                      :key="index"
                      class="original-due-date"
                    >
                      {{ date }}
                    </div>
                    <el-date-picker
                      v-if="
                        editable &&
                        !selectedVersion &&
                        (item.source?.sheet_import_id ||
                          draft.history_backfill) &&
                        !item.task_id
                      "
                      v-model="item.due_date"
                      type="date"
                      value-format="YYYY-MM-DD"
                      aria-label="上期计划完成时间"
                    />
                    <div
                      v-else
                      :class="{ 'original-due-date': item.postponed_due_date }"
                    >
                      {{ item.due_date || "—" }}
                    </div>
                    <div v-if="item.postponed_due_date" class="postponed-date">
                      <span
                        :aria-label="`延期后日期：${item.postponed_due_date}`"
                        >{{ item.postponed_due_date }}</span
                      >
                    </div>
                  </td>
                  <td
                    data-label="完成结果"
                    :id="`review-result-${item.id}`"
                    class="review-result"
                    :class="{
                      'is-missing': reviewResultsRequired && !item.status,
                    }"
                    :data-result="item.status"
                  >
                    <el-select
                      v-if="editable && !selectedVersion"
                      :model-value="item.status"
                      :disabled="!allowedMeeting('assess')"
                      placeholder="请选择"
                      :aria-label="`第${Number(i) + 1}项完成结果（必填）`"
                      @update:model-value="setReviewStatus(item, $event)"
                    >
                      <el-option
                        v-for="s in item.owner_ids.length > 1
                          ? ['已完成', '部分完成', ...statuses.slice(1)]
                          : statuses"
                        :key="s"
                        :label="
                          s === '部分完成' && item.status === '部分完成'
                            ? partialResultLabel(item)
                            : s
                        "
                        :value="s"
                      />
                    </el-select>
                    <StatusTag
                      v-else
                      :value="
                        item.status === '部分完成'
                          ? partialResultLabel(item)
                          : item.status
                      "
                    />
                    <el-button
                      v-if="item.status === '部分完成'"
                      link
                      type="primary"
                      class="owner-result-details"
                      @click="openOwnerAssessment(item)"
                      >{{
                        editable && !selectedVersion && allowedMeeting("assess")
                          ? "查看／修改"
                          : "查看结果"
                      }}</el-button
                    >
                    <span
                      v-if="
                        editable &&
                        !selectedVersion &&
                        reviewResultsRequired &&
                        !item.status
                      "
                      :id="`review-result-error-${item.id}`"
                      class="result-required result-error"
                      role="alert"
                      >请选择完成结果</span
                    >
                  </td>
                  <td data-label="备注">
                    <el-input
                      v-if="editable && !selectedVersion"
                      v-model="item.notes"
                      type="textarea"
                      autosize
                    />
                    <span v-else>{{ item.notes }}</span>
                  </td>
                  <td data-label="操作" v-if="editable">
                    <div class="review-task-actions">
                      <el-button
                        v-if="draft.history_backfill && !selectedVersion"
                        link
                        type="danger"
                        aria-label="删除历史事项"
                        @click="
                          draft.items = draft.items.filter(
                            (x) => x.id !== item.id,
                          )
                        "
                        >删除</el-button
                      >
                      <el-tooltip
                        v-if="
                          item.status === '未到期' &&
                          !selectedVersion &&
                          !draft.history_backfill
                        "
                        :disabled="!hasExtension(item)"
                        :trigger="['hover', 'focus']"
                        content="已在本次任务单中安排，请在下方该任务修改日期"
                        placement="top"
                      >
                        <span
                          class="postponement-trigger"
                          :tabindex="hasExtension(item) ? 0 : undefined"
                          :aria-label="
                            hasExtension(item)
                              ? '已在本次任务单中安排，请在下方该任务修改日期'
                              : undefined
                          "
                        >
                          <el-button
                            link
                            type="primary"
                            :disabled="hasExtension(item)"
                            @click="postpone(item)"
                            >延期</el-button
                          >
                        </span>
                      </el-tooltip>
                      <el-button
                        v-if="canCancelPostponement(item)"
                        link
                        type="primary"
                        @click="cancelPostponement(item)"
                        >取消延期</el-button
                      >
                      <el-button
                        v-if="
                          ['未完成', '部分完成'].includes(item.status) &&
                          !draft.history_backfill
                        "
                        link
                        type="primary"
                        :disabled="hasExtension(item)"
                        @click="extend(item)"
                        >{{
                          hasExtension(item) ? "已延续" : "延续安排"
                        }}</el-button
                      >
                    </div>
                  </td>
                </tr>
                <tr v-if="!visibleReview.length">
                  <td colspan="7" class="empty-cell">
                    {{
                      reviewSearch
                        ? "没有匹配的上期任务"
                        : draft.series_id
                          ? "暂无上期任务，可带入本系列需要继续跟进的任务。"
                          : "请先选择会议系列，再带入上期任务。"
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

        <section
          class="panel document-section meeting-data-card meeting-deployment"
          :style="{ '--work-heading-top': addWorkOffset + 'px' }"
        >
          <header
            class="section-card-heading meeting-deployment-heading"
            :class="{ 'is-editing': !readOnlyView }"
          >
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Tickets /></el-icon
              ></span>
              <h2>本次会议任务部署</h2>
            </div>
            <el-input
              v-if="readOnlyView"
              v-model="arrangementSearch"
              class="meeting-table-search"
              clearable
              placeholder="搜索工作内容、责任人…"
              aria-label="搜索本次工作"
            />
            <el-button v-else type="primary" @click="add"
              >＋ 新增工作</el-button
            >
          </header>
          <p class="section-note">
            本次安排不计入本次达成率。新任务必须填写计划完成时间，草稿可先保存。验证人未填时默认为
            {{ person(show.host_id) }}。
          </p>
          <p
            v-if="!readOnlyView"
            id="task-order-help"
            class="section-note task-order-hint"
          >
            按住序号旁的拖动图标可调整同组任务顺序。
            <span class="task-order-notice" role="status">{{
              taskOrderNotice
            }}</span>
          </p>
          <div class="table-scroll meeting-work-scroll">
            <table
              class="minutes-table meeting-plan-table"
              data-mobile-editor="true"
            >
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
                  <th class="work-actions-heading">操作</th>
                </tr>
              </thead>
              <tbody ref="arrangementBody">
                <template
                  v-for="(item, i) in visibleArrangements"
                  :key="item.id"
                >
                  <tr
                    v-if="arrangementGroupHeading(item, Number(i))"
                    class="arrangement-group-heading"
                  >
                    <td colspan="8">
                      <h3>{{ arrangementGroupHeading(item, Number(i)) }}</h3>
                    </td>
                  </tr>
                  <tr
                    :id="`work-row-${item.id}`"
                    :data-arrangement-id="item.id"
                  >
                    <td data-label="序号">
                      <div class="task-order-index">
                        <button
                          v-if="editable && !selectedVersion"
                          type="button"
                          class="task-drag-handle"
                          :aria-label="`拖动第${Number(i) + 1}项任务调整顺序`"
                          aria-describedby="task-order-help"
                          title="按住拖动调整顺序，也可使用键盘上下方向键"
                          @keydown="orderByKeyboard($event, item.id)"
                        >
                          <svg
                            class="task-grip"
                            viewBox="0 0 16 24"
                            aria-hidden="true"
                          >
                            <circle
                              v-for="point in 6"
                              :key="point"
                              :cx="point % 2 ? 5 : 11"
                              :cy="Math.ceil(point / 2) * 6"
                              r="1.5"
                              fill="currentColor"
                            />
                          </svg>
                        </button>
                        <span>{{ Number(i) + 1 }}</span>
                      </div>
                    </td>
                    <td data-label="工作内容及要求" class="content-cell">
                      <el-input
                        v-if="editable && !selectedVersion"
                        :model-value="mergedWorkContent(item)"
                        @update:model-value="updateWorkContent(item, $event)"
                        type="textarea"
                        autosize
                        aria-label="工作内容及要求"
                      />
                      <p v-else>{{ mergedWorkContent(item) }}</p>
                      <HistoricalTaskLink
                        v-if="show.history_backfill"
                        :item="item"
                        :draft="show"
                        :readonly="!editable || !!selectedVersion"
                        @change="item.backfill_reference = $event"
                      />
                      <TaskSheetReview
                        v-if="sheetReviewActive"
                        :draft="draft"
                        :item="item"
                        :issues="sheetIssuesFor(item)"
                        :users="meetingUsers"
                      />
                    </td>
                    <td data-label="责任人">
                      <PeopleSelect
                        v-if="editable && !selectedVersion"
                        :model-value="item.owner_ids"
                        @update:model-value="
                          updateArrangementOwners(item, $event)
                        "
                        multiple
                        search-only
                        copyable
                        include-inactive
                        :users="meetingUsers"
                        aria-label="责任人"
                        placeholder="输入或粘贴姓名，多个用顿号分隔"
                      />
                      <p
                        v-if="
                          editable &&
                          !selectedVersion &&
                          inactiveOwnerNames(item).length
                        "
                        class="muted small"
                        role="status"
                      >
                        {{
                          canRecordHistoricalOwners(item)
                            ? "按历史任务补录，可保留原责任人发布；下期延续时请更换执行人。"
                            : "含已停用人员，发布延续安排前请更换责任人。"
                        }}
                      </p>
                      <span v-if="!editable || selectedVersion">{{
                        (item.owner_names || item.owner_ids.map(person)).join(
                          "、",
                        )
                      }}</span>
                    </td>
                    <td data-label="计划完成时间">
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
                      <div
                        v-for="(date, index) in item.source?.date_history || []"
                        :key="index"
                        class="original-due-date"
                      >
                        {{ date }}
                      </div>
                      <span v-if="!editable || selectedVersion">{{
                        item.due_date || "—"
                      }}</span>
                    </td>
                    <td data-label="验证人">
                      <PeopleSelect
                        v-if="editable && !selectedVersion"
                        v-model="item.verifier_id"
                        search-only
                        copyable
                        :users="meetingUsers"
                        aria-label="验证人"
                        :placeholder="'默认：' + person(show.host_id)"
                      />
                      <span v-else>{{
                        item.verifier_name ||
                        person(item.verifier_id || show.host_id)
                      }}</span>
                    </td>
                    <td data-label="输出物">
                      <el-input
                        v-if="editable && !selectedVersion"
                        v-model="item.deliverable"
                        type="textarea"
                        autosize
                        aria-label="输出物"
                      />
                      <span v-else>{{ item.deliverable || "—" }}</span>
                    </td>
                    <td data-label="备注">
                      <el-input
                        v-if="editable && !selectedVersion"
                        v-model="item.notes"
                        type="textarea"
                        autosize
                        aria-label="备注"
                      />
                      <span v-else>{{ item.notes || "—" }}</span>
                    </td>
                    <td data-label="操作" class="work-actions-cell">
                      <el-button
                        v-if="editable && !selectedVersion"
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
                </template>
                <tr v-if="!visibleArrangements.length">
                  <td colspan="8" class="empty-cell">
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
              <label
                v-if="editable && allowedFile('upload')"
                class="upload-button"
                >上传会议附件<input
                  type="file"
                  @change="(e) => run(() => upload(e))"
              /></label>
              <el-button
                v-for="a in visibleAttachments"
                :key="a.id"
                link
                type="primary"
                :disabled="!allowedFile('download')"
                @click="
                  run(() => download('/files/' + a.id, attachmentName(a)))
                "
                >{{ attachmentName(a) }}</el-button
              >
            </div>
          </div>
        </section>
      </el-form>
    </article>
    <section
      v-if="meeting?.backfill_history?.length"
      class="panel section-body"
    >
      <h2>补录历史会议</h2>
      <p v-for="history in meeting.backfill_history" :key="history.id">
        <router-link :to="`/meetings/${history.id}`"
          >{{ history.meeting_date }} · {{ history.title }}</router-link
        >
        <span class="muted small"> · 关联 {{ history.task_count }} 项</span>
      </p>
    </section>
    <section v-if="meeting?.published_version" class="panel section-body">
      <h2>考核与任务追溯</h2>
      <p v-if="assessment" class="muted">
        {{
          assessment.effective
            ? "当前有效考核"
            : "历史考核，仅供追溯，不计当前报表"
        }}；回顾 {{ assessment.items }} 项、{{
          assessment.person_count
        }}
        人次，达成率 {{ rate(assessment.rate) }}。本次新安排不计本次达成率。
      </p>
      <el-collapse>
        <el-collapse-item title="本会议考核明细" name="assessments">
          <el-table
            :data="assessment?.details || []"
            empty-text="本次会议没有上期回顾考核"
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
            ><el-table-column label="原因" min-width="220">
              <template #default="{ row }">{{
                row.last_error
                  ? chineseError(row.last_error, "处理失败，请查看操作日志")
                  : "—"
              }}</template>
            </el-table-column>
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
        <el-table-column label="收件人／抄送人" min-width="200"
          ><template #default="{ row }"
            >{{ row.recipient_ids.map(person).join("、") || "—" }}
            <p class="muted small">
              抄送：{{
                [...row.cc_ids.map(person), ...(row.cc_emails || [])].join(
                  "、",
                ) || "—"
              }}
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
        <el-table-column label="失败原因" min-width="220">
          <template #default="{ row }">{{
            row.last_error
              ? chineseError(row.last_error, "处理失败，请查看操作日志")
              : "—"
          }}</template>
        </el-table-column>
      </el-table>
    </section>
    <section
      v-if="
        meeting?.historical &&
        meeting?.can_edit &&
        meeting?.published_version &&
        meeting.versions[0]?.snapshot.items.some(
          (x: any) =>
            !x.backfill_reference &&
            !['已完成', '关闭', '不统计'].includes(x.status),
        )
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
      v-model="reviewSortOpen"
      title="一键排序"
      width="min(440px, 94vw)"
    >
      <el-form label-position="top" @submit.prevent="sortReview">
        <el-form-item label="是否按责任人分类？">
          <el-radio-group v-model="reviewSortMode" class="review-sort-options">
            <el-radio value="owners">按责任人分类</el-radio>
            <el-radio value="result">按完成结果排序</el-radio>
          </el-radio-group>
        </el-form-item>
        <p v-if="reviewSortMode === 'owners'" class="muted small">
          已集中排列的历史事项保留原顺序；分散事项按责任人归组，共同负责的任务只展示一次。
        </p>
        <p v-else class="muted small">
          已完成 → 部分完成 → 未完成 → 未到期 → 不统计 →
          关闭，未评定事项在最后。
        </p>
      </el-form>
      <template #footer>
        <el-button @click="reviewSortOpen = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!editable || !!selectedVersion || loading || carrying"
          @click="sortReview"
          >确认排序</el-button
        >
      </template>
    </el-dialog>
    <OwnerResultsDialog
      :item="ownerAssessmentItem"
      :editable="editable && !selectedVersion && allowedMeeting('assess')"
      :users="meetingUsers"
      @cancel="ownerAssessment = null"
      @confirm="confirmOwnerAssessment"
    />
    <el-dialog
      :model-value="!!extension"
      :title="extension?.mode === 'postpone' ? '任务延期' : '延续安排'"
      width="min(460px, 94vw)"
      @close="extension = null"
    >
      <template v-if="extension">
        <p>{{ mergedWorkContent(extensionSource || {}) }}</p>
        <p class="muted small">
          原计划完成日期：{{ extensionSource?.due_date || "未设置" }}
        </p>
        <el-form label-position="top">
          <el-form-item
            :label="extension.mode === 'postpone' ? '延期日期' : '延续日期'"
            required
            :error="extension.error"
          >
            <el-date-picker
              v-model="extension.date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="请选择新的计划完成日期"
              :clearable="false"
              :aria-label="
                extension.mode === 'postpone' ? '延期日期' : '延续日期'
              "
              :disabled-date="unavailablePostponementDate"
              aria-required="true"
              style="width: 100%"
              @change="extension.error = ''"
            />
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="extension = null">取消</el-button>
        <el-button type="primary" @click="confirmExtension">{{
          extension?.mode === "postpone" ? "确认延期" : "确认延续"
        }}</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="participantDialogVisible"
      title="新增或调整参会人员"
      width="min(560px, 94vw)"
    >
      <el-form label-position="top">
        <p class="participant-dialog-total">
          参会人员（共 {{ participantDialogCount }} 人）
        </p>
        <el-form-item label="现场参会人员">
          <div
            v-if="
              draft.host_id &&
              !onlineParticipantDraftIds.includes(draft.host_id)
            "
            class="participant-dialog-host"
          >
            <el-tag type="info">{{ person(draft.host_id) }} · 主持人</el-tag>
            <span class="muted small">自动计入参会人数</span>
          </div>
          <PeopleSelect
            :model-value="participantDraftIds"
            @update:model-value="setOnsiteParticipants"
            :users="meetingUsers"
            multiple
            :collapse-tags="false"
            reorderable
            aria-label="现场参会人员"
            popper-class="meeting-participants-popper"
            placeholder="输入完整或部分姓名，搜索全公司在职人员"
          />
          <p class="muted small">
            拖动姓名前的图标调整顺序；在另一组选择姓名可切换参会方式。
          </p>
        </el-form-item>
        <el-form-item label="线上参会人员">
          <PeopleSelect
            :model-value="onlineParticipantDraftIds"
            @update:model-value="setOnlineParticipants"
            :users="meetingUsers"
            multiple
            reorderable
            :collapse-tags="false"
            aria-label="线上参会人员"
            placeholder="输入姓名，搜索全公司在职人员，可多选"
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
                (x) =>
                  !x.backfill_reference &&
                  !['已完成', '关闭', '不统计'].includes(x.status),
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
          ><PeopleSelect
            v-model="activation.verifier_id"
            search-only
            copyable
            :users="meetingUsers"
            aria-label="验证人" /></el-form-item
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
        确认后发布最新会议纪要。本次工作将下发给所选责任人，历史考核单独保存。
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
          ><el-button v-if="allowedFile('download')" @click="run(previewTasks)"
            >下载草稿任务单（Excel）</el-button
          >
        </div>
        <PdfPreview
          v-if="preview && previewUrl"
          :src="previewUrl"
          :filename="draft.title + '.pdf'"
          :allow-copy="allowedFile('copy')"
          :allow-download="allowedFile('download')"
        />
        <el-alert
          v-else-if="previewError"
          :title="previewError"
          type="error"
          :closable="false"
          show-icon
        >
          <el-button link type="primary" @click="locatePreviewIssue"
            >返回修改</el-button
          >
          <el-button link type="primary" @click="run(refreshPreview)"
            >重试预览</el-button
          >
        </el-alert>
        <p v-else class="muted">
          补全会议信息和工作行后可生成预览；预览不会创建任务或发送通知。
        </p>
      </section>
      <el-form label-position="top" class="section-body">
        <el-form-item label="发布通知">
          <el-radio-group
            v-model="notify"
            :disabled="meeting?.historical || busy"
            aria-label="发布通知"
          >
            <el-radio :value="true">发送通知</el-radio>
            <el-radio :value="false">不通知</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <p v-if="notify && !editable" class="muted section-body">
        通知按已保存的名单发送；更改名单需要会议编辑权限。
      </p>
      <el-form
        v-if="notify"
        label-position="top"
        class="section-body"
        :disabled="!editable || busy"
        ><el-form-item label="通知接收人"
          ><PeopleSelect
            :model-value="draft.recipient_ids"
            @update:model-value="updateNotificationRecipients"
            :users="meetingUsers"
            multiple
            :allowed-ids="notificationAllowedIds"
            reorderable
            :collapse-tags="false"
            :disabled="!editable || busy"
            aria-label="通知接收人"
          /><el-button link type="primary" @click="useSuggestedRecipients"
            >恢复参会名单（{{ recipientCandidates.length }} 人）</el-button
          >
          <p class="muted small">
            默认与参会人员一致；调整顺序会同步参会名单，可额外添加接收人。
          </p></el-form-item
        ><el-form-item label="邮件收件人"
          ><div
            class="mail-recipient-list"
            role="group"
            aria-label="邮件收件人"
          >
            <el-tag
              v-for="user in mailRecipients"
              :key="user.id"
              type="info"
              class="mail-recipient-tag"
            >
              {{ user.name }} · {{ user.email || "未配置邮箱" }}
            </el-tag>
            <span v-if="!mailRecipients.length" class="muted"
              >尚未选择通知接收人</span
            >
          </div></el-form-item
        ><el-form-item label="额外邮件抄送人（可选）"
          ><MailCcSelect
            v-model:user-ids="draft.cc_ids"
            v-model:emails="draft.cc_emails"
            :users="mailCcUsers"
            @open="run(refreshMeetingPeople)"
            :allowed-ids="
              mailCcUsers
                .filter(
                  (user) =>
                    user.active && !draft.recipient_ids.includes(user.id),
                )
                .map((user) => user.id)
            "
          />
          <p class="muted small">仅额外抄送邮件，不重复加入上方收件人。</p>
        </el-form-item>
        <p class="muted small">
          本次通知按上方名单发送，邮件附一份 Excel 任务单和一张完整纪要图片。
        </p>
        <el-alert
          v-if="notificationIssues.length"
          type="warning"
          :closable="false"
          show-icon
        >
          <p v-for="issue in notificationIssues" :key="issue">{{ issue }}</p>
        </el-alert> </el-form
      ><template #footer
        ><el-button @click="preview = false">返回编辑</el-button
        ><el-button
          v-if="allowedMeeting('publish')"
          type="primary"
          :loading="busy"
          :disabled="previewLoading || !previewUrl"
          @click="
            run(async () => {
              if (!editable || (await save(false))) await publish();
            })
          "
          >确认发布</el-button
        ></template
      ></el-dialog
    >
    <el-dialog
      v-model="nativeDialog"
      title="派发本次会议的企微待办"
      width="min(900px,94vw)"
      top="5vh"
      :close-on-click-modal="false"
      :close-on-press-escape="!nativeDispatchBusy"
      :show-close="!nativeDispatchBusy"
    >
      <MeetingNativeTodos
        v-if="nativeDialog && meeting"
        :key="`${meeting.id}:${meeting.published_version}`"
        :meeting-id="meeting.id"
        :published-version="meeting.published_version"
        @busy-change="(value) => (nativeDispatchBusy = value)"
      />
      <template #footer
        ><el-button :disabled="nativeDispatchBusy" @click="nativeDialog = false"
          >完成，返回会议</el-button
        ></template
      >
    </el-dialog>
  </div>
</template>

<style scoped>
.mail-recipient-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  width: 100%;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
}
.mail-recipient-tag {
  height: auto;
  max-width: 100%;
  padding-block: 3px;
  white-space: normal;
  overflow-wrap: anywhere;
  user-select: text;
}
.historical-mode-toggle {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  column-gap: 20px;
  row-gap: 4px;
}
.sheet-meeting-note {
  padding: 0 24px 16px;
  font-size: 12px;
  color: #526378;
}
.sheet-meeting-note p {
  margin: 0;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.review-task-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.review-task-actions :deep(.el-button) {
  margin-left: 0;
}
.postponement-trigger {
  display: inline-flex;
}
.postponement-trigger:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 3px;
}
.review-due-date {
  font-variant-numeric: tabular-nums;
}
.original-due-date {
  text-decoration: line-through;
}
.postponed-date {
  margin-top: 4px;
  color: #24588f;
  font-size: 14px;
  font-weight: 400;
}
.result-required {
  color: var(--el-color-danger);
}
.result-error {
  display: block;
  margin-top: 5px;
  font-size: 12px;
  line-height: 1.4;
}
.review-result.is-missing :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}
.review-result {
  --review-foreground: var(--ink);
  --review-background: transparent;
}
.review-result[data-result="已完成"] {
  --review-foreground: #16664b;
  --review-background: #edf9f2;
}
.review-result[data-result="未完成"] {
  --review-foreground: #a52b32;
  --review-background: #fff1f1;
}
.review-result[data-result="部分完成"] {
  --review-foreground: #a16207;
  --review-background: #fffbeb;
}
.owner-result-details {
  display: flex;
  margin-top: 6px;
}
.review-result[data-result="未到期"] {
  --review-foreground: #205eac;
  --review-background: #edf5ff;
}
.review-result[data-result="不统计"],
.review-result[data-result="关闭"],
.review-result[data-result="已关闭"] {
  --review-foreground: #556171;
  --review-background: #f1f3f6;
}
.review-result :deep(.el-select__wrapper),
.review-result :deep(.status-tag) {
  color: var(--review-foreground);
  background-color: var(--review-background);
}
.review-result :deep(.el-select__selected-item) {
  color: var(--review-foreground);
}
.review-result :deep(.status-tag) {
  border-color: currentColor;
}
.meeting-save-actions {
  position: sticky;
  top: var(--meeting-header-height, 68px);
  z-index: 14;
  justify-content: flex-end;
  gap: 10px;
  margin-bottom: 15px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  background: #f3f8ff;
}
.meeting-save-actions > [role="status"] {
  margin-right: auto;
}
.meeting-save-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.meeting-add-work-affix {
  flex-shrink: 0;
}
.meeting-deployment-heading.is-editing {
  position: sticky;
  top: var(--work-heading-top, 125px);
  z-index: 13;
  min-height: 52px;
  background: #fff;
  border-bottom: 1px solid var(--line);
}
.meeting-deployment {
  overflow: visible;
  container-type: inline-size;
}
.meeting-plan-table .arrangement-group-heading {
  display: table-row;
  background: var(--surface-muted, #f4f4f5);
}
.meeting-plan-table .arrangement-group-heading td {
  padding: 12px 8px;
}
.arrangement-group-heading h3 {
  margin: 0;
  color: var(--text, #27272a);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}
.meeting-readonly .work-actions-heading,
.meeting-readonly .work-actions-cell {
  display: none;
}
@media (min-width: 768px) {
  .meeting-work-scroll {
    overflow: visible;
  }
  .meeting-plan-table,
  .meeting-plan-table thead,
  .meeting-plan-table tbody {
    display: block;
    width: 100%;
  }
  .meeting-plan-table {
    min-width: 1040px;
  }
  .meeting-plan-table tr {
    display: grid;
    grid-template-columns:
      54px minmax(220px, 2.6fr) minmax(120px, 1.35fr)
      144px minmax(108px, 1.2fr) minmax(100px, 1fr) minmax(140px, 1.5fr) 54px;
    align-items: start;
    border-bottom: 1px solid var(--line);
  }
  .meeting-readonly .meeting-plan-table {
    min-width: 986px;
  }
  .meeting-readonly .meeting-plan-table tr {
    grid-template-columns:
      54px minmax(220px, 2.6fr) minmax(120px, 1.35fr)
      144px minmax(108px, 1.2fr) minmax(100px, 1fr) minmax(140px, 1.5fr);
  }
  .meeting-plan-table th,
  .meeting-plan-table td {
    min-width: 0;
    width: auto;
    padding: 10px 8px;
    border: 0;
  }
  .meeting-plan-table th:nth-child(n) {
    min-width: 0;
    width: auto;
  }
  .meeting-plan-table thead {
    position: sticky;
    top: calc(var(--work-heading-top, 125px) + 52px);
    z-index: 12;
    background: #f3f7fe;
  }
  .meeting-readonly .meeting-plan-table thead {
    top: 56px;
  }
  .meeting-plan-table .task-order-index {
    display: flex;
    gap: 4px;
    align-items: center;
  }
  .meeting-plan-table .task-drag-handle {
    width: 24px;
    height: 32px;
    padding: 0;
  }
  .meeting-plan-table .content-cell {
    min-width: 0;
    max-width: none;
  }
  .meeting-plan-table :deep(.el-select) {
    min-width: 0;
  }
  .meeting-plan-table .empty-cell {
    grid-column: 1 / -1;
  }
  .meeting-plan-table .arrangement-group-heading {
    display: block;
  }
  .meeting-plan-table .arrangement-group-heading td {
    display: block;
  }
  @container (max-width: 1040px) {
    .meeting-work-scroll {
      overflow-x: auto;
    }
  }
}
@media (max-width: 767px) {
  .meeting-save-actions {
    padding: 10px 12px;
  }
  .meeting-save-actions > [role="status"] {
    flex-basis: 100%;
  }
  .meeting-save-actions :deep(.el-button) {
    padding-inline: 12px;
  }
}
.meeting-native-publication {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}
.participant-dialog-total {
  margin: 0 0 18px;
  color: var(--el-text-color-primary);
}
.participant-dialog-host {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-bottom: 8px;
}
.meeting-native-publication p {
  margin: 8px 0 0;
}
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
.review-person-warning {
  margin-top: 10px;
  color: var(--el-color-danger);
}
@media (max-width: 767px) {
  .minutes-table[data-mobile-editor] {
    display: block;
    min-width: 0;
    width: 100%;
    table-layout: auto;
  }
  .minutes-table[data-mobile-editor] thead {
    display: none;
  }
  .minutes-table[data-mobile-editor] tbody {
    display: block;
    width: 100%;
  }
  .minutes-table[data-mobile-editor] tbody > tr {
    display: block;
    padding: 16px 0;
    border-bottom: 1px solid var(--line);
  }
  .minutes-table[data-mobile-editor] tbody > tr > td {
    display: block;
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
    border: 0;
    padding: 8px 12px;
  }
  .minutes-table[data-mobile-editor] td[data-label]::before {
    content: attr(data-label);
    display: block;
    margin-bottom: 6px;
    font-weight: 500;
    color: var(--el-text-color-regular);
  }
  .minutes-table[data-mobile-editor] td[data-label="序号"] {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .minutes-table[data-mobile-editor] td[data-label="序号"]::before {
    margin: 0;
  }
  .minutes-table[data-mobile-editor] :deep(.el-input),
  .minutes-table[data-mobile-editor] :deep(.el-select),
  .minutes-table[data-mobile-editor] :deep(.el-date-editor) {
    width: 100%;
    max-width: 100%;
  }
  .minutes-table[data-mobile-editor] :deep(input),
  .minutes-table[data-mobile-editor] :deep(textarea) {
    font-size: 16px;
  }
  .minutes-table[data-mobile-editor] .content-cell {
    max-width: none;
  }
  .meeting-readonly .meeting-plan-table .work-actions-cell {
    display: none;
  }
}
</style>
