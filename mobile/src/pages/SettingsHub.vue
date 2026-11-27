<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, run, state } from "../api/client";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const operations = ref<any>(null);
const busy = ref(false);

async function load() {
  if (!state.me?.is_admin) return;
  busy.value = true;
  try {
    operations.value = await api("/admin/operations", "GET", undefined, {
      background: true,
    });
  } catch {
    operations.value = null;
  } finally {
    busy.value = false;
  }
}

const entries = [
  {
    key: "monitoring",
    title: "运行监控与告警",
    sub: "独立监控",
    path: "/settings/monitoring",
  },
  {
    key: "integrations",
    title: "企业微信与邮件",
    sub: "应用消息与原生待办",
    path: "/settings/integrations",
  },
  {
    key: "sync",
    title: "通讯录同步",
    sub: "同步记录与停用人员任务调整",
    path: "/settings/sync",
  },
  {
    key: "employees",
    title: "员工通讯录",
    sub: "成员状态与角色分配",
    path: "/settings/employees",
  },
  {
    key: "roles",
    title: "角色管理",
    sub: "自定义角色与模块权限范围",
    path: "/settings/roles",
  },
  {
    key: "notifications",
    title: "通知与失败重试",
    sub: "通知状态、重试与核实送达",
    path: "/settings/notifications",
  },
] as const;

onMounted(() => run(load));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header-white">
      <div class="m-header-bar">
        <button type="button" class="m-back-btn" @click="router.push('/me')">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <h1 class="m-header-title" style="flex: 1">系统管理</h1>
        <button type="button" class="m-btn m-btn-sm" :disabled="busy" @click="run(load)">
          刷新
        </button>
      </div>
    </header>

    <section class="m-section" style="padding-top: 12px">
      <div v-if="!state.me?.is_admin" class="m-alert m-alert-error">
        仅系统管理员可访问系统管理。
      </div>

      <div v-else class="m-card-flush">
        <button
          v-for="item in entries"
          :key="item.key"
          type="button"
          class="m-list-row"
          @click="router.push(item.path)"
        >
          <span style="display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1">
            <span class="m-icon-tile">
              <svg v-if="item.key === 'monitoring'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>
              <svg v-else-if="item.key === 'integrations'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" /></svg>
              <svg v-else-if="item.key === 'sync'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></svg>
              <svg v-else-if="item.key === 'employees'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
              <svg v-else-if="item.key === 'roles'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>
              <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></svg>
            </span>
            <span style="min-width: 0; flex: 1">
              <span style="display: block; font-size: 15px; font-weight: 600; color: var(--m-ink)">
                {{ item.title }}
              </span>
              <span class="m-muted" style="display: block; margin-top: 1px">
                {{ item.sub }}
              </span>
            </span>
          </span>
          <span style="display: inline-flex; align-items: center; gap: 6px">
            <StatusTag
              v-if="item.key === 'monitoring' && operations"
              :value="operations.running ? '正常' : '异常'"
              :tone="operations.running ? 'green' : 'red'"
            />
            <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </span>
        </button>
      </div>
    </section>
  </div>
</template>
