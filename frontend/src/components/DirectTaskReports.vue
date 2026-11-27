<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  api,
  download,
  query,
  rate,
  isLowReportRate,
  run,
  state,
} from "../api";
import {
  reportDepartmentOptions,
  authorizedReportDepartments,
  reportDepartmentScope,
} from "../reportDepartments";
import ReportRateMeter from "./ReportRateMeter.vue";
import ReportOverview from "./ReportOverview.vue";
import PeriodFilter from "./PeriodFilter.vue";
import PeopleSelect from "./PeopleSelect.vue";
import StatusTag from "./StatusTag.vue";

const statuses = ["已完成", "待验证", "未完成", "未到期"];
const access = ref<any>(null),
  data = ref<any>(null);
const range = ref<Record<string, string>>({}),
  department = ref(""),
  person = ref<string | null>(null);
const busy = ref(false),
  error = ref("");
const loaded = ref<Record<string, any> | null>(null);
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
let generation = 0,
  detailGeneration = 0;
const detailsVisible = ref(false),
  detailBusy = ref(false),
  detailError = ref("");
const detailPerson = ref<any>(null),
  detailStatus = ref("");
const detailPage = ref(1),
  detailRows = ref<any[]>([]),
  detailTotal = ref(0);
const detailTitle = computed(
  () =>
    `${detailPerson.value ? detailPerson.value.label + " · " : ""}${detailStatus.value || "全部"}派发任务`,
);
async function load() {
  const ticket = ++generation;
  detailGeneration++;
  detailsVisible.value = false;
  busy.value = true;
  data.value = loaded.value = null;
  error.value = "";
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
    const result = await api("/reports/direct?" + query(requested));
    if (ticket !== generation) return;
    data.value = result;
    loaded.value = requested;
  } catch (e: any) {
    if (ticket === generation) error.value = chineseError(e, "报表加载失败");
  } finally {
    if (ticket === generation) busy.value = false;
  }
}
function changed(value: Record<string, string>) {
  range.value = value;
  load();
}
function departmentChanged() {
  person.value = null;
  load();
}
async function loadDetails() {
  if (!detailsVisible.value || !loaded.value) return;
  const ticket = ++detailGeneration;
  detailBusy.value = true;
  detailError.value = "";
  detailRows.value = [];
  try {
    const result = await api(
      "/reports/direct?" +
        query({
          ...loaded.value,
          details_only: true,
          status: detailStatus.value,
          person_id: detailPerson.value?.id || loaded.value.person_id,
          offset: (detailPage.value - 1) * 10,
          limit: 10,
        }),
    );
    if (ticket !== detailGeneration) return;
    detailRows.value = result.details;
    detailTotal.value = result.details_total;
  } catch (e: any) {
    if (ticket === detailGeneration)
      detailError.value = chineseError(e, "明细加载失败");
  } finally {
    if (ticket === detailGeneration) detailBusy.value = false;
  }
}
function showDetails(status = "", row: any = null) {
  detailStatus.value = status;
  detailPerson.value = row;
  detailPage.value = 1;
  detailTotal.value = 0;
  detailsVisible.value = true;
  loadDetails();
}
watch(detailPage, loadDetails);
watch(detailsVisible, (open) => {
  if (!open) {
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
  <section class="panel filters direct-filters">
    <div class="direct-period">
      <div>
        <span class="date-label">计划完成日期</span
        ><PeriodFilter @change="changed" />
      </div>
      <el-button
        :disabled="busy || !data || access?.can_export === false"
        @click="
          run(() =>
            download(
              '/reports/direct/export?' + query(loaded || {}),
              '直接派发任务统计.xlsx',
            ),
          )
        "
        >导出 Excel</el-button
      >
    </div>
    <div class="direct-filter-fields">
      <label v-if="canViewOthers"
        ><span>部门</span
        ><el-select
          v-model="department"
          clearable
          filterable
          aria-label="派发统计部门"
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
      <label v-if="canViewOthers"
        ><span>人员</span
        ><PeopleSelect
          v-model="person"
          :users="people"
          search-only
          placeholder="全部人员"
          aria-label="派发统计人员"
          @update:model-value="load"
      /></label>
      <div v-else class="direct-self">{{ state.me?.name }}（仅本人）</div>
    </div>
  </section>
  <el-alert v-if="error" type="error" :closable="false" :title="error"
    ><el-button @click="load">重试加载</el-button></el-alert
  >
  <div v-if="busy && !data" v-loading="true" class="panel direct-loading" />
  <template v-if="data">
    <p v-if="data.undated_count" class="muted">
      {{ data.undated_count }} 项任务未设计划完成日期，未计入本期。
    </p>
    <ReportOverview
      :summary="data.summary"
      :statuses="statuses"
      rate-label="到期完成率"
      total-label="任务总数"
      task-label="派发任务"
      :rate-hint="`到期验证通过 ${data.summary.due_completed} / 已到期 ${data.summary.due_items}`"
      @details="showDetails($event)"
    />
    <section class="panel direct-results">
      <div class="panel-heading">
        <h2>{{ canViewOthers ? "人员派发任务统计" : "我的派发任务统计" }}</h2>
        <span class="muted small">共同负责的任务在每人名下分别计数</span>
      </div>
      <el-table
        :data="data.groups"
        max-height="620"
        empty-text="本期暂无直接派发任务"
      >
        <el-table-column label="姓名" min-width="130"
          ><template #default="{ row }"
            >{{ row.label
            }}<span v-if="row.active === false" class="muted small"
              >（已停用）</span
            ></template
          ></el-table-column
        >
        <el-table-column label="任务总数" min-width="95" align="right"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :aria-label="`${row.label}：全部 ${row.items} 项`"
              @click="showDetails('', row)"
              >{{ row.items }}</el-button
            ></template
          ></el-table-column
        >
        <el-table-column
          v-for="s in statuses"
          :key="s"
          :label="s"
          min-width="90"
          align="right"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :aria-label="`${row.label}：${s} ${row.counts[s]} 项`"
              @click="showDetails(s, row)"
              >{{ row.counts[s] }}</el-button
            ></template
          ></el-table-column
        >
        <el-table-column label="到期完成率" min-width="160" align="right"
          ><template #default="{ row }">
            <ReportRateMeter :value="row.rate" /> </template
        ></el-table-column>
      </el-table>
      <p class="direct-rule">
        到期完成率 = 已到期且验证通过 ÷ 已到期有效任务；撤回任务不计入。
      </p>
    </section>
  </template>
  <el-dialog
    v-model="detailsVisible"
    :title="detailTitle"
    width="min(1120px, 94vw)"
    top="5vh"
    append-to-body
    destroy-on-close
  >
    <div class="direct-detail-content">
      <p class="muted small">
        {{ loaded?.start }} — {{ loaded?.end }} · 共 {{ detailTotal }} 项
      </p>
      <el-alert
        v-if="detailError"
        type="error"
        :title="detailError"
        :closable="false"
        ><el-button @click="loadDetails">重试读取明细</el-button></el-alert
      >
      <el-table
        v-loading="detailBusy"
        :data="detailRows"
        max-height="450"
        empty-text="暂无对应任务"
      >
        <el-table-column label="任务内容" min-width="300"
          ><template #default="{ row }"
            ><div class="task-content">{{ row.content }}</div>
            <p v-if="row.requirements" class="muted small">
              要求：{{ row.requirements }}
            </p>
            <p v-if="row.deliverable" class="muted small">
              输出物：{{ row.deliverable }}
            </p></template
          ></el-table-column
        >
        <el-table-column prop="issuer_name" label="派发人" min-width="100" />
        <el-table-column label="责任人" min-width="140"
          ><template #default="{ row }">{{
            row.owner_names.join("、")
          }}</template></el-table-column
        >
        <el-table-column label="计划完成日期" min-width="150"
          ><template #default="{ row }"
            ><del v-for="d in row.date_history" :key="d" class="date-history">{{
              d
            }}</del
            >{{ row.due_date }}</template
          ></el-table-column
        >
        <el-table-column label="状态" min-width="100"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template
        ></el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="detailPage"
        :page-size="10"
        :total="detailTotal"
        layout="total, prev, pager, next"
        aria-label="派发任务明细分页"
      />
    </div>
    <template #footer
      ><el-button @click="detailsVisible = false">关闭明细</el-button></template
    >
  </el-dialog>
</template>

<style scoped>
.direct-period {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.date-label,
.direct-filter-fields label > span {
  display: block;
  margin-bottom: 8px;
  color: var(--text-secondary, #61728a);
  font-size: 13px;
}
.direct-filter-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
  max-width: 760px;
}
.direct-filter-fields label {
  min-width: 0;
}
.direct-filter-fields :deep(.el-select) {
  width: 100%;
}
.direct-self {
  font-size: 14px;
}
.direct-loading {
  min-height: 180px;
}
.direct-summary.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0;
  margin-bottom: 16px;
  overflow: hidden;
}
.direct-period :deep(.period-filter) {
  padding-bottom: 0;
  margin-bottom: 0;
  border-bottom: 0;
}
.direct-summary :deep(.el-button) {
  align-self: flex-start;
  height: auto;
  padding: 0;
}
.direct-summary :deep(.el-button strong) {
  color: var(--el-color-primary);
}
.direct-results .panel-heading {
  flex-wrap: wrap;
  gap: 8px;
}
.direct-rule {
  color: var(--text-secondary, #61728a);
  font-size: 13px;
  margin: 16px 0 0;
  padding: 0 18px 16px;
  line-height: 1.6;
}
.direct-detail-content {
  display: grid;
  gap: 16px;
  max-height: 70vh;
  overflow: auto;
  min-width: 0;
}
.direct-detail-content p {
  margin: 0;
}
.direct-detail-content :deep(.el-pagination) {
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.task-content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.date-history {
  display: block;
  color: var(--text-secondary, #61728a);
  font-size: 12px;
}
@media (min-width: 768px) and (max-width: 1100px) {
  .direct-summary.stat-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .direct-summary.stat-grid .stat:nth-child(3n) {
    border-right: 0;
  }
  .direct-summary.stat-grid .stat:nth-child(-n + 3) {
    border-bottom: 1px solid var(--line);
  }
}
@media (max-width: 767px) {
  .direct-summary.stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .direct-summary.stat-grid .stat:nth-child(2n) {
    border-right: 0;
  }
  .direct-filter-fields {
    grid-template-columns: 1fr;
  }
  .direct-period > div {
    width: 100%;
    min-width: 0;
  }
  .direct-period :deep(.period-filter) {
    flex-wrap: wrap;
  }
  .direct-summary .stat {
    padding: 16px;
  }
}
</style>
