<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { computed, onMounted, ref, useId } from "vue";
import { api } from "../api";
import { isEmployeeAccount } from "../people";
import {
  ArrowDown,
  ArrowUp,
  Filter,
  Refresh,
  Search,
} from "@element-plus/icons-vue";
import StatusTag from "../components/StatusTag.vue";

const { dialog = false } = defineProps<{ dialog?: boolean }>();
const emit = defineEmits<{ loaded: [rows: any[]]; close: [] }>();
const rows = ref<any[]>([]);
const options = ref<{
  can_assign: boolean;
  wecom_bound: boolean;
  native_todo_auto_confirm: boolean;
} | null>(null);
const loading = ref(false);
const loadError = ref("");
const search = ref("");
const filtersExpanded = ref(false);
const filterPanelId = useId();
const content = ref("");
const owner = ref("");
const dateStart = ref<string | null>(null);
const dateEnd = ref<string | null>(null);
const filterError = ref("");
const applied = ref({ search: "", content: "", owner: "", start: "", end: "" });
const hasAppliedFilters = computed(() =>
  Object.values(applied.value).some(Boolean),
);
const advancedFilterCount = computed(
  () =>
    Number(!!applied.value.content) +
    Number(!!applied.value.owner) +
    Number(!!(applied.value.start || applied.value.end)),
);
const ownerOptions = computed<string[]>(() =>
  [...new Set<string>(rows.value.flatMap((row) => row.owner_names))]
    .filter((name) => isEmployeeAccount({ name }))
    .sort((a, b) => a.localeCompare(b, "zh-CN")),
);
const recordsPage = ref(1);
const recordsPageSize = 20;
const filteredRows = computed(() =>
  rows.value.filter((row) => {
    const filters = applied.value;
    const matchesSearch =
      !filters.search ||
      `${row.content} ${row.code} ${row.owner_names.join(" ")}`
        .toLowerCase()
        .includes(filters.search.toLowerCase());
    return (
      matchesSearch &&
      (!filters.content ||
        row.content.toLowerCase().includes(filters.content.toLowerCase())) &&
      (!filters.owner || row.owner_names.includes(filters.owner)) &&
      (!filters.start || (!!row.due_date && row.due_date >= filters.start)) &&
      (!filters.end || (!!row.due_date && row.due_date <= filters.end))
    );
  }),
);
const pagedRows = computed(() =>
  filteredRows.value.slice(
    (recordsPage.value - 1) * recordsPageSize,
    recordsPage.value * recordsPageSize,
  ),
);
const openCount = computed(
  () => rows.value.filter((row) => !row.closed_at && !row.withdrawn_at).length,
);
function applyFilters() {
  if (dateStart.value && dateEnd.value && dateStart.value > dateEnd.value) {
    filterError.value = "开始日期不能晚于结束日期，请调整日期。";
    filtersExpanded.value = true;
    return;
  }
  filterError.value = "";
  applied.value = {
    search: search.value.trim(),
    content: content.value.trim(),
    owner: owner.value || "",
    start: dateStart.value || "",
    end: dateEnd.value || "",
  };
  recordsPage.value = 1;
}
function resetFilters() {
  search.value = content.value = owner.value = "";
  dateStart.value = dateEnd.value = null;
  applyFilters();
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  loadError.value = "";
  const [issued, scope] = await Promise.allSettled([
    api("/direct-assignments"),
    api("/direct-assignments/options"),
  ]);
  if (scope.status === "fulfilled") options.value = scope.value;
  if (issued.status === "fulfilled") {
    rows.value = issued.value;
    emit("loaded", rows.value);
  } else {
    rows.value = [];
    loadError.value = chineseError(
      issued.reason,
      "派发记录暂时无法读取，请稍后重试",
    );
  }
  recordsPage.value = 1;
  loading.value = false;
}
onMounted(load);
</script>
<template>
  <section
    class="dispatch-records"
    :class="{ 'is-dialog': dialog }"
    aria-label="派发记录"
  >
    <header class="dispatch-records-heading">
      <p role="status">
        共 {{ rows.length }} 项 · {{ openCount }} 项正在跟进<span
          v-if="hasAppliedFilters"
        >
          · 筛选到 {{ filteredRows.length }} 项</span
        >
      </p>
      <div class="dispatch-records-actions">
        <div class="dispatch-records-search">
          <el-input
            v-model="search"
            placeholder="搜索任务或责任人"
            clearable
            aria-label="搜索派发记录"
            @keyup.enter="applyFilters"
            @clear="applyFilters"
          />
          <el-button type="primary" :icon="Search" @click="applyFilters"
            >查询</el-button
          >
        </div>
        <el-button
          class="dispatch-filter-toggle"
          size="small"
          :icon="Filter"
          :aria-expanded="filtersExpanded"
          :aria-controls="filterPanelId"
          @click="filtersExpanded = !filtersExpanded"
          >筛选{{ advancedFilterCount ? `（${advancedFilterCount}）` : "" }}
          <el-icon
            ><ArrowUp v-if="filtersExpanded" /><ArrowDown v-else
          /></el-icon>
        </el-button>
        <el-button :icon="Refresh" :loading="loading" @click="load"
          >刷新</el-button
        >
      </div>
    </header>
    <el-form
      v-show="filtersExpanded"
      :id="filterPanelId"
      class="dispatch-records-filters"
      label-position="top"
      aria-label="派发记录筛选条件"
      @submit.prevent="applyFilters"
    >
      <div class="dispatch-filter-fields">
        <el-form-item label="任务内容" class="dispatch-filter-field">
          <el-input
            v-model="content"
            placeholder="输入任务内容关键词"
            aria-label="筛选任务内容"
            clearable
            @keyup.enter="applyFilters"
          />
        </el-form-item>
        <el-form-item
          label="计划完成日期"
          class="dispatch-filter-field dispatch-filter-dates"
        >
          <div class="dispatch-date-range">
            <el-input
              v-model="dateStart"
              type="date"
              placeholder="开始日期"
              aria-label="计划完成开始日期"
              clearable
              ><template #prefix>起</template></el-input
            >
            <el-input
              v-model="dateEnd"
              type="date"
              placeholder="结束日期"
              aria-label="计划完成结束日期"
              clearable
              ><template #prefix>止</template></el-input
            >
          </div>
        </el-form-item>
        <el-form-item label="责任人" class="dispatch-filter-field">
          <el-select
            v-model="owner"
            placeholder="输入姓名筛选"
            aria-label="筛选责任人"
            filterable
            clearable
            no-match-text="没有匹配的责任人"
            no-data-text="暂无责任人"
          >
            <el-option
              v-for="name in ownerOptions"
              :key="name"
              :value="name"
              :label="name"
            />
          </el-select>
        </el-form-item>
      </div>
      <el-alert
        v-if="filterError"
        :title="filterError"
        type="error"
        :closable="false"
        show-icon
      />
      <div class="dispatch-filter-actions">
        <el-button size="small" @click="resetFilters">重置</el-button>
        <el-button size="small" type="primary" @click="applyFilters"
          >查询</el-button
        >
      </div>
    </el-form>
    <el-alert
      v-if="loadError"
      :title="`派发记录加载失败：${loadError}`"
      type="error"
      :closable="false"
      show-icon
    />
    <el-table
      v-loading="loading"
      :data="pagedRows"
      :max-height="dialog ? '52vh' : '60vh'"
      :empty-text="
        loadError
          ? '加载失败，请重试'
          : hasAppliedFilters
            ? '没有匹配的任务'
            : '暂无派发记录'
      "
    >
      <el-table-column label="任务内容" min-width="300"
        ><template #default="{ row }"
          ><RouterLink
            class="table-link dispatch-task-link"
            :to="{ path: '/tasks', query: { id: row.id } }"
            >{{ row.content }}</RouterLink
          >
        </template></el-table-column
      >
      <el-table-column label="责任人" min-width="160"
        ><template #default="{ row }">{{
          row.owner_names.join("、")
        }}</template></el-table-column
      >
      <el-table-column label="计划完成" width="145"
        ><template #default="{ row }"
          ><span :class="{ muted: !row.due_date }">{{
            row.due_date || "未设期限"
          }}</span></template
        ></el-table-column
      >
      <el-table-column label="状态" width="145"
        ><template #default="{ row }"
          ><el-tag v-if="row.withdrawn_at" type="info">已撤回</el-tag
          ><StatusTag v-else :value="row.status" /></template
      ></el-table-column>
      <el-table-column label="操作" width="125" fixed="right"
        ><template #default="{ row }"
          ><div class="dispatch-row-actions">
            <RouterLink
              class="dispatch-detail-link"
              :to="{ path: '/tasks', query: { id: row.id } }"
              >查看详情</RouterLink
            ><RouterLink
              v-if="
                options?.wecom_bound &&
                options?.native_todo_auto_confirm &&
                !row.closed_at &&
                !row.withdrawn_at
              "
              class="dispatch-detail-link"
              :to="{
                path: '/tasks',
                query: { id: row.id, native_confirm: '1' },
              }"
              >企微待办</RouterLink
            >
          </div></template
        ></el-table-column
      >
    </el-table>
    <footer class="dispatch-records-footer">
      <el-pagination
        v-model:current-page="recordsPage"
        :page-size="recordsPageSize"
        :total="filteredRows.length"
        :pager-count="5"
        layout="prev, pager, next"
        hide-on-single-page
      />
      <el-button v-if="dialog" @click="emit('close')">关闭</el-button>
    </footer>
  </section>
</template>
<style scoped>
.dispatch-records {
  --dispatch-text: #233858;
  --dispatch-muted: #64758d;
  --dispatch-line: #e8eef7;
  --dispatch-blue: #2878ef;
}
.dispatch-records-heading p {
  margin: 0;
  color: var(--dispatch-muted);
  font-size: 13px;
}
.dispatch-records {
  min-width: 0;
}
.dispatch-records-heading {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  padding: 22px 24px;
  margin-bottom: 0;
}
.dispatch-records-heading h2 {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dispatch-records-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  max-width: 100%;
}
.dispatch-records-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 220px;
}
.dispatch-records-search .el-input {
  width: 255px;
  min-width: 0;
}
.dispatch-records-actions .el-button {
  margin: 0;
}
.dispatch-filter-toggle .el-icon:last-child {
  margin-left: 4px;
}
.dispatch-records-filters {
  margin: 0 24px 20px;
  padding-top: 16px;
  border-top: 1px solid var(--dispatch-line);
}
.dispatch-filter-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 0 16px;
}
.dispatch-filter-field {
  flex: 1 1 180px;
  min-width: 0;
}
.dispatch-filter-dates {
  flex: 1.5 1 300px;
}
.dispatch-date-range {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}
.dispatch-date-range > .el-input {
  flex: 1 1 140px;
  width: 100%;
  min-width: 0;
}
.dispatch-filter-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.dispatch-filter-actions .el-button {
  margin: 0;
}
.dispatch-records-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin: 0;
  padding: 20px 24px;
}
.dispatch-records > .el-alert {
  margin: 0 24px 16px;
}
.dispatch-records :deep(.cell) {
  padding-left: 18px;
  padding-right: 12px;
}
.dispatch-task-link {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  line-height: 1.65;
  font-weight: 500;
}
.dispatch-detail-link {
  color: var(--dispatch-blue);
  font-size: 13px;
}
.dispatch-row-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
@media (max-width: 680px) {
  .dispatch-records-heading {
    padding: 18px;
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
  }
  .dispatch-records-actions {
    min-width: 0;
    justify-content: flex-end;
  }
  .dispatch-records-search {
    flex-basis: 100%;
    min-width: 0;
  }
  .dispatch-records-search .el-input {
    flex: 1;
    width: 100%;
  }
  .dispatch-date-range > .el-input {
    flex-basis: 100%;
  }
  .dispatch-filter-toggle {
    min-height: 32px;
  }
  .dispatch-records-filters {
    margin-inline: 18px;
  }
  .dispatch-records-footer {
    padding: 18px;
  }
  .dispatch-records > .el-alert {
    margin-inline: 18px;
  }
}
.dispatch-records.is-dialog .dispatch-records-heading {
  padding: 0 0 20px;
}
.dispatch-records.is-dialog .dispatch-records-filters {
  margin-inline: 0;
}
.dispatch-records.is-dialog .dispatch-records-footer {
  padding: 20px 0 0;
}
.dispatch-records-footer > .el-button {
  margin-left: auto;
}
.dispatch-records.is-dialog > .el-alert {
  margin: 0 0 16px;
}
</style>
