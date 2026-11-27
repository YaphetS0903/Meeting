<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { state } from "../api/client";

const route = useRoute();
const router = useRouter();

const active = computed(() => {
  const p = route.path;
  if (p.startsWith("/meetings") || p.startsWith("/history-meetings"))
    return "meetings";
  if (p.startsWith("/tasks")) return "tasks";
  if (p.startsWith("/verifications")) return "verifications";
  if (p.startsWith("/reports")) return "reports";
  return "more";
});

const items = [
  { key: "meetings", label: "会议", path: "/meetings" },
  { key: "tasks", label: "待办", path: "/tasks" },
  { key: "verifications", label: "验证", path: "/verifications" },
  { key: "reports", label: "报表", path: "/reports" },
  { key: "more", label: "更多", path: "/me" },
] as const;

function go(path: string) {
  if (route.path !== path) void router.push(path);
}
</script>

<template>
  <nav class="bb-dock" aria-label="底部主导航">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="bb-dock-cell"
      :class="{ active: active === item.key }"
      @click="go(item.path)"
    >
      <span class="bb-dock-pill">
        <span class="bb-dock-icon-wrap">
          <!-- Calendar icon (会议) -->
          <svg
            v-if="item.key === 'meetings'"
            width="21"
            height="21"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="3" y="4" width="18" height="17" rx="3" />
            <line x1="8" y1="2" x2="8" y2="6" />
            <line x1="16" y1="2" x2="16" y2="6" />
            <line x1="3" y1="10" x2="21" y2="10" />
          </svg>
          <!-- Square Check icon (待办) -->
          <svg
            v-else-if="item.key === 'tasks'"
            width="21"
            height="21"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="4" />
            <polyline points="8.5 12.2 11 14.7 16 9.5" />
          </svg>
          <!-- Shield Check icon (验证) -->
          <svg
            v-else-if="item.key === 'verifications'"
            width="21"
            height="21"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M12 3l7 3v5c0 4.8-3 8.6-7 10-4-1.4-7-5.2-7-10V6l7-3z" />
            <polyline points="9 12.2 11.2 14.4 15.5 10" />
          </svg>
          <!-- Bar Chart icon (报表) -->
          <svg
            v-else-if="item.key === 'reports'"
            width="21"
            height="21"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <line x1="6" y1="20" x2="6" y2="14" />
            <line x1="12" y1="20" x2="12" y2="8" />
            <line x1="18" y1="20" x2="18" y2="4" />
          </svg>
          <!-- 2x2 Grid icon (更多) -->
          <svg
            v-else
            width="21"
            height="21"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="4" y="4" width="6.5" height="6.5" rx="1.5" />
            <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" />
            <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" />
            <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" />
          </svg>
          <span
            v-if="item.key === 'verifications' && state.pendingVerifications > 0"
            class="bb-dock-badge tabular"
          >
            {{ state.pendingVerifications > 99 ? "99+" : state.pendingVerifications }}
          </span>
        </span>
        <span class="bb-dock-label">{{ item.label }}</span>
      </span>
    </button>
  </nav>
</template>

<style scoped>
.bb-dock {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 40;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  background: var(--m-surface);
  border-top: 1px solid var(--m-line);
  box-shadow: var(--m-shadow-dock);
  padding: 5px 6px calc(5px + env(safe-area-inset-bottom));
}
.bb-dock-cell {
  border: none;
  background: transparent;
  padding: 0 2px;
  display: flex;
  justify-content: center;
  align-items: center;
  cursor: pointer;
  color: #52525b;
  font-family: inherit;
}
.bb-dock-pill {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: 100%;
  max-width: 68px;
  padding: 5px 0 4px;
  border-radius: 12px;
  transition: background 0.16s ease, color 0.16s ease;
}
.bb-dock-cell.active {
  color: var(--m-primary);
}
.bb-dock-cell.active .bb-dock-pill {
  background: var(--m-primary-soft);
}
.bb-dock-icon-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 22px;
}
.bb-dock-badge {
  position: absolute;
  top: -5px;
  right: -12px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--m-danger);
  color: #ffffff;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}
.bb-dock-label {
  font-size: 11px;
  font-weight: 500;
  line-height: 1.2;
}
.bb-dock-cell.active .bb-dock-label {
  font-weight: 600;
}
</style>
