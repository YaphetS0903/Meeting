<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, query, run } from "../api/client";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const rows = ref<any[]>([]);
const busy = ref(false);
const refreshing = ref(false);
const q = ref("");
const start = ref("");
const end = ref("");
const filterOpen = ref(false);

const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase();
  return rows.value.filter((t: any) => {
    if (kw) {
      const hay = `${t.content || ""} ${t.code || ""} ${(t.owner_names || []).join(" ")}`.toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    if (start.value || end.value) {
      if (!t.due_date) return false;
      if (start.value && t.due_date < start.value) return false;
      if (end.value && t.due_date > end.value) return false;
    }
    return true;
  });
});

async function load() {
  busy.value = true;
  try {
    const res = await api(
      "/direct-assignments" +
        (q.value || start.value || end.value
          ? "?" + query({ q: q.value.trim(), start: start.value, end: end.value })
          : ""),
    );
    rows.value = Array.isArray(res) ? res : res.items || [];
  } finally {
    busy.value = false;
    refreshing.value = false;
  }
}

onMounted(() => run(load));
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
        <h1 class="m-header-title">我派发的任务</h1>
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-primary"
          @click="router.push('/assignments')"
        >
          + 派发
        </button>
      </div>
      <div style="padding: 0 14px 10px; display: flex; gap: 6px">
        <input
          v-model="q"
          class="m-input"
          style="padding: 8px 10px; font-size: 13px"
          placeholder="搜索任务内容、编号或责任人"
          @keyup.enter="run(load)"
        />
        <button
          type="button"
          class="m-btn m-btn-sm"
          :class="start || end ? 'm-btn-soft' : ''"
          @click="filterOpen = !filterOpen"
        >
          日期
        </button>
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-primary"
          @click="run(load)"
        >
          查询
        </button>
      </div>
      <div
        v-if="filterOpen"
        style="
          padding: 0 14px 10px;
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px;
        "
      >
        <input v-model="start" type="date" class="m-input" />
        <input v-model="end" type="date" class="m-input" />
      </div>
    </header>

    <van-pull-refresh v-model="refreshing" @refresh="run(load)">
      <section class="m-section">
        <div v-if="busy && !rows.length" class="m-empty">
          <van-loading size="22">加载派发记录…</van-loading>
        </div>
        <template v-else-if="filtered.length">
          <div class="m-muted">共 {{ filtered.length }} 条派发任务</div>
          <article
            v-for="t in filtered"
            :key="t.id"
            class="m-card"
            style="display: flex; flex-direction: column; gap: 6px; cursor: pointer"
            @click="router.push({ path: '/tasks', query: { id: t.id } })"
          >
            <div style="display: flex; justify-content: space-between; gap: 8px">
              <span class="m-muted tabular">{{ t.code || "直接派发" }}</span>
              <StatusTag
                :value="
                  t.withdrawn_at
                    ? '已撤回'
                    : t.closed_at
                      ? '已关闭'
                      : t.todo_status || t.status
                "
              />
            </div>
            <div class="m-work-text" style="font-weight: 600">
              {{ t.content }}
            </div>
            <div
              class="m-muted"
              style="display: flex; justify-content: space-between; gap: 8px"
            >
              <span>责任人：{{ (t.owner_names || []).join("、") || "—" }}</span>
              <span class="tabular">截止 {{ t.due_date || "未设" }}</span>
            </div>
          </article>
        </template>
        <div v-else class="m-empty">暂无派发记录</div>
      </section>
    </van-pull-refresh>
  </div>
</template>
