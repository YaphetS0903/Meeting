<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { showConfirmDialog } from "vant";
import { api, departmentLeaf, run, state } from "../api/client";

const router = useRouter();

const desktopUrl = computed(() => {
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:4023/`;
});

const roleSubtitle = computed(() => {
  const dept = state.me?.department_id
    ? departmentLeaf(state.me.department_id)
    : "";
  const role = state.me?.is_admin ? "管理员" : "成员";
  return [dept, role].filter(Boolean).join(" · ");
});

async function logout() {
  await showConfirmDialog({
    title: "退出登录",
    message: "确定要退出当前账号吗？",
  });
  void api("/auth/logout", "POST").catch(() => {});
  sessionStorage.removeItem("token");
  sessionStorage.setItem("wecom:manual-logout", "1");
  state.me = null;
  void router.replace("/meetings");
}
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <h1 class="m-header-title-lg">更多</h1>
      </div>
    </header>

    <section class="m-section">
      <!-- Light-Blue Profile Card (Screen 21) -->
      <div class="m-card-blue-soft more-profile">
        <div class="more-avatar">
          {{ (state.me?.name || "我").slice(0, 1) }}
        </div>
        <div style="min-width: 0; flex: 1">
          <div style="font-size: 17px; font-weight: 700; color: var(--m-ink)">
            {{ state.me?.name }}
          </div>
          <div class="m-muted" style="margin-top: 2px">
            {{ roleSubtitle }}
          </div>
        </div>
      </div>

      <!-- 任务管理 -->
      <template
        v-if="
          state.me?.can_assign_tasks ||
          state.me?.can_view_issued_tasks ||
          state.me?.can_oversee_tasks
        "
      >
        <div class="m-section-heading"><h2>任务管理</h2></div>
        <div class="m-card-flush">
          <button
            v-if="state.me?.can_assign_tasks"
            type="button"
            class="m-list-row"
            @click="router.push('/assignments')"
          >
            <span class="more-row-left">
              <span class="m-icon-tile">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
              </span>
              <span class="more-row-title">直接派发任务</span>
            </span>
            <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>

          <button
            v-if="state.me?.can_view_issued_tasks ?? state.me?.can_assign_tasks"
            type="button"
            class="m-list-row"
            @click="router.push('/my-assignments')"
          >
            <span class="more-row-left">
              <span class="m-icon-tile">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="12 2 2 7 12 12 22 7 12 2" />
                  <polyline points="2 17 12 22 22 17" />
                  <polyline points="2 12 12 17 22 12" />
                </svg>
              </span>
              <span class="more-row-title">我派发的任务</span>
            </span>
            <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>

          <button
            v-if="state.me?.can_oversee_tasks"
            type="button"
            class="m-list-row"
            @click="router.push('/task-overview')"
          >
            <span class="more-row-left">
              <span class="m-icon-tile">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="18" y1="20" x2="18" y2="10" />
                  <line x1="12" y1="20" x2="12" y2="4" />
                  <line x1="6" y1="20" x2="6" y2="14" />
                </svg>
              </span>
              <span class="more-row-title">任务总览</span>
            </span>
            <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
        </div>
      </template>

      <!-- 系统 -->
      <template v-if="state.me?.is_admin || state.me?.can_view_operation_logs">
        <div class="m-section-heading"><h2>系统</h2></div>
        <div class="m-card-flush">
          <button
            v-if="state.me?.is_admin"
            type="button"
            class="m-list-row"
            @click="router.push('/settings')"
          >
            <span class="more-row-left">
              <span class="m-icon-tile">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.6.85 1 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
              </span>
              <span class="more-row-title">系统管理</span>
            </span>
            <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>

          <button
            v-if="state.me?.can_view_operation_logs"
            type="button"
            class="m-list-row"
            @click="router.push('/operation-logs')"
          >
            <span class="more-row-left">
              <span class="m-icon-tile">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="8" y1="13" x2="16" y2="13" />
                  <line x1="8" y1="17" x2="13" y2="17" />
                </svg>
              </span>
              <span class="more-row-title">操作日志</span>
            </span>
            <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
        </div>
      </template>

      <!-- 账号 -->
      <div class="m-card-flush" style="margin-top: 4px">
        <button
          v-if="state.me?.has_password"
          type="button"
          class="m-list-row"
          @click="router.push('/change-password')"
        >
          <span class="more-row-left">
            <span class="m-icon-tile">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                <rect x="4" y="11" width="16" height="10" rx="2" />
                <path d="M8 11V7a4 4 0 0 1 8 0v4" />
              </svg>
            </span>
            <span class="more-row-title">修改密码</span>
          </span>
          <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
        </button>

        <a :href="desktopUrl" class="m-list-row" style="text-decoration: none">
          <span class="more-row-left">
            <span class="m-icon-tile">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>
            </span>
            <span class="more-row-title">打开电脑端（4023）</span>
          </span>
          <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
        </a>

        <button type="button" class="m-list-row" @click="run(logout)">
          <span class="more-row-left">
            <span class="m-icon-tile">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </span>
            <span class="more-row-title">退出登录</span>
          </span>
          <svg class="m-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.more-profile {
  display: flex;
  align-items: center;
  gap: 14px;
}
.more-avatar {
  width: 50px;
  height: 50px;
  border-radius: 13px;
  background: var(--m-primary);
  color: #ffffff;
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.more-row-left {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}
.more-row-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--m-ink);
}
</style>
