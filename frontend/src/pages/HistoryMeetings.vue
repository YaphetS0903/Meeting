<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, departmentPath, kinds, query, rate, run, state } from "../api";
import PeriodFilter from "../components/PeriodFilter.vue";
import { useMeetingListSync } from "../composables/useMeetingListSync";

const route = useRoute(),
  router = useRouter();
const departments = ref<any[]>([]),
  rows = ref<any[]>([]),
  total = ref(0),
  historyTotal = ref(0),
  page = ref(1),
  busy = ref(false),
  error = ref(""),
  search = ref(""),
  dates = ref<Record<string, string>>({}),
  deletingId = ref(""),
  continuingId = ref("");
const department = computed(() =>
  typeof route.query.department === "string" ? route.query.department : "",
);
const series = computed(() =>
  typeof route.query.series === "string" ? route.query.series : "",
);
const seriesName = computed(() =>
  series.value.startsWith("series:")
    ? state.directory.series.find(
        (item: any) => item.id === series.value.slice(7),
      )?.name || "所选会议系列"
    : series.value
      ? "未归入系列"
      : "全部会议系列",
);
const departmentName = computed(() => {
  if (!department.value) return "全部部门";
  return (
    departments.value.find((item: any) => item.key === department.value)
      ?.name || (department.value === "none" ? "专项会议" : "所选部门")
  );
});
let generation = 0;
async function load(silent = false) {
  const current = ++generation;
  if (!silent) {
    busy.value = true;
    error.value = "";
  }
  try {
    const [summary, listing] = await Promise.all([
      api(
        "/history-meeting-departments?" + query({ series_key: series.value }),
      ),
      api(
        "/meetings?" +
          query({
            view: "history",
            department_id: department.value,
            series_key: series.value,
            ...dates.value,
            q: search.value,
            offset: (page.value - 1) * 20,
            limit: 20,
          }),
        "GET",
        undefined,
        { background: silent },
      ),
    ]);
    if (current !== generation) return;
    departments.value = summary.items;
    historyTotal.value = summary.meeting_total;
    rows.value = listing.items;
    total.value = listing.total;
    error.value = "";
    if (page.value > 1 && !rows.value.length) {
      page.value = Math.max(1, Math.ceil(total.value / 20));
      await load(silent);
    }
  } catch (failure: any) {
    if (current === generation && !silent) {
      error.value = failure.message || "历史会议读取失败";
      rows.value = [];
      total.value = 0;
    }
  } finally {
    if (current === generation) busy.value = false;
  }
}
function chooseDepartment(key: string) {
  router.replace({
    path: "/history-meetings",
    query: {
      ...(series.value ? { series: series.value } : {}),
      ...(key ? { department: key } : {}),
    },
  });
}
function changed(value?: Record<string, string>) {
  if (value) dates.value = value;
  page.value = 1;
  load();
}
function meetingLink(row: any) {
  return {
    path: "/meetings/" + row.id,
    query: {
      history_department: department.value || "all",
      ...(series.value ? { history_series: series.value } : {}),
    },
  };
}
async function continueMeeting(row: any) {
  if (continuingId.value) return;
  continuingId.value = row.id;
  try {
    const created = await api("/meetings/" + row.id + "/continue", "POST");
    ElMessage.success("已建立接续会议草稿");
    await router.push({
      path: "/meetings/" + created.id,
      query: row.series_id ? { series: "series:" + row.series_id } : {},
    });
  } finally {
    continuingId.value = "";
  }
}
async function removeMeeting(row: any) {
  if (deletingId.value) return;
  const impact = await api("/meetings/" + row.id + "/deletion-impact");
  if (impact.blocking_meeting_count)
    throw new Error("后续会议仍引用这里的任务，请先处理任务关联再删除。");
  if (impact.active_native_todos)
    throw new Error("这场会议还有未结束的企业微信待办，请先处理。");
  await ElMessageBox.confirm(
    `确定删除“${impact.title}”吗？${impact.published ? `其 ${impact.task_count} 条关联正式任务和会议附件也会删除，原始导入文件保留。` : "草稿及其附件将被删除。"}删除后无法恢复。`,
    "删除历史会议",
    {
      type: "warning",
      confirmButtonText: "确认删除",
      confirmButtonClass: "el-button--danger",
    },
  );
  deletingId.value = row.id;
  try {
    await api("/meetings/" + row.id, "DELETE", {
      expected_draft_version: impact.draft_version,
    });
    generation += 1;
    rows.value = rows.value.filter((item) => item.id !== row.id);
    total.value = Math.max(0, total.value - 1);
    if (!rows.value.length && page.value > 1) page.value -= 1;
    ElMessage.success("会议已删除");
    await load(true);
  } finally {
    deletingId.value = "";
  }
}
watch(
  [department, series],
  () => {
    page.value = 1;
    load();
  },
);
onBeforeUnmount(() => {
  generation += 1;
});
useMeetingListSync(async () => {
  if (!busy.value && !deletingId.value && !continuingId.value) await load(true);
});
</script>

<template>
  <section class="page-heading">
    <div>
      <h1>历史会议</h1>
      <p class="muted">{{ seriesName }} · 按部门查阅往期会议</p>
    </div>
    <div class="actions">
      <el-button
        @click="
          router.push({ path: '/meetings', query: series ? { series } : {} })
        "
        >返回最新会议</el-button
      >
    </div>
  </section>
  <el-alert v-if="error" type="error" :closable="false" show-icon>
    <template #title>{{ error }}</template>
    <el-button size="small" @click="load()">重新加载</el-button>
  </el-alert>
  <div class="history-layout">
    <nav class="panel history-departments" aria-label="历史会议部门">
      <h2>所属部门</h2>
      <button
        type="button"
        :class="{ active: !department }"
        :aria-pressed="!department"
        @click="chooseDepartment('')"
      >
        <span>全部部门</span
        ><span class="history-department-count">{{ historyTotal }}</span>
      </button>
      <button
        v-for="item in departments"
        :key="item.key"
        type="button"
        :class="{ active: department === item.key }"
        :aria-pressed="department === item.key"
        @click="chooseDepartment(item.key)"
      >
        <span
          ><strong>{{ item.name }}</strong
          ><small v-if="item.path !== item.name">{{ item.path }}</small></span
        >
        <span class="history-department-count">{{ item.meeting_count }}</span>
      </button>
      <p v-if="!busy && !departments.length" class="muted small">
        暂无可查看的历史会议
      </p>
    </nav>
    <section class="panel history-content">
      <div class="panel-heading">
        <h2>
          {{ departmentName }} <span class="count">{{ total }}</span>
        </h2>
      </div>
      <div class="history-filters">
        <PeriodFilter allow-all @change="changed" />
        <div class="history-search">
          <el-input
            v-model="search"
            placeholder="搜索会议名称"
            aria-label="搜索历史会议"
            clearable
            @keyup.enter="changed()"
            @clear="changed()"
          /><el-button @click="changed()">查询</el-button>
        </div>
      </div>
      <el-table
        v-loading="busy"
        :data="rows"
        row-key="id"
        empty-text="暂无符合条件的历史会议"
        class="history-meetings-table"
      >
        <el-table-column label="会议名称" min-width="230"
          ><template #default="{ row }">
            <router-link :to="meetingLink(row)" class="table-link">{{
              row.title
            }}</router-link>
            <div class="small muted">
              {{ kinds[row.kind as keyof typeof kinds] || row.kind }} ·
              {{ row.historical ? "历史导入" : "在线会议" }}
            </div>
          </template></el-table-column
        >
        <el-table-column label="所属部门" min-width="140"
          ><template #default="{ row }">{{
            row.department_name || departmentPath(row.department_id)
          }}</template></el-table-column
        >
        <el-table-column prop="meeting_date" label="会议日期" width="120" />
        <el-table-column prop="host_name" label="主持人" width="90" />
        <el-table-column label="工作事项" width="85"
          ><template #default="{ row }">{{
            row.item_count
          }}</template></el-table-column
        >
        <el-table-column label="达成率" width="85"
          ><template #default="{ row }">{{
            rate(row.rate)
          }}</template></el-table-column
        >
        <el-table-column label="状态" width="110"
          ><template #default="{ row }">
            <el-tag v-if="!row.published_version" type="info" size="small"
              >草稿</el-tag
            >
            <el-tag
              v-else
              :type="(row.is_revising ?? row.has_draft) ? 'warning' : 'success'"
              size="small"
              >{{
                (row.is_revising ?? row.has_draft) ? "修订中" : "已发布"
              }}</el-tag
            >
          </template></el-table-column
        >
        <el-table-column label="操作" width="155" fixed="right"
          ><template #default="{ row }">
            <el-button
              v-if="row.can_continue"
              link
              type="primary"
              :loading="continuingId === row.id"
              @click="run(() => continueMeeting(row))"
              >接续新建</el-button
            >
            <el-button
              v-if="row.can_delete"
              link
              type="danger"
              :loading="deletingId === row.id"
              @click="run(() => removeMeeting(row))"
              >删除</el-button
            >
          </template></el-table-column
        >
      </el-table>
      <el-pagination
        v-if="total > 20"
        v-model:current-page="page"
        :page-size="20"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="load()"
      />
    </section>
  </div>
</template>

<style scoped>
.history-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.history-departments {
  padding: 18px 12px;
}
.history-departments h2 {
  margin: 0 10px 14px;
  font-size: 15px;
}
.history-departments button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 12px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.history-departments button:hover {
  background: #f4f7fc;
}
.history-departments button.active {
  border-color: #cddfff;
  background: #edf4ff;
  color: #1765d1;
}
.history-departments button:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}
.history-departments strong {
  font-weight: 500;
}
.history-departments small {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.history-department-count {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
.history-content {
  min-width: 0;
  overflow: hidden;
}
.history-filters {
  padding: 16px 20px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.history-search {
  display: flex;
  gap: 8px;
  width: 270px;
}
.history-content .el-pagination {
  margin: 16px 20px;
  justify-content: flex-end;
}
@media (max-width: 900px) {
  .history-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
  }
  .history-departments {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 12px;
  }
  .history-departments h2 {
    flex-basis: 100%;
    margin: 0 0 6px;
  }
  .history-departments button {
    width: auto;
    max-width: 100%;
    gap: 18px;
  }
  .history-departments small {
    display: none;
  }
  .history-search {
    width: 100%;
  }
  .history-filters {
    padding: 12px;
  }
}
</style>
