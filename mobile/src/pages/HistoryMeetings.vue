<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, departmentPath, kinds, query, rate, run } from "../api/client";
import { chineseError } from "../api/errorMessages";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();

const departments = ref<any[]>([]);
const historyTotal = ref(0);
const rows = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const busy = ref(false);
const refreshing = ref(false);
const error = ref("");
const search = ref("");

const department = computed(() =>
  typeof route.query.department === "string" ? route.query.department : "",
);
const series = computed(() =>
  typeof route.query.series === "string" ? route.query.series : "",
);

async function load(append = false) {
  if (!append) {
    busy.value = true;
    error.value = "";
  }
  try {
    const [summary, listing] = await Promise.all([
      api("/history-meeting-departments?" + query({ series_key: series.value })),
      api(
        "/meetings?" +
          query({
            view: "history",
            department_id: department.value,
            series_key: series.value,
            q: search.value.trim(),
            offset: (page.value - 1) * 20,
            limit: 20,
          }),
      ),
    ]);
    departments.value = summary.items || [];
    historyTotal.value = summary.meeting_total || 0;
    total.value = listing.total || 0;
    rows.value = append
      ? [...rows.value, ...(listing.items || [])]
      : listing.items || [];
  } catch (e: any) {
    error.value = chineseError(e, "历史会议读取失败");
  } finally {
    busy.value = false;
    refreshing.value = false;
  }
}

function chooseDepartment(key: string) {
  page.value = 1;
  void router.replace({
    path: "/history-meetings",
    query: {
      ...(series.value ? { series: series.value } : {}),
      ...(key ? { department: key } : {}),
    },
  });
}

function openMeeting(row: any) {
  void router.push({
    path: `/meetings/${row.id}`,
    query: {
      history_department: department.value || "all",
      ...(series.value ? { history_series: series.value } : {}),
    },
  });
}

watch([department, series], () => {
  page.value = 1;
  void run(() => load(false));
});

onMounted(() => run(() => load(false)));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <div style="display: flex; align-items: center; gap: 4px; min-width: 0">
          <button
            type="button"
            class="m-back-btn"
            @click="router.push({ path: '/meetings', query: series ? { series } : {} })"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <h1 class="m-header-title">历史会议</h1>
        </div>
        <span class="m-muted tabular">共 {{ total }} 场</span>
      </div>

      <div style="padding: 0 16px 10px; display: flex; flex-direction: column; gap: 8px">
        <div style="display: flex; gap: 8px">
          <div class="m-search-box" style="flex: 1">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="7" />
              <line x1="20" y1="20" x2="16.65" y2="16.65" />
            </svg>
            <input
              v-model="search"
              placeholder="搜索会议名称"
              @keyup.enter="
                page = 1;
                run(() => load(false));
              "
            />
          </div>
          <button
            type="button"
            class="m-btn m-btn-sm m-btn-soft"
            @click="
              page = 1;
              run(() => load(false));
            "
          >
            查询
          </button>
        </div>

        <div class="m-chip-row">
          <button
            type="button"
            class="m-chip m-chip-soft"
            :class="{ active: !department }"
            @click="chooseDepartment('')"
          >
            全部部门（{{ historyTotal }}）
          </button>
          <button
            v-for="d in departments"
            :key="d.key"
            type="button"
            class="m-chip m-chip-soft"
            :class="{ active: department === d.key }"
            @click="chooseDepartment(d.key)"
          >
            {{ d.name }}（{{ d.meeting_count }}）
          </button>
        </div>
      </div>
    </header>

    <van-pull-refresh
      v-model="refreshing"
      @refresh="
        page = 1;
        run(() => load(false));
      "
    >
      <section class="m-section">
        <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
        <div v-if="busy && !rows.length" class="m-empty">
          <van-loading size="22">读取历史会议…</van-loading>
        </div>
        <template v-else-if="rows.length">
          <article
            v-for="row in rows"
            :key="row.id"
            class="m-card hm-card"
            @click="openMeeting(row)"
          >
            <div style="min-width: 0; flex: 1">
              <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap">
                <h2 class="hm-title">{{ row.title }}</h2>
                <StatusTag v-if="row.historical" value="历史导入" tone="gray" />
              </div>
              <div class="m-muted tabular" style="margin-top: 5px">
                {{ row.meeting_date }} ·
                {{ row.department_name || departmentPath(row.department_id) || kinds[row.kind] || "专项会议" }}
                <template v-if="row.rate != null"> · 达成率 {{ rate(row.rate) }}</template>
              </div>
            </div>
            <svg class="m-chevron" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </article>

          <button
            v-if="rows.length < total"
            type="button"
            class="m-btn"
            style="width: 100%"
            @click="
              page += 1;
              run(() => load(true));
            "
          >
            加载更多（{{ rows.length }}/{{ total }}）
          </button>
        </template>
        <div v-else class="m-empty">暂无符合条件的历史会议</div>
      </section>
    </van-pull-refresh>
  </div>
</template>

<style scoped>
.hm-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  cursor: pointer;
}
.hm-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--m-ink);
  line-height: 1.4;
}
</style>
