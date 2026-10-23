<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api, departmentPath, query, run, statuses } from "../api";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const options = ref<any>(null),
  data = ref<any>(null);
const loading = ref(false),
  error = ref(""),
  page = ref(1);
const q = ref(""),
  person = ref(""),
  department = ref(""),
  source = ref(
    route.query.source === "direct" || route.query.source === "meeting"
      ? route.query.source
      : "",
  ),
  status = ref("");
const dates = ref<[string, string] | null>(null),
  noDeadline = ref(false),
  issuedByMe = ref(route.query.issued_by_me === "1");
const rows = computed(() => data.value?.items || []);
const metrics = computed(() => [
  ["任务总数", data.value?.summary.total || 0],
  ["逾期未完成", data.value?.summary.overdue || 0],
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
    if (active === generation) error.value = e.message || "服务暂时不可用";
    throw e;
  } finally {
    if (active === generation) loading.value = false;
  }
}
function filterChanged() {
  page.value = 1;
  run(load);
}
function clearFilters() {
  q.value = person.value = department.value = source.value = status.value = "";
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
      <p class="eyebrow">TEAM PROGRESS</p>
      <h1>任务总览</h1>
      <p class="muted">
        {{
          options?.scope === "all"
            ? "查看公司人员的任务进展"
            : "查看本部门及子部门人员的任务进展"
        }}
        · 会议任务与直接派发统一跟进
      </p>
    </div>
    <el-button @click="run(load)">刷新进展</el-button>
  </section>
  <section class="panel filters">
    <div class="filter-row">
      <el-input
        v-model="q"
        clearable
        placeholder="任务内容或编号"
        aria-label="搜索任务"
        @keyup.enter="filterChanged"
        @clear="filterChanged"
      />
      <el-select
        v-model="department"
        clearable
        filterable
        placeholder="全部部门"
        aria-label="筛选部门"
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
          v-for="p in options?.users || []"
          :key="p.id"
          :value="p.id"
          :label="`${p.name}${p.active ? '' : '（已停用）'} · ${p.department_id ? departmentPath(p.department_id) : '未分配部门'}`"
        />
      </el-select>
      <el-select
        v-model="source"
        clearable
        placeholder="全部来源"
        aria-label="筛选任务来源"
        @change="filterChanged"
      >
        <el-option value="meeting" label="会议任务" /><el-option
          value="direct"
          label="直接派发"
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
          v-for="s in [...statuses, '待验证', '已撤回']"
          :key="s"
          :value="s"
          :label="s"
        />
      </el-select>
    </div>
    <div class="filter-row overview-date-row">
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
      <span class="muted small">部门筛选包含子部门</span>
      <el-button type="primary" @click="filterChanged">查询</el-button>
      <el-button @click="clearFilters">清空筛选</el-button>
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
      ><strong>{{ count }}</strong
      ><small>当前筛选范围</small>
    </article>
  </div>
  <section v-loading="loading" class="panel">
    <div class="panel-heading">
      <h2>
        任务进展 <span class="count">{{ data?.total || 0 }}</span>
      </h2>
      <span class="muted small"
        >点击任务查看详情；会议达成率仍在统计报表中查看</span
      >
    </div>
    <el-table
      class="overview-desktop"
      :data="rows"
      :empty-text="error ? '加载失败，请重试' : '当前筛选范围没有任务'"
    >
      <el-table-column label="任务内容" min-width="270"
        ><template #default="{ row }"
          ><RouterLink
            class="table-link"
            :to="{ path: '/tasks', query: { id: row.id } }"
            >{{ row.content }}</RouterLink
          >
          <p class="muted small">
            {{ row.code }} · {{ row.source_title }}
          </p></template
        ></el-table-column
      >
      <el-table-column label="责任人" min-width="150"
        ><template #default="{ row }">{{
          row.owner_names.join("、")
        }}</template></el-table-column
      >
      <el-table-column prop="verifier_name" label="验证人" width="110" />
      <el-table-column label="截止日期" width="130"
        ><template #default="{ row }">{{
          row.due_date || "未设期限"
        }}</template></el-table-column
      >
      <el-table-column label="进展" width="120"
        ><template #default="{ row }"
          ><el-tag v-if="row.status === '已撤回'" type="info">已撤回</el-tag
          ><el-tag v-else-if="row.pending" type="warning">待验证</el-tag
          ><el-tag
            v-else-if="!row.due_date && row.status === '未到期'"
            type="info"
            >未设期限</el-tag
          ><StatusTag v-else :value="row.status" /></template
      ></el-table-column>
      <el-table-column label="派发人" width="110"
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
          <span class="muted small">{{ row.code }}</span
          ><el-tag v-if="row.status === '已撤回'" type="info">已撤回</el-tag
          ><el-tag v-else-if="row.pending" type="warning">待验证</el-tag
          ><el-tag
            v-else-if="!row.due_date && row.status === '未到期'"
            type="info"
            >未设期限</el-tag
          ><StatusTag v-else :value="row.status" />
        </div>
        <strong>{{ row.content }}</strong>
        <p class="muted small">
          {{ row.owner_names.join("、") }} · 截止：{{
            row.due_date || "未设期限"
          }}
        </p>
        <p class="muted small">
          {{ row.source_title }} · 验证人：{{ row.verifier_name }}
        </p>
      </RouterLink>
      <el-empty
        v-if="!rows.length && !loading"
        :description="error ? '加载失败，请重试' : '当前筛选范围没有任务'"
      />
    </div>
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.overview-date-row {
  margin-top: 12px;
}
.overview-mobile {
  display: none;
}
.el-pagination {
  padding: 16px;
  justify-content: center;
}
@media (max-width: 1000px) {
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
    margin-top: 12px;
  }
  .overview-date-row :deep(.el-date-editor) {
    max-width: 100%;
    width: 100%;
  }
}
</style>
