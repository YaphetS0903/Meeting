<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowRight, Filter } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  api,
  state,
  query,
  run,
  rate,
  kinds,
  statuses,
  departmentPath,
} from "../api";
import PeriodFilter from "../components/PeriodFilter.vue";
const route = useRoute();
const router = useRouter();
const rows = ref<any[]>([]),
  seriesRows = ref<any[]>([]),
  seriesMeetingTotal = ref(0),
  total = ref(0),
  page = ref(1),
  busy = ref(false),
  loadError = ref(""),
  continuingId = ref(""),
  creatingSeries = ref(false),
  filters = ref<any>({}),
  search = ref(""),
  department = ref(""),
  publication = ref(""),
  scope = ref("all"),
  kind = ref(""),
  status = ref(""),
  filtersExpanded = ref(false);
const activeSeries = computed(() =>
  typeof route.query.series === "string" ? route.query.series : "",
);
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
      department.value ||
      publication.value ||
      kind.value ||
      status.value ||
      scope.value !== "all"
    ),
);
let generation = 0;
async function load() {
  const g = ++generation;
  busy.value = true;
  loadError.value = "";
  try {
    const common = {
      ...filters.value,
      q: search.value,
      department_id: department.value,
      kind: kind.value,
      scope: scope.value,
    };
    const r = activeSeries.value
      ? await api(
          "/meetings?" +
            query({
              ...common,
              series_key: activeSeries.value,
              publication: publication.value,
              status: status.value,
              offset: (page.value - 1) * 20,
              limit: 20,
            }),
        )
      : await api(
          "/meeting-series?" +
            query({
              ...common,
              publication: publication.value,
              status: status.value,
            }),
        );
    if (g === generation) {
      if (activeSeries.value) rows.value = r.items;
      else {
        seriesRows.value = r.items;
        seriesMeetingTotal.value = r.meeting_total;
      }
      total.value = r.total;
    }
  } catch (error: any) {
    if (g === generation) {
      loadError.value = error.message || "服务暂时不可用";
      rows.value = [];
      seriesRows.value = [];
      total.value = 0;
      seriesMeetingTotal.value = 0;
    }
    throw error;
  } finally {
    if (g === generation) busy.value = false;
  }
}
function openSeries(row: any) {
  search.value = "";
  router.push({ path: "/meetings", query: { series: row.key } });
}
function backToSeries() {
  search.value = "";
  router.push({ path: "/meetings" });
}
function newMeeting() {
  router.push({
    path: "/meetings/new",
    query: activeSeries.value.startsWith("series:")
      ? { series: activeSeries.value }
      : {},
  });
}
async function createSeries() {
  const { value } = await ElMessageBox.prompt(
    "先建立会议系列，之后可进入系列添加会议",
    "新建会议系列",
    { inputValidator: (name) => !!name?.trim() || "请输入会议系列名称" },
  );
  creatingSeries.value = true;
  try {
    const name = value.trim();
    const existed = state.directory.series.some(
      (item: any) => item.name === name,
    );
    const series = await api("/series", "POST", { name });
    if (!state.directory.series.some((item: any) => item.id === series.id))
      state.directory.series.push(series);
    search.value = "";
    if (hasActiveFilters.value) {
      await router.push({
        path: "/meetings",
        query: { series: `series:${series.id}` },
      });
    } else {
      await load();
    }
    ElMessage.success(
      existed
        ? "会议系列已存在，可以进入添加会议"
        : "会议系列已建立，点击它可添加会议",
    );
  } finally {
    creatingSeries.value = false;
  }
}
async function continueMeeting(row: any) {
  if (continuingId.value) return;
  continuingId.value = row.id;
  try {
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
async function removeMeeting(row: any) {
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
  ElMessage.success("会议已删除");
  if (rows.value.length === 1 && page.value > 1) page.value -= 1;
  await load();
}
watch(activeSeries, () => {
  page.value = 1;
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
      <p class="eyebrow">MEETINGS</p>
      <h1>会议纪要与任务单</h1>
      <p class="muted">
        {{ activeSeries ? activeSeriesName : "会议归档" }}
      </p>
    </div>
    <div class="actions">
      <el-button
        v-if="state.me?.is_admin"
        size="large"
        @click="$router.push('/validation?upload=1')"
        >导入历史会议</el-button
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
    :class="{ 'filters-expanded': filtersExpanded }"
  >
    <button
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
      <el-select
        v-if="state.me?.is_admin"
        v-model="scope"
        aria-label="会议范围"
        @change="changed()"
        ><el-option value="all" label="全部会议" /><el-option
          value="related"
          label="与我或我部门相关"
      /></el-select>
      <el-select
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
        v-model="kind"
        placeholder="全部会议类型"
        clearable
        aria-label="会议类型"
        @change="changed()"
        ><el-option
          v-for="(label, key) in kinds"
          :key="key"
          :label="label"
          :value="key" /></el-select
      ><el-select
        v-model="publication"
        placeholder="全部发布状态"
        clearable
        aria-label="发布状态"
        @change="changed()"
      >
        <el-option label="草稿" value="draft" /><el-option
          label="已发布"
          value="published"
        /><el-option label="修订中" value="revising" /> </el-select
      ><el-select
        v-model="status"
        placeholder="上期回顾状态"
        clearable
        aria-label="上期回顾状态"
        @change="changed()"
      >
        <el-option
          v-for="item in statuses"
          :key="item"
          :label="item"
          :value="item"
        /> </el-select
      ><el-input
        v-model="search"
        :placeholder="activeSeries ? '搜索会议名称' : '搜索会议系列或会议名称'"
        clearable
        :aria-label="activeSeries ? '搜索会议' : '搜索会议系列或会议名称'"
        @keyup.enter="changed()"
        @clear="changed()"
      /><el-button @click="changed()">查询</el-button>
    </div>
  </section>
  <el-alert v-if="loadError" type="error" :closable="false" show-icon>
    <template #title>会议列表加载失败：{{ loadError }}</template>
    <el-button size="small" @click="run(load)">重试加载</el-button>
  </el-alert>
  <section v-if="!activeSeries" class="panel">
    <div class="panel-heading">
      <h2>
        会议归档 · 会议系列 <span class="count">{{ total }}</span>
      </h2>
      <div class="actions">
        <span class="muted small">共 {{ seriesMeetingTotal }} 场会议</span>
        <el-button
          type="primary"
          :loading="creatingSeries"
          @click="run(createSeries)"
          >＋ 新建会议系列</el-button
        >
      </div>
    </div>
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
      <el-table-column prop="meeting_count" label="会议数" width="100" />
      <el-table-column label="最近会议" width="135">
        <template #default="{ row }">{{ row.latest_date || "—" }}</template>
      </el-table-column>
      <el-table-column label="" width="55">
        <template #default
          ><el-icon><ArrowRight /></el-icon
        ></template>
      </el-table-column>
    </el-table>
    <div v-loading="busy" class="series-mobile-list">
      <button
        v-for="row in seriesRows"
        :key="row.key"
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
            >{{ row.meeting_count }} 场会议 · 最近
            {{ row.latest_date || "—" }}</small
          >
        </span>
        <el-icon><ArrowRight /></el-icon>
      </button>
      <p v-if="!busy && !seriesRows.length" class="series-mobile-empty">
        {{ loadError ? "加载失败，请重试" : "没有符合筛选条件的会议系列" }}
      </p>
    </div>
  </section>
  <section v-else class="panel">
    <div class="panel-heading">
      <h2>
        {{ activeSeriesName }} <span class="count">{{ total }}</span>
      </h2>
      <span class="muted small">达成率仅计算上期回顾</span>
    </div>
    <el-table
      v-loading="busy"
      :data="rows"
      row-key="id"
      :empty-text="
        loadError
          ? '加载失败，请重试'
          : '这个系列还没有会议，点击右上角「新建会议」添加。'
      "
      ><el-table-column label="会议名称" min-width="260"
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
            {{ kinds[row.kind] }} ·
            {{ row.historical ? "历史导入" : "在线会议" }}
            <el-tag v-if="row.pending_edit_count" type="warning" size="small"
              >待审批 {{ row.pending_edit_count }}</el-tag
            >
          </div></template
        ></el-table-column
      ><el-table-column label="所属部门" min-width="180"
        ><template #default="{ row }">{{
          departmentPath(row.department_id)
        }}</template></el-table-column
      ><el-table-column label="会议类型" width="110"
        ><template #default="{ row }">{{
          kinds[row.kind] || "—"
        }}</template></el-table-column
      ><el-table-column
        prop="meeting_date"
        label="会议日期"
        width="125"
      /><el-table-column
        prop="host_name"
        label="主持人"
        width="100"
      /><el-table-column
        prop="item_count"
        label="工作事项"
        width="95"
      /><el-table-column label="达成率" width="105"
        ><template #default="{ row }"
          ><strong>{{ rate(row.rate) }}</strong></template
        ></el-table-column
      ><el-table-column label="版本" width="140"
        ><template #default="{ row }"
          ><el-tag :type="row.has_draft ? 'info' : 'success'"
            >{{
              row.published_version
                ? "已发布 V" + row.published_version
                : "草稿"
            }}{{
              row.has_draft && row.published_version ? " · 修订中" : ""
            }}</el-tag
          ></template
        ></el-table-column
      ><el-table-column label="操作" width="190"
        ><template #default="{ row }"
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
            @click="run(() => removeMeeting(row))"
            >删除</el-button
          ><span
            v-if="
              !row.can_continue &&
              !row.can_delete &&
              !(row.can_request_edit && row.published_version && row.series_id)
            "
            >—</span
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
