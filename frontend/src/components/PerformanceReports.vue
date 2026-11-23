<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, download, query, rate, run, state } from "../api";
import {
  authorizedReportDepartments,
  reportDepartmentOptions,
  reportDepartmentScope,
} from "../reportDepartments";
import PeopleSelect from "./PeopleSelect.vue";
import PeriodFilter from "./PeriodFilter.vue";
import StatusTag from "./StatusTag.vue";

const access = ref<any>(null),
  data = ref<any>(null);
const range = ref<Record<string, string>>({}),
  department = ref("");
const person = ref<string | null>(null),
  loaded = ref<Record<string, any> | null>(null);
const busy = ref(false),
  error = ref("");
const canViewOthers = computed(() =>
  ["all", "departments"].includes(access.value?.mode),
);
const departments = computed(() => access.value?.departments || []);
const departmentOptions = computed(() =>
  access.value?.mode === "all"
    ? reportDepartmentOptions(departments.value)
    : authorizedReportDepartments(
        departments.value,
        access.value?.department_ids || [],
        access.value?.root_department_ids || [],
      ),
);
const people = computed(() =>
  (access.value?.users || []).filter(
    (p: any) =>
      p.active &&
      (!department.value ||
        reportDepartmentScope(departments.value, department.value).has(
          p.department_id,
        )),
  ),
);
const columns = [
  {
    key: "regular",
    label: "会议纪要统计",
    fields: [
      { key: "items", label: "事项数" },
      { key: "completed", label: "已完成" },
      { key: "incomplete", label: "未完成" },
      { key: "rate", label: "达成率" },
    ],
  },
  {
    key: "direct",
    label: "直接派发任务",
    fields: [
      { key: "items", label: "事项数" },
      { key: "assessed", label: "已到期" },
      { key: "completed", label: "到期已完成" },
      { key: "rate", label: "到期完成率" },
    ],
  },
  {
    key: "total",
    label: "合计",
    fields: [
      { key: "items", label: "事项数" },
      { key: "assessed", label: "考核数" },
      { key: "completed", label: "完成数" },
      { key: "rate", label: "达成率" },
    ],
  },
];
let generation = 0,
  detailGeneration = 0;
const detailsVisible = ref(false),
  detailBusy = ref(false),
  detailError = ref("");
const source = ref("regular"),
  detailPerson = ref<any>(null),
  detailRows = ref<any[]>([]);
const detailPage = ref(1),
  detailTotal = ref(0);
const detailTitle = computed(
  () =>
    `${detailPerson.value?.label || ""} · ${source.value === "regular" ? "会议纪要任务" : "直接派发任务"}`,
);

async function load() {
  const ticket = ++generation;
  detailGeneration++;
  detailsVisible.value = false;
  busy.value = true;
  error.value = "";
  data.value = loaded.value = null;
  try {
    const rights = await api("/reports/access");
    if (ticket !== generation) return;
    access.value = rights;
    if (!departmentOptions.value.some((d: any) => d.id === department.value))
      department.value = "";
    if (!people.value.some((p: any) => p.id === person.value))
      person.value = null;
    const requested = {
      ...range.value,
      department_id: canViewOthers.value ? department.value : undefined,
      person_id: canViewOthers.value ? person.value : state.me?.id,
    };
    const result = await api("/reports/performance?" + query(requested));
    if (ticket !== generation) return;
    data.value = result;
    loaded.value = requested;
  } catch (e: any) {
    if (ticket === generation) error.value = e.message || "绩效统计加载失败";
  } finally {
    if (ticket === generation) busy.value = false;
  }
}
function changed(value: Record<string, string>) {
  range.value = value;
  void load();
}
function departmentChanged() {
  person.value = null;
  void load();
}
async function loadDetails() {
  if (!detailsVisible.value || !loaded.value) return;
  const ticket = ++detailGeneration;
  detailBusy.value = true;
  detailError.value = "";
  detailRows.value = [];
  try {
    const result = await api(
      (source.value === "regular" ? "/reports?" : "/reports/direct?") +
        query({
          ...loaded.value,
          person_id: detailPerson.value.id,
          group_by: "department_people",
          details_only: true,
          offset: (detailPage.value - 1) * 10,
          limit: 10,
        }),
    );
    if (ticket !== detailGeneration) return;
    detailRows.value = result.details;
    detailTotal.value = result.details_total;
  } catch (e: any) {
    if (ticket === detailGeneration)
      detailError.value = e.message || "任务明细加载失败";
  } finally {
    if (ticket === detailGeneration) detailBusy.value = false;
  }
}
function showDetails(key: string, row: any) {
  source.value = key;
  detailPerson.value = row;
  detailPage.value = 1;
  detailTotal.value = 0;
  detailsVisible.value = true;
  void loadDetails();
}
function summary({ columns: tableColumns }: any) {
  return tableColumns.map((column: any, index: number) => {
    if (!index) return "合计（按人）";
    const [source, key] = column.property.split(".");
    const value = data.value.summary[source][key];
    return key === "rate" ? rate(value) : value;
  });
}
watch(detailPage, loadDetails);
watch(detailsVisible, (visible) => {
  if (!visible) {
    detailGeneration++;
    detailBusy.value = false;
  }
});
onBeforeUnmount(() => {
  generation++;
  detailGeneration++;
});
</script>

<template>
  <section class="panel filters performance-filters">
    <div class="performance-period">
      <PeriodFilter @change="changed" />
      <el-button
        :disabled="busy || !data || !loaded || access?.can_export === false"
        @click="
          run(() =>
            download(
              '/reports/performance/export?' + query(loaded || {}),
              '绩效统计.xlsx',
            ),
          )
        "
        >导出 Excel</el-button
      >
    </div>
    <div v-if="canViewOthers" class="performance-filter-fields">
      <label
        ><span>部门</span
        ><el-select
          v-model="department"
          clearable
          filterable
          aria-label="绩效统计部门"
          :placeholder="access?.mode === 'all' ? '全部部门' : '全部授权部门'"
          @change="departmentChanged"
        >
          <el-option
            v-for="d in departmentOptions"
            :key="d.id"
            :value="d.id"
            :label="d.label"
          /> </el-select
      ></label>
      <label
        ><span>人员</span
        ><PeopleSelect
          v-model="person"
          :users="people"
          search-only
          placeholder="全部人员"
          aria-label="绩效统计人员"
          @update:model-value="load"
      /></label>
    </div>
    <p v-else class="performance-self">{{ state.me?.name }}（仅本人）</p>
  </section>
  <el-alert v-if="error" type="error" :closable="false" :title="error">
    <el-button size="small" @click="load">重试加载</el-button>
  </el-alert>
  <div
    v-if="busy && !data"
    v-loading="true"
    class="panel performance-loading"
  />
  <section v-if="data" class="panel performance-results">
    <header class="panel-heading">
      <h2>
        人员绩效考核 <span class="count">{{ data.groups.length }}</span>
      </h2>
    </header>
    <el-table
      :data="data.groups"
      max-height="620"
      show-summary
      :summary-method="summary"
      empty-text="本期暂无符合条件的人员"
    >
      <el-table-column prop="label" label="姓名" min-width="140" fixed>
        <template #default="{ row }"
          >{{ row.label
          }}<span v-if="row.active === false" class="muted small"
            >（已停用）</span
          ></template
        >
      </el-table-column>
      <el-table-column
        v-for="group in columns"
        :key="group.key"
        :label="group.label"
        align="center"
      >
        <el-table-column
          v-for="field in group.fields"
          :key="field.key"
          :prop="`${group.key}.${field.key}`"
          :label="field.label"
          :min-width="field.label.length > 4 ? 110 : 85"
          align="right"
        >
          <template #default="{ row }">
            <el-button
              v-if="field.key === 'items' && group.key !== 'total'"
              link
              type="primary"
              :aria-label="`查看${row.label}的${group.label}任务`"
              @click="showDetails(group.key, row)"
              >{{ row[group.key].items }}</el-button
            >
            <strong v-else-if="field.key === 'rate' && group.key === 'total'">{{
              rate(row.total.rate)
            }}</strong>
            <span v-else>{{
              field.key === "rate"
                ? rate(row[group.key].rate)
                : row[group.key][field.key]
            }}</span>
          </template>
        </el-table-column>
      </el-table-column>
    </el-table>
    <div class="performance-rules">
      <p>
        会议纪要：按会议日期统计，已完成、未完成计入考核。派发：按最新计划完成日期统计，已到期计入考核，验证通过才算完成。
      </p>
      <p>
        合计达成率＝两类完成数之和 ÷
        两类考核数之和；共同负责按每人统计。统计截至 {{ data.as_of }}。
      </p>
      <p v-if="data.undated_count">
        另有 {{ data.undated_count }} 项派发任务未设计划完成日期，未计入本期。
      </p>
    </div>
  </section>
  <el-dialog
    v-model="detailsVisible"
    :title="detailTitle"
    width="min(1040px, 94vw)"
    class="performance-detail-dialog"
  >
    <div class="performance-details" v-loading="detailBusy">
      <p v-if="loaded" class="muted small">
        {{ loaded.start }} — {{ loaded.end }}
      </p>
      <el-alert
        v-if="detailError"
        type="error"
        :closable="false"
        :title="detailError"
        ><el-button @click="loadDetails">重试加载</el-button></el-alert
      >
      <el-table
        v-else
        :data="detailRows"
        max-height="430"
        empty-text="暂无任务明细"
      >
        <el-table-column
          v-if="source === 'regular'"
          prop="meeting_title"
          label="会议名称"
          min-width="190"
        />
        <el-table-column
          v-if="source === 'regular'"
          prop="meeting_date"
          label="会议日期"
          min-width="115"
        />
        <el-table-column prop="content" label="任务内容" min-width="260" />
        <el-table-column prop="due_date" label="计划完成日期" min-width="120" />
        <el-table-column label="完成结果" min-width="100"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template
        ></el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="detailPage"
        :page-size="10"
        :total="detailTotal"
        layout="prev, pager, next, total"
        small
      />
    </div>
    <template #footer
      ><el-button @click="detailsVisible = false">关闭</el-button></template
    >
  </el-dialog>
</template>

<style scoped>
.performance-filters {
  display: grid;
  gap: 18px;
}
.performance-period {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.performance-period :deep(.period-filter) {
  margin: 0;
  padding: 0;
  border: 0;
}
.performance-filter-fields {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 16px;
  border-top: 1px solid var(--el-border-color-light);
  padding-top: 18px;
}
.performance-filter-fields label {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.performance-filter-fields label > span {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.performance-filter-fields :deep(.el-select) {
  width: 100%;
}
.performance-self {
  margin: 0;
}
.performance-loading {
  min-height: 180px;
}
.performance-results {
  min-width: 0;
}
.performance-results :deep(.el-table__footer-wrapper) {
  font-weight: 600;
}
.performance-rules {
  padding: 12px 18px;
  border-top: 1px solid var(--el-border-color-light);
  font-size: 12px;
  color: var(--el-text-color-regular);
  line-height: 1.7;
}
.performance-rules p {
  margin: 4px 0;
}
.performance-details {
  display: grid;
  gap: 16px;
  min-width: 0;
}
@media (max-width: 767px) {
  .performance-filter-fields {
    grid-template-columns: minmax(0, 1fr);
  }
  .performance-rules {
    padding: 12px 14px;
  }
  .performance-details :deep(.el-pagination) {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
