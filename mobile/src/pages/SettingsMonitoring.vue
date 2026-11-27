<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, run, state } from "../api/client";
import { chineseError } from "../api/errorMessages";
import { beijingTime } from "../api/time";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const operations = ref<any>(null);
const busy = ref(false);
const error = ref("");

const deliveryLabels: Record<string, string> = {
  not_started: "监控尚未启动",
  not_configured: "告警接收端未配置",
  idle: "正常（无需新告警）",
  pending: "等待告警接收确认",
  sent: "告警已获接收确认",
  failed: "告警发送失败，将重试",
};

async function load() {
  if (!state.me?.is_admin) return;
  busy.value = true;
  error.value = "";
  try {
    operations.value = await api("/admin/operations");
  } catch (e: any) {
    error.value = chineseError(e, "监控状态读取失败");
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
        <h1 class="m-header-title" style="flex: 1">运行监控与告警</h1>
        <button type="button" class="m-btn m-btn-sm" :disabled="busy" @click="run(load)">
          刷新
        </button>
      </div>
    </header>

    <section class="m-section" style="padding-top: 12px">
      <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      <div v-if="busy && !operations" class="m-empty">
        <van-loading size="22">读取监控状态…</van-loading>
      </div>

      <template v-else-if="operations">
        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
            <span style="font-size: 16px; font-weight: 700">独立监控</span>
            <StatusTag
              :value="operations.running ? '监控进程持续运行' : '监控心跳异常'"
              :tone="operations.running ? 'green' : 'red'"
            />
          </div>
          <div class="m-muted tabular">
            最近检查：{{
              operations.checked_at ? beijingTime(operations.checked_at) : "尚无记录"
            }}
          </div>
          <div class="m-muted">
            告警状态：{{ deliveryLabels[operations.delivery] || "尚无记录" }}
          </div>
          <div v-if="!operations.alert_configured" class="m-alert m-alert-warning">
            外部告警尚未配置，当前仅记录本机检查结果。
          </div>
          <div v-if="operations.last_error" class="m-alert m-alert-error">
            {{ chineseError(operations.last_error, "后台处理失败，请查看操作日志") }}
          </div>
        </div>

        <div class="m-section-heading"><h2>检查项</h2></div>
        <div class="m-card-flush">
          <div
            v-for="item in operations.snapshot?.checks || []"
            :key="item.label"
            class="mon-row"
          >
            <div style="min-width: 0; flex: 1">
              <div style="font-size: 14px; font-weight: 600">{{ item.label }}</div>
              <div v-if="item.detail" class="m-muted" style="margin-top: 2px">
                {{ item.detail }}
              </div>
            </div>
            <StatusTag
              :value="item.healthy ? '正常' : '需处理'"
              :tone="item.healthy ? 'green' : 'red'"
            />
          </div>
          <div
            v-if="!(operations.snapshot?.checks || []).length"
            class="m-empty"
            style="padding: 20px"
          >
            等待独立监控首次检查
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.mon-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 13px 16px;
  border-top: 1px solid #eef2f7;
}
.mon-row:first-child {
  border-top: none;
}
</style>
