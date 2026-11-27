import { ElMessage } from "element-plus";
import { chineseError, reportClientError } from "./errorMessages";
import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "./style.css";
import "./laptop.css";
import "./workspace-ui.css";
import App from "./App.vue";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/meetings" },
    { path: "/meetings", component: () => import("./pages/Meetings.vue") },
    {
      path: "/history-meetings",
      component: () => import("./pages/HistoryMeetings.vue"),
    },
    { path: "/meetings/:id", component: () => import("./pages/Meeting.vue") },
    { path: "/tasks", component: () => import("./pages/Tasks.vue") },
    {
      path: "/task-overview",
      component: () => import("./pages/TaskOverview.vue"),
    },
    {
      path: "/assignments",
      component: () => import("./pages/Assignments.vue"),
    },
    {
      path: "/my-assignments",
      component: () => import("./pages/MyAssignments.vue"),
    },
    {
      path: "/verifications",
      component: () => import("./pages/Verifications.vue"),
    },
    { path: "/validation", redirect: "/meetings" },
    { path: "/reports", component: () => import("./pages/Reports.vue") },
    { path: "/settings", component: () => import("./pages/Settings.vue") },
    {
      path: "/operation-logs",
      component: () => import("./pages/OperationLogs.vue"),
    },
    {
      path: "/settings/employees",
      component: () => import("./pages/Employees.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/meetings" },
  ],
});
const app = createApp(App).use(router).use(ElementPlus, { locale: zhCn });
app.config.errorHandler = (error) => {
  reportClientError("browser");
  ElMessage.error(chineseError(error));
};
router.onError(() => {
  reportClientError("route");
  ElMessage.error("页面加载失败，请重新打开该功能");
});
window.addEventListener("unhandledrejection", (event) => {
  if (event.reason === "cancel" || event.reason === "close") return;
  reportClientError("browser");
  ElMessage.error(chineseError(event.reason));
});
app.mount("#app");
