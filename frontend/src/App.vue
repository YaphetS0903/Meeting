<script setup lang="ts">
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import {
  Document,
  Finished,
  EditPen,
  Checked,
  Warning,
  DataAnalysis,
  Setting,
} from "@element-plus/icons-vue";
import { api, state, loadSession, run } from "./api";
import { insideWeCom } from "./wecom";
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
  ["/task-overview", "任务总览", DataAnalysis],
  ["/verifications", "待我验证", Checked],
  ["/validation", "导入历史会议", Warning],
  ["/reports", "统计报表", DataAnalysis],
  ["/settings", "系统管理", Setting],
] as const;
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
  <div v-else class="app-shell">
    <a class="skip-link" href="#main">跳至主要内容</a>
    <aside class="sidebar">
      <RouterLink class="brand" to="/meetings"
        ><span class="brand-mark">M</span
        ><span>会议协同<small>纪要 · 任务 · 验证</small></span></RouterLink
      >
      <p class="nav-label">工作空间</p>
      <nav aria-label="主导航">
        <RouterLink
          v-for="[path, label, icon] in links.filter(
            (x) =>
              (x[0] !== '/settings' || state.me.is_admin) &&
              (x[0] !== '/assignments' || state.me.can_assign_tasks) &&
              (x[0] !== '/task-overview' || state.me.can_oversee_tasks) &&
              (x[0] !== '/validation' ||
                state.me.is_admin ||
                state.me.can_correct_history),
          )"
          :key="path"
          :to="path"
          ><el-icon><component :is="icon" /></el-icon
          ><span>{{ label }}</span></RouterLink
        >
      </nav>
      <div class="sidebar-note">
        <span class="live-dot" /> 历史考核独立留存
        <p>任务延期不改写已发布结果</p>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <span>部门协作工作台</span>
        <div>
          <span class="avatar">{{ state.me.name.slice(0, 1) }}</span>
          {{ state.me.name }}
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
      <footer>统一使用北京时间 · 周一至周日为一个自然周</footer>
    </div>
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
