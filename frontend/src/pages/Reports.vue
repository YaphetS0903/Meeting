<script setup lang="ts">
import { ref, computed } from "vue";
import {
  api,
  state,
  query,
  run,
  rate,
  download,
  statuses,
  kinds,
  departmentPath,
} from "../api";
import PeopleSelect from "../components/PeopleSelect.vue";
import PeriodFilter from "../components/PeriodFilter.vue";
import StatusTag from "../components/StatusTag.vue";
const range = ref<any>({}),
  group = ref("meeting_department"),
  department = ref(""),
  selectedPerson = ref<string | null>(null),
  kind = ref(""),
  data = ref<any>(null),
  busy = ref(false),
  loadError = ref(""),
  loadedParams = ref<Record<string, any> | null>(null),
  detailStatus = ref(""),
  detailGroup = ref<any>(null);
const params = computed(() => ({
  ...range.value,
  group_by: group.value,
  department_id: department.value,
  person_id: selectedPerson.value,
  kind: kind.value,
}));
let generation = 0;
async function load() {
  const activeGeneration = ++generation;
  const requested = { ...params.value };
  busy.value = true;
  data.value = null;
  loadedParams.value = null;
  loadError.value = "";
  try {
    const result = await api("/reports?" + query(requested));
    if (activeGeneration !== generation) return;
    data.value = result;
    loadedParams.value = requested;
    detailGroup.value = null;
    detailStatus.value = "";
  } catch (error: any) {
    if (activeGeneration === generation)
      loadError.value = error.message || "服务暂时不可用";
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
    data.value?.details.filter(
      (x) =>
        (!detailStatus.value || x.status === detailStatus.value) &&
        (!detailGroup.value ||
          (group.value === "person"
            ? x.user_id === detailGroup.value.id
            : group.value === "person_department"
              ? x.person_department_name === detailGroup.value.label
              : group.value === "kind"
                ? x.kind === detailGroup.value.id
                : x.department_name === detailGroup.value.label)),
    ) || [],
);
</script>
<template>
  <section class="page-heading">
    <div>
      <p class="eyebrow">PERFORMANCE</p>
      <h1>统计报表</h1>
      <p class="muted">达成率 = 已完成人次 ÷（已完成人次 + 未完成人次）</p>
    </div>
    <el-button
      :disabled="busy || !data || !loadedParams || !!loadError"
      @click="
        run(() =>
          download(
            '/reports/export?' + query(loadedParams || {}),
            '会议考核报表.xlsx',
          ),
        )
      "
      >导出 Excel</el-button
    >
  </section>
  <section class="panel filters">
    <PeriodFilter @change="changed" />
    <div class="filter-row">
      <el-select v-model="group" aria-label="统计维度" @change="run(load)"
        ><el-option label="会议所属部门" value="meeting_department" /><el-option
          label="人员主部门（考核时）"
          value="person_department" /><el-option
          label="人员"
          value="person" /><el-option
          label="会议类型"
          value="kind" /></el-select
      ><el-select
        v-model="department"
        placeholder="全部部门"
        clearable
        aria-label="筛选部门"
        @change="run(load)"
        ><el-option
          v-for="d in state.directory.departments"
          :key="d.id"
          :value="d.id"
          :label="departmentPath(d.id)"
      /></el-select>
      <PeopleSelect
        v-model="selectedPerson"
        include-inactive
        placeholder="全部人员（含已停用）"
        aria-label="筛选人员"
        @update:model-value="run(load)"
      />
      <el-select
        v-model="kind"
        clearable
        placeholder="全部会议类型"
        aria-label="筛选会议类型"
        @change="run(load)"
        ><el-option
          v-for="(label, key) in kinds"
          :key="key"
          :label="label"
          :value="key"
      /></el-select>
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
    <el-alert
      v-if="data.quality === '待校验'"
      :title="
        '待校验：本范围还有 ' +
        data.pending_meetings +
        ' 场会议未确认、' +
        data.pending_file_issues +
        ' 项文件解析问题待核实，当前结果不是最终结果。' +
        data.quality_note
      "
      type="warning"
      :closable="false"
      show-icon
    />
    <div class="stat-grid">
      <article class="stat">
        <span>任务达成率</span><strong>{{ rate(data.summary.rate) }}</strong
        ><small>仅包含已完成与未完成人次</small>
      </article>
      <article class="stat">
        <span>工作事项数</span><strong>{{ data.summary.items }}</strong
        ><small>同一会议同一事项计一项</small>
      </article>
      <article class="stat">
        <span>考核人次</span><strong>{{ data.summary.person_count }}</strong
        ><small>共同责任人各计一次</small>
      </article>
      <article class="stat">
        <span>已完成人次</span
        ><strong>{{ data.summary.counts["已完成"] }}</strong
        ><small>月度按原始人次重新计算</small>
      </article>
    </div>
    <section class="panel">
      <div class="panel-heading">
        <h2>汇总结果</h2>
        <span class="muted small">点击数字查看明细</span>
      </div>
      <el-table :data="data.groups" empty-text="当前周期暂无已确认考核数据"
        ><el-table-column
          prop="label"
          label="统计对象"
          min-width="160"
        /><el-table-column prop="items" label="事项数" /><el-table-column
          prop="person_count"
          label="考核人次"
        /><el-table-column v-for="s in statuses" :key="s" :label="s"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              @click="
                detailGroup = row;
                detailStatus = s;
              "
              >{{ row.counts[s] }}</el-button
            ></template
          ></el-table-column
        ><el-table-column label="达成率"
          ><template #default="{ row }"
            ><strong>{{ rate(row.rate) }}</strong></template
          ></el-table-column
        ></el-table
      >
    </section>
    <section class="panel">
      <div class="panel-heading">
        <h2>
          考核明细 <span class="count">{{ details.length }}</span>
        </h2>
        <el-button
          v-if="detailGroup || detailStatus"
          @click="
            detailGroup = null;
            detailStatus = '';
          "
          >清除明细筛选</el-button
        >
      </div>
      <el-table :data="details" max-height="620" empty-text="暂无对应明细"
        ><el-table-column
          prop="user_name"
          label="责任人"
          width="100" /><el-table-column label="来源会议" min-width="230"
          ><template #default="{ row }"
            ><RouterLink
              class="table-link"
              :to="'/meetings/' + row.meeting_id"
              >{{ row.meeting_title }}</RouterLink
            >
            <p class="muted small">
              {{ row.meeting_date }} · V{{ row.meeting_version }}
            </p></template
          ></el-table-column
        ><el-table-column
          prop="content"
          label="工作内容"
          min-width="300" /><el-table-column
          prop="due_date"
          label="计划日期"
          width="115" /><el-table-column label="考核结果" width="110"
          ><template #default="{ row }"
            ><StatusTag :value="row.status" /></template></el-table-column
      ></el-table>
    </section>
  </div>
</template>
