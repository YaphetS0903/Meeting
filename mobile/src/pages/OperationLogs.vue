<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, query, run, state } from "../api/client";
import { beijingTime } from "../api/time";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const view = ref<"operations" | "errors">("operations");
const keyword = ref("");
const rows = ref<any[]>([]);
const total = ref(0);
const page = ref(1);
const busy = ref(false);
const detailOpen = ref(false);
const detail = ref<any>(null);

async function load(append = false) {
  if (!state.me?.can_view_operation_logs) return;
  busy.value = true;
  try {
    const res = await api(
      "/admin/operation-logs?" +
        query({
          keyword: keyword.value.trim(),
          ...(view.value === "errors" ? { view: "errors" } : {}),
          page: page.value,
          page_size: 20,
        }),
    );
    total.value = res.total || 0;
    rows.value = append ? [...rows.value, ...(res.items || [])] : res.items || [];
  } finally {
    busy.value = false;
  }
}

async function openDetail(row: any) {
  detailOpen.value = true;
  detail.value = await api("/admin/operation-logs/" + encodeURIComponent(row.id));
}

onMounted(() => run(() => load(false)));
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
        <h1 class="m-header-title">操作日志</h1>
        <span class="m-muted">{{ total }} 条</span>
      </div>
      <div style="padding: 0 14px 10px; display: flex; flex-direction: column; gap: 8px">
        <div class="m-segmented" style="grid-template-columns: 1fr 1fr">
          <button
            type="button"
            :class="{ active: view === 'operations' }"
            @click="
              view = 'operations';
              page = 1;
              run(() => load(false));
            "
          >
            操作记录
          </button>
          <button
            type="button"
            :class="{ active: view === 'errors' }"
            @click="
              view = 'errors';
              page = 1;
              run(() => load(false));
            "
          >
            系统错误日志
          </button>
        </div>
        <div style="display: flex; gap: 6px">
          <input
            v-model="keyword"
            class="m-input"
            style="padding: 8px 10px; font-size: 13px"
            placeholder="搜索操作人、对象或摘要…"
            @keyup.enter="
              page = 1;
              run(() => load(false));
            "
          />
          <button
            type="button"
            class="m-btn m-btn-sm m-btn-primary"
            @click="
              page = 1;
              run(() => load(false));
            "
          >
            查询
          </button>
        </div>
      </div>
    </header>

    <section class="m-section">
      <div v-if="!state.me?.can_view_operation_logs" class="m-alert m-alert-error">
        仅 admin 系统管理员可查看操作日志。
      </div>
      <div v-else-if="busy && !rows.length" class="m-empty">
        <van-loading size="22">加载日志…</van-loading>
      </div>
      <template v-else-if="rows.length">
        <article
          v-for="row in rows"
          :key="row.id"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 6px; cursor: pointer"
          @click="run(() => openDetail(row))"
        >
          <div style="display: flex; justify-content: space-between; gap: 8px">
            <span style="font-weight: 700; font-size: 13px">
              {{ row.action_label || row.action }}
            </span>
            <StatusTag
              :value="row.result === 'success' ? '成功' : row.result === 'failed' ? '失败' : row.result"
              :tone="row.result === 'success' ? 'green' : 'red'"
            />
          </div>
          <div style="font-size: 13px" class="m-work-text">
            {{ row.summary || row.target_label || "—" }}
          </div>
          <div class="m-muted tabular" style="display: flex; justify-content: space-between">
            <span>{{ row.actor_name || "系统" }}</span>
            <span>{{ beijingTime(row.created_at) }}</span>
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
          加载更多（{{ rows.length }}/{{ total }}）
        </button>
      </template>
      <div v-else class="m-empty">暂无日志记录</div>
    </section>

    <van-popup
      v-model:show="detailOpen"
      position="bottom"
      round
      :style="{ maxHeight: '78vh', display: 'flex', flexDirection: 'column' }"
    >
      <div style="padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px">
        <div style="display: flex; justify-content: space-between">
          <span style="font-size: 15px; font-weight: 700">日志详情</span>
          <button
            type="button"
            style="border: none; background: transparent; color: var(--m-muted)"
            @click="detailOpen = false"
          >
            关闭
          </button>
        </div>
        <template v-if="detail">
          <div class="m-muted">时间：{{ beijingTime(detail.created_at) }}</div>
          <div class="m-muted">操作人：{{ detail.actor_name || "系统" }}</div>
          <div style="font-size: 13px; font-weight: 600">
            {{ detail.action_label || detail.action }}
          </div>
          <div class="m-work-text" style="font-size: 13px">
            {{ detail.summary || "—" }}
          </div>
          <pre
            v-if="detail.details || detail.changes"
            style="
              background: #f4f4f5;
              padding: 10px;
              border-radius: 8px;
              font-size: 11px;
              overflow: auto;
              margin: 0;
            "
          >{{ JSON.stringify(detail.details || detail.changes, null, 2) }}</pre>
        </template>
      </div>
    </van-popup>
  </div>
</template>
