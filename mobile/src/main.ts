import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import Vant, { showFailToast } from "vant";
import "vant/lib/index.css";
import "./styles/tokens.css";
import App from "./App.vue";
import { chineseError, reportClientError } from "./api/errorMessages";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/meetings" },
    {
      path: "/meetings",
      component: () => import("./pages/Meetings.vue"),
      meta: { tab: true },
    },
    {
      path: "/history-meetings",
      component: () => import("./pages/HistoryMeetings.vue"),
      meta: { tab: true },
    },
    {
      path: "/meetings/:id",
      component: () => import("./pages/MeetingDetail.vue"),
    },
    {
      path: "/meetings/:id/native-todos",
      component: () => import("./pages/MeetingNativeTodos.vue"),
    },
    {
      path: "/tasks",
      component: () => import("./pages/Tasks.vue"),
      meta: { tab: true },
    },
    {
      path: "/verifications",
      component: () => import("./pages/Verifications.vue"),
      meta: { tab: true },
    },
    {
      path: "/reports",
      component: () => import("./pages/Reports.vue"),
      meta: { tab: true },
    },
    {
      path: "/me",
      component: () => import("./pages/Me.vue"),
      meta: { tab: true },
    },
    {
      path: "/change-password",
      component: () => import("./pages/ChangePassword.vue"),
    },
    {
      path: "/assignments",
      component: () => import("./pages/Assignments.vue"),
    },
    {
      path: "/my-assignments",
      component: () => import("./pages/MyAssignments.vue"),
      meta: { tab: true },
    },
    {
      path: "/task-overview",
      component: () => import("./pages/TaskOverview.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings",
      component: () => import("./pages/SettingsHub.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings/monitoring",
      component: () => import("./pages/SettingsMonitoring.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings/integrations",
      component: () => import("./pages/SettingsIntegrations.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings/sync",
      component: () => import("./pages/SettingsSync.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings/employees",
      component: () => import("./pages/Employees.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings/roles",
      component: () => import("./pages/SettingsRoles.vue"),
      meta: { tab: true },
    },
    {
      path: "/settings/notifications",
      component: () => import("./pages/SettingsNotifications.vue"),
      meta: { tab: true },
    },
    {
      path: "/operation-logs",
      component: () => import("./pages/OperationLogs.vue"),
      meta: { tab: true },
    },
    { path: "/:pathMatch(.*)*", redirect: "/meetings" },
  ],
  scrollBehavior() {
    return { top: 0 };
  },
});

const app = createApp(App).use(router).use(Vant);

app.config.errorHandler = (error) => {
  reportClientError("browser");
  showFailToast(chineseError(error));
};

router.onError(() => {
  reportClientError("route");
  showFailToast("页面加载失败，请重试");
});

window.addEventListener("unhandledrejection", (event) => {
  if (event.reason === "cancel" || event.reason === "close") return;
  reportClientError("browser");
  showFailToast(chineseError(event.reason));
});

app.mount("#app");
