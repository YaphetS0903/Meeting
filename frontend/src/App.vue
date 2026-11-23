<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import {
  Document,
  Finished,
  EditPen,
  Tickets,
  Checked,
  DataAnalysis,
  Setting,
  Notebook,
  ArrowLeft,
} from "@element-plus/icons-vue";
import { api, state, loadSession, run } from "./api";
import { insideWeCom } from "./wecom";
import WorkspaceNavigation from "./components/WorkspaceNavigation.vue";
// Desktop navigation remembers one choice; phones use a focus-trapped drawer.
const navigationKey = "meeting:navigation-mode";
const compactViewport = window.matchMedia("(max-width: 1440px)");
const mobileViewport = window.matchMedia("(max-width: 767px)");
const laptop = ref(compactViewport.matches),
  mobile = ref(mobileViewport.matches);
const navigationPreference = ref<string | null>(null);
const mobileNavigation = ref(false);
try {
  navigationPreference.value = localStorage.getItem(navigationKey);
  if (
    !navigationPreference.value &&
    localStorage.getItem("meeting:sidebar-collapsed") === "1"
  )
    navigationPreference.value = "compact";
  else if (
    !navigationPreference.value &&
    localStorage.getItem("meeting:sidebar-labels") === "1"
  )
    navigationPreference.value = "expanded";
} catch {
  /* Preferences are optional. */
}
const compactNavigation = computed(() =>
  navigationPreference.value
    ? navigationPreference.value === "compact"
    : laptop.value,
);
const navigationExpanded = computed(() =>
  mobile.value ? mobileNavigation.value : !compactNavigation.value,
);
function syncNavigationViewport() {
  laptop.value = compactViewport.matches;
  mobile.value = mobileViewport.matches;
  if (!mobile.value) mobileNavigation.value = false;
}
compactViewport.addEventListener("change", syncNavigationViewport);
mobileViewport.addEventListener("change", syncNavigationViewport);
onUnmounted(() => {
  compactViewport.removeEventListener("change", syncNavigationViewport);
  mobileViewport.removeEventListener("change", syncNavigationViewport);
});
function toggleNavigation() {
  if (mobile.value) {
    mobileNavigation.value = !mobileNavigation.value;
    return;
  }
  navigationPreference.value = compactNavigation.value ? "expanded" : "compact";
  try {
    localStorage.setItem(navigationKey, navigationPreference.value);
  } catch {
    /* Navigation still works. */
  }
}
const wecom = ref(false);
const wecomQr = ref(false);
const autoLoginKey = "wecom:auto-login-until";
const logoutKey = "wecom:manual-logout";
const loadingMessage = ref("正在加载工作台…");
function mayAutoLogin() {
  try {
    return (
      !sessionStorage.getItem(logoutKey) &&
      Date.now() > Number(sessionStorage.getItem(autoLoginKey) || 0)
    );
  } catch {
    return false;
  }
}
async function wecomLogin(mode: "oauth" | "qr" = "oauth") {
  if (busy.value) return false;
  busy.value = true;
  loginError.value = "";
  try {
    sessionStorage.setItem(autoLoginKey, String(Date.now() + 10 * 60 * 1000));
    const r = await api(
      "/auth/wecom/url?" +
        new URLSearchParams({
          return_to: router.currentRoute.value.fullPath,
          mode,
        }),
    );
    window.location.assign(r.url);
    return true;
  } catch (error: any) {
    loginError.value = error.message || "企业微信登录暂时不可用，请重试";
    return false;
  } finally {
    busy.value = false;
  }
}
const router = useRouter(),
  route = useRoute(),
  ready = ref(false),
  busy = ref(false),
  username = ref(""),
  password = ref(""),
  loginError = ref(""),
  passwordDialog = ref(false),
  changingPassword = ref(false),
  passwordError = ref(""),
  currentPassword = ref(""),
  newPassword = ref(""),
  confirmPassword = ref("");
const links = [
  ["/meetings", "会议纪要与任务单", Document],
  ["/tasks", "我的待办", Finished],
  ["/assignments", "直接派发任务", EditPen],
  ["/my-assignments", "我派发的任务", Tickets],
  ["/task-overview", "任务总览", DataAnalysis],
  ["/verifications", "待我验证", Checked],
  ["/reports", "统计报表", DataAnalysis],
  ["/settings", "系统管理", Setting],
  ["/operation-logs", "操作日志", Notebook],
] as const;
const visibleLinks = computed(() =>
  links.filter(
    ([path]) =>
      (path !== "/settings" || state.me?.is_admin) &&
      (path !== "/operation-logs" || state.me?.can_view_operation_logs) &&
      (path !== "/assignments" || state.me?.can_assign_tasks) &&
      (path !== "/my-assignments" ||
        (state.me?.can_view_issued_tasks ?? state.me?.can_assign_tasks)) &&
      (path !== "/task-overview" || state.me?.can_oversee_tasks),
  ),
);
watch(
  () => route.fullPath,
  () => {
    mobileNavigation.value = false;
  },
);

const backLocation = computed(() => {
  const current = route.fullPath;
  const previous = router.options.history.state.back;
  if (
    typeof previous === "string" &&
    previous.startsWith("/") &&
    !previous.startsWith("//") &&
    previous !== current
  ) {
    const target = router.resolve(previous);
    if (
      (links.some(([path]) => path === target.path) ||
        target.path === "/settings/employees" ||
        target.path === "/history-meetings" ||
        /^\/meetings\/[^/]+$/.test(target.path)) &&
      !target.query.code &&
      !target.query.state
    )
      return { path: previous, history: true };
  }
  if (/^\/meetings\/[^/]+$/.test(route.path)) {
    const department = route.query.history_department;
    if (typeof department === "string")
      return {
        path: router.resolve({
          path: "/history-meetings",
          query: {
            ...(department === "all" ? {} : { department }),
            ...(typeof route.query.history_series === "string"
              ? { series: route.query.history_series }
              : {}),
          },
        }).fullPath,
        history: false,
      };
    if (typeof route.query.series === "string")
      return {
        path: router.resolve({
          path: "/meetings",
          query: { series: route.query.series },
        }).fullPath,
        history: false,
      };
  }
  if (route.path.startsWith("/settings/"))
    return { path: "/settings", history: false };
  return current === "/meetings" ? null : { path: "/meetings", history: false };
});
async function goBack() {
  const target = backLocation.value;
  if (!target) return;
  if (target.history) router.back();
  else await router.replace(target.path);
}
onMounted(async () => {
  await router.isReady();
  const params = new URLSearchParams(window.location.search);
  const callback = params.has("code") || params.has("state");
  if (callback) {
    loadingMessage.value = "正在验证企业微信身份…";
    try {
      if (!params.get("code") || !params.get("state"))
        throw new Error("企业微信登录链接不完整，请重新登录");
      const r = await api("/auth/wecom/exchange", "POST", {
        code: params.get("code"),
        state: params.get("state"),
      });
      sessionStorage.setItem("token", r.access_token);
      await router.replace(r.return_to || "/tasks");
      await loadSession();
      sessionStorage.removeItem(autoLoginKey);
      sessionStorage.removeItem(logoutKey);
    } catch (error: any) {
      state.me = null;
      sessionStorage.removeItem("token");
      loginError.value = error.message || "企业微信登录失败，请重试";
      const query = { ...router.currentRoute.value.query };
      delete query.code;
      delete query.state;
      await router.replace({ path: router.currentRoute.value.path, query });
    }
  } else if (sessionStorage.getItem("token")) {
    try {
      await loadSession();
    } catch (error: any) {
      loginError.value = error.message || "登录状态加载失败，请重试";
    }
  }
  const options = await api("/auth/options").catch(() => ({
    wecom: false,
    wecom_qr: false,
  }));
  wecom.value = options.wecom;
  wecomQr.value = options.wecom_qr;
  if (
    !state.me &&
    !sessionStorage.getItem("token") &&
    !callback &&
    wecom.value &&
    insideWeCom() &&
    mayAutoLogin()
  ) {
    loadingMessage.value = "正在通过企业微信自动登录…";
    if (await wecomLogin()) return;
  }
  ready.value = true;
});
async function login() {
  busy.value = true;
  loginError.value = "";
  try {
    const r = await api("/auth/login", "POST", {
      username: username.value,
      password: password.value,
    });
    sessionStorage.setItem("token", r.access_token);
    await loadSession();
    sessionStorage.removeItem(autoLoginKey);
    sessionStorage.removeItem(logoutKey);
    password.value = "";
  } catch (e: any) {
    loginError.value = e.message;
  } finally {
    busy.value = false;
  }
}
function clearPasswordForm() {
  currentPassword.value = "";
  newPassword.value = "";
  confirmPassword.value = "";
  passwordError.value = "";
}
async function changePassword() {
  passwordError.value = "";
  if (newPassword.value.length < 15) {
    passwordError.value = "新密码至少 15 个字符";
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = "两次输入的新密码不一致";
    return;
  }
  changingPassword.value = true;
  try {
    const response = await api("/me/password", "PUT", {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    });
    sessionStorage.setItem("token", response.access_token);
    passwordDialog.value = false;
    clearPasswordForm();
    ElMessage.success("密码已修改，其他设备需要重新登录");
  } catch (error: any) {
    passwordError.value = error.message || "修改密码失败";
  } finally {
    changingPassword.value = false;
  }
}
function logout() {
  void api("/auth/logout", "POST").catch(() => {
    // Local logout remains available if the session or connection has expired.
  });
  sessionStorage.removeItem("token");
  sessionStorage.setItem(logoutKey, "1");
  passwordDialog.value = false;
  clearPasswordForm();
  state.me = null;
  router.push("/meetings");
}
</script>
<template>
  <div v-if="!ready" class="loading-screen" role="status">
    {{ loadingMessage }}
  </div>
  <main v-else-if="!state.me" class="login-screen">
    <section class="login-intro">
      <div class="brand-mark">M</div>
      <p class="eyebrow">MEETING & ACTIONS</p>
      <h1>让会议有记录，<br />让工作有结果。</h1>
      <p>从会议纪要到责任落实，把安排、跟进和验证<br />放在同一个工作台。</p>
      <div class="login-rule">
        会议录入 <span>→</span> 任务下发 <span>→</span> 验证关闭
      </div>
    </section>
    <form class="login-card" @submit.prevent="login">
      <p class="eyebrow">工作台登录</p>
      <h2>欢迎回来</h2>
      <p class="muted">使用管理员账号或企业微信身份登录</p>
      <label for="username">账号</label
      ><el-input
        id="username"
        v-model="username"
        autocomplete="username"
        size="large"
        required
      /><label for="password">密码</label
      ><el-input
        id="password"
        v-model="password"
        type="password"
        show-password
        autocomplete="current-password"
        size="large"
        required
      /><el-alert
        v-if="loginError"
        :title="loginError"
        type="error"
        :closable="false"
      /><el-button
        type="primary"
        native-type="submit"
        size="large"
        :loading="busy"
        >进入工作台</el-button
      >
      <p class="muted small">账号由管理员提供。</p>
      <el-button
        v-if="wecom && insideWeCom()"
        :loading="busy"
        @click="wecomLogin()"
        >企业微信登录</el-button
      >
      <el-button
        v-if="wecomQr && !insideWeCom()"
        :loading="busy"
        @click="wecomLogin('qr')"
        >企业微信扫码登录</el-button
      >
      <p v-if="wecomQr && !insideWeCom()" class="muted small">
        使用手机企业微信扫描二维码，确认后进入工作台。
      </p>
    </form>
  </main>
  <div
    v-else
    class="app-shell"
    :class="{ 'navigation-compact': compactNavigation }"
  >
    <a class="skip-link" href="#main">跳至主要内容</a>
    <button
      v-if="!mobile"
      class="navigation-trigger navigation-edge-trigger"
      type="button"
      :aria-label="navigationExpanded ? '收起导航栏' : '展开导航栏'"
      :title="navigationExpanded ? '收起导航栏' : '展开导航栏'"
      :aria-expanded="navigationExpanded"
      aria-controls="workspace-navigation"
      @click="toggleNavigation"
    >
      <svg class="navigation-edge-shape" viewBox="0 0 24 72" aria-hidden="true">
        <path d="M0 0C0 14 24 10 24 26V46C24 62 0 58 0 72Z" />
      </svg>
      <svg class="navigation-edge-arrow" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M10 3.5 5.5 8 10 12.5Z" />
      </svg>
    </button>
    <aside class="sidebar" v-if="!mobile">
      <RouterLink class="brand" to="/meetings" aria-label="会议协同首页">
        <span class="brand-mark">M</span>
        <span class="brand-title"
          >会议协同<small>纪要 · 任务 · 验证</small></span
        >
      </RouterLink>
      <p class="nav-label">工作空间</p>
      <WorkspaceNavigation id="workspace-navigation" :links="visibleLinks" />
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="topbar-leading">
          <button
            v-if="mobile"
            class="navigation-trigger"
            type="button"
            :aria-label="navigationExpanded ? '收起导航栏' : '展开导航栏'"
            :title="navigationExpanded ? '收起导航栏' : '展开导航栏'"
            :aria-expanded="navigationExpanded"
            :aria-controls="
              mobile ? 'mobile-workspace-navigation' : 'workspace-navigation'
            "
            @click="toggleNavigation"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M9 3v18" />
            </svg>
          </button>
          <el-button
            class="workspace-back"
            :icon="ArrowLeft"
            :disabled="!backLocation"
            :title="backLocation ? '返回上一页' : '当前已是会议列表首页'"
            @click="run(goBack)"
            >返回</el-button
          >
          <span class="workspace-label">部门协作工作台</span>
        </div>
        <div class="topbar-account">
          <span class="avatar">{{ state.me.name.slice(0, 1) }}</span>
          <span class="workspace-user-name">{{ state.me.name }}</span>
          <el-button
            v-if="state.me.has_password"
            text
            @click="passwordDialog = true"
            >修改密码</el-button
          >
          <el-button text @click="logout">退出</el-button>
        </div>
      </header>
      <main id="main" tabindex="-1"><RouterView /></main>
    </div>
    <el-drawer
      v-model="mobileNavigation"
      class="navigation-drawer"
      title="工作空间"
      direction="ltr"
      size="min(280px, calc(100vw - 48px))"
      :append-to-body="true"
    >
      <RouterLink
        class="brand"
        to="/meetings"
        @click="mobileNavigation = false"
      >
        <span class="brand-mark">M</span
        ><span class="brand-title"
          >会议协同<small>纪要 · 任务 · 验证</small></span
        >
      </RouterLink>
      <WorkspaceNavigation
        id="mobile-workspace-navigation"
        :links="visibleLinks"
        @navigate="mobileNavigation = false"
      />
    </el-drawer>
    <el-dialog
      v-model="passwordDialog"
      title="修改密码"
      width="min(420px, calc(100vw - 32px))"
      @closed="clearPasswordForm"
    >
      <el-form label-position="top" @submit.prevent="changePassword">
        <el-form-item label="当前密码">
          <el-input
            v-model="currentPassword"
            type="password"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        <el-form-item label="新密码（至少 15 个字符）">
          <el-input
            v-model="newPassword"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="再次输入新密码">
          <el-input
            v-model="confirmPassword"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-alert
          v-if="passwordError"
          :title="passwordError"
          type="error"
          :closable="false"
        />
      </el-form>
      <template #footer>
        <el-button @click="passwordDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="changingPassword"
          @click="changePassword"
          >确认修改</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>
