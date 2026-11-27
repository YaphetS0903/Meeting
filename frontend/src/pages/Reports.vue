<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { ref, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, state, query, run, download, statuses } from "../api";
import {
  reportDepartmentOptions,
  reportDepartmentScope,
  authorizedReportDepartments,
} from "../reportDepartments";
import PeopleSelect from "../components/PeopleSelect.vue";
import PeriodFilter from "../components/PeriodFilter.vue";
import ReportRateMeter from "../components/ReportRateMeter.vue";
import ReportOverview from "../components/ReportOverview.vue";
import StatusTag from "../components/StatusTag.vue";
import DirectTaskReports from "../components/DirectTaskReports.vue";
import PerformanceReports from "../components/PerformanceReports.vue";
const range = ref<any>({}),
  department = ref(""),
  selectedPerson = ref<string | null>(null),
  selectedTopic = ref(""),
  selectedLeader = ref(""),
  meetingsVisible = ref(false),
  meetingPage = ref(1),
  data = ref<any>(null),
  busy = ref(false),
  loadError = ref(""),
  loadedParams = ref<Record<string, any> | null>(null),
  detailStatus = ref(""),
  detailPerson = ref<any>(null),
  detailsVisible = ref(false),
  detailPage = ref(1),
  detailPageSize = ref(10);
const detailItems = ref<any[]>([]),
  detailTotal = ref(0),
  detailBusy = ref(false),
  detailError = ref("");
const detailKind = ref<"review" | "new">("review");
let detailGeneration = 0;
const access = ref<any>(null);
const route = useRoute();
const router = useRouter();
const meetingScope = ref("regular");
const primaryReportMode = computed({
  get: () => {
    const view = route.query.view;
    return view === "regular" || view === "direct" ? view : "performance";
  },
  set: (view: string) => {
    void router.replace({
      query: {
        ...route.query,
        view: view === "performance" ? undefined : view,
      },
    });
  },
});
const directOnly = computed(() => primaryReportMode.value === "direct");
const performanceOnly = computed(
  () => primaryReportMode.value === "performance",
);
const seniorOnly = computed(() => meetingScope.value === "senior");
const canViewSenior = computed(() => !!access.value?.can_view_senior_meetings);
const canViewOthers = computed(
  () => access.value?.mode === "all" || access.value?.mode === "departments",
);
const reportDepartments = computed(() => access.value?.departments || []);
const departmentOptions = computed(() =>
  access.value?.mode === "all"
    ? reportDepartmentOptions(reportDepartments.value)
    : authorizedReportDepartments(
        reportDepartments.value,
        access.value?.department_ids || [],
        access.value?.root_department_ids || [],
      ),
);
const selectedDepartmentScope = computed(() =>
  reportDepartmentScope(reportDepartments.value, department.value),
);
const topicOptions = computed(() =>
  (access.value?.topics || []).filter(
    (topic: any) =>
      !department.value ||
      topic.department_ids.some((id: string) =>
        selectedDepartmentScope.value.has(id),
      ),
  ),
);
const selectedDepartmentName = computed(
  () =>
    departmentOptions.value.find(
      (item: any) => item.id === loadedParams.value?.department_id,
    )?.label || "所选部门",
);
const params = computed(() => ({
  ...range.value,
  group_by: "department_people",
  department_id: canViewOthers.value ? department.value : undefined,
  person_id: canViewOthers.value ? selectedPerson.value : state.me?.id,
  series_key: selectedTopic.value,
  senior_only: seniorOnly.value || undefined,
  leader_id: seniorOnly.value ? selectedLeader.value : undefined,
}));
let generation = 0;
async function load() {
  const activeGeneration = ++generation;
  busy.value = true;
  detailsVisible.value = false;
  meetingsVisible.value = false;
  detailGeneration += 1;
  detailStatus.value = "";
  detailPerson.value = null;
  detailPage.value = 1;
  data.value = null;
  loadedParams.value = null;
  loadError.value = "";
  try {
    const currentAccess = await api(
      "/reports/access?" +
        query({
          senior_only: seniorOnly.value || undefined,
          leader_id: seniorOnly.value ? selectedLeader.value : undefined,
        }),
    );
    if (activeGeneration !== generation) return;
    access.value = currentAccess;
    if (
      department.value &&
      !departmentOptions.value.some((d) => d.id === department.value)
    ) {
      department.value = "";
      selectedPerson.value = null;
    }
    if (
      selectedPerson.value &&
      !peopleOptions.value.some((p: any) => p.id === selectedPerson.value)
    )
      selectedPerson.value = null;
    if (
      selectedTopic.value &&
      !topicOptions.value.some(
        (topic: any) => topic.key === selectedTopic.value,
      )
    )
      selectedTopic.value = "";
    const requested = { ...params.value };
    const result = await api(
      "/reports?" + query({ ...requested, include_details: false }),
    );
    if (activeGeneration !== generation) return;
    data.value = result;
    loadedParams.value = requested;
  } catch (error: any) {
    if (activeGeneration === generation)
      loadError.value = chineseError(error, "服务暂时不可用");
    throw error;
  } finally {
    if (activeGeneration === generation) busy.value = false;
  }
}
function changed(r: any) {
  range.value = r;
  run(load);
}
const details = computed(
  () =>
    (data.value?.details || []).filter(
      (x) =>
        (!detailStatus.value || x.status === detailStatus.value) &&
        (!detailPerson.value || x.user_id === detailPerson.value.id),
    ) || [],
);
const detailRows = computed(() =>
  detailKind.value === "new" || data.value?.details_paginated
    ? detailItems.value
    : details.value.slice(
        (detailPage.value - 1) * detailPageSize.value,
        detailPage.value * detailPageSize.value,
      ),
);
const detailTitle = computed(() =>
  detailKind.value === "new"
    ? "本次新安排"
    : `${detailPerson.value ? detailPerson.value.label + " · " : ""}${detailStatus.value}任务明细`,
);
const peopleRows = computed(() => {
  if (canViewOthers.value || data.value?.groups?.length)
    return data.value?.groups || [];
  if (!data.value || !state.me) return [];
  return [{ id: state.me.id, label: state.me.name, ...data.value.summary }];
});
const peopleOptions = computed(() =>
  (access.value?.users || []).filter(
    (person: any) =>
      person.active &&
      (!department.value ||
        selectedDepartmentScope.value.has(person.department_id)),
  ),
);
function departmentChanged() {
  selectedPerson.value = null;
  selectedTopic.value = "";
  run(load);
}
watch(primaryReportMode, () => {
  generation++;
  detailGeneration++;
  detailsVisible.value = meetingsVisible.value = false;
  data.value = loadedParams.value = null;
  busy.value = false;
  loadError.value = "";
  meetingScope.value = "regular";
  resetMeetingFilters();
});
function resetMeetingFilters() {
  department.value = selectedTopic.value = "";
  selectedLeader.value = "";
  selectedPerson.value = null;
}
function meetingScopeChanged() {
  resetMeetingFilters();
  run(load);
}
async function loadDetails() {
  if (
    !detailsVisible.value ||
    (detailKind.value !== "new" && !data.value?.details_paginated) ||
    !loadedParams.value
  )
    return;
  const ticket = ++detailGeneration;
  detailBusy.value = true;
  detailError.value = "";
  detailItems.value = [];
  try {
    const result = await api(
      (detailKind.value === "new"
        ? "/reports/new-arrangements?"
        : "/reports?") +
        query({
          ...loadedParams.value,
          details_only: true,
          status: detailStatus.value,
          person_id: detailPerson.value?.id || loadedParams.value.person_id,
          offset: (detailPage.value - 1) * detailPageSize.value,
          limit: detailPageSize.value,
        }),
    );
    if (ticket !== detailGeneration) return;
    detailItems.value = result.details;
    detailTotal.value =
      detailKind.value === "new" ? result.total : result.details_total;
  } catch (error: any) {
    if (ticket === detailGeneration)
      detailError.value = chineseError(error, "明细读取失败，请重试");
  } finally {
    if (ticket === detailGeneration) detailBusy.value = false;
  }
}
const matchingTotal = computed(() =>
  detailKind.value === "new" || data.value?.details_paginated
    ? detailTotal.value
    : details.value.length,
);
watch([detailPage, detailPageSize], () => {
  void loadDetails();
});
watch(detailsVisible, (visible) => {
  if (!visible) {
    detailGeneration += 1;
    detailBusy.value = false;
  }
});
function showDetails(
  status = "",
  person: any = null,
  kind: "review" | "new" = "review",
) {
  detailKind.value = kind;
  detailStatus.value = status;
  detailPerson.value = person;
  detailPage.value = 1;
  detailsVisible.value = true;
  detailTotal.value = 0;
  void loadDetails();
}
</script>
<template>
  <section class="page-heading">
    <div>
      <h1>统计报表</h1>
    </div>
    <el-button
      v-if="!directOnly && !performanceOnly"
      :disabled="
        busy ||
        !data ||
        !loadedParams ||
        !!loadError ||
        access?.can_export === false
      "
      @click="
        run(() =>
          download(
            '/reports/export?' + query(loadedParams || {}),
            loadedParams?.senior_only
              ? '中高层会议统计报表.xlsx'
              : '会议考核报表.xlsx',
          ),
        )
      "
      >导出 Excel</el-button
    >
  </section>
  <section class="panel filters report-module-panel">
    <el-radio-group
      v-model="primaryReportMode"
      class="report-module-picker"
      aria-label="统计模块"
    >
      <el-radio-button value="performance">绩效统计</el-radio-button>
      <el-radio-button value="regular">会议纪要统计</el-radio-button>
      <el-radio-button value="direct">直接派发任务统计</el-radio-button>
    </el-radio-group>
  </section>
  <DirectTaskReports v-if="directOnly" />
  <PerformanceReports v-if="performanceOnly" />
  <section
    v-if="!directOnly && !performanceOnly"
    class="panel filters report-filter-panel"
  >
    <el-radio-group
      v-if="canViewSenior"
      v-model="meetingScope"
      class="report-meeting-scope"
      aria-label="会议统计范围"
      @change="meetingScopeChanged"
    >
      <el-radio-button value="regular">全部会议</el-radio-button>
      <el-radio-button value="senior">中高层会议统计</el-radio-button>
    </el-radio-group>
    <PeriodFilter @change="changed" />
    <div
      class="report-filters"
      :class="{ 'self-only': !canViewOthers, 'with-leader': seniorOnly }"
    >
      <label v-if="canViewOthers" class="report-filter"
        ><span>部门</span
        ><el-select
          v-model="department"
          :placeholder="access?.mode === 'all' ? '全部部门' : '全部授权部门'"
          clearable
          filterable
          aria-label="筛选部门"
          @change="departmentChanged"
          ><el-option
            v-for="d in departmentOptions"
            :key="d.id"
            :value="d.id"
            :label="d.label" /></el-select
      ></label>
      <label v-if="seniorOnly" class="report-filter">
        <span>参会领导</span>
        <el-select
          v-model="selectedLeader"
          clearable
          filterable
          placeholder="全部参会领导"
          aria-label="筛选参会领导"
          @change="run(load)"
        >
          <el-option
            v-for="person in access?.senior_leaders || []"
            :key="person.id"
            :value="person.id"
            :label="person.name"
          />
        </el-select>
      </label>
      <label v-if="canViewOthers" class="report-filter"
        ><span>人员</span>
        <PeopleSelect
          v-model="selectedPerson"
          :users="peopleOptions"
          search-only
          placeholder="全部人员"
          aria-label="筛选人员"
          @update:model-value="run(load)"
        />
      </label>
      <div v-else class="report-filter">
        <span>人员</span>
        <span class="report-self-person">{{ state.me?.name }}（仅本人）</span>
      </div>
      <label class="report-filter"
        ><span>会议主题</span>
        <el-select
          v-model="selectedTopic"
          clearable
          filterable
          placeholder="全部会议主题"
          no-data-text="该范围暂无会议主题"
          aria-label="筛选会议主题"
          @change="run(load)"
          ><el-option
            v-for="topic in topicOptions"
            :key="topic.key"
            :label="topic.name"
            :value="topic.key" /></el-select
      ></label>
    </div>
  </section>
  <el-alert v-if="loadError" type="error" :closable="false" show-icon>
    <template #title>报表加载失败：{{ loadError }}</template>
    <el-button size="small" @click="run(load)">重试加载</el-button>
  </el-alert>
  <div
    v-if="busy && !data"
    v-loading="busy"
    class="panel"
    style="min-height: 180px"
  />
  <div v-loading="busy" v-if="data">
    <ReportOverview
      :summary="data.summary"
      :statuses="statuses"
      rate-label="任务达成率"
      total-label="工作事项数"
      task-label="任务明细"
      rate-hint="已完成 ÷（已完成 + 未完成）。共同负责的事项按每人的结果统计。"
      @details="showDetails($event)"
    />
    <section id="department-people-results" class="panel report-people-results">
      <div class="panel-heading">
        <h2>
          {{
            seniorOnly
              ? "中高层会议 · 人员任务完成情况"
              : canViewOthers
                ? loadedParams?.department_id
                  ? selectedDepartmentName + " · 人员任务完成情况"
                  : "会议纪要统计 · 人员任务完成情况"
                : "我的已评定任务情况"
          }}
          <span v-if="canViewOthers" class="count">{{
            peopleRows.length
          }}</span>
        </h2>
        <div v-if="seniorOnly" class="report-detail-actions">
          <el-button
            size="small"
            aria-label="查看筛选后的会议"
            @click="
              meetingPage = 1;
              meetingsVisible = true;
            "
            >会议（{{ data.senior_published_meeting_count }}）</el-button
          >
          <el-button
            size="small"
            :aria-label="`查看本次新安排 ${data.new_arrangements_count} 项`"
            aria-haspopup="dialog"
            @click="showDetails('', null, 'new')"
            >本次新安排（{{ data.new_arrangements_count }}）</el-button
          >
        </div>
      </div>
      <el-table
        :data="peopleRows"
        max-height="620"
        :empty-text="
          seniorOnly ? '本期暂无符合条件的已评定事项' : '本期暂无符合条件的人员'
        "
      >
        <el-table-column prop="label" label="姓名" min-width="160">
          <template #default="{ row }"
            >{{ row.label
            }}<span v-if="row.active === false" class="muted small"
              >（已停用）</span
            ></template
          >
        </el-table-column>
        <el-table-column label="会议名称" min-width="260">
          <template #default="{ row }">
            <ul v-if="row.meetings?.length" class="report-person-meetings">
              <li v-for="meeting in row.meetings" :key="meeting.id">
                <span>{{ meeting.title }}</span>
                <time class="muted small" :datetime="meeting.meeting_date">{{
                  meeting.meeting_date
                }}</time>
              </li>
            </ul>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="事项数" min-width="85" align="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :aria-label="`${row.label}：全部 ${row.items} 项`"
              aria-haspopup="dialog"
              @click="showDetails('', row)"
              >{{ row.items }}</el-button
            >
          </template>
        </el-table-column>
        <el-table-column
          v-for="s in statuses"
          :key="s"
          :label="s"
          min-width="85"
          align="right"
        >
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :aria-label="`${row.label}：${s} ${row.counts[s]} 项`"
              :aria-expanded="
                detailsVisible &&
                detailPerson?.id === row.id &&
                detailStatus === s
              "
              aria-haspopup="dialog"
              @click="showDetails(s, row)"
              >{{ row.counts[s] }}</el-button
            >
          </template>
        </el-table-column>
        <el-table-column label="达成率" min-width="160" align="right">
          <template #default="{ row }"
            ><ReportRateMeter :value="row.rate"
          /></template>
        </el-table-column>
      </el-table>
    </section>
    <el-dialog
      v-model="meetingsVisible"
      title="筛选后的中高层会议"
      width="min(860px,94vw)"
      append-to-body
    >
      <p class="muted">
        {{ data.leader_name }} · {{ loadedParams?.start }} —
        {{ loadedParams?.end }}
      </p>
      <el-table
        :data="
          (data.senior_meetings || []).slice(
            (meetingPage - 1) * 10,
            meetingPage * 10,
          )
        "
        max-height="480"
        empty-text="暂无符合条件的已发布会议"
      >
        <el-table-column prop="title" label="会议名称" min-width="220" />
        <el-table-column prop="meeting_date" label="召开日期" width="115" />
        <el-table-column label="参会领导" min-width="130"
          ><template #default="{ row }">{{
            row.leaders.join("、")
          }}</template></el-table-column
        >
      </el-table>
      <el-pagination
        v-if="data.senior_meetings?.length > 10"
        v-model:current-page="meetingPage"
        :page-size="10"
        :total="data.senior_meetings.length"
        layout="prev, pager, next"
      />
    </el-dialog>
    <el-dialog
      v-model="detailsVisible"
      :title="detailTitle"
      width="min(1180px, 94vw)"
      top="5vh"
      append-to-body
      destroy-on-close
    >
      <div id="report-details" class="report-details-content">
        <p class="report-detail-caption">
          {{ loadedParams?.start }} — {{ loadedParams?.end }} · 共
          {{ matchingTotal }} 项
        </p>
        <el-alert
          v-if="detailError"
          :title="detailError"
          type="error"
          :closable="false"
        >
          <el-button link type="primary" @click="loadDetails"
            >重试读取明细</el-button
          >
        </el-alert>
        <el-table
          v-loading="detailBusy"
          :class="{ 'report-arrangement-table': detailKind === 'new' }"
          :data="detailRows"
          max-height="420"
          empty-text="暂无对应明细"
          ><el-table-column
            prop="user_name"
            label="责任人"
            min-width="120"
          /><el-table-column label="来源会议" min-width="230"
            ><template #default="{ row }"
              ><RouterLink
                v-if="canViewOthers"
                class="table-link"
                :to="'/meetings/' + row.meeting_id"
                >{{ row.meeting_title }}</RouterLink
              >
              <span v-else>{{ row.meeting_title }}</span>
              <p class="muted small">
                {{ row.meeting_date }}
              </p></template
            ></el-table-column
          ><el-table-column
            :label="detailKind === 'new' ? '工作内容及要求' : '工作内容'"
            min-width="300"
            ><template #default="{ row }">
              {{ row.content }}
              <p
                v-if="detailKind === 'new' && row.requirements"
                class="muted small"
              >
                {{ row.requirements }}
              </p>
            </template></el-table-column
          ><el-table-column
            prop="due_date"
            label="计划日期"
            width="115"
          /><el-table-column
            v-if="detailKind !== 'new'"
            label="考核结果"
            width="110"
            ><template #default="{ row }"
              ><StatusTag :value="row.status" /></template></el-table-column
          ><el-table-column
            v-if="detailKind === 'new'"
            label="输出物 / 备注"
            min-width="220"
          >
            <template #default="{ row }">
              <div>{{ row.deliverable }}</div>
              <div class="muted small">{{ row.notes }}</div>
            </template></el-table-column
          ></el-table
        >
        <ol
          v-if="detailKind === 'new'"
          v-loading="detailBusy"
          class="report-arrangement-list"
        >
          <li v-for="row in detailRows" :key="row.id">
            <p class="arrangement-content">{{ row.content }}</p>
            <p v-if="row.requirements" class="muted small">
              {{ row.requirements }}
            </p>
            <dl>
              <dt>责任人</dt>
              <dd>{{ row.user_name }}</dd>
              <dt>计划日期</dt>
              <dd>{{ row.due_date || "—" }}</dd>
              <dt>来源会议</dt>
              <dd>{{ row.meeting_title }}<br />{{ row.meeting_date }}</dd>
              <template v-if="row.deliverable"
                ><dt>输出物</dt>
                <dd>{{ row.deliverable }}</dd></template
              >
              <template v-if="row.notes"
                ><dt>备注</dt>
                <dd>{{ row.notes }}</dd></template
              >
            </dl>
          </li>
          <li v-if="!detailBusy && !detailRows.length" class="muted">
            暂无对应明细
          </li>
        </ol>
        <el-pagination
          v-model:current-page="detailPage"
          v-model:page-size="detailPageSize"
          :page-sizes="[10, 20, 50]"
          :total="matchingTotal"
          layout="total, sizes, prev, pager, next"
          aria-label="任务明细分页"
          @size-change="detailPage = 1"
        />
      </div>
      <template #footer
        ><el-button @click="detailsVisible = false"
          >关闭明细</el-button
        ></template
      >
    </el-dialog>
  </div>
</template>
<style scoped>
.report-module-panel {
  overflow-x: auto;
}
.report-module-picker {
  flex-wrap: nowrap;
  white-space: nowrap;
}
.report-meeting-scope {
  width: 100%;
  margin-bottom: 16px;
}
.report-person-meetings {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.report-person-meetings li {
  display: grid;
  gap: 2px;
  overflow-wrap: anywhere;
  line-height: 1.5;
}

.report-people-results > .panel-heading {
  flex-wrap: wrap;
}
.report-detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.report-detail-actions .el-button + .el-button {
  margin-left: 0;
}
.report-filters {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.report-filter {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.report-filter > span {
  color: var(--text-secondary, #61728a);
  font-size: 13px;
}
.report-filter :deep(.el-select) {
  width: 100%;
}
.report-filters.self-only {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.report-filters.with-leader {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.report-filter .report-self-person {
  color: var(--ink);
  font-size: 14px;
  line-height: 32px;
}
.report-details-content {
  --el-color-primary: #245cba;
  display: grid;
  gap: 16px;
  min-width: 0;
  max-height: 68vh;
  overflow: auto;
}
.report-people-results {
  --el-color-primary: #245cba;
}
.report-people-results .muted {
  color: #52627a;
}
.report-detail-caption {
  margin: 0;
  color: #52627a;
  font-size: 13px;
}
.report-arrangement-list {
  display: none;
}
.report-details-content :deep(.el-pagination) {
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}
@media (min-width: 768px) and (max-width: 1440px) {
  .report-filter-panel {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px 16px;
  }
  .report-module-picker {
    margin-bottom: 0;
  }
  .report-meeting-scope {
    margin-bottom: 0;
  }
  .report-filter-panel :deep(.period-filter) {
    flex: 1 1 450px;
    margin: 0;
    padding: 0;
    border-bottom: 0;
  }
  .report-filters {
    flex-basis: 100%;
    gap: 12px;
  }
  .report-filter {
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 8px;
  }
  .report-filter > span {
    white-space: nowrap;
  }
}
@media (max-width: 767px) {
  .report-module-picker {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    width: 100%;
    white-space: normal;
  }
  .report-module-picker :deep(.el-radio-button__inner) {
    width: 100%;
    border: 1px solid var(--el-border-color);
    border-radius: 6px;
    box-shadow: none;
    padding: 10px 8px;
  }
  .report-module-picker :deep(.el-radio-button:last-child) {
    grid-column: 1 / -1;
  }
  .report-arrangement-table {
    display: none;
  }
  .report-arrangement-list {
    display: grid;
    gap: 16px;
    padding: 0;
    margin: 0;
    list-style: none;
  }
  .report-arrangement-list li {
    padding-bottom: 16px;
    border-bottom: 1px solid var(--el-border-color-light);
    overflow-wrap: anywhere;
  }
  .report-arrangement-list .arrangement-content {
    margin: 0 0 8px;
    color: var(--ink);
    white-space: pre-wrap;
  }
  .report-arrangement-list dl {
    display: grid;
    grid-template-columns: 64px minmax(0, 1fr);
    gap: 8px;
    margin: 12px 0 0;
    font-size: 13px;
  }
  .report-arrangement-list dt {
    color: #52627a;
  }
  .report-arrangement-list dd {
    margin: 0;
    white-space: pre-wrap;
  }
  .report-filters,
  .report-filters.with-leader,
  .report-filters.self-only {
    grid-template-columns: minmax(0, 1fr);
  }
  .report-details-content :deep(.el-pagination) {
    justify-content: flex-start;
  }
  .report-details-content :deep(.el-pagination > *) {
    margin: 0;
  }
}
</style>
