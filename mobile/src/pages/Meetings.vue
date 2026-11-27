<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showConfirmDialog, showSuccessToast, showToast } from "vant";
import {
  api,
  departmentLeaf,
  departmentPath,
  hasBusinessPermission,
  kinds,
  query,
  rate,
  refreshDirectory,
  run,
  state,
} from "../api/client";
import { chineseError } from "../api/errorMessages";
import StatusTag from "../components/StatusTag.vue";
import TaskSheetImportSheet from "../components/TaskSheetImportSheet.vue";

const route = useRoute();
const router = useRouter();

const activeSeries = computed(() =>
  typeof route.query.series === "string" ? route.query.series : "",
);

const activeSeriesObj = computed(() => {
  const key = activeSeries.value;
  if (!key.startsWith("series:")) return null;
  const id = key.slice(7);
  return (
    (state.directory.series || []).find((s: any) => s.id === id) ||
    seriesRows.value.find((s: any) => s.key === key) ||
    null
  );
});

const activeSeriesName = computed(() => {
  const key = activeSeries.value;
  if (key.startsWith("series:"))
    return activeSeriesObj.value?.name || "会议系列";
  if (key === "unassigned" || key.startsWith("unassigned:"))
    return "未归入系列";
  return "";
});

const seriesRows = ref<any[]>([]);
const seriesMeetingTotal = ref(0);
const latestMeeting = ref<any>(null);
const rows = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const busy = ref(false);
const refreshing = ref(false);
const loadError = ref("");
const canStartMeeting = ref(false);

const searchOpen = ref(false);
const search = ref("");
const department = ref("");
const kind = ref("");
const scope = ref("all");
const filterOpen = ref(false);
const importOpen = ref(false);

const createSeriesOpen = ref(false);
const createSeriesName = ref("");
const createSeriesKind = ref<"department" | "special">("department");
const createSeriesDept = ref("");
const createSeriesError = ref("");
const creatingSeries = ref(false);

const renameOpen = ref(false);
const renameTarget = ref<any>(null);
const renameValue = ref("");
const renameSaving = ref(false);
const renameOptions = ref<any[]>([]);

const creationDepartments = computed(() => {
  const me = (state.directory.users || []).find(
    (u: any) => u.id === state.me?.id,
  );
  return (state.directory.departments || []).filter(
    (d: any) =>
      d.id === state.me?.department_id ||
      me?.department_ids?.includes(d.id) ||
      hasBusinessPermission("series", "create", d.id),
  );
});

const hasFilter = computed(
  () => Boolean(department.value || kind.value || scope.value !== "all"),
);

function seriesSubtitle(s: any) {
  const kindText =
    (s.kinds || [])
      .map((k: string) => kinds[k] || k)
      .join(" / ") ||
    kinds[s.kind] ||
    "未设置";
  return `${kindText} · ${s.meeting_count ?? 0} 场`;
}

function tileClass(idx: number) {
  const mod = idx % 3;
  if (mod === 1) return "m-icon-tile-violet";
  if (mod === 2) return "m-icon-tile-teal";
  return "";
}

async function loadRenameOptions() {
  if (!(state.me?.is_admin || state.me?.can_rename_meetings)) return;
  if (state.me?.is_admin) return;
  try {
    const res = await api("/meeting-series-name-options", "GET", undefined, {
      background: true,
    });
    renameOptions.value = res.items || [];
  } catch {
    renameOptions.value = [];
  }
}

function canRenameSeriesRow(row: any) {
  return (
    row.key?.startsWith("series:") &&
    (state.me?.is_admin ||
      renameOptions.value.some(
        (item) => `series:${item.id}` === row.key && item.can_rename,
      ))
  );
}

async function load(append = false) {
  if (!append) {
    busy.value = true;
    loadError.value = "";
  }
  try {
    const common = {
      view: "current",
      q: search.value.trim(),
      department_id: activeSeries.value ? undefined : department.value,
      kind: activeSeries.value ? undefined : kind.value,
      scope: activeSeries.value ? "all" : scope.value,
    };
    if (activeSeries.value) {
      const r = await api(
        "/meetings?" +
          query({
            ...common,
            series_key: activeSeries.value,
            offset: (page.value - 1) * 20,
            limit: 20,
          }),
      );
      total.value = r.total || 0;
      rows.value = append ? [...rows.value, ...(r.items || [])] : r.items || [];
      canStartMeeting.value = Boolean(
        activeSeries.value.startsWith("series:") &&
          (r.can_start_meeting ?? state.me?.is_admin),
      );
    } else {
      const [seriesRes, latestRes] = await Promise.all([
        api("/meeting-series?" + query(common)),
        api("/meetings?" + query({ view: "current", scope: "all", offset: 0, limit: 1 }), "GET", undefined, {
          background: true,
        }).catch(() => ({ items: [] })),
      ]);
      seriesRows.value = seriesRes.items || [];
      seriesMeetingTotal.value = seriesRes.meeting_total || 0;
      total.value = seriesRes.total || seriesRows.value.length;
      latestMeeting.value = (latestRes.items || [])[0] || null;
      void loadRenameOptions();
    }
  } catch (e: any) {
    loadError.value = chineseError(e, "会议列表加载失败");
    throw e;
  } finally {
    busy.value = false;
    refreshing.value = false;
  }
}

function openSeries(row: any) {
  search.value = "";
  searchOpen.value = false;
  void router.push({ path: "/meetings", query: { series: row.key } });
}

function backToSeries() {
  search.value = "";
  searchOpen.value = false;
  void router.push({ path: "/meetings" });
}

function openCreateSeries() {
  createSeriesName.value = "";
  const choices = creationDepartments.value;
  createSeriesKind.value =
    choices.length || state.me?.is_admin ? "department" : "special";
  createSeriesDept.value =
    choices.find((d: any) => d.id === state.me?.department_id)?.id ||
    choices[0]?.id ||
    "";
  createSeriesError.value = "";
  createSeriesOpen.value = true;
}

async function submitCreateSeries() {
  const name = createSeriesName.value.trim();
  if (!name) {
    createSeriesError.value = "请输入会议系列名称";
    return;
  }
  if (createSeriesKind.value === "department" && !createSeriesDept.value) {
    createSeriesError.value = "请选择所属部门";
    return;
  }
  creatingSeries.value = true;
  createSeriesError.value = "";
  try {
    const created = await api("/series", "POST", {
      name,
      kind: createSeriesKind.value,
      department_id:
        createSeriesKind.value === "department"
          ? createSeriesDept.value
          : createSeriesDept.value || null,
    });
    createSeriesOpen.value = false;
    await refreshDirectory(true);
    showSuccessToast("会议系列已创建");
    void router.push({
      path: "/meetings",
      query: { series: `series:${created.id}` },
    });
  } catch (e: any) {
    createSeriesError.value = chineseError(e, "创建失败");
  } finally {
    creatingSeries.value = false;
  }
}

function openRename(row: any) {
  renameTarget.value = { id: row.key.slice(7), name: row.name };
  renameValue.value = row.name;
  renameOpen.value = true;
}

async function submitRename() {
  const name = renameValue.value.trim();
  if (!name || !renameTarget.value) {
    showToast("请输入会议系列名称");
    return;
  }
  renameSaving.value = true;
  try {
    await api(`/meeting-series/${renameTarget.value.id}/name`, "PUT", {
      name,
      expected_name: renameTarget.value.name,
    });
    renameOpen.value = false;
    await refreshDirectory(true);
    showSuccessToast("系列名称已更新");
    await load(false);
  } finally {
    renameSaving.value = false;
  }
}

async function continueMeeting(row: any) {
  const created = await api(`/meetings/${row.id}/continue`, "POST");
  showSuccessToast("已生成接续会议草稿");
  void router.push({
    path: `/meetings/${created.id}`,
    query: { series: activeSeries.value },
  });
}

async function deleteMeeting(row: any) {
  const impact = await api(`/meetings/${row.id}/deletion-impact`);
  if (impact.blocked_reasons?.length) {
    showToast(impact.blocked_reasons[0]);
    return;
  }
  await showConfirmDialog({
    title: "确认删除会议",
    message: `确定删除「${row.title}」吗？${impact.summary || "删除后无法恢复。"}`,
  });
  await api(`/meetings/${row.id}`, "DELETE", {
    confirm_title: row.title,
  });
  showSuccessToast("会议已删除");
  await load(false);
}

function newMeeting() {
  const targetSeries =
    activeSeries.value ||
    seriesRows.value.find((s) => s.key?.startsWith("series:"))?.key ||
    "";
  void router.push({
    path: "/meetings/new",
    query: targetSeries ? { series: targetSeries } : {},
  });
}

function onImported(id: string) {
  void router.push({
    path: `/meetings/${id}`,
    query: activeSeries.value ? { series: activeSeries.value } : {},
  });
}

watch(activeSeries, () => {
  page.value = 1;
  void run(() => load(false));
});

onMounted(() => run(() => load(false)));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <div style="display: flex; align-items: center; gap: 4px; min-width: 0; flex: 1">
          <button
            v-if="activeSeries"
            type="button"
            class="m-back-btn"
            @click="backToSeries"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <h1 class="m-header-title">
            {{ activeSeries ? activeSeriesName : "会议纪要与任务单" }}
          </h1>
        </div>

        <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0">
          <button
            v-if="!activeSeries"
            type="button"
            class="mt-icon-btn"
            :class="{ active: searchOpen || hasFilter }"
            aria-label="搜索或筛选会议系列"
            @click="searchOpen = !searchOpen"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="7" />
              <line x1="20" y1="20" x2="16.65" y2="16.65" />
            </svg>
          </button>
          <button
            v-if="!activeSeries || canStartMeeting"
            type="button"
            class="m-btn m-btn-sm m-btn-primary"
            @click="newMeeting"
          >
            ＋ 新建会议
          </button>
        </div>
      </div>

      <!-- Collapsible Search Row on Root OR Permanent Search Row inside Series -->
      <div v-if="activeSeries || searchOpen" class="mt-search-wrap">
        <div class="m-search-box" style="flex: 1">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="7" />
            <line x1="20" y1="20" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="search"
            :placeholder="activeSeries ? '搜索会议名称' : '搜索会议系列名称'"
            @keyup.enter="
              page = 1;
              run(() => load(false));
            "
          />
        </div>
        <button
          v-if="!activeSeries"
          type="button"
          class="m-btn m-btn-sm"
          :class="hasFilter ? 'm-btn-soft' : ''"
          @click="filterOpen = true"
        >
          筛选{{ hasFilter ? " •" : "" }}
        </button>
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
    </header>

    <van-pull-refresh
      v-model="refreshing"
      @refresh="
        page = 1;
        run(() => load(false));
      "
    >
      <section class="m-section">
        <div v-if="loadError" class="m-alert m-alert-error">{{ loadError }}</div>

        <div v-if="busy && !seriesRows.length && !rows.length" class="m-empty">
          <van-loading size="22">正在加载会议…</van-loading>
        </div>

        <!-- 02 会议首页：焦点蓝卡 + 历史会议入口 + 单行系列卡 -->
        <template v-else-if="!activeSeries">
          <!-- Focal Brand-Blue Latest Meeting Hero Card -->
          <article
            v-if="latestMeeting"
            class="mt-hero-card"
            @click="
              router.push({
                path: `/meetings/${latestMeeting.id}`,
                query: latestMeeting.series_id
                  ? { series: `series:${latestMeeting.series_id}` }
                  : {},
              })
            "
          >
            <div class="mt-hero-eyebrow">最近会议</div>
            <div class="mt-hero-row">
              <div style="min-width: 0; flex: 1">
                <h2 class="mt-hero-title">{{ latestMeeting.title }}</h2>
                <div class="mt-hero-meta tabular">
                  {{ latestMeeting.meeting_date }} ·
                  {{ departmentLeaf(latestMeeting.department_id) }}
                </div>
              </div>
              <div class="mt-hero-right">
                <span class="mt-hero-pill">
                  {{
                    (latestMeeting.is_revising ?? latestMeeting.has_draft) &&
                    latestMeeting.published_version
                      ? "修订中"
                      : latestMeeting.published_version
                        ? "已发布"
                        : "草稿"
                  }}
                </span>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.85)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </div>
            </div>
          </article>

          <!-- History Meetings Quick Entry Row -->
          <button
            type="button"
            class="m-card mt-history-row"
            @click="router.push('/history-meetings')"
          >
            <div style="display: flex; align-items: center; gap: 10px">
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#3f4c63" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
              <span style="font-size: 15px; font-weight: 600; color: var(--m-ink)">
                历史会议
              </span>
            </div>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>

          <!-- Series Section Heading -->
          <div class="m-section-heading">
            <h2>会议系列</h2>
            <button
              type="button"
              class="m-link-btn"
              @click="openCreateSeries"
            >
              ＋ 新建会议系列
            </button>
          </div>

          <!-- Single-row Clean White Series Cards -->
          <article
            v-for="(s, idx) in seriesRows"
            :key="s.key"
            class="m-card mt-series-row"
            @click="openSeries(s)"
          >
            <span class="m-icon-tile" :class="tileClass(idx)">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="4" width="18" height="17" rx="3" />
                <line x1="8" y1="2" x2="8" y2="6" />
                <line x1="16" y1="2" x2="16" y2="6" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
            </span>
            <div style="min-width: 0; flex: 1">
              <div class="mt-series-name">{{ s.name }}</div>
              <div class="mt-series-sub">
                <span class="mt-series-badge">{{ seriesSubtitle(s) }}</span>
              </div>
            </div>
            <button
              v-if="canRenameSeriesRow(s)"
              type="button"
              class="m-link-btn"
              style="font-size: 12px; padding: 4px 6px"
              @click.stop="openRename(s)"
            >
              重命名
            </button>
            <svg class="m-chevron" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </article>

          <div v-if="!seriesRows.length" class="m-empty">暂无可见会议系列</div>
        </template>

        <!-- 03 会议系列详情 -->
        <template v-else>
          <!-- Cool Blue Tinted Series Summary -->
          <div class="m-card-blue-soft mt-series-summary">
            <div>
              <div style="font-size: 12px; color: var(--m-primary); font-weight: 600">
                会议系列
              </div>
              <div style="font-size: 16px; font-weight: 700; margin-top: 2px">
                {{ activeSeriesName }}
              </div>
              <div class="m-muted" style="margin-top: 2px">
                共 {{ total }} 场当前会议
              </div>
            </div>
            <div style="display: flex; gap: 8px; flex-wrap: wrap">
              <button
                v-if="activeSeries.startsWith('series:')"
                type="button"
                class="m-btn m-btn-sm"
                @click="importOpen = true"
              >
                导入任务单
              </button>
              <button
                type="button"
                class="m-btn m-btn-sm"
                @click="
                  router.push({
                    path: '/history-meetings',
                    query: { series: activeSeries },
                  })
                "
              >
                历史会议
              </button>
            </div>
          </div>

          <div class="m-section-heading">
            <h2>会议记录</h2>
            <span class="m-muted">{{ total }} 场</span>
          </div>

          <article
            v-for="m in rows"
            :key="m.id"
            class="m-card mt-meeting-row"
            @click="
              router.push({
                path: `/meetings/${m.id}`,
                query: { series: activeSeries },
              })
            "
          >
            <div class="mt-meeting-top">
              <h3 class="mt-meeting-title">{{ m.title }}</h3>
              <StatusTag
                :value="
                  (m.is_revising ?? m.has_draft) && m.published_version
                    ? '修订中'
                    : m.published_version
                      ? '已发布'
                      : '草稿'
                "
              />
            </div>

            <div class="mt-meeting-meta tabular">
              <span>{{ m.meeting_date }}</span>
              <span>{{ departmentLeaf(m.department_id) }}</span>
              <span>主持 {{ m.host_name || "—" }}</span>
              <span>事项 {{ m.item_count ?? 0 }}</span>
              <span v-if="(m.rate ?? m.achievement_rate) != null">
                达成率 {{ rate(m.rate ?? m.achievement_rate) }}
              </span>
            </div>

            <div class="mt-meeting-foot">
              <div style="display: flex; gap: 8px">
                <button
                  v-if="m.published_version && m.can_continue"
                  type="button"
                  class="m-btn m-btn-sm m-btn-soft"
                  @click.stop="run(() => continueMeeting(m))"
                >
                  接续新建
                </button>
                <button
                  v-if="m.can_delete"
                  type="button"
                  class="m-btn m-btn-sm m-btn-danger-soft"
                  @click.stop="run(() => deleteMeeting(m))"
                >
                  删除
                </button>
              </div>
              <span
                style="
                  font-size: 12px;
                  font-weight: 600;
                  display: inline-flex;
                  align-items: center;
                  gap: 2px;
                "
                :style="{
                  color:
                    !m.published_version || (m.is_revising ?? m.has_draft)
                      ? 'var(--m-primary)'
                      : 'var(--m-muted)',
                }"
              >
                {{
                  !m.published_version || (m.is_revising ?? m.has_draft)
                    ? "继续编辑"
                    : "查看纪要"
                }}
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </span>
            </div>
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
            加载更多
          </button>

          <div v-if="!rows.length" class="m-empty">
            该系列下还没有当前会议，点击右上角「＋ 新建会议」开始吧
          </div>
        </template>
      </section>
    </van-pull-refresh>

    <!-- 09 导入任务单 Sheet -->
    <TaskSheetImportSheet
      v-if="activeSeries.startsWith('series:')"
      v-model:show="importOpen"
      :series-id="activeSeries.slice(7)"
      @created="onImported"
    />

    <!-- Series Filter Popup -->
    <van-popup v-model:show="filterOpen" position="bottom" round>
      <div class="sheet-pad">
        <div class="sheet-bar">
          <span style="font-weight: 700; font-size: 16px">筛选会议系列</span>
          <button
            type="button"
            class="m-btn m-btn-sm"
            @click="
              department = '';
              kind = '';
              scope = 'all';
              filterOpen = false;
              run(() => load(false));
            "
          >
            重置
          </button>
        </div>
        <div>
          <label class="m-label">范围</label>
          <select v-model="scope" class="m-select">
            <option value="all">全部会议</option>
            <option value="related">与我或我部门相关</option>
          </select>
        </div>
        <div>
          <label class="m-label">会议类型</label>
          <select v-model="kind" class="m-select">
            <option value="">全部类型</option>
            <option value="department">部门例会</option>
            <option value="special">专项会议</option>
          </select>
        </div>
        <div>
          <label class="m-label">所属部门</label>
          <select v-model="department" class="m-select">
            <option value="">全部部门</option>
            <option
              v-for="d in state.directory.departments || []"
              :key="d.id"
              :value="d.id"
            >
              {{ departmentPath(d.id) }}
            </option>
          </select>
        </div>
        <button
          type="button"
          class="m-btn m-btn-primary"
          style="width: 100%"
          @click="
            filterOpen = false;
            run(() => load(false));
          "
        >
          应用筛选
        </button>
      </div>
    </van-popup>

    <!-- 34 新建会议系列 Sheet -->
    <van-popup v-model:show="createSeriesOpen" position="bottom" round>
      <div class="sheet-pad">
        <div class="sheet-bar">
          <span style="font-weight: 700; font-size: 16px">新建会议系列</span>
          <button
            type="button"
            style="border: none; background: transparent; color: var(--m-muted)"
            @click="createSeriesOpen = false"
          >
            取消
          </button>
        </div>
        <div>
          <label class="m-label m-label-req">会议系列名称</label>
          <input
            v-model="createSeriesName"
            class="m-input"
            placeholder="例如：设备管理例会"
          />
        </div>
        <div>
          <label class="m-label m-label-req">会议类型</label>
          <div class="m-segmented" style="grid-template-columns: 1fr 1fr">
            <button
              type="button"
              :class="{ active: createSeriesKind === 'department' }"
              @click="createSeriesKind = 'department'"
            >
              部门例会
            </button>
            <button
              type="button"
              :class="{ active: createSeriesKind === 'special' }"
              @click="createSeriesKind = 'special'"
            >
              专项会议
            </button>
          </div>
        </div>
        <div v-if="createSeriesKind === 'department'">
          <label class="m-label m-label-req">所属部门</label>
          <select v-model="createSeriesDept" class="m-select">
            <option value="">请选择所属部门</option>
            <option
              v-for="d in state.me?.is_admin
                ? state.directory.departments
                : creationDepartments"
              :key="d.id"
              :value="d.id"
            >
              {{ departmentPath(d.id) }}
            </option>
          </select>
        </div>
        <div v-if="createSeriesError" class="m-alert m-alert-error">
          {{ createSeriesError }}
        </div>
        <div style="display: flex; gap: 10px">
          <button
            type="button"
            class="m-btn"
            style="flex: 1"
            @click="createSeriesOpen = false"
          >
            取消
          </button>
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="flex: 1.5"
            :disabled="creatingSeries"
            @click="run(submitCreateSeries)"
          >
            {{ creatingSeries ? "创建中…" : "确定" }}
          </button>
        </div>
      </div>
    </van-popup>

    <!-- Rename Series Sheet -->
    <van-popup v-model:show="renameOpen" position="bottom" round>
      <div class="sheet-pad">
        <div class="sheet-bar">
          <span style="font-weight: 700; font-size: 16px">编辑系列名称</span>
          <button
            type="button"
            style="border: none; background: transparent; color: var(--m-muted)"
            @click="renameOpen = false"
          >
            取消
          </button>
        </div>
        <div>
          <label class="m-label m-label-req">新系列名称</label>
          <input v-model="renameValue" class="m-input" />
        </div>
        <button
          type="button"
          class="m-btn m-btn-primary"
          style="width: 100%"
          :disabled="renameSaving"
          @click="run(submitRename)"
        >
          保存名称
        </button>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.mt-icon-btn {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  border: 1px solid var(--m-line);
  background: var(--m-surface);
  color: var(--m-text);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.mt-icon-btn.active {
  background: var(--m-primary-soft);
  border-color: var(--m-primary-border);
  color: var(--m-primary);
}
.mt-search-wrap {
  padding: 0 16px 10px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.mt-hero-card {
  border-radius: 16px;
  padding: 16px 18px;
  background: var(--m-primary-grad);
  color: #ffffff;
  box-shadow: var(--m-shadow-hero);
  cursor: pointer;
}
.mt-hero-eyebrow {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.85);
  font-weight: 500;
  margin-bottom: 6px;
}
.mt-hero-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.mt-hero-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.35;
  color: #ffffff;
}
.mt-hero-meta {
  margin-top: 6px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.86);
}
.mt-hero-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.mt-hero-pill {
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.22);
  color: #ffffff;
  font-size: 11px;
  font-weight: 600;
}
.mt-history-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  cursor: pointer;
  padding: 14px 16px;
  font-family: inherit;
}
.mt-series-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px;
  cursor: pointer;
}
.mt-series-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--m-ink);
  line-height: 1.35;
}
.mt-series-sub {
  margin-top: 4px;
}
.mt-series-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  color: var(--m-primary);
  background: var(--m-primary-soft);
  padding: 2px 8px;
  border-radius: 6px;
}
.mt-series-summary {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mt-meeting-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  cursor: pointer;
}
.mt-meeting-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.mt-meeting-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--m-ink);
  line-height: 1.4;
}
.mt-meeting-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 12px;
  color: var(--m-muted);
}
.mt-meeting-foot {
  padding-top: 8px;
  border-top: 1px solid #eef2f7;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.sheet-pad {
  padding: 16px 16px calc(20px + env(safe-area-inset-bottom));
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sheet-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
