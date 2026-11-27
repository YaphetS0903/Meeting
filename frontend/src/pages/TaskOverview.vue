<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api, departmentPath, query, run, state, statuses } from "../api";
import StatusTag from "../components/StatusTag.vue";
import { isEmployeeAccount, withDirectoryDetails } from "../people";

const route = useRoute();
const options = ref<any>(null),
  data = ref<any>(null);
const loading = ref(false),
  error = ref(""),
  page = ref(1);
const q = ref(""),
  person = ref(""),
  department = ref(""),
  series = ref(""),
  source = ref(
    route.query.source === "direct" || route.query.source === "meeting"
      ? route.query.source
      : "",
  ),
  status = ref("");
const dates = ref<[string, string] | null>(null),
  noDeadline = ref(false),
  issuedByMe = ref(route.query.issued_by_me === "1"),
  moreFilters = ref(false);
const advancedFilters = computed(() => {
  const labels: string[] = [];
  if (source.value)
    labels.push(`来源：${source.value === "direct" ? "直接派发" : "会议任务"}`);
  if (series.value) {
    const selected = options.value?.series?.find(
      (item: any) => item.id === series.value,
    );
    labels.push(`会议系列：${selected?.name || series.value}`);
  }
  if (noDeadline.value) labels.push("未设期限");
  else if (dates.value)
    labels.push(`截止日期：${dates.value[0]} 至 ${dates.value[1]}`);
  if (issuedByMe.value) labels.push("我派发的");
  return labels;
});
const rows = computed(() => data.value?.items || []);
const personOptions = computed(() =>
  withDirectoryDetails(
    options.value?.users || [],
    state.directory.users,
  ).filter(isEmployeeAccount),
);
const departmentNames = (row: any) =>
  row.department_names?.join("、") || "未分配部门";
const sourceLabel = (row: any) =>
  [row.source_meeting_date, row.source_title].filter(Boolean).join(" · ");
const metrics = computed(() => [
  ["任务总数", data.value?.summary.total || 0],
  ["未完成", data.value?.summary.overdue || 0],
  ["到期未验证", data.value?.summary.unverified_due || 0],
  ["待验证", data.value?.summary.pending || 0],
  ["已关闭", data.value?.summary.closed || 0],
  ["已撤回", data.value?.summary.withdrawn || 0],
]);
let generation = 0;
async function load() {
  const active = ++generation;
  loading.value = true;
  error.value = "";
  data.value = null;
  const params = {
    q: q.value,
    person_id: person.value,
    department_id: department.value,
    source: source.value,
    series_id: series.value,
    status: status.value,
    no_deadline: noDeadline.value,
    issued_by_me: issuedByMe.value,
    start: noDeadline.value ? null : dates.value?.[0],
    end: noDeadline.value ? null : dates.value?.[1],
    offset: (page.value - 1) * 20,
    limit: 20,
  };
  try {
    const [scope, result] = await Promise.all([
      options.value
        ? Promise.resolve(options.value)
        : api("/task-overview/options"),
      api("/task-overview?" + query(params)),
    ]);
    if (active !== generation) return;
    options.value = scope;
    data.value = result;
  } catch (e: any) {
    if (active === generation) error.value = chineseError(e, "服务暂时不可用");
    throw e;
  } finally {
    if (active === generation) loading.value = false;
  }
}
function filterChanged() {
  page.value = 1;
  run(load);
}
function seriesChanged() {
  if (series.value) source.value = "meeting";
  filterChanged();
}
function sourceChanged() {
  if (source.value === "direct") series.value = "";
  filterChanged();
}
function clearFilters() {
  q.value =
    person.value =
    department.value =
    source.value =
    status.value =
    series.value =
      "";
  dates.value = null;
  noDeadline.value = false;
  issuedByMe.value = false;
  filterChanged();
}
onMounted(() => run(load));
</script>

<template>
  <section class="page-heading">
    <div>
      <h1>任务总览</h1>
    </div>
    <el-button @click="run(load)">刷新进展</el-button>
  </section>
  <section class="panel filters">
    <div class="filter-row overview-basic-filters">
      <el-input
        v-model="q"
        clearable
        placeholder="搜索任务内容"
        aria-label="搜索任务"
        @keyup.enter="filterChanged"
        @clear="filterChanged"
      />
      <el-select
        v-model="department"
        clearable
        filterable
        placeholder="全部部门"
        aria-label="筛选部门（包含子部门）"
        title="包含所选部门及子部门的会议任务，以及该部门人员负责的任务"
        @change="filterChanged"
      >
        <el-option
          v-for="d in options?.departments || []"
          :key="d.id"
          :value="d.id"
          :label="departmentPath(d.id)"
        />
      </el-select>
      <el-select
        v-model="person"
        clearable
        filterable
        placeholder="全部责任人"
        aria-label="筛选责任人"
        @change="filterChanged"
      >
        <el-option
          v-for="p in personOptions"
          :key="p.id"
          :value="p.id"
          :label="`${p.name}${p.active ? '' : '（已停用）'} · ${p.department_id ? departmentPath(p.department_id) : '未分配部门'}`"
        />
      </el-select>
      <el-select
        v-model="status"
        clearable
        placeholder="全部状态"
        aria-label="筛选进展"
        @change="filterChanged"
      >
        <el-option
          v-for="s in [
            ...statuses,
            '到期未验证',
            '未到期验证',
            '待验证',
            '已撤回',
          ]"
          :key="s"
          :value="s"
          :label="s"
        />
      </el-select>
      <div class="actions overview-filter-actions">
        <el-button
          :aria-expanded="moreFilters"
          aria-controls="task-overview-more-filters"
          @click="moreFilters = !moreFilters"
          >{{ moreFilters ? "收起筛选" : "更多筛选"
          }}{{
            advancedFilters.length ? `（${advancedFilters.length}）` : ""
          }}</el-button
        >
        <el-button type="primary" @click="filterChanged">查询</el-button>
        <el-button @click="clearFilters">清空筛选</el-button>
      </div>
    </div>
    <div
      v-if="advancedFilters.length"
      class="overview-filter-summary"
      aria-live="polite"
    >
      <span class="muted small">已启用</span>
      <el-tag v-for="label in advancedFilters" :key="label" size="small">{{
        label
      }}</el-tag>
    </div>
    <div
      v-show="moreFilters"
      id="task-overview-more-filters"
      class="filter-row overview-more-filters"
    >
      <el-select
        v-model="source"
        clearable
        placeholder="全部来源"
        aria-label="筛选任务来源"
        @change="sourceChanged"
      >
        <el-option value="meeting" label="会议任务" /><el-option
          value="direct"
          label="直接派发"
        />
      </el-select>
      <el-select
        v-model="series"
        class="overview-series-filter"
        clearable
        filterable
        placeholder="全部会议系列"
        aria-label="筛选会议系列"
        no-data-text="当前范围暂无会议系列"
        no-match-text="没有匹配的会议系列"
        @change="seriesChanged"
      >
        <el-option
          v-for="item in options?.series || []"
          :key="item.id"
          :value="item.id"
          :label="item.name"
        />
      </el-select>
      <el-date-picker
        v-model="dates"
        type="daterange"
        value-format="YYYY-MM-DD"
        start-placeholder="截止日期从"
        end-placeholder="截止日期至"
        :disabled="noDeadline"
        aria-label="筛选截止日期范围"
        @change="filterChanged"
      />
      <el-checkbox v-model="noDeadline" @change="filterChanged"
        >只看未设期限</el-checkbox
      >
      <el-checkbox v-model="issuedByMe" @change="filterChanged"
        >只看我派发</el-checkbox
      >
    </div>
  </section>
  <el-alert v-if="error" type="error" :closable="false" show-icon>
    <template #title>任务进展加载失败：{{ error }}</template>
    <el-button size="small" @click="run(load)">重试加载</el-button>
  </el-alert>
  <div v-if="data" class="stat-grid overview-stats">
    <article
      v-for="[label, count] in metrics"
      :key="String(label)"
      class="stat"
    >
      <span>{{ label }}</span
      ><strong>{{ count }}</strong>
    </article>
  </div>
  <section v-loading="loading" class="panel overview-results">
    <div class="panel-heading">
      <h2>
        任务进展 <span class="count">{{ data?.total || 0 }}</span>
      </h2>
    </div>
    <el-table
      class="overview-desktop"
      :data="rows"
      :empty-text="error ? '加载失败，请重试' : '当前筛选范围没有任务'"
    >
      <el-table-column label="所属部门" min-width="130"
        ><template #default="{ row }">{{
          departmentNames(row)
        }}</template></el-table-column
      >
      <el-table-column label="截止日期" width="120" class-name="overview-date"
        ><template #default="{ row }">{{
          row.due_date || "未设期限"
        }}</template></el-table-column
      >
      <el-table-column label="责任人" min-width="140"
        ><template #default="{ row }">{{
          row.owner_names.join("、")
        }}</template></el-table-column
      >
      <el-table-column label="任务内容" min-width="240"
        ><template #default="{ row }"
          ><RouterLink
            class="table-link"
            :to="{ path: '/tasks', query: { id: row.id } }"
            >{{ row.content }}</RouterLink
          >
          <p class="muted small">
            {{ sourceLabel(row) }}
          </p></template
        ></el-table-column
      >
      <el-table-column label="进展" width="110"
        ><template #default="{ row }"
          ><el-tag v-if="row.status === '已撤回'" type="info">已撤回</el-tag
          ><el-tag v-else-if="row.pending" type="warning">待验证</el-tag
          ><el-tag
            v-else-if="!row.due_date && row.status === '未到期'"
            type="info"
            >未设期限</el-tag
          ><StatusTag v-else :value="row.status" /></template
      ></el-table-column>
      <el-table-column prop="verifier_name" label="验证人" width="100" />
      <el-table-column label="派发人" width="100"
        ><template #default="{ row }">{{
          row.issuer_name || "会议指派"
        }}</template></el-table-column
      >
    </el-table>
    <div class="overview-mobile">
      <RouterLink
        v-for="row in rows"
        :key="row.id"
        class="overview-mobile-task"
        :to="{ path: '/tasks', query: { id: row.id } }"
      >
        <div class="task-card-top">
          <span class="muted small">{{ departmentNames(row) }}</span
          ><el-tag v-if="row.status === '已撤回'" type="info">已撤回</el-tag
          ><el-tag v-else-if="row.pending" type="warning">待验证</el-tag
          ><el-tag
            v-else-if="!row.due_date && row.status === '未到期'"
            type="info"
            >未设期限</el-tag
          ><StatusTag v-else :value="row.status" />
        </div>
        <p class="overview-mobile-date">
          截止日期：{{ row.due_date || "未设期限" }}
        </p>
        <p class="muted small">责任人：{{ row.owner_names.join("、") }}</p>
        <strong>{{ row.content }}</strong>
        <p class="muted small">{{ sourceLabel(row) }}</p>
        <p class="muted small">
          验证人：{{ row.verifier_name }} · 派发人：{{
            row.issuer_name || "会议指派"
          }}
        </p>
      </RouterLink>
      <el-empty
        v-if="!rows.length && !loading"
        :description="error ? '加载失败，请重试' : '当前筛选范围没有任务'"
        :image-size="60"
      />
    </div>
    <p v-if="data" class="muted small overview-status-help">
      会议任务仅按已发布的评定结果计入未完成；尚未评定的到期任务单独列为到期未验证。
    </p>
    <el-pagination
      v-if="data?.total"
      v-model:current-page="page"
      :page-size="20"
      :total="data.total"
      layout="prev, pager, next, total"
      @current-change="run(load)"
    />
  </section>
</template>

<style scoped>
.overview-stats {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}
.overview-status-help {
  margin: 12px 16px;
}
.overview-basic-filters > .el-input {
  min-width: 200px;
}
.overview-basic-filters > .el-select {
  flex: 1 1 140px;
  width: 140px;
}
.overview-filter-actions {
  gap: 8px;
}
.overview-filter-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.overview-filter-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.overview-filter-summary :deep(.el-tag) {
  height: auto;
  white-space: normal;
  overflow-wrap: anywhere;
}
.overview-more-filters {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.overview-more-filters > .el-select {
  flex: 0 1 180px;
  width: 180px;
  min-width: 0;
}
.overview-more-filters > .overview-series-filter {
  flex: 1 1 240px;
  width: 240px;
}
.overview-more-filters :deep(.el-date-editor) {
  flex: 0 1 320px;
  width: 320px;
  min-width: 0;
  max-width: 100%;
}
.overview-mobile {
  display: none;
}
.overview-desktop :deep(.overview-date),
.overview-mobile-date {
  font-variant-numeric: tabular-nums;
}
.el-pagination {
  padding: 16px;
  justify-content: center;
}
@media (min-width: 768px) and (max-width: 1440px) {
  .overview-basic-filters {
    display: grid;
    grid-template-columns:
      minmax(150px, 1.4fr) repeat(3, minmax(110px, 1fr))
      auto;
    align-items: center;
  }
  .overview-basic-filters > .el-input,
  .overview-basic-filters > .el-select {
    width: 100%;
    min-width: 0;
  }
  .overview-filter-actions {
    gap: 6px;
    flex-wrap: nowrap;
  }
  .overview-filter-actions :deep(.el-button) {
    padding-inline: 10px;
  }
  .overview-status-help {
    margin: 10px 16px;
    font-size: 12px;
    line-height: 1.5;
  }
  .overview-desktop :deep(.el-table__cell) {
    padding-block: 7px;
  }
  .overview-results .panel-heading {
    min-height: 40px;
    padding-block: 7px;
  }
  .overview-stats .stat {
    min-height: 64px;
    padding-block: 6px;
  }
  .overview-desktop :deep(.table-link) {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .overview-desktop :deep(.muted.small) {
    margin: 4px 0 0;
  }
}
@media (min-width: 768px) and (max-width: 1440px) and (max-height: 700px) {
  .overview-desktop :deep(.table-link) {
    -webkit-line-clamp: 1;
  }
}
@media (min-width: 768px) and (max-width: 1000px) {
  .overview-basic-filters {
    grid-template-columns: minmax(0, 1fr) repeat(2, minmax(100px, 1fr));
  }
  .overview-filter-actions {
    grid-column: span 2;
    justify-content: flex-end;
  }
}
@media (max-width: 767px) {
  .overview-stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 767px) {
  .overview-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .overview-desktop {
    display: none;
  }
  .overview-mobile {
    display: block;
  }
  .overview-mobile-task {
    display: block;
    padding: 16px;
    border-top: 1px solid var(--line);
    color: var(--text);
    text-decoration: none;
    overflow-wrap: anywhere;
  }
  .overview-mobile-task strong {
    display: block;
    margin-bottom: 8px;
  }
  .overview-mobile-date {
    margin: 12px 0 4px;
  }
  .overview-more-filters > .el-select,
  .overview-more-filters :deep(.el-date-editor) {
    flex-basis: 100%;
    width: 100%;
  }
}
</style>
