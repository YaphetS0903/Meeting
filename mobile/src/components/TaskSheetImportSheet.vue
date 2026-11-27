<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { showSuccessToast } from "vant";
import { api, departmentPath, state } from "../api/client";
import { chineseError } from "../api/errorMessages";
import PeoplePicker from "./PeoplePicker.vue";
import StatusTag from "./StatusTag.vue";

const props = defineProps<{ show: boolean; seriesId: string }>();
const emit = defineEmits<{
  "update:show": [value: boolean];
  created: [id: string];
}>();

const input = ref<HTMLInputElement>();
const resumableId = ref("");
const source = ref<any>(null);
const busy = ref(false);
const uploading = ref(false);
const creating = ref(false);
const error = ref("");
const selected = ref(0);
const users = ref<any[]>([]);
const departments = ref<any[]>([]);
const form = ref<any>({});

const fixedSeriesKind = computed(() => {
  const kind = (state.directory.series || []).find(
    (s: any) => s.id === props.seriesId,
  )?.kind;
  return ["department", "special"].includes(kind) ? kind : "";
});

const candidate = computed(() => source.value?.candidates?.[selected.value]);

const stage = computed(() => {
  if (creating.value) return 3;
  if (candidate.value) return 2;
  return 1;
});

const canCreate = computed(
  () =>
    candidate.value &&
    (candidate.value?.usable ||
      (form.value.history_backfill && form.value.backfill_all_new)) &&
    form.value.title?.trim() &&
    form.value.meeting_date &&
    form.value.host_id &&
    form.value.kind &&
    (form.value.kind === "special" || form.value.department_id),
);

let generation = 0;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
const storageKey = () => `sheet-import:${state.me?.id}:${props.seriesId}`;

function stopPolling() {
  generation++;
  clearTimeout(pollTimer);
  busy.value = false;
}

onBeforeUnmount(stopPolling);

watch(
  () => [props.show, props.seriesId] as const,
  async ([open]) => {
    stopPolling();
    if (!open || !props.seriesId) return;
    const current = generation;
    source.value = null;
    error.value = "";
    selected.value = 0;
    users.value = state.directory.users || [];
    departments.value = state.directory.departments || [];
    try {
      const ctx = await api("/meeting-people");
      if (current !== generation) return;
      users.value = ctx.users || users.value;
      departments.value = ctx.departments || departments.value;
    } catch {
      /* session directory fallback */
    }
    if (current !== generation) return;
    const saved = sessionStorage.getItem(storageKey());
    resumableId.value = saved || "";
    if (saved) await refreshSource(saved, current);
  },
);

function receive(value: any, current: number) {
  if (current !== generation) return;
  source.value = value;
  resumableId.value = value.id;
  sessionStorage.setItem(storageKey(), value.id);
  error.value = value.error
    ? chineseError(value.error, "无法识别任务单，请检查原文件后重试")
    : "";
  busy.value = ["pending", "processing"].includes(value.status);
  if (busy.value) {
    pollTimer = setTimeout(() => refreshSource(value.id, current), 2000);
  } else if (value.candidates?.length) {
    selectCandidate(0);
  }
}

async function refreshSource(id: string, current = generation) {
  try {
    const value = await api(`/meeting-sheet-imports/${id}`, "GET", undefined, {
      background: true,
    });
    receive(value, current);
  } catch (e: any) {
    if (current !== generation) return;
    busy.value = false;
    error.value = `${chineseError(e)}；可点击“更新识别状态”，或重新选择文件。`;
  }
}

async function retryRecognition() {
  const current = generation;
  busy.value = true;
  error.value = "";
  try {
    receive(
      await api(`/meeting-sheet-imports/${source.value.id}/retry`, "POST"),
      current,
    );
  } catch (e: any) {
    if (current === generation) {
      error.value = chineseError(e);
      busy.value = false;
    }
  }
}

function selectCandidate(index: number) {
  selected.value = index;
  const p = source.value.candidates[index].payload;
  form.value = {
    candidate_index: index,
    title: p.title || "",
    meeting_date: p.meeting_date || "",
    department_id: fixedSeriesKind.value === "special" ? null : p.department_id,
    kind:
      fixedSeriesKind.value ||
      (["department", "special"].includes(p.kind) ? p.kind : ""),
    host_id: p.host_id || "",
    recorder_id: p.recorder_id || state.me?.id || "",
    history_backfill: false,
    backfill_all_new: false,
  };
}

async function recognize(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || []);
  if (!files.length) return;
  error.value = "";
  if (files.some((f) => !/\.(xlsx|xls|pdf|png|jpe?g)$/i.test(f.name))) {
    error.value = "请选择 Excel、PDF、PNG 或 JPG 文件";
    return;
  }
  if (
    files.length > 20 ||
    (files.length > 1 && files.some((f) => !/\.(png|jpe?g)$/i.test(f.name)))
  ) {
    error.value = "一次选择一个 Excel / PDF，或最多 20 张同一会议的照片";
    return;
  }
  if (
    files.some((f) => !f.size) ||
    files.reduce((n, f) => n + f.size, 0) > 30 * 1024 * 1024
  ) {
    error.value = "文件不能为空，合计大小不超过 30 MB";
    return;
  }
  stopPolling();
  const current = generation;
  const key = storageKey();
  busy.value = true;
  uploading.value = true;
  source.value = null;
  resumableId.value = "";
  sessionStorage.removeItem(key);
  try {
    const body = new FormData();
    files.forEach((file) => body.append("file", file));
    const value = await api(
      `/series/${props.seriesId}/task-sheet-imports`,
      "POST",
      body,
    );
    sessionStorage.setItem(key, value.id);
    receive(value, current);
  } catch (e: any) {
    if (current === generation) error.value = chineseError(e);
  } finally {
    uploading.value = false;
    if (
      current === generation &&
      !["pending", "processing"].includes(source.value?.status)
    )
      busy.value = false;
    if (input.value) input.value.value = "";
  }
}

async function createDraft() {
  if (!canCreate.value || creating.value) return;
  creating.value = true;
  error.value = "";
  try {
    const result = await api(
      `/meeting-sheet-imports/${source.value.id}/draft`,
      "POST",
      form.value,
    );
    showSuccessToast(
      result.duplicate
        ? "该任务单已导入，已打开已有会议"
        : "已生成会议草稿，请核对后发布",
    );
    sessionStorage.removeItem(storageKey());
    emit("update:show", false);
    emit("created", result.id);
  } catch (e: any) {
    error.value = chineseError(e);
  } finally {
    creating.value = false;
  }
}
</script>

<template>
  <van-popup
    :show="show"
    position="bottom"
    round
    :style="{ height: '90vh', display: 'flex', flexDirection: 'column' }"
    @update:show="emit('update:show', $event)"
  >
    <div class="imp-wrap">
      <div class="imp-head">
        <span style="font-size: 16px; font-weight: 700">导入任务单</span>
        <button
          type="button"
          style="border: none; background: transparent; color: var(--m-muted); font-size: 14px"
          :disabled="creating || uploading"
          @click="emit('update:show', false)"
        >
          关闭
        </button>
      </div>

      <!-- 3-Stage Compact Progress Indicator -->
      <div class="imp-steps">
        <div class="imp-step" :class="{ active: stage >= 1, current: stage === 1 }">
          <span>1</span>上传文件
        </div>
        <div class="imp-step-line" />
        <div class="imp-step" :class="{ active: stage >= 2, current: stage === 2 }">
          <span>2</span>核对识别
        </div>
        <div class="imp-step-line" />
        <div class="imp-step" :class="{ active: stage >= 3, current: stage === 3 }">
          <span>3</span>生成草稿
        </div>
      </div>

      <div class="imp-body">
        <input
          ref="input"
          type="file"
          accept=".xls,.xlsx,.pdf,.png,.jpg,.jpeg"
          multiple
          style="display: none"
          @change="recognize"
        />

        <!-- Source File Strip -->
        <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
            <div style="min-width: 0; flex: 1">
              <div style="font-size: 14px; font-weight: 600; word-break: break-all">
                {{
                  source
                    ? (source.filenames || [source.filename]).join("、")
                    : "选择任务单文件"
                }}
              </div>
              <div class="m-muted">Excel / PDF / 照片（合计不超过 30 MB）</div>
            </div>
            <button
              type="button"
              class="m-btn m-btn-sm m-btn-soft"
              :disabled="busy || creating"
              @click="input?.click()"
            >
              {{ source ? "重新选择" : "上传文件" }}
            </button>
          </div>

          <div v-if="busy" class="m-alert m-alert-info">
            <van-loading size="14" style="display: inline-flex; margin-right: 6px" />
            正在识别任务单，请稍候…
          </div>
          <div v-else-if="candidate" style="display: flex; align-items: center; gap: 8px">
            <StatusTag value="识别完成" tone="green" />
            <span class="m-muted">
              上期任务 {{ form.backfill_all_new ? 0 : candidate.review_count }} 条 ·
              本次任务
              {{
                form.backfill_all_new
                  ? candidate.payload?.items?.length ??
                    candidate.review_count + candidate.new_count
                  : candidate.new_count
              }}
              条
            </span>
          </div>

          <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>

          <div v-if="source?.status === 'failed'" style="display: flex; gap: 8px">
            <button
              type="button"
              class="m-btn m-btn-sm m-btn-outline-blue"
              :disabled="busy"
              @click="retryRecognition"
            >
              重新识别
            </button>
          </div>
          <div v-else-if="error && !candidate && resumableId">
            <button
              type="button"
              class="m-btn m-btn-sm m-btn-outline-blue"
              :disabled="busy"
              @click="refreshSource(resumableId)"
            >
              更新识别状态
            </button>
          </div>
        </div>

        <!-- Candidate Verification Form -->
        <div
          v-if="candidate"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 12px"
        >
          <div style="font-size: 15px; font-weight: 700">核对会议信息</div>

          <div v-if="source.candidates.length > 1">
            <label class="m-label">选择会议工作表</label>
            <select
              :value="selected"
              class="m-select"
              @change="selectCandidate(Number(($event.target as HTMLSelectElement).value))"
            >
              <option
                v-for="(c, i) in source.candidates"
                :key="i"
                :value="Number(i)"
              >
                {{ c.sheet }} · {{ c.payload.meeting_date || "日期待确认" }}
              </option>
            </select>
          </div>

          <div
            v-if="!candidate.usable && !form.backfill_all_new"
            class="m-alert m-alert-error"
          >
            有任务无法确定分区。可勾选「补录历史会议」并选择「全部作为本次新安排」，或修正原表后重新上传。
          </div>

          <div style="display: flex; flex-direction: column; gap: 8px">
            <label style="display: flex; align-items: center; gap: 8px; font-size: 13px">
              <input
                v-model="form.history_backfill"
                type="checkbox"
                @change="!form.history_backfill && (form.backfill_all_new = false)"
              />
              补录历史会议（仅保存历史，不派发、不通知）
            </label>
            <label
              v-if="form.history_backfill"
              style="display: flex; align-items: center; gap: 8px; font-size: 13px; padding-left: 20px"
            >
              <input v-model="form.backfill_all_new" type="checkbox" />
              全部作为本次新安排
            </label>
          </div>

          <div>
            <label class="m-label m-label-req">会议名称</label>
            <input v-model="form.title" class="m-input" maxlength="500" />
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <div>
              <label class="m-label m-label-req">召开日期</label>
              <input v-model="form.meeting_date" type="date" class="m-input" />
            </div>
            <div>
              <label class="m-label m-label-req">会议类型</label>
              <select
                v-model="form.kind"
                class="m-select"
                :disabled="Boolean(fixedSeriesKind)"
              >
                <option value="department">部门例会</option>
                <option value="special">专项会议</option>
              </select>
            </div>
          </div>

          <div v-if="form.kind === 'department'">
            <label class="m-label m-label-req">所属部门</label>
            <select v-model="form.department_id" class="m-select">
              <option value="">请选择所属部门</option>
              <option v-for="d in departments" :key="d.id" :value="d.id">
                {{ d.path || d.name || departmentPath(d.id) }}
              </option>
            </select>
          </div>

          <div>
            <label class="m-label m-label-req">主持人</label>
            <PeoplePicker
              v-model="form.host_id"
              :users="users"
              placeholder="选择主持人"
              title="选择主持人"
            />
          </div>

          <div v-if="state.me?.is_admin">
            <label class="m-label">编制人</label>
            <PeoplePicker
              v-model="form.recorder_id"
              :users="users"
              placeholder="选择编制人"
              title="选择编制人"
            />
          </div>
        </div>
      </div>

      <div class="imp-foot">
        <button
          type="button"
          class="m-btn m-btn-primary"
          style="width: 100%"
          :disabled="!canCreate || busy || creating"
          @click="createDraft"
        >
          {{ creating ? "正在生成草稿…" : "生成草稿" }}
        </button>
      </div>
    </div>
  </van-popup>
</template>

<style scoped>
.imp-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--m-canvas);
}
.imp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  background: var(--m-surface);
  border-bottom: 1px solid var(--m-line);
}
.imp-steps {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--m-surface);
  border-bottom: 1px solid var(--m-line);
}
.imp-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--m-muted);
  font-weight: 500;
}
.imp-step span {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #edf1f7;
  color: var(--m-muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
}
.imp-step.active {
  color: var(--m-primary);
  font-weight: 600;
}
.imp-step.active span {
  background: var(--m-primary);
  color: #ffffff;
}
.imp-step-line {
  width: 22px;
  height: 1px;
  background: var(--m-line);
}
.imp-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.imp-foot {
  padding: 10px 16px calc(12px + env(safe-area-inset-bottom));
  background: var(--m-surface);
  border-top: 1px solid var(--m-line);
}
</style>
