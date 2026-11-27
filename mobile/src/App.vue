<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showSuccessToast } from "vant";
import { api, loadSession, state } from "./api/client";
import { chineseError } from "./api/errorMessages";
import { insideWeCom } from "./api/wecom";
import AppTabbar from "./components/AppTabbar.vue";

const router = useRouter();
const route = useRoute();

const ready = ref(false);
const loadingMessage = ref("正在加载工作台…");
const busy = ref(false);
const username = ref("");
const password = ref("");
const showPwd = ref(false);
const loginError = ref("");
const wecom = ref(false);
const wecomQr = ref(false);

const autoLoginKey = "wecom:auto-login-until";
const logoutKey = "wecom:manual-logout";

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
          return_to: router.currentRoute.value.fullPath || "/meetings",
          mode,
          client: "mobile",
        }),
    );
    window.location.assign(r.url);
    return true;
  } catch (error: any) {
    loginError.value = chineseError(error, "企业微信登录暂时不可用，请重试");
    return false;
  } finally {
    busy.value = false;
  }
}

async function login() {
  if (!username.value.trim() || !password.value) {
    loginError.value = "请输入账号和密码";
    return;
  }
  busy.value = true;
  loginError.value = "";
  try {
    const r = await api("/auth/login", "POST", {
      username: username.value.trim(),
      password: password.value,
    });
    sessionStorage.setItem("token", r.access_token);
    await loadSession();
    sessionStorage.removeItem(autoLoginKey);
    sessionStorage.removeItem(logoutKey);
    password.value = "";
    showSuccessToast("欢迎回来");
  } catch (e: any) {
    loginError.value = chineseError(e, "账号或密码错误");
  } finally {
    busy.value = false;
  }
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
      await router.replace(r.return_to || "/meetings");
      await loadSession();
      sessionStorage.removeItem(autoLoginKey);
      sessionStorage.removeItem(logoutKey);
    } catch (error: any) {
      state.me = null;
      sessionStorage.removeItem("token");
      loginError.value = chineseError(error, "企业微信登录失败，请重试");
      const q = { ...router.currentRoute.value.query };
      delete q.code;
      delete q.state;
      await router.replace({ path: router.currentRoute.value.path, query: q });
    }
  } else if (sessionStorage.getItem("token")) {
    try {
      await loadSession();
    } catch (error: any) {
      loginError.value = chineseError(error, "登录状态已失效，请重新登录");
    }
  }
  const options = await api("/auth/options").catch(() => ({
    wecom: false,
    wecom_qr: false,
  }));
  wecom.value = !!options.wecom;
  wecomQr.value = !!options.wecom_qr;
  if (
    !state.me &&
    !sessionStorage.getItem("token") &&
    !callback &&
    wecom.value &&
    insideWeCom() &&
    mayAutoLogin()
  ) {
    loadingMessage.value = "正在通过企业微信自动登录…";
    if (await wecomLogin("oauth")) return;
  }
  ready.value = true;
});
</script>

<template>
  <div v-if="!ready" class="bb-loading-screen">
    <div class="bb-monogram">纪</div>
    <van-loading size="22" color="#2563eb">{{ loadingMessage }}</van-loading>
  </div>

  <main v-else-if="!state.me" class="bb-login-page">
    <header class="bb-login-banner">
      <div class="bb-login-monogram">纪</div>
      <h1>会议纪要任务单系统</h1>
    </header>

    <section class="bb-login-body">
      <form class="bb-login-card" @submit.prevent="login">
        <h2>工作台登录</h2>

        <div class="bb-input-row">
          <span class="bb-input-icon" aria-hidden="true">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="8" r="4" />
              <path d="M5 20c0-3.8 3.1-6 7-6s7 2.2 7 6" />
            </svg>
          </span>
          <span class="bb-input-sep" aria-hidden="true" />
          <input
            id="m-username"
            v-model="username"
            autocomplete="username"
            placeholder="请输入账号"
            aria-label="账号"
          />
        </div>

        <div class="bb-input-row">
          <span class="bb-input-icon" aria-hidden="true">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <rect x="4" y="11" width="16" height="10" rx="2" />
              <path d="M8 11V7a4 4 0 0 1 8 0v4" />
            </svg>
          </span>
          <span class="bb-input-sep" aria-hidden="true" />
          <input
            id="m-password"
            v-model="password"
            :type="showPwd ? 'text' : 'password'"
            autocomplete="current-password"
            placeholder="请输入密码"
            aria-label="密码"
          />
          <button
            type="button"
            class="bb-eye-btn"
            :aria-label="showPwd ? '隐藏密码' : '显示密码'"
            @click="showPwd = !showPwd"
          >
            <svg
              v-if="!showPwd"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" />
              <circle cx="12" cy="12" r="2.8" />
            </svg>
            <svg
              v-else
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M17.94 17.94A10.9 10.9 0 0 1 12 18c-6.5 0-10-6-10-6a18.5 18.5 0 0 1 5.06-5.94" />
              <path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 6 10 6a18.5 18.5 0 0 1-2.16 3.19" />
              <line x1="2" y1="2" x2="22" y2="22" />
            </svg>
          </button>
        </div>

        <div v-if="loginError" class="m-alert m-alert-error" style="margin-bottom: 12px">
          {{ loginError }}
        </div>

        <button
          type="submit"
          class="m-btn m-btn-primary"
          style="width: 100%; min-height: 46px; font-size: 15px"
          :disabled="busy"
        >
          {{ busy ? "正在登录…" : "进入工作台" }}
        </button>

        <template v-if="(wecom && insideWeCom()) || (wecomQr && !insideWeCom())">
          <div class="bb-or-divider">
            <span /><em>或</em><span />
          </div>

          <button
            v-if="wecom && insideWeCom()"
            type="button"
            class="m-btn bb-wecom-btn"
            :disabled="busy"
            @click="wecomLogin('oauth')"
          >
            <svg
              width="19"
              height="19"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#2563eb"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
            </svg>
            企业微信登录
          </button>

          <button
            v-else-if="wecomQr && !insideWeCom()"
            type="button"
            class="m-btn bb-wecom-btn"
            :disabled="busy"
            @click="wecomLogin('qr')"
          >
            <svg
              width="19"
              height="19"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#2563eb"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
            </svg>
            企业微信扫码登录
          </button>
        </template>

        <p class="bb-login-note">账号由管理员提供。</p>
      </form>
    </section>
  </main>

  <div v-else>
    <RouterView />
    <AppTabbar v-if="route.meta.tab" />
  </div>
</template>

<style scoped>
.bb-loading-screen {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: var(--m-canvas);
}
.bb-monogram {
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: var(--m-primary-grad);
  color: #ffffff;
  font-size: 26px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--m-shadow-hero);
}
.bb-login-page {
  min-height: 100dvh;
  background: var(--m-canvas);
  display: flex;
  flex-direction: column;
}
.bb-login-banner {
  background: linear-gradient(150deg, #1b56e6 0%, #3178f6 52%, #5298ff 100%);
  padding: calc(56px + env(safe-area-inset-top)) 20px 64px;
  border-bottom-left-radius: 50% 24px;
  border-bottom-right-radius: 50% 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  color: #ffffff;
}
.bb-login-monogram {
  width: 56px;
  height: 56px;
  border-radius: 15px;
  background: #ffffff;
  color: var(--m-primary);
  font-size: 28px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(15, 38, 110, 0.22);
}
.bb-login-banner h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.bb-login-body {
  padding: 0 18px calc(36px + env(safe-area-inset-bottom));
  margin-top: -28px;
}
.bb-login-card {
  background: var(--m-surface);
  border: 1px solid #edf1f7;
  border-radius: 18px;
  padding: 24px 20px 22px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.07);
}
.bb-login-card h2 {
  margin: 0 0 18px;
  font-size: 20px;
  font-weight: 700;
  color: var(--m-ink);
}
.bb-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--m-line);
  border-radius: 11px;
  background: var(--m-surface);
  padding: 0 12px;
  height: 48px;
  margin-bottom: 14px;
  transition: border-color 0.14s ease, box-shadow 0.14s ease;
}
.bb-input-row:focus-within {
  border-color: var(--m-primary);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}
.bb-input-icon {
  color: #6b7280;
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}
.bb-input-sep {
  width: 1px;
  height: 18px;
  background: #e5e7eb;
  flex-shrink: 0;
}
.bb-input-row input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: var(--m-ink);
  font-family: inherit;
}
.bb-input-row input::placeholder {
  color: #9ca3af;
}
.bb-eye-btn {
  border: none;
  background: transparent;
  color: #6b7280;
  padding: 4px;
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}
.bb-or-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 16px 0;
}
.bb-or-divider span {
  flex: 1;
  height: 1px;
  background: var(--m-line);
}
.bb-or-divider em {
  font-style: normal;
  font-size: 12px;
  color: #9ca3af;
}
.bb-wecom-btn {
  width: 100%;
  min-height: 46px;
  border-color: #d7dee9;
  color: var(--m-ink);
  font-size: 15px;
}
.bb-login-note {
  margin: 16px 0 0;
  text-align: center;
  font-size: 12px;
  color: #9ca3af;
}
</style>
