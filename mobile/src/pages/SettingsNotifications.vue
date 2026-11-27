<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { showSuccessToast, showToast } from "vant";
import { api, run, state } from "../api/client";
import { chineseError } from "../api/errorMessages";
import { beijingTime } from "../api/time";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const events = ref<any[]>([]);
const busy = ref(false);
const error = ref("");

const promptOpen = ref(false);
const promptTitle = ref("");
const promptPlaceholder = ref("");
const promptReason = ref("");
const promptAction = ref<null | ((reason: string) => Promise<void>)>(null);
const promptBusy = ref(false);

const stateLabels: Record<string, string> = {
  sent: "已发送",
  delivered: "已发送",
  pending: "待发送",
  failed: "失败",
  blocked: "已拦截",
  uncertain: "待核实",
  cancelled: "已取消",
};

async function load() {
  if (!state.me?.is_admin) return;
  busy.value = true;
  error.value = "";
  try {
    events.value = (await api("/admin/notifications")) || [];
  } catch (e: any) {
    error.value = chineseError(e, "通知记录加载失败");
  } finally {
    busy.value = false;
  }
}

async function retryEvent(row: any) {
  await api(`/admin/notifications/${row.id}/retry`, "POST", {
    reason: "管理员在手机端发起重试",
  });
  showSuccessToast("已加入重试队列");
  await load();
}

function openCancel(row: any) {
  promptTitle.value = "取消通知";
  promptPlaceholder.value = "请填写取消原因…";
  promptReason.value = "";
  promptAction.value = async (reason: string) => {
    await api(`/admin/notifications/${row.id}/cancel`, "POST", { reason });
    showSuccessToast("通知已取消");
    await load();
  };
  promptOpen.value = true;
}

function openResolve(row: any, delivered: boolean) {
  promptTitle.value = delivered ? "核实已送达" : "核实未送达";
  promptPlaceholder.value = "请填写外部核实依据（至少 5 个字）…";
  promptReason.value = "";
  promptAction.value = async (reason: string) => {
    if (reason.length < 5) {
      showToast("请填写至少 5 个字的核实依据");
      throw new Error("cancel");
    }
    await api(`/admin/notifications/${row.id}/resolve`, "POST", {
      delivered,
      reason,
    });
    showSuccessToast("已记录核实结果");
    await load();
  };
  promptOpen.value = true;
}

async function submitPrompt() {
  const reason = promptReason.value.trim();
  if (!reason) {
    showToast("请填写说明");
    return;
  }
  if (!promptAction.value) return;
  promptBusy.value = true;
  try {
    await promptAction.value(reason);
    promptOpen.value = false;
  } finally {
    promptBusy.value = false;
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
        <h1 class="m-header-title" style="flex: 1">通知与失败重试</h1>
        <button type="button" class="m-btn m-btn-sm" :disabled="busy" @click="run(load)">
          刷新
        </button>
      </div>
    </header>

    <section class="m-section" style="padding-top: 12px">
      <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      <div v-if="busy && !events.length" class="m-empty">
        <van-loading size="22">加载通知记录…</van-loading>
      </div>

      <template v-else-if="events.length">
        <article
          v-for="row in events"
          :key="row.id"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 6px"
        >
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px">
            <div style="font-size: 14px; font-weight: 700; flex: 1">
              {{ row.payload?.title || row.event_key }}
            </div>
            <StatusTag :value="stateLabels[row.state] || row.state" />
          </div>
          <div class="m-muted tabular">
            {{ row.channel === "wecom" ? "企业微信" : row.channel === "email" ? "纪要邮件" : row.channel }}
            · {{ beijingTime(row.created_at) }} · 尝试次数 {{ row.attempts ?? 0 }}
          </div>
          <div v-if="row.last_error" class="m-muted" style="color: var(--m-danger)">
            {{ chineseError(row.last_error, "处理失败，请查看操作日志") }}
          </div>
          <div
            v-if="['failed', 'blocked', 'pending', 'uncertain'].includes(row.state)"
            style="display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px"
          >
            <button
              v-if="['failed', 'blocked'].includes(row.state)"
              type="button"
              class="m-btn m-btn-sm m-btn-outline-blue"
              @click="run(() => retryEvent(row))"
            >
              重试
            </button>
            <button
              v-if="['pending', 'blocked', 'failed'].includes(row.state)"
              type="button"
              class="m-btn m-btn-sm"
              @click="openCancel(row)"
            >
              取消通知
            </button>
            <template v-if="row.state === 'uncertain'">
              <button
                type="button"
                class="m-btn m-btn-sm m-btn-soft"
                @click="openResolve(row, true)"
              >
                已核实送达
              </button>
              <button
                type="button"
                class="m-btn m-btn-sm"
                @click="openResolve(row, false)"
              >
                已核实未送达
              </button>
            </template>
          </div>
        </article>
      </template>

      <div v-else class="m-empty">暂无通知事件记录</div>
    </section>

    <van-popup v-model:show="promptOpen" position="bottom" round>
      <div style="padding: 16px 16px calc(18px + env(safe-area-inset-bottom)); display: flex; flex-direction: column; gap: 12px">
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-size: 16px; font-weight: 700">{{ promptTitle }}</span>
          <button type="button" style="border: none; background: transparent; color: var(--m-muted)" @click="promptOpen = false">取消</button>
        </div>
        <textarea
          v-model="promptReason"
          class="m-textarea"
          rows="3"
          :placeholder="promptPlaceholder"
        />
        <button
          type="button"
          class="m-btn m-btn-primary"
          style="width: 100%"
          :disabled="promptBusy"
          @click="run(submitPrompt)"
        >
          {{ promptBusy ? "提交中…" : "确认" }}
        </button>
      </div>
    </van-popup>
  </div>
</template>
