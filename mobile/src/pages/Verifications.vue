<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showImagePreview, showSuccessToast, showToast } from "vant";
import { api, fetchAuthBlob, person, run, state } from "../api/client";
import StatusTag from "../components/StatusTag.vue";

const route = useRoute();
const router = useRouter();
const requested =
  typeof route.query.request === "string" ? route.query.request : null;

const rows = ref<any[]>([]);
const busy = ref(false);
const refreshing = ref(false);
const pendingOnly = ref(true);
const missingRequested = ref(false);

const decisionSheet = ref(false);
const activeRow = ref<any>(null);
const activeDecision = ref<"approved" | "rejected">("approved");
const opinion = ref("");
const submitting = ref(false);

const quickPhrases = computed(() =>
  activeDecision.value === "approved"
    ? ["情况属实，验收通过", "已核对交付凭证，同意关闭", "按期完成，验证通过"]
    : ["请补充正式交付凭证后重新提交", "数据尚未闭环，请继续推进", "与会议要求存在偏差，请整改"],
);

const pendingCount = computed(
  () => rows.value.filter((x) => x.state === "pending").length,
);

const visibleRows = computed(() =>
  rows.value.filter((x) => !pendingOnly.value || x.state === "pending"),
);

const stateLabels: Record<string, string> = {
  pending: "待验证",
  approved: "已通过",
  rejected: "已驳回",
  invalidated: "已失效",
};

async function load(background = false) {
  if (!background) busy.value = true;
  try {
    rows.value = await api("/verifications", "GET", undefined, { background });
    state.pendingVerifications = pendingCount.value;
    if (requested) {
      const target = rows.value.find((r) => r.id === requested);
      missingRequested.value = !target;
      if (target && target.state !== "pending") pendingOnly.value = false;
      await nextTick();
      document
        .getElementById("verification-" + requested)
        ?.scrollIntoView({ block: "center" });
    }
  } finally {
    busy.value = false;
    refreshing.value = false;
  }
}

function openDecision(row: any, decision: "approved" | "rejected") {
  activeRow.value = row;
  activeDecision.value = decision;
  opinion.value = "";
  decisionSheet.value = true;
}

async function submitDecision() {
  if (!activeRow.value) return;
  if (!opinion.value.trim()) {
    showToast("请填写验证意见");
    return;
  }
  submitting.value = true;
  try {
    await api("/verifications/" + activeRow.value.id, "POST", {
      decision: activeDecision.value,
      opinion: opinion.value.trim(),
    });
    decisionSheet.value = false;
    showSuccessToast(
      activeDecision.value === "approved"
        ? "已验证通过并关闭任务"
        : "已驳回完成申请",
    );
    await load(true);
  } finally {
    submitting.value = false;
  }
}

async function openEvidence(id: string) {
  const blob = await fetchAuthBlob("/files/" + id);
  if (blob.type.startsWith("image/")) {
    const url = URL.createObjectURL(blob);
    showImagePreview({
      images: [url],
      closeable: true,
      onClose: () => URL.revokeObjectURL(url),
    });
  } else {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "完成证据";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
}

onMounted(() => run(load));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <h1 class="m-header-title-lg">待我验证</h1>
        <StatusTag
          v-if="pendingCount"
          :value="`${pendingCount} 项待处理`"
          tone="amber"
        />
      </div>
      <div class="v-filter-bar">
        <span style="font-size: 13px; color: var(--m-text); font-weight: 500">
          只看待验证
        </span>
        <button
          type="button"
          role="switch"
          :aria-checked="pendingOnly"
          class="m-switch"
          :class="{ on: pendingOnly }"
          @click="pendingOnly = !pendingOnly"
        />
      </div>
    </header>

    <van-pull-refresh v-model="refreshing" @refresh="run(() => load(false))">
      <section class="m-section">
        <div v-if="missingRequested" class="m-alert m-alert-info">
          消息中的申请当前不在您的验证范围，请查看当前待验证清单或联系会议负责人。
        </div>

        <div v-if="busy && !rows.length" class="m-empty">
          <van-loading size="22">正在加载验证申请…</van-loading>
        </div>

        <template v-else-if="visibleRows.length">
          <article
            v-for="r in visibleRows"
            :id="'verification-' + r.id"
            :key="r.id"
            class="m-card v-card"
          >
            <div class="v-head">
              <h2 class="v-title m-work-text">
                {{ r.task?.content || r.task_code }}
              </h2>
              <StatusTag :value="stateLabels[r.state] || r.state" />
            </div>

            <div class="v-meta-grid">
              <div>
                <span class="m-muted">申请人</span>
                <span>{{ r.applicant_name || "—" }}</span>
              </div>
              <div class="tabular">
                <span class="m-muted">截止</span>
                <span>{{ r.task?.due_date || "未设期限" }}</span>
              </div>
              <div>
                <span class="m-muted">来源会议</span>
                <a
                  v-if="r.source_meeting_id"
                  href="javascript:void(0)"
                  style="color: var(--m-primary); text-decoration: none"
                  @click="router.push('/meetings/' + r.source_meeting_id)"
                >
                  {{ r.source_meeting_title }}
                </a>
                <span v-else>{{ r.source_meeting_title || "直接派发" }}</span>
              </div>
              <div class="tabular">
                <span class="m-muted">任务版本</span>
                <span>V{{ r.task_version }}</span>
              </div>
            </div>

            <div
              v-if="r.state === 'pending' && !r.can_verify"
              class="m-alert m-alert-warning"
            >
              任务已变化，此申请不能继续验证，请刷新或联系会议负责人。
            </div>

            <div class="v-note-box">
              <div class="m-muted" style="margin-bottom: 3px">完成说明</div>
              <div class="m-work-text" style="font-size: 13px; color: var(--m-ink)">
                {{ r.explanation || "—" }}
              </div>
            </div>

            <div v-if="r.evidence_ids?.length" class="v-evidences">
              <button
                v-for="(fid, idx) in r.evidence_ids"
                :key="fid"
                type="button"
                class="v-attach-strip"
                @click="run(() => openEvidence(fid))"
              >
                <span style="display: inline-flex; align-items: center; gap: 6px; min-width: 0">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                  完成证据 {{ Number(idx) + 1 }}
                </span>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" />
                  <circle cx="12" cy="12" r="2.6" />
                </svg>
              </button>
            </div>

            <div v-if="r.opinion" class="m-alert m-alert-info">
              验证意见：{{ r.opinion }}
            </div>

            <div v-if="r.can_verify" class="v-actions">
              <button
                type="button"
                class="m-btn"
                style="flex: 1"
                @click="openDecision(r, 'rejected')"
              >
                驳回
              </button>
              <button
                type="button"
                class="m-btn m-btn-primary"
                style="flex: 1.5"
                @click="openDecision(r, 'approved')"
              >
                验证通过并关闭
              </button>
            </div>
          </article>
        </template>

        <div v-else class="m-empty">没有待处理的验证申请</div>
      </section>
    </van-pull-refresh>

    <van-popup v-model:show="decisionSheet" position="bottom" round>
      <div class="v-sheet">
        <div class="v-grab" />
        <div class="v-sheet-head">
          <div>
            <div style="font-size: 17px; font-weight: 700">
              {{ activeDecision === "approved" ? "验证通过" : "驳回完成申请" }}
            </div>
            <div v-if="activeRow" class="m-muted" style="margin-top: 2px">
              {{ activeRow.task?.content || activeRow.task_code }}
            </div>
          </div>
        </div>

        <div style="padding: 12px 16px; display: flex; flex-direction: column; gap: 10px">
          <div
            class="m-alert"
            :class="
              activeDecision === 'approved' ? 'm-alert-info' : 'm-alert-error'
            "
          >
            {{
              activeDecision === "approved"
                ? "通过后将关闭所有共同责任人的待办。"
                : "驳回后任务将继续执行，责任人可补充材料后重新提交。"
            }}
          </div>

          <div class="m-chip-row">
            <button
              v-for="phrase in quickPhrases"
              :key="phrase"
              type="button"
              class="m-chip"
              @click="opinion = phrase"
            >
              {{ phrase }}
            </button>
          </div>

          <div>
            <label class="m-label m-label-req">验证意见</label>
            <textarea
              v-model="opinion"
              class="m-textarea"
              rows="3"
              placeholder="请填写验收意见或驳回原因…"
            />
          </div>
        </div>

        <div
          style="
            padding: 10px 16px calc(12px + env(safe-area-inset-bottom));
            border-top: 1px solid var(--m-line);
            display: flex;
            gap: 10px;
          "
        >
          <button
            type="button"
            class="m-btn"
            style="flex: 1"
            @click="decisionSheet = false"
          >
            取消
          </button>
          <button
            type="button"
            class="m-btn"
            :class="
              activeDecision === 'approved' ? 'm-btn-primary' : 'm-btn-danger'
            "
            style="flex: 1.5"
            :disabled="submitting"
            @click="run(submitDecision)"
          >
            {{
              submitting
                ? "正在保存…"
                : activeDecision === "approved"
                  ? "确认通过"
                  : "确认驳回"
            }}
          </button>
        </div>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.v-filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 16px 12px;
}
.v-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.v-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.v-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
  flex: 1;
}
.v-meta-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 10px;
  font-size: 12px;
}
.v-meta-grid > div {
  display: flex;
  gap: 6px;
  align-items: baseline;
  min-width: 0;
}
.v-note-box {
  background: #f5f7fb;
  border-radius: 10px;
  padding: 10px 12px;
}
.v-evidences {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.v-attach-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #d8e6ff;
  background: var(--m-primary-soft);
  color: var(--m-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
}
.v-actions {
  display: flex;
  gap: 10px;
  padding-top: 4px;
}
.v-grab {
  width: 36px;
  height: 4px;
  border-radius: 999px;
  background: #d4dce8;
  margin: 8px auto 0;
}
.v-sheet-head {
  padding: 12px 16px 8px;
}
</style>
