<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  api,
  departmentLeaf,
  download,
  isLowReportRate,
  localDate,
  query,
  rate,
  run,
  state,
} from "../api/client";
import { chineseError } from "../api/errorMessages";
import {
  authorizedReportDepartments,
  reportDepartmentOptions,
} from "../api/reportDepartments";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();

const mode = ref<"performance" | "regular" | "direct">(
  route.query.view === "regular" || route.query.view === "direct"
    ? (route.query.view as any)
    : "performance",
);
const meetingScope = ref<"regular" | "senior">("regular");
const periodType = ref<"month" | "custom">("month");
const monthValue = ref(localDate().slice(0, 7));
const startDate = ref("");
const endDate = ref("");
const department = ref("");
const leaderId = ref("");
const peopleSearch = ref("");
const visibleCount = ref(20);

const access = ref<any>(null);
const data = ref<any>(null);
const busy = ref(false);
const error = ref("");
const loadedParams = ref<Record<string, any> | null>(null);

// Detail bottom sheet
const detailOpen = ref(false);
const detailTitle = ref("");
const detailRows = ref<any[]>([]);
const detailTotal = ref(0);
const detailPage = ref(1);
const detailBusy = ref(false);
const detailSource = ref<"regular" | "direct">("regular");
const detailContext = ref<any>(null);

const canViewOthers = computed(
  () => access.value?.mode === "all" || access.value?.mode === "departments",
);
const canViewSenior = computed(() => Boolean(access.value?.can_view_senior_meetings));

const departmentOptions = computed(() => {
  const list = access.value?.departments || [];
  return access.value?.mode === "all"
    ? reportDepartmentOptions(list)
    : authorizedReportDepartments(
        list,
        access.value?.department_ids || [],
        access.value?.root_department_ids || [],
      );
});

const dateRangeParams = computed(() => {
  if (periodType.value === "month" && monthValue.value) {
    const [y, m] = monthValue.value.split("-").map(Number);
    const lastDay = new Date(y, m, 0).getDate();
    return {
      start: `${monthValue.value}-01`,
      end: `${monthValue.value}-${String(lastDay).padStart(2, "0")}`,
    };
  }
  return {
    start: startDate.value || undefined,
    end: endDate.value || undefined,
  };
});

async function load() {
  busy.value = true;
  error.value = "";
  visibleCount.value = 20;
  try {
    const senior = mode.value === "regular" && meetingScope.value === "senior";
    const rights = await api(
      "/reports/access?" +
        query({
          senior_only: senior || undefined,
          leader_id: senior ? leaderId.value : undefined,
        }),
    );
    access.value = rights;
    if (
      department.value &&
      !departmentOptions.value.some((d) => d.id === department.value)
    )
      department.value = "";

    const baseParams: Record<string, any> = {
      ...dateRangeParams.value,
      department_id: canViewOthers.value ? department.value : undefined,
      person_id: canViewOthers.value ? undefined : state.me?.id,
    };

    let res: any;
    if (mode.value === "performance") {
      res = await api("/reports/performance?" + query(baseParams));
    } else if (mode.value === "direct") {
      res = await api("/reports/direct?" + query(baseParams));
    } else {
      const regularParams = {
        ...baseParams,
        group_by: "department_people",
        senior_only: senior || undefined,
        leader_id: senior ? leaderId.value : undefined,
        include_details: false,
      };
      res = await api("/reports?" + query(regularParams));
      Object.assign(baseParams, regularParams);
    }
    data.value = res;
    loadedParams.value = baseParams;
  } catch (e: any) {
    error.value = chineseError(e, "统计报表加载失败");
    data.value = null;
  } finally {
    busy.value = false;
  }
}

const userDeptMap = computed(() => {
  const map = new Map<string, string>();
  for (const u of access.value?.users || state.directory.users || []) {
    if (u?.id && u.department_id) {
      map.set(u.id, departmentLeaf(u.department_id));
    }
  }
  return map;
});

const summary = computed(() => {
  if (!data.value) return null;
  if (mode.value === "performance") {
    const groups = data.value.groups || [];
    const selfRow = groups.find((row: any) => row.id === state.me?.id);
    const totalObj = canViewOthers.value
      ? data.value.summary?.total || {}
      : selfRow?.total || data.value.summary?.total || {};
    const total = Number(totalObj.items ?? 0);
    const completed = Number(totalObj.completed ?? 0);
    const incomplete = Number(totalObj.incomplete ?? 0);
    const notDue = Number(totalObj.not_due ?? 0);
    const excluded = Number(totalObj.excluded ?? 0);
    const closed = Number(totalObj.closed ?? 0);
    const rateVal = totalObj.rate ?? null;
    return {
      title: "绩效达成率",
      totalLabel: "任务总数",
      rateVal,
      total,
      completed,
      incomplete,
      segments: [
        { label: `已完成 ${completed}`, count: completed, color: "#10b981" },
        { label: `未完成 ${incomplete}`, count: incomplete, color: "#ef4444" },
        { label: `未到期 ${notDue}`, count: notDue, color: "#3b82f6" },
        { label: `已关闭 ${closed}`, count: closed, color: "#14b8a6" },
        { label: `不统计 ${excluded}`, count: excluded, color: "#a1a1aa" },
      ],
    };
  }
  const s = data.value.summary || {};
  const counts = s.counts || {};
  const total = Number(s.items ?? 0);
  const completed = Number(counts["已完成"] ?? 0);
  const incomplete = Number(counts["未完成"] ?? 0);
  const rateVal = s.rate ?? null;
  if (mode.value === "direct") {
    const pending = Number(counts["待验证"] ?? 0);
    const notDue = Number(counts["未到期"] ?? 0);
    return {
      title: "到期完成率",
      totalLabel: "任务总数",
      rateVal,
      total,
      completed,
      incomplete,
      segments: [
        { label: `已完成 ${completed}`, count: completed, color: "#10b981" },
        { label: `待验证 ${pending}`, count: pending, color: "#f59e0b" },
        { label: `未完成 ${incomplete}`, count: incomplete, color: "#ef4444" },
        { label: `未到期 ${notDue}`, count: notDue, color: "#3b82f6" },
      ],
    };
  }
  const notDue = Number(counts["未到期"] ?? 0);
  const excluded = Number(counts["不统计"] ?? 0);
  const closed = Number(counts["关闭"] ?? counts["已关闭"] ?? 0);
  return {
    title: "任务达成率",
    totalLabel: "工作事项数",
    rateVal,
    total,
    completed,
    incomplete,
    segments: [
      { label: `已完成 ${completed}`, count: completed, color: "#10b981" },
      { label: `未完成 ${incomplete}`, count: incomplete, color: "#ef4444" },
      { label: `未到期 ${notDue}`, count: notDue, color: "#3b82f6" },
      { label: `关闭 ${closed}`, count: closed, color: "#14b8a6" },
      { label: `不统计 ${excluded}`, count: excluded, color: "#a1a1aa" },
    ],
  };
});

const ringOffset = computed(() => {
  const v = summary.value?.rateVal;
  if (v == null) return 100;
  return Math.max(0, Math.min(100, 100 - Math.round(v * 100)));
});

const statusSegments = computed(() => {
  const s = summary.value;
  if (!s) return [];
  const totalCount = s.segments.reduce((acc, item) => acc + item.count, 0);
  if (!totalCount) return [];
  return s.segments
    .filter((item) => item.count > 0)
    .map((item) => ({
      ...item,
      pct: Math.max(2, Math.round((item.count / totalCount) * 100)),
    }));
});

const peopleRows = computed(() => {
  if (!data.value) return [];
  if (mode.value === "performance") {
    return (data.value.groups || []).map((r: any) => {
      const t = r.total || {};
      return {
        id: r.id,
        name: r.label || r.name || "—",
        active: r.active !== false,
        dept: r.department_name || userDeptMap.value.get(r.id) || "",
        total: Number(t.items ?? 0),
        completed: Number(t.completed ?? 0),
        incomplete: Number(t.incomplete ?? 0),
        notDue: Number(t.not_due ?? 0),
        closed: Number(t.closed ?? 0),
        rateVal: t.rate ?? null,
        raw: r,
      };
    });
  }
  const list = data.value.groups || [];
  if (!list.length && data.value.summary && state.me) {
    const s = data.value.summary;
    const c = s.counts || {};
    return [
      {
        id: state.me.id,
        name: state.me.name,
        active: true,
        dept: userDeptMap.value.get(state.me.id) || "",
        total: Number(s.items ?? 0),
        completed: Number(c["已完成"] ?? 0),
        incomplete: Number(c["未完成"] ?? 0),
        notDue: Number(c["未到期"] ?? 0),
        closed: Number(c["关闭"] ?? c["已关闭"] ?? 0),
        rateVal: s.rate ?? null,
        raw: { id: state.me.id, label: state.me.name, ...s },
      },
    ];
  }
  return list.map((r: any) => {
    const c = r.counts || {};
    return {
      id: r.id,
      name: r.label || r.name || "—",
      active: r.active !== false,
      dept: r.department_name || userDeptMap.value.get(r.id) || "",
      total: Number(r.items ?? 0),
      completed: Number(c["已完成"] ?? 0),
      incomplete: Number(c["未完成"] ?? 0),
      notDue: Number(c["未到期"] ?? 0),
      closed: Number(c["关闭"] ?? c["已关闭"] ?? 0),
      rateVal: r.rate ?? null,
      raw: r,
    };
  });
});

const filteredPeople = computed(() => {
  const kw = peopleSearch.value.trim().toLowerCase();
  if (!kw) return peopleRows.value;
  return peopleRows.value.filter(
    (p: any) =>
      String(p.name || "").toLowerCase().includes(kw) ||
      String(p.dept || "").toLowerCase().includes(kw),
  );
});

const shownPeople = computed(() =>
  filteredPeople.value.slice(0, visibleCount.value),
);

const detailStatusBuckets = computed(() => {
  const ctx = detailContext.value;
  if (!ctx?.person?.raw) return [];
  const raw = ctx.person.raw;
  if (mode.value === "performance") {
    const src =
      (detailSource.value === "regular" ? raw.regular : raw.direct) || {};
    return [
      { label: "事项数", status: "", count: Number(src.items ?? 0), tone: "primary" },
      { label: "已完成", status: "已完成", count: Number(src.completed ?? 0), tone: "success" },
      { label: "未完成", status: "未完成", count: Number(src.incomplete ?? 0), tone: "danger" },
      { label: "未到期", status: "未到期", count: Number(src.not_due ?? 0), tone: "info" },
      { label: "不统计", status: "不统计", count: Number(src.excluded ?? 0), tone: "muted" },
      { label: "关闭", status: "已关闭", count: Number(src.closed ?? 0), tone: "teal" },
    ];
  }
  const c = raw.counts || {};
  if (mode.value === "direct") {
    return [
      { label: "事项数", status: "", count: Number(raw.items ?? 0), tone: "primary" },
      { label: "已完成", status: "已完成", count: Number(c["已完成"] ?? 0), tone: "success" },
      { label: "未完成", status: "未完成", count: Number(c["未完成"] ?? 0), tone: "danger" },
      { label: "待验证", status: "待验证", count: Number(c["待验证"] ?? 0), tone: "warn" },
      { label: "未到期", status: "未到期", count: Number(c["未到期"] ?? 0), tone: "info" },
      { label: "关闭", status: "已关闭", count: Number(c["已关闭"] ?? c["关闭"] ?? 0), tone: "teal" },
    ];
  }
  return [
    { label: "事项数", status: "", count: Number(raw.items ?? 0), tone: "primary" },
    { label: "已完成", status: "已完成", count: Number(c["已完成"] ?? 0), tone: "success" },
    { label: "未完成", status: "未完成", count: Number(c["未完成"] ?? 0), tone: "danger" },
    { label: "未到期", status: "未到期", count: Number(c["未到期"] ?? 0), tone: "info" },
    { label: "不统计", status: "不统计", count: Number(c["不统计"] ?? 0), tone: "muted" },
    { label: "关闭", status: "关闭", count: Number(c["关闭"] ?? c["已关闭"] ?? 0), tone: "teal" },
  ];
});

function selectDetailStatus(statusVal: string) {
  if (!detailContext.value) return;
  const next = detailContext.value.status === statusVal ? "" : statusVal;
  detailContext.value = { ...detailContext.value, status: next };
  detailTitle.value = `${detailContext.value.person.name}${next ? ` · ${next}` : ""}明细`;
  detailPage.value = 1;
  void run(loadDetails);
}

async function openPersonDetails(personRow: any, statusFilter = "") {
  detailContext.value = { person: personRow, status: statusFilter };
  detailTitle.value = `${personRow.name}${statusFilter ? ` · ${statusFilter}` : ""}明细`;
  detailPage.value = 1;
  if (mode.value === "performance") {
    detailSource.value = personRow.raw?.regular?.items ? "regular" : "direct";
  }
  detailOpen.value = true;
  await loadDetails();
}

async function loadDetails() {
  if (!loadedParams.value || !detailContext.value) return;
  detailBusy.value = true;
  try {
    const { person: p, status: st } = detailContext.value;
    let res: any;
    if (mode.value === "performance") {
      res = await api(
        "/reports/performance/details?" +
          query({
            ...loadedParams.value,
            person_id: p.id,
            source: detailSource.value,
            status: st,
            offset: (detailPage.value - 1) * 10,
            limit: 10,
          }),
      );
    } else if (mode.value === "direct") {
      res = await api(
        "/reports/direct?" +
          query({
            ...loadedParams.value,
            details_only: true,
            person_id: p.id,
            status: st,
            offset: (detailPage.value - 1) * 10,
            limit: 10,
          }),
      );
    } else {
      res = await api(
        "/reports?" +
          query({
            ...loadedParams.value,
            include_details: true,
            details_only: true,
            person_id: p.id,
            status: st,
            offset: (detailPage.value - 1) * 10,
            limit: 10,
          }),
      );
    }
    detailRows.value = res.details || [];
    detailTotal.value = res.details_total ?? detailRows.value.length;
  } finally {
    detailBusy.value = false;
  }
}

async function exportExcel() {
  if (!loadedParams.value) return;
  const endpoint =
    mode.value === "performance"
      ? "/reports/performance/export"
      : mode.value === "direct"
        ? "/reports/direct/export"
        : "/reports/export";
  const name =
    mode.value === "performance"
      ? "绩效统计.xlsx"
      : mode.value === "direct"
        ? "直接派发任务统计.xlsx"
        : "会议纪要统计.xlsx";
  await download(`${endpoint}?${query(loadedParams.value)}`, name);
}

function switchMode(next: "performance" | "regular" | "direct") {
  mode.value = next;
  void router.replace({
    query: next === "performance" ? {} : { view: next },
  });
}

watch([mode, meetingScope, periodType, monthValue, department, leaderId], () => {
  void run(load);
});

onMounted(() => run(load));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <h1 class="m-header-title">统计报表</h1>
        <button
          type="button"
          class="m-btn m-btn-sm"
          :disabled="busy || !loadedParams"
          @click="run(exportExcel)"
        >
          导出 Excel
        </button>
      </div>

      <div class="rep-filters">
        <div
          class="m-segmented m-segmented-blue"
          style="grid-template-columns: repeat(3, 1fr)"
        >
          <button
            type="button"
            :class="{ active: mode === 'performance' }"
            @click="switchMode('performance')"
          >
            绩效统计
          </button>
          <button
            type="button"
            :class="{ active: mode === 'regular' }"
            @click="switchMode('regular')"
          >
            会议纪要
          </button>
          <button
            type="button"
            :class="{ active: mode === 'direct' }"
            @click="switchMode('direct')"
          >
            直接派发
          </button>
        </div>

        <div
          v-if="mode === 'regular' && canViewSenior"
          class="m-chip-row"
        >
          <button
            type="button"
            class="m-chip m-chip-soft"
            :class="{ active: meetingScope === 'regular' }"
            @click="meetingScope = 'regular'"
          >
            全部会议
          </button>
          <button
            type="button"
            class="m-chip m-chip-soft"
            :class="{ active: meetingScope === 'senior' }"
            @click="meetingScope = 'senior'"
          >
            中高层会议统计
          </button>
        </div>

        <div class="rep-row">
          <input
            v-if="periodType === 'month'"
            v-model="monthValue"
            type="month"
            class="m-input"
            style="padding: 7px 10px; font-size: 13px; flex: 1"
          />
          <template v-else>
            <input
              v-model="startDate"
              type="date"
              class="m-input"
              style="padding: 7px 8px; font-size: 12px; flex: 1"
              @change="run(load)"
            />
            <input
              v-model="endDate"
              type="date"
              class="m-input"
              style="padding: 7px 8px; font-size: 12px; flex: 1"
              @change="run(load)"
            />
          </template>
          <button
            type="button"
            class="m-btn m-btn-sm"
            style="flex-shrink: 0"
            @click="periodType = periodType === 'month' ? 'custom' : 'month'"
          >
            {{ periodType === "month" ? "自定义区间" : "按月" }}
          </button>
        </div>

        <div v-if="canViewOthers" class="rep-row">
          <select
            v-model="department"
            class="m-select"
            style="padding: 7px 10px; font-size: 13px; flex: 1"
          >
            <option value="">全部授权部门</option>
            <option
              v-for="d in departmentOptions"
              :key="d.id"
              :value="d.id"
            >
              {{ d.label }}
            </option>
          </select>
          <select
            v-if="mode === 'regular' && meetingScope === 'senior'"
            v-model="leaderId"
            class="m-select"
            style="padding: 7px 10px; font-size: 13px; width: 120px"
          >
            <option value="">全部领导</option>
            <option
              v-for="person in access?.senior_leaders || []"
              :key="person.id"
              :value="person.id"
            >
              {{ person.name }}
            </option>
          </select>
        </div>
      </div>
    </header>

    <section class="m-section">
      <div v-if="busy" class="m-empty">
        <van-loading size="22">正在汇总统计数据…</van-loading>
      </div>
      <div v-else-if="error" class="m-alert m-alert-error">{{ error }}</div>

      <template v-else-if="summary">
        <!-- Brand-Blue Ice-Tinted Hero Summary Card (Screens 17/18/19/20) -->
        <div class="m-card-blue-soft bb-rep-hero">
          <div style="display: flex; justify-content: space-between; align-items: baseline">
            <span style="font-size: 14px; font-weight: 700; color: var(--m-ink)">
              {{ summary.title }}
            </span>
            <span class="m-muted tabular">
              {{ dateRangeParams.start || "开始" }} 至 {{ dateRangeParams.end || "结束" }}
            </span>
          </div>
          <div
            class="bb-rep-rate tabular"
            :style="{
              color: isLowReportRate(summary.rateVal)
                ? 'var(--m-danger)'
                : 'var(--m-primary)',
            }"
          >
            {{ rate(summary.rateVal) }}
          </div>
          <div class="bb-rep-bar">
            <div
              class="bb-rep-bar-fill"
              :style="{
                width: `${Math.max(0, Math.min(100, Math.round((summary.rateVal ?? 0) * 100)))}%`,
                background: isLowReportRate(summary.rateVal)
                  ? '#dc2626'
                  : 'var(--m-primary)',
              }"
            />
          </div>
          <div
            v-if="mode === 'direct' && data?.summary"
            class="m-muted tabular"
            style="margin-top: 8px; color: #1e40af"
          >
            已到期 {{ data.summary.due_items ?? 0 }} · 到期验证通过
            {{ data.summary.due_completed ?? 0 }}
          </div>
          <div class="bb-rep-trio">
            <div>
              <span class="m-muted">已完成</span>
              <strong class="tabular">{{ summary.completed }}</strong>
            </div>
            <div>
              <span class="m-muted">未完成</span>
              <strong class="tabular">{{ summary.incomplete }}</strong>
            </div>
            <div>
              <span class="m-muted">{{ summary.totalLabel }}</span>
              <strong class="tabular">{{ summary.total }}</strong>
            </div>
          </div>
        </div>

        <!-- Stacked Status Bar -->
        <div v-if="statusSegments.length" class="m-card" style="padding: 14px 16px">
          <div style="font-size: 14px; font-weight: 700; margin-bottom: 10px">
            任务状态分布
          </div>
          <div class="stack-bar">
            <div
              v-for="seg in statusSegments"
              :key="seg.label"
              :style="{ width: seg.pct + '%', background: seg.color }"
            />
          </div>
          <div class="stack-legend">
            <span v-for="seg in statusSegments" :key="seg.label">
              <i :style="{ background: seg.color }" />{{ seg.label }}
            </span>
          </div>
        </div>

        <!-- Individual Brand-Blue White Cards per Person -->
        <div class="m-section-heading">
          <h2>
            {{
              mode === "performance"
                ? "人员绩效考核"
                : mode === "direct"
                  ? "人员派发任务统计"
                  : "人员任务完成情况"
            }}（{{ filteredPeople.length }} 人）
          </h2>
          <span class="m-muted">点击查看明细</span>
        </div>

        <div class="m-search-box">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="7" />
            <line x1="20" y1="20" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="peopleSearch"
            placeholder="输入员工姓名或部门快速定位…"
            @input="visibleCount = 20"
          />
        </div>

        <template v-if="shownPeople.length">
          <article
            v-for="p in shownPeople"
            :key="p.id"
            class="m-card bb-person-card"
            @click="run(() => openPersonDetails(p, ''))"
          >
            <div class="bb-person-top">
              <div style="min-width: 0; flex: 1">
                <div style="font-size: 15px; font-weight: 700; color: var(--m-ink)">
                  {{ p.name }}
                </div>
                <div class="m-muted tabular" style="margin-top: 2px">
                  <template v-if="p.dept">{{ p.dept }} · </template>
                  总数 {{ p.total }} · 已完成 {{ p.completed }} · 未完成 {{ p.incomplete }}
                </div>
              </div>
              <div style="display: flex; align-items: center; gap: 4px; flex-shrink: 0">
                <span
                  class="tabular"
                  style="font-size: 16px; font-weight: 700"
                  :style="{
                    color: isLowReportRate(p.rateVal)
                      ? 'var(--m-danger)'
                      : p.rateVal != null
                        ? 'var(--m-primary)'
                        : '#71717a',
                  }"
                >
                  {{ rate(p.rateVal) }}
                </span>
                <svg class="m-chevron" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </div>
            </div>
            <div class="bb-person-bar">
              <div
                class="bb-person-bar-fill"
                :style="{
                  width: `${p.rateVal == null ? 0 : Math.max(0, Math.min(100, Math.round(p.rateVal * 100)))}%`,
                  background: isLowReportRate(p.rateVal)
                    ? '#dc2626'
                    : 'var(--m-primary)',
                }"
              />
            </div>
          </article>

          <button
            v-if="shownPeople.length < filteredPeople.length"
            type="button"
            class="m-btn m-btn-sm"
            style="width: 100%"
            @click="visibleCount += 30"
          >
            展开更多人员（已显示 {{ shownPeople.length }}/{{ filteredPeople.length }}）
          </button>
        </template>

        <div v-else class="m-empty" style="padding: 18px">
          当前筛选条件下暂无人员考核记录
        </div>
      </template>
    </section>

    <!-- Paginated Details Bottom Sheet -->
    <van-popup
      v-model:show="detailOpen"
      position="bottom"
      round
      :style="{ height: '82vh', display: 'flex', flexDirection: 'column' }"
    >
      <div class="d-sheet">
        <div class="d-head">
          <span style="font-size: 15px; font-weight: 700">{{ detailTitle }}</span>
          <button
            type="button"
            style="border: none; background: transparent; color: var(--m-muted)"
            @click="detailOpen = false"
          >
            关闭
          </button>
        </div>
        <div class="d-body">
          <div
            v-if="mode === 'performance'"
            class="m-segmented"
            style="grid-template-columns: 1fr 1fr; flex-shrink: 0"
          >
            <button
              type="button"
              :class="{ active: detailSource === 'regular' }"
              @click="
                detailSource = 'regular';
                if (detailContext) {
                  detailContext = { ...detailContext, status: '' };
                  detailTitle = `${detailContext.person.name}明细`;
                }
                detailPage = 1;
                run(loadDetails);
              "
            >
              会议纪要任务
            </button>
            <button
              type="button"
              :class="{ active: detailSource === 'direct' }"
              @click="
                detailSource = 'direct';
                if (detailContext) {
                  detailContext = { ...detailContext, status: '' };
                  detailTitle = `${detailContext.person.name}明细`;
                }
                detailPage = 1;
                run(loadDetails);
              "
            >
              直接派发任务
            </button>
          </div>

          <div
            v-if="detailStatusBuckets.length"
            class="d-stat-grid"
            role="tablist"
            aria-label="按任务状态筛选"
          >
            <button
              v-for="b in detailStatusBuckets"
              :key="b.label"
              type="button"
              class="d-stat-tile"
              :class="[
                `tone-${b.tone}`,
                { active: (detailContext?.status || '') === b.status },
              ]"
              @click="selectDetailStatus(b.status)"
            >
              <span class="d-stat-label">{{ b.label }}</span>
              <strong class="d-stat-val tabular">{{ b.count }}</strong>
            </button>
          </div>

          <div v-if="detailBusy" class="m-empty">
            <van-loading size="20">加载任务明细…</van-loading>
          </div>
          <template v-else-if="detailRows.length">
            <div class="m-muted">
              {{ detailContext?.status ? `当前筛选「${detailContext.status}」· ` : "" }}共 {{ detailTotal }} 条明细
            </div>
            <div
              v-for="(item, idx) in detailRows"
              :key="item.id || idx"
              class="m-card"
              style="padding: 10px; display: flex; flex-direction: column; gap: 6px"
            >
              <div style="display: flex; justify-content: space-between; gap: 8px">
                <span class="m-muted">
                  {{ item.meeting_title || item.source_title || "直接派发" }}
                </span>
                <StatusTag :value="item.status || item.result" />
              </div>
              <div class="m-work-text" style="font-size: 13px; font-weight: 600">
                {{ item.content }}
              </div>
              <div class="m-muted tabular">
                截止：{{ item.due_date || "—" }}
                <template v-if="item.meeting_date">
                  · 会议：{{ item.meeting_date }}
                </template>
              </div>
            </div>
            <div
              v-if="detailTotal > 10"
              style="display: flex; justify-content: space-between; gap: 10px"
            >
              <button
                type="button"
                class="m-btn m-btn-sm"
                :disabled="detailPage <= 1"
                @click="
                  detailPage -= 1;
                  run(loadDetails);
                "
              >
                上一页
              </button>
              <span class="m-muted tabular" style="align-self: center">
                第 {{ detailPage }} / {{ Math.ceil(detailTotal / 10) }} 页
              </span>
              <button
                type="button"
                class="m-btn m-btn-sm"
                :disabled="detailPage * 10 >= detailTotal"
                @click="
                  detailPage += 1;
                  run(loadDetails);
                "
              >
                下一页
              </button>
            </div>
          </template>
          <div v-else class="m-empty">
            {{ detailContext?.status ? `暂无「${detailContext.status}」状态的任务明细` : "暂无明细记录" }}
          </div>
        </div>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.rep-filters {
  padding: 0 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rep-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.bb-rep-hero {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bb-rep-rate {
  font-size: 36px;
  font-weight: 800;
  line-height: 1.1;
}
.bb-rep-bar {
  height: 10px;
  border-radius: 999px;
  background: #d8e3f5;
  overflow: hidden;
}
.bb-rep-bar-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.24s ease;
}
.bb-rep-trio {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 8px;
  padding-top: 10px;
  border-top: 1px solid rgba(37, 99, 235, 0.12);
}
.bb-rep-trio > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-right: 8px;
}
.bb-rep-trio > div + div {
  padding-left: 12px;
  border-left: 1px solid rgba(37, 99, 235, 0.14);
}
.bb-rep-trio strong {
  font-size: 20px;
  font-weight: 700;
  color: var(--m-ink);
}
.bb-person-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  cursor: pointer;
}
.bb-person-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.bb-person-bar {
  height: 6px;
  border-radius: 999px;
  background: #edf1f7;
  overflow: hidden;
}
.bb-person-bar-fill {
  height: 100%;
  border-radius: 999px;
}
.stack-bar {
  height: 12px;
  border-radius: 999px;
  overflow: hidden;
  display: flex;
  background: #f4f4f5;
}
.stack-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--m-muted);
}
.stack-legend i {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
}
.people-list {
  display: flex;
  flex-direction: column;
}
.p-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 0;
  border-top: 1px solid #f4f4f5;
  cursor: pointer;
}
.p-row:first-child {
  border-top: none;
  padding-top: 2px;
}
.d-sheet {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.d-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid var(--m-line);
}
.d-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.d-stat-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  flex-shrink: 0;
}
@media (min-width: 520px) {
  .d-stat-grid {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}
.d-stat-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 8px 4px;
  border-radius: 10px;
  border: 1px solid #e4e8ef;
  background: #f8fafd;
  cursor: pointer;
  min-width: 0;
  transition: all 0.14s ease;
}
.d-stat-tile:active {
  transform: scale(0.98);
}
.d-stat-label {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
}
.d-stat-val {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--m-ink);
}
.d-stat-tile.tone-primary .d-stat-val {
  color: var(--m-primary);
}
.d-stat-tile.tone-success .d-stat-val {
  color: #16a34a;
}
.d-stat-tile.tone-danger .d-stat-val {
  color: #dc2626;
}
.d-stat-tile.tone-info .d-stat-val {
  color: #2563eb;
}
.d-stat-tile.tone-warn .d-stat-val {
  color: #d97706;
}
.d-stat-tile.tone-teal .d-stat-val {
  color: #0d9488;
}
.d-stat-tile.tone-muted .d-stat-val {
  color: #64748b;
}
.d-stat-tile.active {
  background: #eaf2ff;
  border-color: var(--m-primary);
  box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.16);
}
.d-stat-tile.active .d-stat-label {
  color: var(--m-primary);
  font-weight: 600;
}
</style>
