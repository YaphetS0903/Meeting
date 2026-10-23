<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  api,
  state,
  run,
  statuses,
  query,
  download,
  importIssueMessage,
} from "../api";
import HistoricalMeetingReview from "../components/HistoricalMeetingReview.vue";
const route = useRoute();
const router = useRouter();
const linkItem = ref<any>(null),
  linkSuggestions = ref<any[]>([]),
  linkReason = ref(""),
  confirmProblems = ref<string[]>([]);
async function suggestLinks(item: any) {
  if (!(await save())) return;
  linkSuggestions.value = await api(
    `/validation/${selected.value.id}/items/${item.id}/suggestions`,
  );
  linkItem.value = item;
  linkReason.value = "";
}
function selectLink(row: any) {
  linkItem.value.historical_reference = {
    meeting_id: row.meeting_id,
    version: row.version,
    item_id: row.item_id,
    reason: linkReason.value,
  };
  linkItem.value.historical_link_label = `${row.meeting_date} ${row.title} · ${row.content}`;
  linkItem.value = null;
  ElMessage.success("已选择关联，请保存更正后确认入库");
}
async function rematch() {
  const r = await api("/admin/import-rematch", "POST");
  ElMessage.success(
    "已更新 " + r.updated_candidates + " 条尚未人工更正的会议记录",
  );
  await load();
}
const rows = ref<any[]>([]),
  total = ref(0),
  summary = ref<any>({ item_count: 0, section_counts: {}, issue_counts: {} }),
  page = ref(1),
  busy = ref(false),
  loadError = ref(""),
  sourceError = ref(""),
  filter = ref("actionable"),
  search = ref(""),
  meetingDates = ref<[string, string] | null>(null),
  scopeFilter = ref("all"),
  stateCounts = ref<Record<string, number>>({}),
  selected = ref<any>(null),
  payload = ref<any>(null),
  reason = ref(""),
  saving = ref(false),
  batches = ref<any[]>([]),
  batch = ref(""),
  sources = ref<any[]>([]),
  sourceKeyword = ref(""),
  showSources = ref(false),
  showUpload = ref(false),
  uploading = ref(false),
  issueChecks = ref<string[]>([]),
  aiConfigured = ref(false),
  aiModel = ref(""),
  aiBusy = ref(false),
  aiApplied = ref(0),
  aiFailed = ref(0),
  aiSuggestions = ref<Record<string, any>>({});
const filteredSources = computed(() => {
  const value = sourceKeyword.value.trim().toLowerCase();
  return value
    ? sources.value.filter((source) =>
        String(source.filename || "")
          .toLowerCase()
          .includes(value),
      )
    : sources.value;
});
const recentSources = computed(() => sources.value.slice(0, 5));
const pendingCount = computed(
  () =>
    (stateCounts.value.pending || 0) + (stateCounts.value.out_of_scope || 0),
);
const refreshTimers: number[] = [];
let configLoaded = false;
let loadGeneration = 0;
let reviewGeneration = 0;
let aiGeneration = 0;
const labels = {
  pending: "待核对",
  confirmed: "已归档",
  duplicate: "重复副本",
  out_of_scope: "范围外",
  ignored: "已排除",
};
async function load() {
  const generation = ++loadGeneration;
  busy.value = true;
  loadError.value = "";
  try {
    const r = await api(
      "/validation?" +
        query({
          state: filter.value,
          batch_id: batch.value,
          q: search.value,
          start: meetingDates.value?.[0],
          end: meetingDates.value?.[1],
          scope: scopeFilter.value,
          offset: (page.value - 1) * 20,
          limit: 20,
        }),
    );
    const nextBatches = await api("/imports");
    if (generation !== loadGeneration) return;
    rows.value = r.items;
    total.value = r.total;
    stateCounts.value = r.state_counts || {};
    summary.value = r.summary || summary.value;
    batches.value = nextBatches;
    if (!configLoaded) {
      const config = await api("/validation/config");
      if (generation !== loadGeneration) return;
      aiConfigured.value = !!config.ai_configured;
      aiModel.value = config.ai_model || "";
      configLoaded = true;
    }
  } catch (error: any) {
    if (generation === loadGeneration) {
      loadError.value = error.message || "服务暂时不可用";
      rows.value = [];
      total.value = 0;
      stateCounts.value = {};
      batches.value = [];
    }
    throw error;
  } finally {
    if (generation === loadGeneration) busy.value = false;
  }
}
function filtersChanged() {
  page.value = 1;
  run(load);
}
async function refresh() {
  await Promise.all([load(), ...(state.me?.is_admin ? [sourceList()] : [])]);
}
async function open(id: string) {
  const generation = ++reviewGeneration;
  aiGeneration += 1;
  aiBusy.value = false;
  saving.value = false;
  const result = await api("/validation/" + id);
  if (generation !== reviewGeneration) return;
  selected.value = result;
  payload.value = JSON.parse(JSON.stringify(selected.value.corrected));
  confirmProblems.value = [];
  if (!["department", "special"].includes(payload.value.kind))
    payload.value.kind = null;
  aiSuggestions.value = {};
  aiApplied.value = 0;
  aiFailed.value = 0;
  reason.value = "已核对 Excel 原文和系统预填内容";
  issueChecks.value = payload.value.confirmed_issue_ids || [];
  if (route.query.candidate !== id)
    await router.push({ path: "/validation", query: { candidate: id } });
  if (generation !== reviewGeneration) return;
  if (aiConfigured.value && selected.value.state === "pending")
    void run(() => recognizeCandidate());
}
async function closeReview() {
  reviewGeneration += 1;
  aiGeneration += 1;
  aiBusy.value = false;
  saving.value = false;
  selected.value = null;
  payload.value = null;
  await router.replace({ path: "/validation" });
}
const aiIssueCodes = new Set([
  "owners_unmatched",
  "missing_owners",
  "invalid_status",
  "section_unknown",
  "overdue_undue",
]);
function aiTargetIds() {
  const indexes = new Set<number>();
  for (const issue of selected.value?.issues || []) {
    if (issue.resolved || !aiIssueCodes.has(issue.code)) continue;
    const match = String(issue.field_path || "").match(/^items\.(\d+)\./);
    if (match) indexes.add(Number(match[1]));
  }
  return [...indexes]
    .map((index) => payload.value.items[index])
    .filter((item) => item && item.section !== "attachment")
    .map((item) => item.id);
}
function applyRecognizedSuggestion(
  suggestion: any,
  baseline: { section: string; status: string },
) {
  const index = payload.value.items.findIndex(
    (item: any) => item.id === suggestion.id,
  );
  if (index < 0) return;
  const item = payload.value.items[index];
  const codes = new Set(
    selected.value.issues
      .filter(
        (issue: any) =>
          !issue.resolved &&
          String(issue.field_path || "").startsWith(`items.${index}.`),
      )
      .map((issue: any) => issue.code),
  );
  if (
    codes.has("section_unknown") &&
    suggestion.section &&
    item.section === baseline.section
  )
    item.section = suggestion.section;
  if (
    (codes.has("invalid_status") || codes.has("overdue_undue")) &&
    suggestion.status &&
    item.status === baseline.status
  )
    item.status = suggestion.status;
}
async function recognizeCandidate() {
  const candidate = selected.value;
  const target = payload.value;
  if (!candidate || !target) return;
  const generation = reviewGeneration;
  const request = ++aiGeneration;
  const current = () =>
    generation === reviewGeneration &&
    request === aiGeneration &&
    selected.value === candidate &&
    payload.value === target;
  aiBusy.value = true;
  try {
    const itemIds = aiTargetIds();
    const baselines = new Map<string, { section: string; status: string }>(
      target.items.map((item: any) => [
        item.id,
        { section: item.section, status: item.status },
      ]),
    );
    if (!itemIds.length) {
      if (aiApplied.value) ElMessage.info("需要智能补充的内容已经处理完成");
      return;
    }
    const chunks: string[][] = [];
    for (let index = 0; index < itemIds.length; index += 4)
      chunks.push(itemIds.slice(index, index + 4));
    const suggestions: any[] = [];
    let failures = 0;
    for (let index = 0; index < chunks.length; index += 3) {
      const responses = await Promise.allSettled(
        chunks.slice(index, index + 3).map((ids) =>
          api(`/validation/${candidate.id}/ai-suggestions`, "POST", {
            item_ids: ids,
          }),
        ),
      );
      if (!current()) return;
      for (const response of responses) {
        if (response.status === "fulfilled")
          suggestions.push(...(response.value.suggestions || []));
        else failures += 1;
      }
    }
    if (!current()) return;
    for (const suggestion of suggestions) {
      const baseline = baselines.get(suggestion.id);
      if (baseline) applyRecognizedSuggestion(suggestion, baseline);
    }
    aiSuggestions.value = {
      ...aiSuggestions.value,
      ...Object.fromEntries(suggestions.map((item: any) => [item.id, item])),
    };
    aiApplied.value = Object.keys(aiSuggestions.value).length;
    aiFailed.value = failures;
    if (failures)
      ElMessage.warning(`AI 已分析 ${suggestions.length} 条，部分内容仍需查看`);
    else if (suggestions.length)
      ElMessage.success(`AI 已分析 ${suggestions.length} 条工作`);
  } finally {
    if (current()) aiBusy.value = false;
  }
}
function issueSatisfied(issue: any) {
  if (issue.code === "meeting_conflict") return false;
  const match = String(issue.field_path || "").match(/^items\.(\d+)\./);
  const item = match ? payload.value.items[Number(match[1])] : null;
  if (["owners_unmatched", "missing_owners"].includes(issue.code)) {
    const itemIndex = match ? Number(match[1]) : -1;
    const originalNames = new Set(
      (selected.value.original.items?.[itemIndex]?.owner_names || []).filter(
        Boolean,
      ),
    );
    const ownerIds = new Set(item?.owner_ids || []);
    return originalNames.size
      ? ownerIds.size >= originalNames.size
      : ownerIds.size > 0;
  }
  if (issue.code === "section_unknown")
    return ["review", "new", "attachment"].includes(item?.section);
  if (issue.code === "invalid_due_date") return true;
  if (issue.code === "invalid_status")
    return item?.section !== "review" || statuses.includes(item?.status);
  if (issue.code === "overdue_undue")
    return (
      item?.status !== "未到期" ||
      !item?.due_date ||
      !payload.value.meeting_date ||
      item.due_date >= payload.value.meeting_date
    );
  if (issue.code === "verifier_unmatched") return !!item?.verifier_id;
  if (["department_unmatched", "missing_department"].includes(issue.code))
    return payload.value.kind === "special" || !!payload.value.department_id;
  if (["host_unmatched", "missing_host"].includes(issue.code))
    return !!payload.value.host_id;
  if (issue.code === "recorder_unmatched")
    return (
      !selected.value.original.recorder_name || !!payload.value.recorder_id
    );
  if (issue.code === "reviewer_unmatched") return true;
  if (issue.code === "invalid_meeting_date")
    return !!payload.value.meeting_date;
  if (issue.code === "meeting_kind")
    return ["department", "special"].includes(payload.value.kind);
  if (issue.code === "attendees_unmatched") return true;
  return false;
}
function acknowledgedIssues() {
  return (selected.value.issues || [])
    .filter((issue: any) => issue.resolved || issueSatisfied(issue))
    .map((issue: any) => issue.id);
}
async function save() {
  const candidate = selected.value;
  const target = payload.value;
  const generation = reviewGeneration;
  if (!candidate || !target) return false;
  const current = () =>
    generation === reviewGeneration && selected.value === candidate;
  saving.value = true;
  try {
    target.confirmed_issue_ids = [
      ...new Set([...issueChecks.value, ...acknowledgedIssues()]),
    ];
    const r = await api("/validation/" + candidate.id, "PUT", {
      expected_version: candidate.version,
      reason: reason.value,
      payload: target,
    });
    if (!current()) return false;
    candidate.version = r.version;
    const fresh = await api("/validation/" + candidate.id);
    if (!current()) return false;
    candidate.remaining = fresh.remaining;
    ElMessage.success("更正已保存");
    confirmProblems.value = [];
    await load();
    return current();
  } finally {
    if (current()) saving.value = false;
  }
}
async function newSeries() {
  const { value } = await ElMessageBox.prompt(
    "为连续会议设置一个名称，例如：信息系统优化会议",
    "新建会议系列",
    { inputValidator: (v) => !!v?.trim() || "请输入名称" },
  );
  const series = await api("/series", "POST", { name: value });
  if (!state.directory.series.some((item: any) => item.id === series.id))
    state.directory.series.push(series);
  payload.value.series_id = series.id;
}
async function confirm() {
  if (!(await save())) return;
  const candidate = selected.value;
  const generation = reviewGeneration;
  let m;
  try {
    m = await api("/validation/" + candidate.id + "/confirm", "POST", {
      expected_version: candidate.version,
      reason: reason.value,
    });
  } catch (error: any) {
    if (generation !== reviewGeneration || selected.value !== candidate) return;
    try {
      const detail = JSON.parse(error.message);
      if (Array.isArray(detail.issues)) {
        confirmProblems.value = detail.issues.map((issue: any) => {
          const match = String(issue.field || "").match(/^items\.(\d+)\./);
          const item = match ? payload.value.items[Number(match[1])] : null;
          return `${item ? `${item.source_sheet || "Excel"} 第 ${item.source_row} 行：` : ""}${issue.message}`;
        });
        throw new Error(
          `还有 ${confirmProblems.value.length} 项需要核对，请查看页面顶部提示`,
        );
      }
    } catch (parseError: any) {
      if (confirmProblems.value.length) throw parseError;
    }
    throw error;
  }
  if (generation !== reviewGeneration || selected.value !== candidate) return m;
  ElMessage.success("已确认入库，未发送历史通知");
  await closeReview();
  await load();
  return m;
}
async function scope(row: any, target: string) {
  const { value } = await ElMessageBox.prompt(
    target === "pending"
      ? "请说明纳入本次导入范围的依据"
      : "请说明排除依据（不会删除原文件）",
    "调整导入范围",
    { inputValidator: (v) => !!v?.trim() || "请填写原因" },
  );
  await api("/validation/" + row.id + "/scope", "POST", {
    expected_version: row.version,
    state: target,
    reason: value,
  });
  await load();
  if (target === "pending") await open(row.id);
}
async function upload(e: Event) {
  const input = e.target as HTMLInputElement;
  const files = input.files;
  if (!files?.length) return;
  uploading.value = true;
  try {
    const b = await api("/imports", "POST", {
      name: "历史会议导入 " + sourceTime(new Date().toISOString()),
    });
    let uploaded = 0;
    const failures: string[] = [];
    for (const f of files) {
      const data = new FormData();
      data.append("file", f);
      try {
        await api("/imports/" + b.id + "/files", "POST", data);
        uploaded += 1;
      } catch (error: any) {
        failures.push(`${f.name}：${error.message || "上传失败"}`);
      }
    }
    if (!uploaded) throw new Error(failures.join("；") || "文件上传失败");
    batch.value = b.id;
    filter.value = "actionable";
    search.value = "";
    meetingDates.value = null;
    page.value = 1;
    showUpload.value = false;
    await Promise.all([load(), sourceList()]);
    if (failures.length)
      ElMessage.warning(`已上传 ${uploaded} 个文件；${failures.join("；")}`);
    else ElMessage.success(`已上传 ${uploaded} 个文件，解析结果会显示在本页`);
    for (const delay of [2000, 5000, 10000]) {
      refreshTimers.push(
        window.setTimeout(() => {
          void run(async () => {
            await Promise.all([load(), sourceList()]);
          });
        }, delay),
      );
    }
  } finally {
    uploading.value = false;
    input.value = "";
  }
}
async function sourceList() {
  sourceError.value = "";
  try {
    sources.value = await api("/import-sources");
  } catch (error: any) {
    sources.value = [];
    sourceError.value = error.message || "服务暂时不可用";
    throw error;
  }
}
async function openSources() {
  await sourceList();
  showSources.value = true;
}
function sourceStatus(source: any) {
  const issueCount =
    source.issues?.filter((issue: any) => !issue.resolved).length || 0;
  const issueLabel = issueCount ? ` · ${issueCount} 项文件问题待处理` : "";
  if (source.duplicate_of) return "重复上传，未新增会议";
  if (source.diagnostics?.some((d: any) => d.code === "queued"))
    return "正在解析";
  if (source.candidate_count) {
    const counts = source.state_counts || {};
    const actionable = (counts.pending || 0) + (counts.out_of_scope || 0);
    return actionable
      ? `${actionable} 场待处理${counts.confirmed ? ` · ${counts.confirmed} 场已归档` : ""}${issueLabel}`
      : counts.confirmed
        ? `${counts.confirmed} 场已归档${issueLabel}`
        : counts.ignored
          ? `${counts.ignored} 场已排除${issueLabel}`
          : `${source.candidate_count} 场已提取${issueLabel}`;
  }
  return source.diagnostics?.some((d: any) =>
    String(d.code || "").includes("parse_error"),
  )
    ? "解析有问题，请查看详情"
    : "未提取到会议，请查看详情";
}
async function viewSourceMeetings(source: any) {
  batch.value = source.original_batch_id || source.batch_id;
  search.value = source.original_filename || source.filename;
  meetingDates.value = null;
  filter.value = "all";
  page.value = 1;
  await load();
  document
    .getElementById("import-meetings")
    ?.scrollIntoView({ behavior: "smooth" });
}
async function openSourceMeetings(source: any) {
  showSources.value = false;
  await viewSourceMeetings(source);
}
function sourceTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
function diagnosticText(diagnostic: any) {
  if (importIssueMessage(diagnostic) !== diagnostic.message)
    return `${diagnostic.sheet ? diagnostic.sheet + "：" : ""}${importIssueMessage(diagnostic)}`;
  if (String(diagnostic.code || "").includes("parse_error"))
    return `${diagnostic.sheet ? diagnostic.sheet + "：" : ""}无法读取。文件可能仍有密码保护或格式损坏，请在 Excel/WPS 中取消保护后“另存为”新文件再上传。`;
  return `${diagnostic.sheet ? diagnostic.sheet + "：" : ""}${diagnostic.message || "需要检查原文件"}`;
}
async function removeSource(source: any) {
  const { value } = await ElMessageBox.prompt(
    source.duplicate_of
      ? "删除本次重复上传记录。请输入删除原因。"
      : "删除后，该文件产生的未确认会议、解析问题、重复上传记录和原文件将一并清理。请输入删除原因。",
    "删除导入记录",
    {
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
      confirmButtonClass: "el-button--danger",
      inputPlaceholder: "例如：首次上传的文件未解密",
      inputValidator: (v) => !!v?.trim() || "请填写删除原因",
    },
  );
  const result = await api("/import-sources/" + source.id, "DELETE", {
    reason: value,
  });
  ElMessage.success(
    result.deleted_sources > 1
      ? `已删除原记录及 ${result.deleted_sources - 1} 条重复上传记录`
      : "导入记录已删除，可重新上传修正后的文件",
  );
  await Promise.all([load(), sourceList()]);
}
async function acknowledge(issue: any) {
  const { value } = await ElMessageBox.prompt(
    "请记录人工检查结果、补录位置或不纳入统计的依据。",
    "文件解析问题确认",
    {
      inputType: "textarea",
      inputValidator: (v) => !!v?.trim() || "请填写处理说明",
    },
  );
  await api("/import-issues/" + issue.id + "/acknowledge", "POST", {
    reason: value,
  });
  await sourceList();
  if (selected.value?.source_issues)
    selected.value.source_issues = selected.value.source_issues.filter(
      (row: any) => row.id !== issue.id,
    );
}
onMounted(() =>
  run(async () => {
    await Promise.allSettled([
      load(),
      ...(state.me?.is_admin ? [sourceList()] : []),
    ]);
    if (route.query.upload === "1" && state.me?.is_admin)
      showUpload.value = true;
    if (typeof route.query.candidate === "string")
      await open(route.query.candidate);
  }),
);
onUnmounted(() => {
  reviewGeneration += 1;
  aiGeneration += 1;
  refreshTimers.forEach((timer) => window.clearTimeout(timer));
});
</script>
<template>
  <template v-if="!selected">
    <section class="page-heading">
      <div>
        <p class="eyebrow">HISTORY IMPORT</p>
        <h1>导入历史会议</h1>
        <p class="muted">
          上传 Excel，核对提取出的会议，确认后即可在“会议纪要与任务单”查看。
        </p>
      </div>
      <div class="actions">
        <el-button @click="run(refresh)">刷新结果</el-button>
        <el-button v-if="state.me?.is_admin" @click="run(openSources)"
          >查看全部上传文件</el-button
        >
        <el-button
          v-if="state.me?.is_admin"
          type="primary"
          @click="showUpload = true"
          >＋ 导入 Excel 文件</el-button
        >
      </div>
    </section>
    <el-alert v-if="sourceError" type="error" :closable="false" show-icon>
      <template #title>上传文件列表加载失败：{{ sourceError }}</template>
      <el-button size="small" @click="run(sourceList)">重试加载</el-button>
    </el-alert>
    <el-alert
      title="上传后先在“最近上传的文件”查看解析状态。提取的会议会列在下方；核对并确认入库后，本人负责或所属部门相关的成员就能在会议归档中查看。历史导入不会自动发送待办或通知。"
      type="info"
      :closable="false"
      show-icon
    />
    <section v-if="state.me?.is_admin" class="panel recent-imports">
      <div class="panel-heading">
        <h2>最近上传的文件</h2>
        <el-button link type="primary" @click="run(openSources)"
          >查看全部文件与问题说明</el-button
        >
      </div>
      <div v-if="recentSources.length" class="recent-import-list">
        <div
          v-for="source in recentSources"
          :key="source.id"
          class="recent-import-row"
        >
          <div>
            <strong>{{ source.filename }}</strong>
            <p class="muted small">
              {{ sourceTime(source.created_at) }} · {{ source.batch_name }}
            </p>
          </div>
          <span class="recent-import-status">{{ sourceStatus(source) }}</span>
          <el-button
            v-if="source.candidate_count || source.duplicate_of"
            link
            type="primary"
            @click="run(() => viewSourceMeetings(source))"
            >查看提取的会议</el-button
          >
          <el-button v-else link @click="run(openSources)">查看详情</el-button>
          <el-button
            v-if="source.issues?.some((issue: any) => !issue.resolved)"
            link
            type="warning"
            @click="run(openSources)"
            >处理文件问题</el-button
          >
        </div>
      </div>
      <el-empty
        v-else
        :description="
          sourceError ? '加载失败，请重试' : '还没有上传历史会议文件'
        "
      />
    </section>
    <section class="panel filters">
      <h2 class="filter-heading">查找导入的会议</h2>
      <div class="filter-row">
        <el-select
          v-model="filter"
          aria-label="处理状态"
          @change="filtersChanged"
          ><el-option value="actionable" label="待处理的会议" /><el-option
            value="all"
            label="全部会议" /><el-option
            v-for="(label, key) in labels"
            :key="key"
            :value="key"
            :label="label" /></el-select
        ><el-input
          v-model="search"
          clearable
          placeholder="搜索会议标题或文件名"
          aria-label="搜索历史会议标题或文件名"
          @keyup.enter="filtersChanged"
          @clear="filtersChanged"
        /><el-date-picker
          v-model="meetingDates"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="会议开始日期"
          end-placeholder="会议结束日期"
          range-separator="至"
          aria-label="按会议日期筛选"
          @change="filtersChanged"
        /><el-select
          v-if="state.me?.is_admin"
          v-model="scopeFilter"
          aria-label="相关范围"
          @change="filtersChanged"
          ><el-option value="all" label="全部部门" /><el-option
            value="related"
            label="与我或我部门相关"
        /></el-select>
        <el-select
          v-if="batches.length > 1"
          v-model="batch"
          clearable
          placeholder="全部上传批次"
          aria-label="上传批次"
          @change="filtersChanged"
          ><el-option
            v-for="b in batches"
            :key="b.id"
            :value="b.id"
            :label="b.name" /></el-select
        ><el-button @click="filtersChanged">搜索</el-button>
      </div>
    </section>
    <el-alert v-if="loadError" type="error" :closable="false" show-icon>
      <template #title>历史会议列表加载失败：{{ loadError }}</template>
      <el-button size="small" @click="run(load)">重试加载</el-button>
    </el-alert>
    <section v-if="!loadError" class="panel import-summary">
      <div class="summary-stat">
        <strong>{{ pendingCount }}</strong
        ><span>待处理会议</span>
      </div>
      <div class="summary-stat">
        <strong>{{ stateCounts.confirmed || 0 }}</strong
        ><span>已确认入库</span>
      </div>
      <div class="summary-stat">
        <strong>{{ stateCounts.out_of_scope || 0 }}</strong
        ><span>旧范围限制待处理</span>
      </div>
      <div class="summary-stat">
        <strong>{{ stateCounts.duplicate || 0 }}</strong
        ><span>重复记录</span>
      </div>
    </section>
    <section id="import-meetings" class="panel">
      <div class="panel-heading">
        <h2>
          从文件提取的会议 <span class="count">{{ total }}</span>
        </h2>
        <span class="muted small"
          >选择一场会议核对；已归档的可直接打开正式纪要。</span
        >
      </div>
      <el-table
        v-loading="busy"
        :data="rows"
        :empty-text="
          loadError
            ? '加载失败，请重试'
            : '没有找到符合条件的会议。可清除日期、标题或批次筛选后再查看。'
        "
        ><el-table-column label="会议 / 来源" min-width="330"
          ><template #default="{ row }"
            ><RouterLink
              v-if="row.meeting_id"
              class="table-link"
              :to="'/meetings/' + row.meeting_id"
              >{{ row.title || "会议名称待确认" }}</RouterLink
            ><el-button
              v-else
              link
              type="primary"
              @click="run(() => open(row.id))"
              >{{ row.title || "会议名称待确认" }}</el-button
            >
            <div class="muted small">
              {{ row.filename }} · {{ row.sheet }}
            </div></template
          ></el-table-column
        ><el-table-column
          prop="department_name"
          label="部门原文"
          min-width="120"
        /><el-table-column
          prop="meeting_date"
          label="会议日期"
          width="120"
        /><el-table-column
          prop="item_count"
          label="条目"
          width="70"
        /><el-table-column label="状态" width="130"
          ><template #default="{ row }"
            ><el-tag
              :type="
                row.state === 'pending'
                  ? 'warning'
                  : row.state === 'confirmed'
                    ? 'success'
                    : 'info'
              "
              >{{ labels[row.state] }}</el-tag
            ></template
          ></el-table-column
        ><el-table-column label="操作" width="150"
          ><template #default="{ row }"
            ><el-button
              v-if="row.state === 'pending'"
              link
              type="primary"
              @click="run(() => open(row.id))"
              >核对并归档</el-button
            ><el-button
              v-if="['out_of_scope', 'ignored'].includes(row.state)"
              link
              type="primary"
              @click="run(() => scope(row, 'pending'))"
              >纳入并核对</el-button
            ><RouterLink
              v-if="row.meeting_id"
              class="table-link"
              :to="'/meetings/' + row.meeting_id"
              >查看归档</RouterLink
            ><el-button
              v-if="row.state === 'pending'"
              link
              @click="run(() => scope(row, 'ignored'))"
              >排除</el-button
            ></template
          ></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="page"
        :page-size="20"
        :total="total"
        layout="prev,pager,next,total"
        @current-change="run(load)"
      />
    </section>
  </template>
  <el-dialog
    v-model="showUpload"
    title="导入历史会议 Excel"
    width="min(560px, 94vw)"
  >
    <p>
      选择一个或多个历史会议文件。系统会按文件里的实际会议日期整理，不需要先指定月份。
    </p>
    <p class="muted small">
      支持 .xls、.xlsx；已有 Word 纪要也可上传
      .docx。上传后可在本页看到文件状态和提取结果。
    </p>
    <label class="upload-button">
      {{ uploading ? "正在上传…" : "选择文件并开始导入" }}
      <input
        type="file"
        accept=".xls,.xlsx,.docx"
        multiple
        :disabled="uploading"
        @change="(e) => run(() => upload(e))"
      />
    </label>
  </el-dialog>
  <HistoricalMeetingReview
    v-if="selected && payload"
    :selected="selected"
    :payload="payload"
    :saving="saving"
    :ai-busy="aiBusy"
    :ai-configured="aiConfigured"
    :ai-model="aiModel"
    :ai-applied="aiApplied"
    :ai-failed="aiFailed"
    :ai-suggestions="aiSuggestions"
    :reason="reason"
    :issue-checks="issueChecks"
    :confirm-problems="confirmProblems"
    @update:reason="reason = $event"
    @update:issue-checks="issueChecks = $event"
    @close="run(closeReview)"
    @save="run(save)"
    @confirm="run(confirm)"
    @include="run(() => scope(selected, 'pending'))"
    @new-series="run(newSeries)"
    @source-issues="run(openSources)"
    @recognize="run(recognizeCandidate)"
    @link="run(() => suggestLinks($event))"
    @download="
      run(() =>
        download(
          '/files/' + selected.source.attachment_id,
          selected.source.filename,
        ),
      )
    "
  />
  <el-dialog
    :model-value="!!linkItem"
    @close="linkItem = null"
    title="人工确认历史任务关联"
    width="min(1100px,96vw)"
  >
    <el-alert
      title="建议仅来自已确认入库的历史纪要。请先按日期确认前期纪要，再核对内容、人员和期限；选择关联不会覆盖历史考核，也不会发送待办。"
      :closable="false"
      type="info"
    />
    <el-input
      v-model="linkReason"
      placeholder="填写确认属于同一工作的依据"
      aria-label="历史关联依据"
    />
    <el-table
      :data="linkSuggestions"
      empty-text="没有可关联的历史事项，可保留为独立工作"
    >
      <el-table-column label="来源会议" min-width="180"
        ><template #default="{ row }"
          >{{ row.meeting_date }} · {{ row.title }}
          <div class="muted">
            {{ row.source.sheet }} · 第 {{ row.source.row }} 行
          </div></template
        ></el-table-column
      >
      <el-table-column prop="content" label="工作内容" min-width="220" />
      <el-table-column label="责任人与期限" min-width="160"
        ><template #default="{ row }"
          >{{ row.owner_names.join("、") }}
          <div>{{ row.due_date }} · {{ row.status }}</div></template
        ></el-table-column
      >
      <el-table-column label="建议依据" min-width="150"
        ><template #default="{ row }">{{
          row.match_reasons.join("；") || "工作内容相近"
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="110"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :disabled="!linkReason.trim()"
            @click="selectLink(row)"
            >确认关联</el-button
          ></template
        ></el-table-column
      >
    </el-table>
  </el-dialog>
  <el-dialog v-model="showSources" title="全部上传文件" width="min(1000px,96vw)"
    ><el-alert v-if="sourceError" type="error" :closable="false" show-icon>
      <template #title>上传文件列表加载失败：{{ sourceError }}</template>
      <el-button size="small" @click="run(sourceList)">重试加载</el-button>
    </el-alert>
    <el-alert
      title="这里记录每次上传、解析状态和原文件。重复上传不会生成新会议；可查看第一次上传的提取结果。"
      type="info"
      :closable="false"
      show-icon
    />
    <el-input
      v-model="sourceKeyword"
      clearable
      placeholder="按文件名搜索"
      aria-label="搜索导入文件"
      class="source-search"
    />
    <el-empty
      v-if="!filteredSources.length"
      :description="sourceError ? '加载失败，请重试' : '没有找到对应的导入文件'"
    />
    <article v-for="s in filteredSources" :key="s.id" class="source-row">
      <div class="source-row-heading">
        <div>
          <el-button
            link
            type="primary"
            :disabled="!s.attachment_id"
            @click="
              run(() => download('/files/' + s.attachment_id, s.filename))
            "
            >{{ s.filename }}</el-button
          >
          <el-tag v-if="s.duplicate_of" type="info">重复上传</el-tag>
          <el-tag
            v-else-if="
              s.diagnostics?.some((d) => String(d.code).includes('parse_error'))
            "
            type="warning"
            >解析有问题</el-tag
          >
          <span class="source-status-text">{{ sourceStatus(s) }}</span>
          <p class="muted small source-meta">
            {{ sourceTime(s.created_at) }} · {{ s.batch_name }}
          </p>
        </div>
        <div class="actions">
          <el-button
            v-if="s.candidate_count || s.duplicate_of"
            link
            type="primary"
            @click="run(() => openSourceMeetings(s))"
            >查看提取的会议</el-button
          >
          <el-tooltip
            :content="s.delete_block_reason || '删除该次导入及其未确认解析结果'"
          >
            <span>
              <el-button
                link
                type="danger"
                :disabled="!s.can_delete"
                @click="run(() => removeSource(s))"
                >删除导入记录</el-button
              >
            </span>
          </el-tooltip>
        </div>
      </div>
      <p v-if="s.duplicate_of" class="source-guidance">
        本次上传与此前文件内容完全相同，因此未再次解析。若已经解密，请确认保存后使用“另存为”生成新文件再上传。
      </p>
      <p v-for="(d, i) in s.diagnostics" :key="i" class="muted small">
        {{ diagnosticText(d) }}
      </p>
      <div v-for="issue in s.issues" :key="issue.id">
        <el-alert
          :title="importIssueMessage(issue)"
          :type="issue.resolved ? 'success' : 'warning'"
          :closable="false"
        /><el-button
          v-if="!issue.resolved"
          @click="run(() => acknowledge(issue))"
          >记录人工检查结果</el-button
        >
        <p v-else>{{ issue.resolution }}</p>
      </div>
    </article>
    <template #footer>
      <el-button @click="run(rematch)">重新匹配通讯录</el-button>
      <el-button @click="showSources = false">关闭</el-button>
    </template>
  </el-dialog>
</template>
