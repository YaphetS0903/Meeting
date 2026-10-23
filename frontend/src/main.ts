import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "./style.css";
import App from "./App.vue";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/meetings" },
    { path: "/meetings", component: () => import("./pages/Meetings.vue") },
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
      path: "/verifications",
      component: () => import("./pages/Verifications.vue"),
    },
    { path: "/validation", component: () => import("./pages/Validation.vue") },
    { path: "/reports", component: () => import("./pages/Reports.vue") },
    { path: "/settings", component: () => import("./pages/Settings.vue") },
    { path: "/:pathMatch(.*)*", redirect: "/meetings" },
  ],
});
createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount("#app");
