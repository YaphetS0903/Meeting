<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  api,
  departmentPath,
  query,
  run,
  state,
  statuses,
} from "../api/client";
import { chineseError } from "../api/errorMessages";
import { isEmployeeAccount, withDirectoryDetails } from "../api/people";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();

const options = ref<any>(null);
const data = ref<any>(null);
const rows = ref<any[]>([]);
const page = ref(1);
const busy = ref(false);
const refreshing = ref(false);
const error = ref("");
const filterOpen = ref(false);

const q = ref("");
const department = ref("");
const personId = ref("");
const status = ref("");
const source = ref(
  route.query.source === "direct" || route.query.source === "meeting"
    ? String(route.query.source)
    : "",
);
const series = ref("");
const start = ref("");
const end = ref("");
const noDeadline = ref(false);
const issuedByMe = ref(route.query.issued_by_me === "1");

const personOptions = computed(() =>
  withDirectoryDetails(
    options.value?.users || [],
    state.directory.users || [],
  ).filter(isEmployeeAccount),
);

const metrics = computed(() => {
  const s = data.value?.summary || {};
  return [
    { label: "任务总数", value: s.total || 0, filterStatus: "" },
    { label: "未完成", value: s.overdue || 0, filterStatus: "未完成", tone: "#b91c1c" },
    { label: "到期未验证", value: s.unverified_due || 0, filterStatus: "到期未验证", tone: "#2563eb" },
    { label: "待验证", value: s.pending || 0, filterStatus: "待验证", tone: "#a16207" },
    { label: "已关闭", value: s.closed || 0, filterStatus: "关闭", tone: "#15803d" },
    { label: "已撤回", value: s.withdrawn || 0, filterStatus: "已撤回", tone: "#71717a" },
  ];
});

async function load(append = false) {
  if (!append) {
    busy.value = true;
    error.value = "";
  }
  try {
    const params = {
      q: q.value.trim(),
      person_id: personId.value,
      department_id: department.value,
      source: source.value,
      series_id: series.value,
      status: status.value,
      no_deadline: noDeadline.value || undefined,
      issued_by_me: issuedByMe.value || undefined,
      start: noDeadline.value ? undefined : start.value,
      end: noDeadline.value ? undefined : end.value,
      offset: (page.value - 1) * 20,
      limit: 20,
    };
    const [scope, result] = await Promise.all([
      options.value ? Promise.resolve(options.value) : api("/task-overview/options"),
      api("/task-overview?" + query(params)),
    ]);
    options.value = scope;
    data.value = result;
    rows.value = append ? [...rows.value, ...(result.items || [])] : result.items || [];
  } catch (e: any) {
    error.value = chineseError(e, "任务总览加载失败");
  } finally {
    busy.value = false;
    refreshing.value = false;
  }
}

function search() {
  page.value = 1;
  return load(false);
}

function clickMetric(filterStatus: string) {
  status.value = status.value === filterStatus ? "" : filterStatus;
  void run(search);
}

function resetFilters() {
  q.value = "";
  department.value = "";
  personId.value = "";
  status.value = "";
  source.value = "";
  series.value = "";
  start.value = "";
  end.value = "";
  noDeadline.value = false;
  issuedByMe.value = false;
  filterOpen.value = false;
  void run(search);
}

onMounted(() => run(search));
</script>

<template>
  <div class="m-page-sub">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <button
          type="button"
          class="m-btn m-btn-sm"
          style="border: none; padding: 0 6px"
          @click="router.back()"
        >
          ‹ 返回
        </button>
        <h1 class="m-header-title">任务总览</h1>
        <button
          type="button"
          class="m-btn m-btn-sm"
          @click="filterOpen = true"
        >
          筛选
        </button>
      </div>
      <div style="padding: 0 14px 10px; display: flex; gap: 6px">
        <input
          v-model="q"
          class="m-input"
          style="padding: 8px 10px; font-size: 13px"
          placeholder="搜索任务内容…"
          @keyup.enter="run(search)"
        />
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-primary"
          @click="run(search)"
        >
          查询
        </button>
      </div>
    </header>

    <van-pull-refresh v-model="refreshing" @refresh="run(search)">
      <section class="m-section">
        <!-- 6-cell Light-Blue Tinted KPI Card (Screen 16) -->
        <div v-if="data" class="m-card-blue-soft ov-grid">
          <button
            v-for="m in metrics"
            :key="m.label"
            type="button"
            class="ov-stat"
            :class="{
              active: status === m.filterStatus && m.filterStatus !== '',
              'ov-rose': m.filterStatus === '未完成',
              'ov-amber': m.filterStatus === '待验证',
            }"
            @click="clickMetric(m.filterStatus)"
          >
            <span class="m-muted">{{ m.label }}</span>
            <strong class="tabular" :style="{ color: m.tone || '#18181b' }">
              {{ m.value }}
            </strong>
          </button>
        </div>

        <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>

        <div v-if="busy && !rows.length" class="m-empty">
          <van-loading size="22">加载任务进展…</van-loading>
        </div>

        <template v-else-if="rows.length">
          <div class="m-muted">
            当前筛选共 {{ data?.total || rows.length }} 条（点上方指标卡可快速切换状态）
          </div>
          <article
            v-for="row in rows"
            :key="row.id"
            class="m-card"
            style="display: flex; flex-direction: column; gap: 6px; cursor: pointer"
            @click="router.push({ path: '/tasks', query: { id: row.id } })"
          >
            <div style="display: flex; justify-content: space-between; gap: 8px">
              <span class="m-muted">
                {{ (row.department_names || []).join("、") || "未分配部门" }}
              </span>
              <StatusTag
                :value="
                  row.status === '已撤回'
                    ? '已撤回'
                    : row.pending
                      ? '待验证'
                      : !row.due_date && row.status === '未到期'
                        ? '未设期限'
                        : row.status || row.progress_status
                "
              />
            </div>
            <div class="m-work-text" style="font-weight: 600">
              {{ row.content }}
            </div>
            <div class="m-muted">
              {{ [row.source_meeting_date, row.source_title].filter(Boolean).join(" · ") || "直接派发" }}
            </div>
            <div
              class="m-muted"
              style="
                display: flex;
                justify-content: space-between;
                padding-top: 6px;
                border-top: 1px solid #f4f4f5;
              "
            >
              <span>责任人：{{ (row.owner_names || []).join("、") || "—" }}</span>
              <span class="tabular">截止 {{ row.due_date || "未设" }}</span>
            </div>
          </article>

          <button
            v-if="rows.length < (data?.total || 0)"
            type="button"
            class="m-btn"
            style="width: 100%"
            @click="
              page += 1;
              run(() => load(true));
            "
          >
            加载更多（{{ rows.length }}/{{ data.total }}）
          </button>
        </template>

        <div v-else class="m-empty">当前筛选范围没有任务</div>
      </section>
    </van-pull-refresh>

    <van-popup
      v-model:show="filterOpen"
      position="bottom"
      round
      :style="{ maxHeight: '86vh', display: 'flex', flexDirection: 'column' }"
    >
      <div style="padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px">
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-size: 16px; font-weight: 700">筛选任务总览</span>
          <button type="button" class="m-btn m-btn-sm" @click="resetFilters">
            清空筛选
          </button>
        </div>

        <div>
          <label class="m-label">所属部门</label>
          <select v-model="department" class="m-select">
            <option value="">全部部门</option>
            <option
              v-for="d in options?.departments || []"
              :key="d.id"
              :value="d.id"
            >
              {{ departmentPath(d.id) }}
            </option>
          </select>
        </div>

        <div>
          <label class="m-label">责任人</label>
          <select v-model="personId" class="m-select">
            <option value="">全部责任人</option>
            <option v-for="p in personOptions" :key="p.id" :value="p.id">
              {{ p.name }}{{ p.active ? "" : "（已停用）" }}
            </option>
          </select>
        </div>

        <div>
          <label class="m-label">进展状态</label>
          <select v-model="status" class="m-select">
            <option value="">全部状态</option>
            <option
              v-for="s in [...statuses, '到期未验证', '未到期验证', '待验证', '已撤回']"
              :key="s"
              :value="s"
            >
              {{ s }}
            </option>
          </select>
        </div>

        <div>
          <label class="m-label">任务来源</label>
          <select v-model="source" class="m-select">
            <option value="">全部来源</option>
            <option value="meeting">会议任务</option>
            <option value="direct">直接派发</option>
          </select>
        </div>

        <div v-if="options?.series?.length">
          <label class="m-label">会议系列</label>
          <select v-model="series" class="m-select">
            <option value="">全部会议系列</option>
            <option v-for="s in options.series" :key="s.id" :value="s.id">
              {{ s.name }}
            </option>
          </select>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px">
          <div>
            <label class="m-label">截止从</label>
            <input v-model="start" type="date" class="m-input" :disabled="noDeadline" />
          </div>
          <div>
            <label class="m-label">截止至</label>
            <input v-model="end" type="date" class="m-input" :disabled="noDeadline" />
          </div>
        </div>

        <div style="display: flex; gap: 16px; font-size: 13px">
          <label style="display: flex; align-items: center; gap: 6px">
            <input v-model="noDeadline" type="checkbox" /> 只看未设期限
          </label>
          <label style="display: flex; align-items: center; gap: 6px">
            <input v-model="issuedByMe" type="checkbox" /> 只看我派发
          </label>
        </div>

        <button
          type="button"
          class="m-btn m-btn-primary"
          style="width: 100%"
          @click="
            filterOpen = false;
            run(search);
          "
        >
          应用筛选
        </button>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.ov-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding: 12px;
}
.ov-stat {
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.72);
  border-radius: 10px;
  padding: 10px 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  cursor: pointer;
}
.ov-stat strong {
  font-size: 20px;
  font-weight: 700;
}
.ov-stat.ov-rose {
  background: var(--m-danger-soft);
}
.ov-stat.ov-amber {
  background: var(--m-warning-soft);
}
.ov-stat.active {
  border-color: var(--m-primary);
  background: #ffffff;
}
</style>
