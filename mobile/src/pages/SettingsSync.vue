<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, run, state } from "../api/client";
import { chineseError } from "../api/errorMessages";
import { beijingTime } from "../api/time";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const syncHistory = ref<any[]>([]);
const reassignments = ref<any[]>([]);
const busy = ref(false);
const error = ref("");

async function load() {
  if (!state.me?.is_admin) return;
  busy.value = true;
  error.value = "";
  try {
    const [hist, reass] = await Promise.all([
      api("/admin/directory-sync-history"),
      api("/admin/reassignment"),
    ]);
    syncHistory.value = hist || [];
    reassignments.value = reass || [];
  } catch (e: any) {
    error.value = chineseError(e, "同步记录加载失败");
  } finally {
    busy.value = false;
  }
}

onMounted(() => run(load));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header-white">
      <div class="m-header-bar">
        <button type="button" class="m-back-btn" @click="router.push('/settings')">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <h1 class="m-header-title" style="flex: 1">通讯录同步</h1>
      </div>
    </header>

    <section class="m-section" style="padding-top: 12px">
      <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      <div v-if="busy && !syncHistory.length" class="m-empty">
        <van-loading size="22">加载同步记录…</van-loading>
      </div>

      <template v-else>
        <div class="m-card-blue-soft">
          <div style="font-size: 15px; font-weight: 700">通讯录同步记录</div>
          <div class="m-muted" style="margin-top: 4px; color: #1e40af">
            每小时自动同步企业微信人员及部门
          </div>
        </div>

        <article
          v-for="(row, idx) in syncHistory.slice(0, 15)"
          :key="row.id || idx"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 6px"
        >
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
            <span class="tabular" style="font-size: 14px; font-weight: 700">
              {{ beijingTime(row.created_at) }}
            </span>
            <StatusTag
              :value="row.state === 'succeeded' ? '成功' : '失败'"
              :tone="row.state === 'succeeded' ? 'green' : 'red'"
            />
          </div>
          <div class="m-muted">
            {{ row.actor_name || "自动同步" }} ·
            {{
              row.state === "succeeded"
                ? `${row.result?.users ?? 0} 名成员 · ${row.result?.departments ?? 0} 个部门`
                : row.result?.error || "同步失败"
            }}
          </div>
        </article>

        <div v-if="!syncHistory.length" class="m-empty" style="padding: 20px">
          尚未执行企业通讯录同步
        </div>

        <template v-if="reassignments.length">
          <div class="m-section-heading">
            <h2>待重新指派 {{ reassignments.length }} 项</h2>
          </div>
          <article
            v-for="task in reassignments"
            :key="task.id"
            class="m-card"
            style="display: flex; flex-direction: column; gap: 8px"
          >
            <div style="display: flex; justify-content: space-between; gap: 8px; align-items: flex-start">
              <div class="m-work-text" style="font-size: 14px; font-weight: 700; flex: 1">
                {{ task.content }}
              </div>
              <StatusTag value="责任人已停用" tone="red" />
            </div>
            <div class="m-muted tabular">
              有效期限 {{ task.due_date || "未设期限" }}
              <template v-if="task.inactive_owners?.length">
                · 停用责任人 {{ task.inactive_owners.join("、") }}
              </template>
            </div>
            <div>
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-outline-blue"
                @click="router.push({ path: '/tasks', query: { id: task.id } })"
              >
                调整任务
              </button>
            </div>
          </article>
        </template>
      </template>
    </section>
  </div>
</template>
