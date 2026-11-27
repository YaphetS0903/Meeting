<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { showConfirmDialog, showSuccessToast, showToast } from "vant";
import {
  addDays,
  api,
  clientId,
  localDate,
  run,
  state,
  thisFriday,
} from "../api/client";
import { chineseError } from "../api/errorMessages";
import {
  assignmentDeliveryMessage,
  confirmAssignedTodo,
  type NativeResult,
} from "../api/nativeAssignment";
import { isEmployeeAccount, withDirectoryDetails } from "../api/people";
import { insideWeCom } from "../api/wecom";
import PeoplePicker from "../components/PeoplePicker.vue";

const router = useRouter();

const options = ref<any>(null);
const loading = ref(false);
const loadError = ref("");
const busy = ref(false);
const nativeResult = ref<NativeResult | null>(null);

const form = ref({
  content: "",
  requirements: "",
  deliverable: "",
  due_date: thisFriday(),
  owner_ids: [] as string[],
  verifier_id: state.me?.id || "",
});

const draftStorageKey = `mobile-direct-assignment:${state.me?.id}`;
let attemptKey = clientId();
let attemptPayload = "";

try {
  const prev = JSON.parse(sessionStorage.getItem(draftStorageKey) || "null");
  if (prev?.key && prev?.payload && Array.isArray(prev.payload.owner_ids)) {
    form.value = { ...form.value, ...prev.payload };
    attemptKey = prev.key;
    attemptPayload = JSON.stringify(prev.payload);
  }
} catch {
  sessionStorage.removeItem(draftStorageKey);
}

const allowedUsers = computed(() => {
  const raw = options.value?.users || [];
  return withDirectoryDetails(raw, state.directory.users || [])
    .map((p: any) => ({ ...p, active: p.active !== false }))
    .filter((p: any) => p.active && isEmployeeAccount(p));
});

const verifiers = computed(() => {
  if (!state.me || allowedUsers.value.some((p: any) => p.id === state.me.id))
    return allowedUsers.value;
  return [...allowedUsers.value, { ...state.me, active: true }];
});

const verifierWarning = computed(() => {
  const sel: any = verifiers.value.find(
    (p: any) => p.id === form.value.verifier_id,
  );
  const bound =
    sel?.wecom_bound ??
    (sel?.id === state.me?.id ? options.value?.wecom_bound : undefined);
  return bound === false
    ? "该验证人未绑定企业微信，无法收到企微验证提醒；可在系统内处理或改选已绑定人员。"
    : "";
});

const quickDates = computed(() => [
  { label: "今天", value: localDate() },
  { label: "明天", value: addDays(1) },
  { label: `本周五 ${thisFriday().slice(5)}`, value: thisFriday() },
  { label: `一周后 ${addDays(7).slice(5)}`, value: addDays(7) },
]);

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    options.value = await api("/direct-assignments/options");
  } catch (e: any) {
    loadError.value = chineseError(e, "派发权限加载失败");
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!form.value.content.trim()) {
    showToast("请填写任务内容");
    return;
  }
  if (!form.value.owner_ids.length) {
    showToast("请选择至少一位责任人");
    return;
  }
  if (!form.value.due_date) {
    showToast("请选择计划完成时间");
    return;
  }
  if (!form.value.verifier_id) {
    showToast("请选择验证人");
    return;
  }
  const names = form.value.owner_ids
    .map(
      (id) =>
        allowedUsers.value.find((u: any) => u.id === id)?.name || "未找到人员",
    )
    .join("、");

  await showConfirmDialog({
    title: "确认派发任务",
    message: `确定向 ${names} 派发任务吗？截止日期：${form.value.due_date}。${verifierWarning.value}`,
  });

  const payload = {
    content: form.value.content.trim(),
    requirements: form.value.requirements.trim(),
    deliverable: form.value.deliverable.trim(),
    due_date: form.value.due_date,
    owner_ids: [...form.value.owner_ids].sort(),
    verifier_id: form.value.verifier_id,
  };
  const serialized = JSON.stringify(payload);
  if (serialized !== attemptPayload) attemptKey = clientId();
  attemptPayload = serialized;
  sessionStorage.setItem(
    draftStorageKey,
    JSON.stringify({ key: attemptKey, payload }),
  );

  busy.value = true;
  nativeResult.value = null;
  try {
    const task = await api("/direct-assignments", "POST", {
      ...payload,
      idempotency_key: attemptKey,
    });
    sessionStorage.removeItem(draftStorageKey);
    attemptKey = clientId();
    attemptPayload = "";
    showSuccessToast(
      "任务已派发。" + assignmentDeliveryMessage(task.wecom_notification_state),
    );
    form.value = {
      content: "",
      requirements: "",
      deliverable: "",
      due_date: thisFriday(),
      owner_ids: [],
      verifier_id: state.me?.id || "",
    };
    if (options.value?.native_todo_auto_confirm && options.value?.wecom_bound) {
      nativeResult.value = await confirmAssignedTodo(task);
    }
  } finally {
    busy.value = false;
  }
}

onMounted(() => run(load));
</script>

<template>
  <div class="m-page-sub">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <button
          type="button"
          class="m-btn m-btn-sm"
          style="border: none; padding: 0 6px"
          @click="router.back()"
        >
          ‹ 返回
        </button>
        <h1 class="m-header-title">直接派发任务</h1>
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-soft"
          @click="router.push('/my-assignments')"
        >
          我派发的
        </button>
      </div>
    </header>

    <section class="m-section">
      <div v-if="loading" class="m-empty">
        <van-loading size="22">读取可派发人员范围…</van-loading>
      </div>
      <div v-else-if="loadError" class="m-alert m-alert-error">{{ loadError }}</div>

      <template v-else-if="options">
        <div
          v-if="nativeResult"
          class="m-alert"
          :class="
            nativeResult.state === 'created'
              ? 'm-alert-success'
              : 'm-alert-info'
          "
        >
          {{ nativeResult.message }}
        </div>

        <div class="m-card" style="display: flex; flex-direction: column; gap: 14px">
          <div>
            <label class="m-label m-label-req">任务内容</label>
            <textarea
              v-model="form.content"
              class="m-textarea"
              rows="4"
              placeholder="请填写清晰的任务事项、目标和验收标准…"
            />
          </div>

          <div>
            <label class="m-label m-label-req">
              责任人（{{ options.scope === "all" ? "全部在用员工" : "授权部门范围" }}）
            </label>
            <PeoplePicker
              v-model="form.owner_ids"
              :users="allowedUsers"
              multiple
              title="选择任务责任人"
            />
          </div>

          <div>
            <label class="m-label m-label-req">计划完成时间</label>
            <div class="m-chip-row" style="margin-bottom: 8px">
              <button
                v-for="d in quickDates"
                :key="d.label"
                type="button"
                class="m-chip m-chip-soft"
                :class="{ active: form.due_date === d.value }"
                @click="form.due_date = d.value"
              >
                {{ d.label }}
              </button>
            </div>
            <input v-model="form.due_date" type="date" class="m-input" />
          </div>

          <div>
            <label class="m-label m-label-req">验证人</label>
            <PeoplePicker
              v-model="form.verifier_id"
              :users="verifiers"
              title="选择任务验证人"
              :clearable="false"
            />
            <div
              v-if="verifierWarning"
              class="m-alert m-alert-warning"
              style="margin-top: 8px"
            >
              {{ verifierWarning }}
            </div>
          </div>

          <div>
            <label class="m-label">输出物（选填）</label>
            <input
              v-model="form.deliverable"
              class="m-input"
              placeholder="例如：汇总表、签收凭证"
            />
          </div>

          <div>
            <label class="m-label">补充说明（选填）</label>
            <textarea
              v-model="form.requirements"
              class="m-textarea"
              rows="2"
              placeholder="选填工作背景或协同要求"
            />
          </div>
        </div>

        <div class="m-alert m-alert-info">
          {{
            options.wecom_notifications_enabled
              ? "任务建立后将排队发送企业微信应用消息。"
              : "应用消息通知当前关闭，责任人可登录系统查看任务。"
          }}
          <template v-if="options.native_todo_auto_confirm && insideWeCom()">
            派发后将自动弹出企微原生待办确认窗。
          </template>
        </div>
      </template>
    </section>

    <div class="m-sticky-footer">
      <button
        type="button"
        class="m-btn m-btn-primary"
        style="width: 100%"
        :disabled="busy || loading || !options"
        @click="run(submit)"
      >
        {{ busy ? "正在派发…" : "确认派发任务" }}
      </button>
    </div>
  </div>
</template>
