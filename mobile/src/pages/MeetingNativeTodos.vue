<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api/client";
import { chineseError } from "../api/errorMessages";
import { confirmAssignedTodo, type NativeResult } from "../api/nativeAssignment";
import { insideWeCom, viewWeComTodo } from "../api/wecom";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();
const meetingId = computed(() => String(route.params.id || ""));

const plan = ref<any>(null);
const meetingTitle = ref("");
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const progress = ref("");
const results = ref<Record<string, NativeResult>>({});
let disposed = false;

const readyTasks = computed(() =>
  (plan.value?.tasks || []).filter(
    (task: any) =>
      !task.native_todo?.link &&
      !task.native_todo?.pending_attempt &&
      task.native_todo?.can_create &&
      !["created", "linked", "pending"].includes(
        results.value[task.id]?.state || "",
      ),
  ),
);

const linkedCount = computed(
  () =>
    (plan.value?.tasks || []).filter(
      (task: any) =>
        task.native_todo?.link ||
        ["created", "linked"].includes(results.value[task.id]?.state || ""),
    ).length,
);

const pendingCount = computed(
  () =>
    (plan.value?.tasks || []).filter(
      (task: any) =>
        task.native_todo?.pending_attempt ||
        results.value[task.id]?.state === "pending",
    ).length,
);

function stateLabel(task: any) {
  if (task.native_todo?.link?.state === "finished") return "待办已完成";
  if (
    task.native_todo?.link ||
    ["created", "linked"].includes(results.value[task.id]?.state || "")
  )
    return "已创建";
  if (
    task.native_todo?.pending_attempt ||
    results.value[task.id]?.state === "pending"
  )
    return "待核实";
  if (results.value[task.id]?.state === "cancelled") return "已取消确认";
  if (!task.native_todo?.can_create) return "暂不可派发";
  return "待确认";
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [p, m] = await Promise.all([
      api(`/meetings/${meetingId.value}/native-todos`),
      api(`/meetings/${meetingId.value}`, "GET", undefined, {
        background: true,
      }).catch(() => null),
    ]);
    plan.value = p;
    if (m?.title) meetingTitle.value = m.title;
  } catch (e: any) {
    error.value = chineseError(e, "无法读取待办状态，请重试");
  } finally {
    loading.value = false;
  }
}

async function dispatchAll() {
  if (busy.value || !insideWeCom() || !plan.value?.can_confirm) return;
  busy.value = true;
  progress.value = "正在核对最新任务及已有待办…";
  try {
    await load();
    if (!plan.value?.can_confirm) return;
    const tasks = [...readyTasks.value];
    for (let i = 0; i < tasks.length; i++) {
      if (disposed) return;
      const task = tasks[i];
      progress.value = `正在确认第 ${i + 1} / ${tasks.length} 项：${task.content}`;
      const res = await confirmAssignedTodo(task);
      if (disposed) return;
      results.value[task.id] = res;
      if (res.state !== "created" && res.state !== "linked") {
        progress.value = "派发已暂停，已创建的待办保留。可稍后继续确认。";
        return;
      }
    }
    progress.value = tasks.length
      ? "本轮确认已完成，请核对下方每项任务的待办状态。"
      : "没有需要新建的待办。";
  } catch (e: any) {
    progress.value = `待办派发中断：${chineseError(e, "请刷新状态后继续")}`;
  } finally {
    if (!disposed) await load();
    busy.value = false;
  }
}

async function openTaskTodo(task: any) {
  if (task.native_todo?.link?.todo_id && insideWeCom()) {
    await viewWeComTodo(task.native_todo.link.todo_id);
    return;
  }
  void router.push({ path: "/tasks", query: { id: task.id } });
}

onBeforeUnmount(() => {
  disposed = true;
});

onMounted(load);
</script>

<template>
  <div class="m-page-sub" style="padding-bottom: 126px">
    <header class="m-sticky-header-white">
      <div class="m-header-bar">
        <button type="button" class="m-back-btn" @click="router.back()">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
        <h1 class="m-header-title" style="flex: 1">派发本次会议的企微待办</h1>
        <button type="button" class="m-btn m-btn-sm" :disabled="loading || busy" @click="load">
          刷新
        </button>
      </div>
    </header>

    <section class="m-section">
      <div v-if="!insideWeCom()" class="m-alert m-alert-info">
        请在企业微信中打开本页面，再点击下方「确认派发全部待办」逐项确认；当前浏览器可查看关联状态。
      </div>

      <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      <div v-if="loading && !plan" class="m-empty">
        <van-loading size="22">正在读取待办状态…</van-loading>
      </div>

      <template v-else-if="plan">
        <div class="m-card-blue-soft">
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
            <div style="font-size: 15px; font-weight: 700">
              {{ meetingTitle || "已发布会议" }}
            </div>
            <StatusTag :value="`已发布 · V${plan.published_version}`" tone="green" />
          </div>
          <div class="m-muted tabular" style="margin-top: 6px; color: #1e40af">
            待确认 {{ readyTasks.length }} 项 · 已关联 {{ linkedCount }} 项
            <template v-if="pendingCount"> · 待核实 {{ pendingCount }} 项</template>
          </div>
        </div>

        <div v-if="plan.reason" class="m-alert m-alert-warning">{{ plan.reason }}</div>
        <div v-if="progress" class="m-alert m-alert-info">{{ progress }}</div>

        <article
          v-for="task in plan.tasks"
          :key="task.id"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 6px"
        >
          <div style="display: flex; justify-content: space-between; gap: 8px; align-items: flex-start">
            <div class="m-work-text" style="font-size: 14px; font-weight: 700; flex: 1">
              {{ task.content }}
            </div>
            <StatusTag :value="stateLabel(task)" />
          </div>
          <div class="m-muted tabular">
            责任人 {{ (task.owner_names || []).join("、") || "—" }} · 计划完成
            {{ task.due_date || "未设期限" }}
          </div>
          <div v-if="results[task.id]?.message" class="m-muted">
            {{ results[task.id].message }}
          </div>
          <div v-else-if="task.native_todo?.reason" class="m-muted">
            {{ task.native_todo.reason }}
          </div>
          <div>
            <button type="button" class="m-link-btn" @click="openTaskTodo(task)">
              查看 / 处理待办
            </button>
          </div>
        </article>

        <article
          v-for="(task, idx) in plan.excluded_tasks || []"
          :key="task.id || `ex-${idx}`"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 6px"
        >
          <div style="display: flex; justify-content: space-between; gap: 8px">
            <div class="m-work-text" style="font-size: 14px; font-weight: 600; flex: 1">
              {{ task.content }}
            </div>
            <StatusTag value="未派发" tone="gray" />
          </div>
          <div v-if="task.owner_names?.length" class="m-muted">
            责任人 {{ task.owner_names.join("、") }}
          </div>
          <div class="m-muted">{{ task.reason }}</div>
        </article>

        <div
          v-if="!plan.tasks?.length && !plan.excluded_tasks?.length"
          class="m-empty"
        >
          本次会议没有需要派发的任务
        </div>
      </template>
    </section>

    <div class="m-sticky-footer" style="flex-direction: column; gap: 8px">
      <button
        type="button"
        class="m-btn m-btn-primary"
        style="width: 100%"
        :disabled="
          loading ||
          busy ||
          !plan?.can_confirm ||
          !insideWeCom() ||
          !readyTasks.length
        "
        @click="dispatchAll"
      >
        {{ busy ? "正在逐项确认…" : "确认派发全部待办" }}
      </button>
      <button
        type="button"
        class="m-btn"
        style="width: 100%"
        @click="router.push(`/meetings/${meetingId}`)"
      >
        完成，返回会议
      </button>
    </div>
  </div>
</template>
