<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { showSuccessToast } from "vant";
import { api } from "../api/client";
import { chineseError } from "../api/errorMessages";

const router = useRouter();
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const showCur = ref(false);
const showNew = ref(false);
const showConfirm = ref(false);
const saving = ref(false);
const error = ref("");

async function submit() {
  error.value = "";
  if (!currentPassword.value) {
    error.value = "请输入当前密码";
    return;
  }
  if (newPassword.value.length < 15) {
    error.value = "新密码至少 15 个字符";
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = "两次输入的新密码不一致";
    return;
  }
  saving.value = true;
  try {
    const res = await api("/me/password", "PUT", {
      current_password: currentPassword.value,
      new_password: newPassword.value,
    });
    sessionStorage.setItem("token", res.access_token);
    showSuccessToast("密码已修改，其他设备需要重新登录");
    router.back();
  } catch (e: any) {
    error.value = chineseError(e, "修改密码失败");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="m-page-sub">
    <header class="m-sticky-header-white">
      <div class="m-header-bar">
        <button type="button" class="m-back-btn" @click="router.back()">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <h1 class="m-header-title" style="flex: 1">修改密码</h1>
      </div>
    </header>

    <section class="m-section" style="padding-top: 14px">
      <form class="m-card" style="display: flex; flex-direction: column; gap: 14px" @submit.prevent="submit">
        <div>
          <label class="m-label m-label-req">当前密码</label>
          <div class="pwd-wrap">
            <input
              v-model="currentPassword"
              :type="showCur ? 'text' : 'password'"
              class="m-input"
              autocomplete="current-password"
              placeholder="请输入当前密码"
            />
            <button type="button" class="pwd-eye" @click="showCur = !showCur">
              {{ showCur ? "隐藏" : "显示" }}
            </button>
          </div>
        </div>

        <div>
          <label class="m-label m-label-req">新密码</label>
          <div class="pwd-wrap">
            <input
              v-model="newPassword"
              :type="showNew ? 'text' : 'password'"
              class="m-input"
              autocomplete="new-password"
              placeholder="请输入新密码"
            />
            <button type="button" class="pwd-eye" @click="showNew = !showNew">
              {{ showNew ? "隐藏" : "显示" }}
            </button>
          </div>
          <div class="m-muted" style="margin-top: 5px">至少 15 个字符</div>
        </div>

        <div>
          <label class="m-label m-label-req">再次输入新密码</label>
          <div class="pwd-wrap">
            <input
              v-model="confirmPassword"
              :type="showConfirm ? 'text' : 'password'"
              class="m-input"
              autocomplete="new-password"
              placeholder="请再次输入新密码"
            />
            <button type="button" class="pwd-eye" @click="showConfirm = !showConfirm">
              {{ showConfirm ? "隐藏" : "显示" }}
            </button>
          </div>
        </div>

        <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      </form>
    </section>

    <div class="m-sticky-footer">
      <button
        type="button"
        class="m-btn m-btn-primary"
        style="width: 100%"
        :disabled="saving"
        @click="submit"
      >
        {{ saving ? "正在保存…" : "保存新密码" }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.pwd-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.pwd-wrap .m-input {
  padding-right: 54px;
}
.pwd-eye {
  position: absolute;
  right: 10px;
  border: none;
  background: transparent;
  color: var(--m-muted);
  font-size: 12px;
  cursor: pointer;
}
</style>
