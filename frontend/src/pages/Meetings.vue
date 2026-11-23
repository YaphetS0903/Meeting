<script setup lang="ts">
import { hasBusinessPermission } from "../api";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Filter,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, state, query, run, rate, kinds, departmentPath } from "../api";
import PeriodFilter from "../components/PeriodFilter.vue";
import TaskSheetImport from "../components/TaskSheetImport.vue";
import { useMeetingListSync } from "../composables/useMeetingListSync";
const route = useRoute();
const router = useRouter();
function meetingDepartment(row: any) {
  return (
    state.directory.departments.find((d: any) => d.id === row.department_id)
      ?.name ||
    (row.department_name || departmentPath(row.department_id))
      .split(" / ")
      .at(-1) ||
    "—"
  );
}
const rows = ref<any[]>([]),
  seriesRows = ref<any[]>([]),
  seriesMeetingTotal = ref(0),
  total = ref(0),
  page = ref(1),
  busy = ref(false),
  loadError = ref(""),
  continuingId = ref(""),
  deletingMeetingId = ref(""),
  deletingSeries = ref(false),
  seriesImpact = ref<any>(null),
  seriesDeleteVisible = ref(false),
  creatingSeries = ref(false),
  createSeriesVisible = ref(false),
  createSeriesName = ref(""),
  createSeriesKind = ref("department"),
  createSeriesDepartment = ref(""),
  createSeriesError = ref(""),
  filters = ref<any>({}),
  search = ref(""),
  department = ref(""),
  scope = ref("all"),
  kind = ref(""),
  filtersExpanded = ref(false);
const historyExpanded = ref(false),
  historyRows = ref<any[]>([]),
  historyTotal = ref(0),
  historyPage = ref(1),
  historyBusy = ref(false),
  historyError = ref("");
const sheetImportVisible = ref(false);
function importedMeeting(id: string) {
  router.push({
    path: `/meetings/${id}`,
    query: { series: activeSeries.value },
  });
}
const activeSeries = computed(() =>
  typeof route.query.series === "string" ? route.query.series : "",
);
const canRenameSeries = computed(
  () => state.me?.is_admin || state.me?.can_rename_meetings,
);
const renameVisible = ref(false),
  renameSaving = ref(false),
  renameOptions = ref<any[]>([]),
  renamePermissionsError = ref(""),
  renameSelected = ref<any>(null),
  renameName = ref(""),
  renameError = ref("");
let renameSearchGeneration = 0;
async function loadRenameSeries() {
  if (!canRenameSeries.value || state.me?.is_admin) return;
  const generation = ++renameSearchGeneration;
  renamePermissionsError.value = "";
  try {
    const result = await api("/meeting-series-name-options");
    if (generation !== renameSearchGeneration) return;
    renameOptions.value = result.items;
  } catch (error: any) {
    if (generation === renameSearchGeneration) {
      renameOptions.value = [];
      renamePermissionsError.value =
        error.message || "会议系列编辑权限读取失败";
    }
  }
}
function canRenameSeriesRow(row: any) {
  return (
    row.key.startsWith("series:") &&
    (state.me?.is_admin ||
      renameOptions.value.some(
        (item) => `series:${item.id}` === row.key && item.can_rename,
      ))
  );
}
function openRenameSeries(row: any) {
  if (!canRenameSeriesRow(row)) return;
  renameSelected.value = {
    id: row.key.slice(7),
    name: row.name,
    can_rename: true,
  };
  renameName.value = row.name;
  renameError.value = "";
  renameVisible.value = true;
}
async function saveSeriesName() {
  if (renameSaving.value || !renameSelected.value?.can_rename) return;
  const name = renameName.value.trim();
  if (!name) {
    renameError.value = "请输入会议系列名称";
    return;
  }
  const selected = renameSelected.value;
  renameSaving.value = true;
  renameError.value = "";
  try {
    const result = await api(`/meeting-series/${selected.id}/name`, "PUT", {
      name,
      expected_name: selected.name,
    });
    generation += 1;
    historyGeneration += 1;
    for (const series of state.directory.series)
      if (series.id === result.id) series.name = result.name;
    for (const row of seriesRows.value)
      if (row.key === `series:${result.id}`) row.name = result.name;
    for (const row of [...rows.value, ...historyRows.value])
      if (row.series_id === result.id) row.series_name = result.name;
    renameVisible.value = false;
    ElMessage.success("会议系列名称已更新");
    await Promise.all([load(true), loadHistory(true)]);
  } catch (error: any) {
    renameError.value = error.message || "修改失败，请重试";
  } finally {
    renameSaving.value = false;
  }
}
const activeSeriesName = computed(() => {
  const key = activeSeries.value;
  if (key.startsWith("series:"))
    return (
      state.directory.series.find((item: any) => item.id === key.slice(7))
        ?.name || "会议系列"
    );
  if (key === "unassigned" || key.startsWith("unassigned:"))
    return "未归入系列";
  return "";
});
const hasActiveFilters = computed(
  () =>
    !!(
      filters.value.start ||
      filters.value.end ||
      search.value ||
      (!activeSeries.value &&
        (department.value || kind.value || scope.value !== "all"))
    ),
);
let generation = 0;
let foregroundGeneration = 0;
let historyGeneration = 0;
const deletedMeetingIds = new Set<string>();
function withoutDeletedMeetings(result: any) {
  const items = result.items.filter(
    (item: any) => !deletedMeetingIds.has(item.id),
  );
  return {
    items,
    total: Math.max(0, result.total - (result.items.length - items.length)),
  };
}
async function loadHistory(silent = false) {
  if (!historyExpanded.value || !activeSeries.value) return;
  const current = ++historyGeneration;
  if (!silent) {
    historyBusy.value = true;
    historyError.value = "";
  }
  try {
    const result = await api(
      "/meetings?" +
        query({
          view: "history",
          series_key: activeSeries.value,
          scope: "all",
          offset: (historyPage.value - 1) * 20,
          limit: 20,
        }),
      "GET",
      undefined,
      { background: silent },
    );
    if (current !== historyGeneration) return;
    const visible = withoutDeletedMeetings(result);
    historyError.value = "";
    historyRows.value = visible.items;
    historyTotal.value = visible.total;
    const lastPage = Math.max(1, Math.ceil(visible.total / 20));
    if (historyPage.value > lastPage) {
      historyPage.value = lastPage;
      await loadHistory(silent);
    }
  } catch (error: any) {
    if (current === historyGeneration && !silent)
      historyError.value = error.message || "历史会议读取失败";
  } finally {
    if (current === historyGeneration) historyBusy.value = false;
  }
}
function toggleHistory() {
  historyExpanded.value = !historyExpanded.value;
  if (historyExpanded.value) run(loadHistory);
  else {
    historyGeneration += 1;
    historyBusy.value = false;
  }
}
async function load(silent = false) {
  const g = ++generation;
  if (!silent) {
    foregroundGeneration = g;
    busy.value = true;
    loadError.value = "";
  }
  try {
    const common = {
      view: "current",
      ...filters.value,
      q: search.value,
      department_id: activeSeries.value ? undefined : department.value,
      kind: activeSeries.value ? undefined : kind.value,
      scope: activeSeries.value ? "all" : scope.value,
    };
    const r = activeSeries.value
      ? await api(
          "/meetings?" +
            query({
              ...common,
              series_key: activeSeries.value,
              offset: (page.value - 1) * 20,
              limit: 20,
            }),
          "GET",
          undefined,
          { background: silent },
        )
      : await api(
          "/meeting-series?" +
            query({
              ...common,
            }),
          "GET",
          undefined,
          { background: silent },
        );
    if (g === generation) {
      const visible = activeSeries.value ? withoutDeletedMeetings(r) : r;
      if (
        activeSeries.value &&
        page.value > Math.max(1, Math.ceil(visible.total / 20))
      ) {
        page.value = Math.max(1, Math.ceil(visible.total / 20));
        await load(silent);
        return;
      }
      loadError.value = "";
      if (activeSeries.value) rows.value = visible.items;
      else {
        seriesRows.value = r.items;
        seriesMeetingTotal.value = r.meeting_total;
        void loadRenameSeries();
      }
      total.value = visible.total;
    }
  } catch (error: any) {
    if (g === generation && !silent) {
      loadError.value = error.message || "服务暂时不可用";
      rows.value = [];
      seriesRows.value = [];
      total.value = 0;
      seriesMeetingTotal.value = 0;
    }
    if (!silent) throw error;
  } finally {
    if (!silent && g === foregroundGeneration) busy.value = false;
  }
}
useMeetingListSync(async () => {
  if (
    busy.value ||
    historyBusy.value ||
    deletingMeetingId.value ||
    deletingSeries.value ||
    seriesDeleteVisible.value
  )
    return;
  await Promise.all([load(true), loadHistory(true)]);
});
onBeforeUnmount(() => {
  generation += 1;
  historyGeneration += 1;
  renameSearchGeneration += 1;
});
function openSeries(row: any) {
  search.value = "";
  router.push({ path: "/meetings", query: { series: row.key } });
}
function backToSeries() {
  search.value = "";
  router.push({ path: "/meetings" });
}
function newMeeting() {
  if (!activeSeries.value.startsWith("series:")) return;
  router.push({
    path: "/meetings/new",
    query: { series: activeSeries.value },
  });
}
function openCreateSeries() {
  createSeriesName.value = "";
  createSeriesKind.value = "department";
  const choices = state.directory.departments.filter((d: any) =>
    hasBusinessPermission(
      "series",
      "create",
      d.id,
      d.id === state.me?.department_id,
    ),
  );
  createSeriesDepartment.value =
    choices.find((d: any) => d.id === state.me?.department_id)?.id ||
    choices[0]?.id ||
    "";
  createSeriesError.value = "";
  createSeriesVisible.value = true;
}
async function createSeries() {
  if (creatingSeries.value) return;
  const name = createSeriesName.value.trim();
  if (!name) {
    createSeriesError.value = "请输入会议系列名称";
    return;
  }
  creatingSeries.value = true;
  createSeriesError.value = "";
  try {
    const series = await api("/series", "POST", {
      name,
      kind: createSeriesKind.value,
      department_id: createSeriesDepartment.value || null,
    });
    if (!state.directory.series.some((item: any) => item.id === series.id))
      state.directory.series.push(series);
    createSeriesVisible.value = false;
    search.value = "";
    if (series.existing || hasActiveFilters.value) {
      await router.push({
        path: "/meetings",
        query: { series: `series:${series.id}` },
      });
    } else {
      await load(true);
    }
    ElMessage.success(
      series.existing
        ? "已进入已有会议系列，可查看历史会议或添加会议"
        : "会议系列已建立，点击它可添加会议",
    );
  } catch (error: any) {
    createSeriesError.value = error.message || "会议系列创建失败，请重试";
  } finally {
    creatingSeries.value = false;
  }
}
async function continueMeeting(row: any) {
  if (continuingId.value) return;
  continuingId.value = row.id;
  try {
    await ElMessageBox.confirm("是否确认新建会议？", "确认接续新建", {
      type: "info",
      confirmButtonText: "确认新建",
      cancelButtonText: "取消",
    });
    const created = await api("/meetings/" + row.id + "/continue", "POST");
    ElMessage.success("已建立接续会议草稿，请核对带入事项和日期");
    await router.push({
      path: "/meetings/" + created.id,
      query: { series: activeSeries.value },
    });
  } finally {
    continuingId.value = "";
  }
}
async function removeMeeting(row: any, historical = false) {
  if (deletingMeetingId.value) return;
  deletingMeetingId.value = row.id;
  try {
    const impact = await api("/meetings/" + row.id + "/deletion-impact");
    if (impact.blocking_meeting_count) {
      const titles = (impact.blocking_meetings || [])
        .map((item: any) => `“${item.title}”`)
        .join("、");
      throw new Error(
        `还有 ${impact.blocking_meeting_count} 场会议引用这里的任务${titles ? `：${titles}` : ""}。请先处理后续会议中的任务关联，再删除。`,
      );
    }
    const consequences = impact.published
      ? `此会议已发布。删除后，其 ${impact.task_count} 条关联正式任务和会议附件也会删除；原始导入文件保留。`
      : "这条草稿及其附件将被删除。";
    if (impact.active_native_todos)
      throw new Error("这场会议还有未结束的企业微信待办，请先处理");
    await ElMessageBox.confirm(
      `确定删除“${impact.title}”吗？${consequences}删除后无法恢复。`,
      "删除会议",
      {
        type: "warning",
        confirmButtonText: "确认删除",
        confirmButtonClass: "el-button--danger",
      },
    );
    await api("/meetings/" + row.id, "DELETE", {
      expected_draft_version: impact.draft_version,
    });
    // Keep a confirmed deletion out of late or temporarily stale list results.
    deletedMeetingIds.add(row.id);
    generation += 1;
    historyGeneration += 1;
    if (historical) {
      historyRows.value = historyRows.value.filter(
        (item) => item.id !== row.id,
      );
      historyTotal.value = Math.max(0, historyTotal.value - 1);
      if (!historyRows.value.length && historyPage.value > 1)
        historyPage.value -= 1;
    } else {
      const wasLastRow = rows.value.length === 1;
      rows.value = rows.value.filter((item) => item.id !== row.id);
      total.value = Math.max(0, total.value - 1);
      if (wasLastRow && page.value > 1) page.value -= 1;
    }
    ElMessage.success("会议已删除");
    await Promise.all([load(true), loadHistory(true)]);
  } finally {
    deletingMeetingId.value = "";
  }
}
async function inspectSeriesDeletion(row: any) {
  seriesImpact.value = await api(
    `/meeting-series/${row.key.slice(7)}/deletion-impact`,
  );
  seriesDeleteVisible.value = true;
}
async function removeSeries() {
  const impact = seriesImpact.value;
  if (!impact?.can_delete || deletingSeries.value) return;
  await ElMessageBox.prompt(
    `将永久删除“${impact.name}”及其全部 ${impact.meeting_count} 场会议。请输入完整系列名称确认。`,
    "再次确认删除整个系列",
    {
      inputValidator: (name) =>
        name === impact.name || "请输入与系列名称完全一致的文字",
      confirmButtonText: "永久删除系列及会议",
      confirmButtonClass: "el-button--danger",
      cancelButtonText: "取消",
      closeOnClickModal: false,
    },
  );
  deletingSeries.value = true;
  try {
    await api(`/meeting-series/${impact.id}`, "DELETE", {
      impact_token: impact.impact_token,
      confirmed_name: impact.name,
    });
    state.directory.series = state.directory.series.filter(
      (item: any) => item.id !== impact.id,
    );
    const removedGroup = seriesRows.value.find(
      (item) => item.key === `series:${impact.id}`,
    );
    seriesRows.value = seriesRows.value.filter(
      (item) => item.key !== `series:${impact.id}`,
    );
    if (removedGroup) {
      total.value = Math.max(0, total.value - 1);
      seriesMeetingTotal.value = Math.max(
        0,
        seriesMeetingTotal.value - removedGroup.meeting_count,
      );
    }
    seriesDeleteVisible.value = false;
    ElMessage.success(`已删除系列及 ${impact.meeting_count} 场会议`);
    await load(true);
  } catch (error) {
    // Refresh the scope after a concurrent edit; require another explicit confirmation.
    seriesImpact.value = await api(
      `/meeting-series/${impact.id}/deletion-impact`,
    ).catch(() => impact);
    throw error;
  } finally {
    deletingSeries.value = false;
  }
}
watch(activeSeries, () => {
  search.value = "";
  page.value = 1;
  historyGeneration += 1;
  historyExpanded.value = false;
  historyRows.value = [];
  historyTotal.value = 0;
  historyPage.value = 1;
  historyBusy.value = false;
  historyError.value = "";
  run(load);
});
function changed(r?: any) {
  if (r) filters.value = r;
  page.value = 1;
  run(load);
}
</script>
<template>
  <section class="page-heading">
    <div>
      <h1>会议纪要与任务单</h1>
    </div>
    <div
      v-if="
        activeSeries.startsWith('series:') &&
        hasBusinessPermission('meetings', 'import')
      "
      class="actions"
    >
      <el-button type="primary" size="large" @click="newMeeting"
        >＋ 新建会议</el-button
      >
    </div>
  </section>
  <div
    v-if="activeSeries"
    class="archive-breadcrumb"
    role="navigation"
    aria-label="归档位置"
  >
    <el-button link type="primary" @click="backToSeries">会议系列</el-button>
    <el-icon><ArrowRight /></el-icon>
    <strong>{{ activeSeriesName }}</strong>
  </div>
  <section
    class="panel filters meeting-archive-filters"
    :class="{
      'filters-expanded': filtersExpanded || activeSeries,
      'series-detail-filters': activeSeries,
    }"
  >
    <button
      v-if="!activeSeries"
      class="archive-filter-toggle"
      type="button"
      :aria-expanded="filtersExpanded"
      @click="filtersExpanded = !filtersExpanded"
    >
      <span
        ><el-icon><Filter /></el-icon> 筛选会议</span
      >
      <span>{{
        hasActiveFilters ? "已筛选" : filtersExpanded ? "收起" : "展开"
      }}</span>
    </button>
    <PeriodFilter allow-all @change="changed" />
    <div class="filter-row">
      <el-input
        v-model="search"
        :placeholder="activeSeries ? '搜索会议名称' : '搜索会议系列名称'"
        clearable
        :aria-label="activeSeries ? '搜索会议' : '搜索会议系列名称'"
        @keyup.enter="changed()"
        @clear="changed()"
      /><el-button @click="changed()">查询</el-button>
      <el-select
        v-if="!activeSeries && state.me?.is_admin"
        v-model="scope"
        aria-label="会议范围"
        @change="changed()"
        ><el-option value="all" label="全部会议" /><el-option
          value="related"
          label="与我或我部门相关"
      /></el-select>
      <el-select
        v-if="!activeSeries"
        v-model="department"
        placeholder="全部部门"
        clearable
        aria-label="部门"
        @change="changed()"
        ><el-option
          v-for="d in state.directory.departments"
          :key="d.id"
          :label="departmentPath(d.id)"
          :value="d.id" /></el-select
      ><el-select
        v-if="!activeSeries"
        v-model="kind"
        placeholder="全部会议类型"
        clearable
        aria-label="会议类型"
        @change="changed()"
        ><el-option
          v-for="(label, key) in kinds"
          :key="key"
          :label="label"
          :value="key"
      /></el-select>
    </div>
  </section>
  <el-alert v-if="loadError" type="error" :closable="false" show-icon>
    <template #title>会议列表加载失败：{{ loadError }}</template>
    <el-button size="small" @click="run(load)">重试加载</el-button>
  </el-alert>
  <section v-if="!activeSeries" class="panel">
    <div class="panel-heading">
      <h2>
        会议系列 <span class="count">{{ total }}</span>
      </h2>
      <div class="actions">
        <span class="muted small">共 {{ seriesMeetingTotal }} 场最新会议</span>
        <el-button
          type="primary"
          v-if="hasBusinessPermission('series', 'create')"
          :loading="creatingSeries"
          @click="openCreateSeries"
          >＋ 新建会议系列</el-button
        >
      </div>
    </div>
    <el-alert
      v-if="renamePermissionsError"
      type="error"
      :closable="false"
      show-icon
    >
      <template #title>{{ renamePermissionsError }}</template>
      <el-button size="small" @click="loadRenameSeries"
        >重试读取编辑权限</el-button
      >
    </el-alert>
    <el-table
      v-loading="busy"
      class="series-archive-table"
      :data="seriesRows"
      row-key="key"
      :empty-text="
        loadError ? '加载失败，请重试' : '没有符合筛选条件的会议系列'
      "
      @row-click="openSeries"
    >
      <el-table-column label="会议系列" min-width="300">
        <template #default="{ row }">
          <button
            class="table-link series-name-button"
            @click.stop="openSeries(row)"
          >
            {{ row.name }}
          </button>
          <el-tag
            v-if="row.pending_edit_count"
            type="warning"
            size="small"
            style="margin-left: 8px"
            >待审批 {{ row.pending_edit_count }}</el-tag
          >
        </template>
      </el-table-column>
      <el-table-column label="会议类型" width="160">
        <template #default="{ row }">{{
          (row.kinds || [])
            .map((kind: keyof typeof kinds) => kinds[kind] || kind)
            .join(" / ") || "未设置"
        }}</template>
      </el-table-column>
      <el-table-column prop="meeting_count" label="会议数" width="100" />
      <el-table-column label="最近会议" width="135">
        <template #default="{ row }">{{ row.latest_date || "—" }}</template>
      </el-table-column>
      <el-table-column
        v-if="canRenameSeries"
        label="操作"
        :width="state.me?.is_admin ? 200 : 110"
        fixed="right"
        class-name="series-row-actions"
      >
        <template #default="{ row }">
          <el-button
            v-if="canRenameSeriesRow(row)"
            link
            type="primary"
            :aria-label="`编辑系列名称：${row.name}`"
            @click.stop="openRenameSeries(row)"
            >编辑名称</el-button
          >
          <el-button
            v-if="state.me?.is_admin && row.key.startsWith('series:')"
            link
            type="danger"
            :aria-label="`删除系列：${row.name}`"
            @click.stop="run(() => inspectSeriesDeletion(row))"
            >删除系列</el-button
          >
        </template>
      </el-table-column>
      <el-table-column label="" width="55" fixed="right">
        <template #default
          ><el-icon><ArrowRight /></el-icon
        ></template>
      </el-table-column>
    </el-table>
    <div v-loading="busy" class="series-mobile-list">
      <div v-for="row in seriesRows" :key="row.key" class="series-mobile-entry">
        <button
          class="series-mobile-row"
          type="button"
          @click="openSeries(row)"
        >
          <span class="series-mobile-details">
            <strong>{{ row.name }}</strong>
            <el-tag v-if="row.pending_edit_count" type="warning" size="small"
              >待审批 {{ row.pending_edit_count }}</el-tag
            >
            <small
              >{{
                (row.kinds || [])
                  .map((kind: keyof typeof kinds) => kinds[kind] || kind)
                  .join(" / ") || "未设置"
              }}
              · {{ row.meeting_count }} 场会议 · 最近
              {{ row.latest_date || "—" }}</small
            >
          </span>
          <el-icon><ArrowRight /></el-icon>
        </button>
        <div
          v-if="
            canRenameSeriesRow(row) ||
            (state.me?.is_admin && row.key.startsWith('series:'))
          "
          class="series-mobile-actions"
        >
          <el-button
            v-if="canRenameSeriesRow(row)"
            type="primary"
            link
            :aria-label="`编辑系列名称：${row.name}`"
            @click.stop="openRenameSeries(row)"
            >编辑名称</el-button
          >
          <el-button
            v-if="state.me?.is_admin && row.key.startsWith('series:')"
            type="danger"
            link
            :aria-label="`删除系列：${row.name}`"
            @click.stop="run(() => inspectSeriesDeletion(row))"
            >删除系列</el-button
          >
        </div>
      </div>
      <p v-if="!busy && !seriesRows.length" class="series-mobile-empty">
        {{ loadError ? "加载失败，请重试" : "没有符合筛选条件的会议系列" }}
      </p>
    </div>
  </section>
  <el-dialog
    v-model="createSeriesVisible"
    title="新建会议系列"
    width="min(480px, calc(100vw - 24px))"
    :close-on-click-modal="false"
    :close-on-press-escape="!creatingSeries"
    :show-close="!creatingSeries"
  >
    <el-form label-position="top" @submit.prevent="createSeries">
      <el-form-item label="会议系列名称" required>
        <el-input
          v-model="createSeriesName"
          @input="createSeriesError = ''"
          aria-label="会议系列名称"
          maxlength="250"
          :disabled="creatingSeries"
          autofocus
        />
      </el-form-item>
      <el-form-item
        v-if="createSeriesKind === 'department'"
        label="所属部门"
        required
      >
        <el-select
          v-model="createSeriesDepartment"
          aria-label="新系列所属部门"
          filterable
          :disabled="creatingSeries"
        >
          <el-option
            v-for="d in state.directory.departments.filter((d: any) =>
              hasBusinessPermission(
                'series',
                'create',
                d.id,
                d.id === state.me?.department_id,
              ),
            )"
            :key="d.id"
            :value="d.id"
            :label="departmentPath(d.id)"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="会议类型" required>
        <el-radio-group
          v-model="createSeriesKind"
          aria-label="新系列会议类型"
          :disabled="creatingSeries"
        >
          <el-radio value="department">部门例会</el-radio>
          <el-radio value="special">专项会议</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-alert
        v-if="createSeriesError"
        :title="createSeriesError"
        type="error"
        :closable="false"
        show-icon
      />
    </el-form>
    <template #footer>
      <el-button :disabled="creatingSeries" @click="createSeriesVisible = false"
        >取消</el-button
      >
      <el-button type="primary" :loading="creatingSeries" @click="createSeries"
        >确定</el-button
      >
    </template>
  </el-dialog>
  <section v-if="activeSeries" class="panel meeting-data-panel">
    <div class="panel-heading">
      <h2>
        {{ activeSeriesName }} <span class="count">{{ total }}</span>
      </h2>
      <div class="actions">
        <el-button
          v-if="activeSeries.startsWith('series:')"
          @click="sheetImportVisible = true"
          >导入任务单</el-button
        >
      </div>
    </div>
    <TaskSheetImport
      v-model="sheetImportVisible"
      :series-id="activeSeries.slice(7)"
      @created="importedMeeting"
    />
    <el-table
      v-loading="busy"
      :data="rows"
      class="meeting-current-table"
      scrollbar-always-on
      row-key="id"
      :empty-text="
        loadError
          ? '加载失败，请重试'
          : hasActiveFilters
            ? '未找到符合条件的会议'
            : '暂无当前会议，可查看下方历史会议或新建会议。'
      "
      ><el-table-column
        prop="meeting_date"
        label="会议日期"
        width="104"
      /><el-table-column label="会议名称" min-width="176"
        ><template #default="{ row }"
          ><RouterLink
            class="table-link"
            :to="{
              path: '/meetings/' + row.id,
              query: { series: activeSeries },
            }"
            >{{ row.title }}</RouterLink
          >
          <div class="muted small">
            {{ row.historical ? "历史导入" : "在线会议" }}
            <el-tag v-if="row.pending_edit_count" type="warning" size="small"
              >待审批 {{ row.pending_edit_count }}</el-tag
            >
          </div>
        </template></el-table-column
      ><el-table-column label="所属部门" min-width="96"
        ><template #default="{ row }"
          ><span :title="departmentPath(row.department_id)">{{
            meetingDepartment(row)
          }}</span></template
        ></el-table-column
      ><el-table-column label="会议类型" min-width="84"
        ><template #default="{ row }">{{
          kinds[row.kind] || "—"
        }}</template></el-table-column
      ><el-table-column
        prop="host_name"
        label="主持人"
        min-width="92"
      /><el-table-column
        prop="item_count"
        label="工作事项"
        min-width="72"
      /><el-table-column label="达成率" min-width="80"
        ><template #default="{ row }"
          ><strong>{{ rate(row.rate) }}</strong></template
        ></el-table-column
      ><el-table-column label="状态" width="128"
        ><template #default="{ row }"
          ><el-tag
            :type="
              !row.published_version || (row.is_revising ?? row.has_draft)
                ? 'info'
                : 'success'
            "
            >{{ row.published_version ? "已发布" : "草稿"
            }}{{
              (row.is_revising ?? row.has_draft) && row.published_version
                ? " · 修订中"
                : ""
            }}</el-tag
          ></template
        ></el-table-column
      ><el-table-column
        label="操作"
        width="160"
        fixed="right"
        class-name="meeting-row-actions"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            @click="
              router.push({
                path: '/meetings/' + row.id,
                query: { series: activeSeries },
              })
            "
            >查看</el-button
          ><el-button
            v-if="row.can_continue"
            link
            type="primary"
            :loading="continuingId === row.id"
            :disabled="!!continuingId"
            @click="run(() => continueMeeting(row))"
            >接续新建</el-button
          ><el-button
            v-if="
              row.can_request_edit && row.published_version && row.series_id
            "
            link
            @click="router.push('/meetings/' + row.id)"
            >申请接续权限</el-button
          ><el-button
            v-if="row.can_delete"
            link
            type="danger"
            :loading="deletingMeetingId === row.id"
            :disabled="!!deletingMeetingId"
            @click="run(() => removeMeeting(row))"
            >删除</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <p class="meeting-scroll-hint">左右滑动查看完整信息</p>
    <div v-loading="busy" class="meeting-current-mobile">
      <ul v-if="rows.length" class="meeting-history-list">
        <li v-for="row in rows" :key="row.id">
          <div class="meeting-mobile-heading">
            <span class="muted small">{{ row.meeting_date }}</span>
            <el-tag
              :type="
                !row.published_version || (row.is_revising ?? row.has_draft)
                  ? 'info'
                  : 'success'
              "
            >
              {{ row.published_version ? "已发布" : "草稿"
              }}{{
                (row.is_revising ?? row.has_draft) && row.published_version
                  ? " · 修订中"
                  : ""
              }}
            </el-tag>
          </div>
          <RouterLink
            class="table-link"
            :to="{
              path: '/meetings/' + row.id,
              query: { series: activeSeries },
            }"
            >{{ row.title }}</RouterLink
          >
          <el-tag v-if="row.pending_edit_count" type="warning" size="small"
            >待审批 {{ row.pending_edit_count }}</el-tag
          >
          <dl class="meeting-mobile-meta">
            <div>
              <dt>所属部门</dt>
              <dd>
                <span :title="departmentPath(row.department_id)">{{
                  meetingDepartment(row)
                }}</span>
              </dd>
            </div>
            <div>
              <dt>会议类型</dt>
              <dd>
                {{ kinds[row.kind] || "—" }} ·
                {{ row.historical ? "历史导入" : "在线会议" }}
              </dd>
            </div>
            <div>
              <dt>主持人</dt>
              <dd>{{ row.host_name || "—" }}</dd>
            </div>
            <div>
              <dt>工作事项</dt>
              <dd>{{ row.item_count }} 项 · 达成率 {{ rate(row.rate) }}</dd>
            </div>
          </dl>
          <div class="actions meeting-mobile-actions">
            <el-button
              link
              type="primary"
              @click="
                router.push({
                  path: '/meetings/' + row.id,
                  query: { series: activeSeries },
                })
              "
              >查看</el-button
            >
            <el-button
              v-if="row.can_continue"
              link
              type="primary"
              :loading="continuingId === row.id"
              :disabled="!!continuingId"
              @click="run(() => continueMeeting(row))"
              >接续新建</el-button
            >
            <el-button
              v-if="
                row.can_request_edit && row.published_version && row.series_id
              "
              link
              @click="router.push('/meetings/' + row.id)"
              >申请接续权限</el-button
            >
            <el-button
              v-if="row.can_delete"
              link
              type="danger"
              :loading="deletingMeetingId === row.id"
              :disabled="!!deletingMeetingId"
              @click="run(() => removeMeeting(row))"
              >删除</el-button
            >
          </div>
        </li>
      </ul>
      <p v-else class="meeting-history-empty muted">
        {{
          loadError
            ? "加载失败，请重试"
            : hasActiveFilters
              ? "未找到符合条件的会议"
              : "暂无当前会议，可查看下方历史会议或新建会议。"
        }}
      </p>
    </div>
    <el-pagination
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="prev,pager,next,total"
      @current-change="run(load)"
    />
  </section>
  <section
    v-if="activeSeries"
    class="panel meeting-history-panel meeting-data-panel"
  >
    <button
      type="button"
      class="meeting-history-toggle"
      aria-label="历史会议"
      :aria-expanded="historyExpanded"
      aria-controls="series-history-content"
      @click="toggleHistory"
    >
      <span class="meeting-history-copy">
        <strong
          >历史会议
          <span v-if="historyTotal" class="count">{{
            historyTotal
          }}</span></strong
        >
      </span>
      <span class="meeting-history-indicator">
        <span>{{ historyExpanded ? "收起" : "展开" }}</span>
        <el-icon
          ><ArrowDown v-if="!historyExpanded" /><ArrowUp v-else
        /></el-icon>
      </span>
    </button>
    <div
      v-if="historyExpanded"
      id="series-history-content"
      class="meeting-history-body"
      role="region"
      aria-label="本系列历史会议"
      :aria-busy="historyBusy"
    >
      <el-alert v-if="historyError" type="error" :closable="false" show-icon>
        <template #title>历史会议加载失败：{{ historyError }}</template>
        <el-button size="small" @click="run(loadHistory)"
          >重试加载历史会议</el-button
        >
      </el-alert>
      <template v-else>
        <el-table
          v-loading="historyBusy"
          :data="historyRows"
          row-key="id"
          class="meeting-history-table"
          scrollbar-always-on
          empty-text="本系列暂无历史会议"
        >
          <el-table-column prop="meeting_date" label="会议日期" width="104" />
          <el-table-column label="会议名称" min-width="176">
            <template #default="{ row }">
              <RouterLink
                class="table-link"
                :to="{
                  path: '/meetings/' + row.id,
                  query: { series: activeSeries },
                }"
                >{{ row.title }}</RouterLink
              >
              <div class="muted small">
                {{ row.historical ? "历史导入" : "在线会议" }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="所属部门" min-width="96">
            <template #default="{ row }"
              ><span
                :title="
                  row.department_name || departmentPath(row.department_id)
                "
                >{{ meetingDepartment(row) }}</span
              ></template
            >
          </el-table-column>
          <el-table-column label="会议类型" min-width="84">
            <template #default="{ row }">{{ kinds[row.kind] || "—" }}</template>
          </el-table-column>
          <el-table-column prop="host_name" label="主持人" min-width="92" />
          <el-table-column prop="item_count" label="工作事项" min-width="72" />
          <el-table-column label="达成率" min-width="80"
            ><template #default="{ row }">{{
              rate(row.rate)
            }}</template></el-table-column
          >
          <el-table-column label="状态" width="128"
            ><template #default="{ row }"
              ><el-tag
                :type="
                  !row.published_version
                    ? 'info'
                    : (row.is_revising ?? row.has_draft)
                      ? 'warning'
                      : 'success'
                "
                size="small"
                >{{
                  !row.published_version
                    ? "草稿"
                    : (row.is_revising ?? row.has_draft)
                      ? "修订中"
                      : "已发布"
                }}</el-tag
              ></template
            ></el-table-column
          >
          <el-table-column
            label="操作"
            width="160"
            fixed="right"
            class-name="meeting-row-actions"
            ><template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="
                  router.push({
                    path: '/meetings/' + row.id,
                    query: { series: activeSeries },
                  })
                "
                >查看</el-button
              >
              <el-button
                v-if="row.can_continue"
                link
                type="primary"
                :loading="continuingId === row.id"
                :disabled="!!continuingId"
                @click="run(() => continueMeeting(row))"
                >接续新建</el-button
              >
              <el-button
                v-if="row.can_delete"
                link
                type="danger"
                :loading="deletingMeetingId === row.id"
                :disabled="!!deletingMeetingId"
                @click="run(() => removeMeeting(row, true))"
                >删除</el-button
              >
            </template></el-table-column
          >
        </el-table>
        <p class="meeting-scroll-hint">左右滑动查看完整信息</p>
        <div v-loading="historyBusy" class="meeting-history-mobile">
          <ul v-if="historyRows.length" class="meeting-history-list">
            <li v-for="row in historyRows" :key="row.id">
              <RouterLink
                class="table-link"
                :to="{
                  path: '/meetings/' + row.id,
                  query: { series: activeSeries },
                }"
                >{{ row.title }}</RouterLink
              >
              <p class="muted small">
                {{ row.meeting_date }} · {{ row.host_name }} ·
                {{ row.item_count }} 项工作
              </p>
              <div class="actions">
                <el-tag
                  :type="
                    !row.published_version
                      ? 'info'
                      : (row.is_revising ?? row.has_draft)
                        ? 'warning'
                        : 'success'
                  "
                  size="small"
                  >{{
                    !row.published_version
                      ? "草稿"
                      : (row.is_revising ?? row.has_draft)
                        ? "修订中"
                        : "已发布"
                  }}</el-tag
                >
                <el-button
                  v-if="row.can_continue"
                  link
                  type="primary"
                  :loading="continuingId === row.id"
                  :disabled="!!continuingId"
                  @click="run(() => continueMeeting(row))"
                  >接续新建</el-button
                >
                <el-button
                  v-if="row.can_delete"
                  link
                  type="danger"
                  :loading="deletingMeetingId === row.id"
                  :disabled="!!deletingMeetingId"
                  @click="run(() => removeMeeting(row, true))"
                  >删除</el-button
                >
              </div>
            </li>
          </ul>
          <p v-else class="meeting-history-empty muted">
            {{ historyBusy ? "正在加载历史会议…" : "本系列暂无历史会议" }}
          </p>
        </div>
        <el-pagination
          v-if="historyTotal > 20"
          v-model:current-page="historyPage"
          :page-size="20"
          :total="historyTotal"
          layout="prev, pager, next, total"
          @current-change="run(loadHistory)"
        />
      </template>
    </div>
  </section>
  <el-dialog
    v-model="renameVisible"
    title="编辑会议系列名称"
    width="min(560px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!renameSaving"
    :show-close="!renameSaving"
  >
    <el-form label-position="top" @submit.prevent="saveSeriesName">
      <p class="series-rename-current">当前系列：{{ renameSelected?.name }}</p>
      <el-form-item label="新系列名称" required>
        <el-input
          v-model="renameName"
          aria-label="新系列名称"
          maxlength="250"
          placeholder="请输入会议系列名称"
          :disabled="!renameSelected?.can_rename || renameSaving"
          @keydown.enter.prevent="saveSeriesName"
        />
      </el-form-item>
      <p class="muted small">
        仅修改系列名称，系列内各场会议名称和已发布文件保留。
      </p>
      <el-alert v-if="renameError" type="error" :closable="false" show-icon>
        <template #title>{{ renameError }}</template>
      </el-alert>
    </el-form>
    <template #footer>
      <el-button :disabled="renameSaving" @click="renameVisible = false"
        >取消</el-button
      >
      <el-button
        type="primary"
        :loading="renameSaving"
        :disabled="
          !renameSelected?.can_rename ||
          !renameName.trim() ||
          renameName.trim() === renameSelected.name
        "
        @click="saveSeriesName"
        >保存名称</el-button
      >
    </template>
  </el-dialog>
  <el-dialog
    v-model="seriesDeleteVisible"
    title="删除会议系列 · 影响范围"
    width="min(680px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!deletingSeries"
    :show-close="!deletingSeries"
  >
    <template v-if="seriesImpact">
      <el-alert type="warning" :closable="false" show-icon>
        <template #title
          >将删除“{{
            seriesImpact.name
          }}”整个系列，包含筛选条件外的全部会议。</template
        >
      </el-alert>
      <p class="series-delete-summary">
        {{ seriesImpact.meeting_count }} 场会议、{{
          seriesImpact.task_count
        }}
        条正式任务、 {{ seriesImpact.assessment_count }} 条会议考核记录、{{
          seriesImpact.attachment_count
        }}
        个关联附件将被删除。 导入原文件和操作审计保留，删除后无法在页面恢复。
      </p>
      <p v-if="seriesImpact.retained_task_count" class="muted">
        另有
        {{ seriesImpact.retained_task_count }}
        条系列外任务将保留，仅清除系列归属。
      </p>
      <el-alert
        v-if="seriesImpact.active_native_todos"
        type="error"
        :closable="false"
      >
        <template #title
          >存在
          {{ seriesImpact.active_native_todos }}
          个未结束的企业微信待办，请先完成或处理同步。</template
        >
      </el-alert>
      <el-alert
        v-if="seriesImpact.blocking_meetings.length"
        type="error"
        :closable="false"
      >
        <template #title
          >系列外会议仍引用这里的事项，请先处理引用：{{
            seriesImpact.blocking_meetings
              .map((item: any) => item.title)
              .join("、")
          }}</template
        >
      </el-alert>
      <el-table
        :data="seriesImpact.meetings"
        max-height="320"
        empty-text="此系列没有会议"
      >
        <el-table-column prop="title" label="将删除的会议" min-width="180" />
        <el-table-column prop="meeting_date" label="会议日期" width="120" />
      </el-table>
    </template>
    <template #footer>
      <el-button :disabled="deletingSeries" @click="seriesDeleteVisible = false"
        >取消</el-button
      >
      <el-button
        type="danger"
        :disabled="!seriesImpact?.can_delete"
        :loading="deletingSeries"
        @click="run(removeSeries)"
        >继续删除并确认名称</el-button
      >
    </template>
  </el-dialog>
</template>
<style scoped>
.series-detail-filters .filter-row {
  flex-wrap: nowrap;
}
.series-detail-filters .filter-row > :deep(.el-input) {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 560px;
}
.series-detail-filters .filter-row > :deep(.el-button) {
  flex: 0 0 auto;
}
.meeting-history-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
  min-height: 76px;
  padding: 16px 20px;
  border: 0;
  background: transparent;
  color: var(--el-text-color-primary);
  text-align: left;
}
.meeting-history-toggle:hover {
  background: var(--el-color-primary-light-9);
}
.meeting-history-toggle:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -3px;
}
.meeting-history-copy {
  display: grid;
  gap: 6px;
}
.meeting-history-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-color-primary);
  font-size: 13px;
}
.meeting-history-body {
  border-top: 1px solid var(--line);
}
.meeting-history-mobile,
.meeting-current-mobile {
  display: none;
}
.meeting-history-body > .el-pagination {
  padding: 16px 20px;
}
@media (max-width: 767px) {
  .meeting-history-table,
  .meeting-current-table {
    display: none;
  }
  .meeting-history-mobile,
  .meeting-current-mobile {
    display: block;
  }
  .meeting-history-toggle {
    padding: 16px;
  }
  .meeting-history-list {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .meeting-history-list li {
    padding: 16px;
    border-bottom: 1px solid var(--line);
    overflow-wrap: anywhere;
  }
  .meeting-history-list p {
    margin-block: 8px;
  }
  .meeting-history-empty {
    padding: 24px 16px;
    margin: 0;
  }
}
.meeting-mobile-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.meeting-mobile-meta {
  display: grid;
  gap: 7px;
  margin: 14px 0 6px;
  font-size: 13px;
}
.meeting-mobile-meta > div {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 10px;
}
.meeting-mobile-meta dt {
  color: var(--muted);
}
.meeting-mobile-meta dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.meeting-mobile-actions {
  justify-content: flex-end;
  gap: 8px 16px;
}
.meeting-mobile-actions .el-button {
  margin-left: 0;
  min-height: 44px;
}
.meeting-current-table :deep(.meeting-row-actions .cell),
.meeting-history-table :deep(.meeting-row-actions .cell) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.meeting-current-table :deep(.meeting-row-actions .el-button + .el-button),
.meeting-history-table :deep(.meeting-row-actions .el-button + .el-button) {
  margin-left: 0;
}
.meeting-data-panel {
  container-type: inline-size;
}
.meeting-current-table :deep(.cell),
.meeting-history-table :deep(.cell) {
  padding-inline: 12px;
  line-height: 1.7;
}
.meeting-current-table :deep(th:first-child .cell),
.meeting-current-table :deep(td:first-child .cell),
.meeting-history-table :deep(th:first-child .cell),
.meeting-history-table :deep(td:first-child .cell) {
  padding-left: 16px;
}
.meeting-current-table :deep(td.el-table__cell),
.meeting-history-table :deep(td.el-table__cell) {
  padding-block: 20px;
}
.meeting-current-table :deep(.meeting-row-actions .el-button),
.meeting-history-table :deep(.meeting-row-actions .el-button) {
  white-space: nowrap;
  min-height: 32px;
  padding-block: 4px;
  padding-inline: 0;
}
.meeting-scroll-hint {
  display: none;
  margin: 0;
  padding: 10px 16px;
  color: var(--muted);
  font-size: 12px;
}
@container (max-width: 991px) {
  .meeting-scroll-hint {
    display: block;
  }
}
@media (max-width: 767px) {
  .meeting-scroll-hint {
    display: none;
  }
}
.series-mobile-entry {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.series-mobile-entry > .series-mobile-row {
  flex: 1;
  min-width: 0;
}
.series-mobile-actions,
.series-archive-table :deep(.series-row-actions .cell) {
  display: flex;
  align-items: center;
  gap: 16px;
}
.series-mobile-actions .el-button + .el-button,
.series-archive-table :deep(.series-row-actions .el-button + .el-button) {
  margin-left: 0;
}
.series-rename-current {
  overflow-wrap: anywhere;
}
@media (max-width: 767px) {
  .series-mobile-entry {
    gap: 0;
    border-bottom: 1px solid var(--line);
  }
  .series-mobile-entry > .series-mobile-row {
    border-bottom: 0;
  }
  .series-mobile-entry:last-child {
    border-bottom: 0;
  }
  .series-mobile-actions {
    width: 100%;
    justify-content: flex-end;
    padding: 0 16px 8px;
  }
  .series-mobile-actions .el-button {
    min-height: 44px;
  }
}
.series-delete-summary {
  margin-block: 16px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
</style>
