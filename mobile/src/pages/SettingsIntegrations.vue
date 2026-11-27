<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { showSuccessToast } from "vant";
import { api, loadSession, run, state } from "../api/client";
import { chineseError } from "../api/errorMessages";
import { beijingTime } from "../api/time";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const config = ref<any>(null);
const nativeTodo = ref<any>(null);
const reminders = ref<any>(null);
const connectionChecks = ref<any>({});
const busy = ref(false);
const syncing = ref(false);
const checking = ref("");
const savingReminders = ref(false);
const mailExpanded = ref(false);
const reminderExpanded = ref(false);
const error = ref("");

async function load() {
  if (!state.me?.is_admin) return;
  busy.value = true;
  error.value = "";
  try {
    const [cfg, nt, rem, chk] = await Promise.all([
      api("/admin/config"),
      api("/admin/native-todo"),
      api("/admin/reminders"),
      api("/admin/integration-checks"),
    ]);
    config.value = cfg;
    nativeTodo.value = nt;
    reminders.value = rem;
    connectionChecks.value = chk || {};
  } catch (e: any) {
    error.value = chineseError(e, "配置加载失败");
  } finally {
    busy.value = false;
  }
}

async function syncDirectory() {
  syncing.value = true;
  try {
    const r = await api("/admin/wecom/sync", "POST");
    showSuccessToast(`已同步 ${r.users} 名成员、${r.departments} 个部门`);
    await loadSession();
    await load();
  } finally {
    syncing.value = false;
  }
}

async function checkConn(target: "wecom" | "smtp") {
  checking.value = target;
  try {
    const result = await api("/admin/integration-checks", "POST", { target });
    connectionChecks.value = { ...connectionChecks.value, [target]: result };
    showSuccessToast(target === "wecom" ? "企业微信检查完成" : "SMTP 检查完成");
  } finally {
    checking.value = "";
  }
}

async function saveReminders() {
  if (!reminders.value) return;
  savingReminders.value = true;
  try {
    const res = await api("/admin/reminders", "PUT", reminders.value);
    const { cancelled_events: cancelled, ...saved } = res;
    reminders.value = saved;
    showSuccessToast(
      `提醒规则已保存${cancelled ? `，已取消 ${cancelled} 条未发送通知` : ""}`,
    );
  } finally {
    savingReminders.value = false;
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
        <h1 class="m-header-title" style="flex: 1">企业微信与邮件</h1>
      </div>
    </header>

    <section class="m-section" style="padding-top: 12px">
      <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      <div v-if="busy && !config" class="m-empty">
        <van-loading size="22">加载集成配置…</van-loading>
      </div>

      <template v-else-if="config">
        <!-- 1. 企业微信自建应用 -->
        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span style="font-size: 15px; font-weight: 700">企业微信自建应用</span>
            <StatusTag
              :value="config.wecom_configured ? '已配置' : '待配置'"
              :tone="config.wecom_configured ? 'green' : 'gray'"
            />
          </div>
          <div class="m-muted">
            从企业微信同步成员和部门，以成员标识绑定身份。
          </div>
          <div>
            <button
              type="button"
              class="m-btn m-btn-sm m-btn-outline-blue"
              :disabled="syncing || !config.wecom_configured"
              @click="run(syncDirectory)"
            >
              {{ syncing ? "同步中…" : "同步通讯录" }}
            </button>
          </div>
        </div>

        <!-- 2. 企业微信消息与原生待办 -->
        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div style="font-size: 15px; font-weight: 700">企业微信消息与原生待办</div>
          <div class="int-row">
            <span>应用消息</span>
            <StatusTag
              :value="nativeTodo?.direct_messages_enabled ? '已开启' : '未开启'"
              :tone="nativeTodo?.direct_messages_enabled ? 'green' : 'gray'"
            />
          </div>
          <div class="int-row">
            <span>自建应用原生待办</span>
            <StatusTag
              :value="nativeTodo?.app_enabled && nativeTodo?.direct_enabled ? '已开启' : '未开启'"
              :tone="nativeTodo?.app_enabled && nativeTodo?.direct_enabled ? 'green' : 'gray'"
            />
          </div>
          <div class="int-row">
            <span>会议待办</span>
            <StatusTag
              :value="nativeTodo?.meeting_enabled ? '已开启' : '未开启'"
              :tone="nativeTodo?.meeting_enabled ? 'green' : 'gray'"
            />
          </div>
        </div>

        <!-- 3. 企业连接检查 -->
        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div style="font-size: 15px; font-weight: 700">企业连接检查</div>
          <div style="display: flex; gap: 8px">
            <button
              type="button"
              class="m-btn m-btn-sm"
              style="flex: 1"
              :disabled="Boolean(checking)"
              @click="run(() => checkConn('wecom'))"
            >
              {{ checking === "wecom" ? "检查中…" : "检查企业微信连接" }}
            </button>
            <button
              type="button"
              class="m-btn m-btn-sm"
              style="flex: 1"
              :disabled="Boolean(checking)"
              @click="run(() => checkConn('smtp'))"
            >
              {{ checking === "smtp" ? "检查中…" : "检查 SMTP 连接" }}
            </button>
          </div>
          <template
            v-for="t in (['wecom', 'smtp'] as const).filter((k) => connectionChecks[k])"
            :key="t"
          >
            <div class="m-muted tabular">
              {{ t === "wecom" ? "企业微信" : "SMTP" }}检查 ·
              {{ beijingTime(connectionChecks[t].checked_at) }}
            </div>
            <div
              v-for="c in connectionChecks[t].checks || []"
              :key="c.label"
              class="m-alert"
              :class="
                c.state === 'ready'
                  ? 'm-alert-success'
                  : c.state === 'failed'
                    ? 'm-alert-error'
                    : 'm-alert-warning'
              "
            >
              {{ c.label }}：{{ c.message }}
            </div>
          </template>
        </div>

        <!-- 4. Disclosure rows: 纪要邮件 & 提醒规则 -->
        <div class="m-card-flush">
          <button
            type="button"
            class="m-list-row"
            @click="mailExpanded = !mailExpanded"
          >
            <span style="font-size: 15px; font-weight: 600">纪要邮件</span>
            <span style="display: inline-flex; align-items: center; gap: 6px">
              <StatusTag
                :value="config.smtp_configured ? 'SMTP 已配置' : '待配置'"
                :tone="config.smtp_configured ? 'green' : 'gray'"
              />
              <span class="m-muted">{{ mailExpanded ? "▴" : "▾" }}</span>
            </span>
          </button>
          <div v-if="mailExpanded" style="padding: 0 16px 14px" class="m-muted">
            发布前确认收件人与抄送人，邮件附件包含完整纪要图片及 Excel 任务单。凭据由服务器环境变量管理。
          </div>

          <button
            type="button"
            class="m-list-row"
            @click="reminderExpanded = !reminderExpanded"
          >
            <span style="font-size: 15px; font-weight: 600">提醒规则</span>
            <span class="m-muted">{{ reminderExpanded ? "▴" : "▾" }}</span>
          </button>
          <div
            v-if="reminderExpanded && reminders"
            style="padding: 4px 16px 16px; display: flex; flex-direction: column; gap: 12px"
          >
            <div class="int-row">
              <span>会议提醒及纪要邮件</span>
              <button
                type="button"
                class="m-switch"
                :class="{ on: reminders.enabled }"
                @click="reminders.enabled = !reminders.enabled"
              />
            </div>
            <div class="int-row">
              <span>任务截止日期提醒</span>
              <button
                type="button"
                class="m-switch"
                :class="{ on: reminders.deadline_enabled }"
                @click="reminders.deadline_enabled = !reminders.deadline_enabled"
              />
            </div>
            <div class="int-row">
              <span>每日任务提醒</span>
              <button
                type="button"
                class="m-switch"
                :class="{ on: reminders.daily_enabled }"
                @click="reminders.daily_enabled = !reminders.daily_enabled"
              />
            </div>
            <button
              type="button"
              class="m-btn m-btn-sm m-btn-primary"
              :disabled="savingReminders"
              @click="run(saveReminders)"
            >
              {{ savingReminders ? "保存中…" : "保存提醒规则" }}
            </button>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.int-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: var(--m-text);
  padding: 4px 0;
}
</style>
