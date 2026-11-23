<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Upload } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { api, departmentPath, state } from "../api";
import PeopleSelect from "./PeopleSelect.vue";
const props = defineProps<{ modelValue: boolean; seriesId: string }>();
const emit = defineEmits(["update:modelValue", "created"]);
const input = ref<HTMLInputElement>();
const resumableId = ref("");
const source = ref<any>(null),
  busy = ref(false),
  uploading = ref(false),
  creating = ref(false),
  error = ref("");
const selected = ref(0),
  users = ref<any[]>([]),
  departments = ref<any[]>([]);
const form = ref<any>({});
const fixedSeriesKind = computed(() => {
  const kind = state.directory.series.find(
    (series: any) => series.id === props.seriesId,
  )?.kind;
  return ["department", "special"].includes(kind) ? kind : "";
});
function diagnosticMessage(notice: any) {
  return /strm=0x0006|strm=6/.test(notice.message || "")
    ? "已跳过旧版 Excel 内部模块，不影响会议任务识别"
    : notice.message;
}
const candidate = computed(() => source.value?.candidates[selected.value]);
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
  () => [props.modelValue, props.seriesId] as const,
  async ([open]) => {
    stopPolling();
    if (!open) return;
    const current = generation;
    source.value = null;
    error.value = "";
    selected.value = 0;
    users.value = state.directory.users;
    departments.value = state.directory.departments;
    try {
      const context = await api("/meeting-people");
      if (current !== generation) return;
      users.value = context.users || users.value;
      departments.value = context.departments || departments.value;
    } catch {
      /* The session directory remains available. */
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
  error.value = value.error || "";
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
    error.value = `${e.message}；可点击“更新识别状态”，或重新选择文件。`;
  }
}
function updateStatus() {
  const id = source.value?.id || resumableId.value;
  if (id) refreshSource(id);
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
      error.value = e.message;
      busy.value = false;
    }
  }
}
function selectCandidate(index: number) {
  selected.value = index;
  const p = source.value.candidates[index].payload;
  form.value = {
    candidate_index: index,
    title: p.title,
    meeting_date: p.meeting_date,
    department_id: fixedSeriesKind.value === "special" ? null : p.department_id,
    kind:
      fixedSeriesKind.value ||
      (["department", "special"].includes(p.kind) ? p.kind : ""),
    host_id: p.host_id,
    recorder_id: p.recorder_id || state.me.id,
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
    error.value = "一次选择一个 Excel／PDF，或最多 20 张同一会议的照片";
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
    // Save the queued job even if the dialog was closed during upload.
    sessionStorage.setItem(key, value.id);
    receive(value, current);
  } catch (e: any) {
    if (current === generation) error.value = e.message;
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
async function create() {
  if (!canCreate.value || creating.value) return;
  creating.value = true;
  error.value = "";
  try {
    const result = await api(
      `/meeting-sheet-imports/${source.value.id}/draft`,
      "POST",
      form.value,
    );
    ElMessage.success(
      result.duplicate
        ? "该任务单已导入，已打开已有会议"
        : "已生成会议草稿，请核对后手动发布",
    );
    emit("update:modelValue", false);
    sessionStorage.removeItem(storageKey());
    emit("created", result.id);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    creating.value = false;
  }
}
</script>
<template>
  <el-dialog
    :model-value="modelValue"
    title="导入任务单"
    width="640px"
    top="16px"
    append-to-body
    class="sheet-import-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!creating && !uploading"
    :show-close="!creating && !uploading"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="sheet-intro">
      上传 Excel、PDF
      或照片，识别会议基本信息、上期任务及本次任务，生成可编辑草稿。核对后再手动发布。
    </p>
    <input
      ref="input"
      type="file"
      accept=".xls,.xlsx,.pdf,.png,.jpg,.jpeg"
      multiple
      class="sheet-file-input"
      aria-label="选择任务单文件"
      @change="recognize"
    />
    <el-button
      :icon="Upload"
      :loading="busy"
      :disabled="creating"
      @click="input?.click()"
      >{{ source ? "重新选择文件" : "选择任务单文件" }}</el-button
    >
    <span v-if="source" class="sheet-filename">{{
      (source.filenames || [source.filename]).join("、")
    }}</span>
    <p class="sheet-intro sheet-file-help">
      单个 Excel／PDF，或同一会议的多张照片；合计不超过 30 MB，PDF 最多 20 页。
    </p>
    <p v-if="busy" role="status" class="sheet-intro">
      正在识别任务单，请稍候。可以先关闭，重新打开后查看识别结果。
    </p>
    <p v-if="error" class="sheet-error" role="alert">{{ error }}</p>
    <el-button
      v-if="source?.status === 'failed'"
      :disabled="busy"
      @click="retryRecognition"
      >重新识别</el-button
    >
    <el-button
      v-else-if="error && !candidate && resumableId"
      :disabled="busy"
      @click="updateStatus"
      >更新识别状态</el-button
    >
    <template v-if="candidate">
      <el-form label-position="top" class="sheet-import-form">
        <el-form-item
          v-if="source.candidates.length > 1"
          label="选择要导入的会议工作表"
        >
          <el-select
            :model-value="selected"
            aria-label="会议工作表"
            @change="selectCandidate"
          >
            <el-option
              v-for="(c, i) in source.candidates"
              :key="i"
              :value="Number(i)"
              :label="`${c.sheet} · ${c.payload.meeting_date || '日期待确认'}`"
            />
          </el-select>
        </el-form-item>
        <div class="sheet-counts">
          上期任务 {{ form.backfill_all_new ? 0 : candidate.review_count }} 条 ·
          本次任务
          {{
            form.backfill_all_new
              ? (candidate.payload.items?.length ??
                candidate.review_count + candidate.new_count)
              : candidate.new_count
          }}
          条<span v-if="candidate.issues.length">
            · {{ candidate.issues.length }} 项待核对</span
          >
        </div>
        <p
          v-if="!candidate.usable && !form.backfill_all_new"
          class="sheet-error"
        >
          有任务无法确定分区。可在历史补录中确认全部为本次新安排，或明确分区标题后重新上传。
        </p>
        <div class="sheet-backfill-options">
          <el-checkbox
            v-model="form.history_backfill"
            @change="
              (value) => {
                if (!value) form.backfill_all_new = false;
              }
            "
            >补录历史会议</el-checkbox
          >
          <p v-if="form.history_backfill" class="sheet-intro">
            仅保存历史，不派发、不通知；生成草稿后可关联后续已发布任务。
          </p>
          <el-checkbox
            v-if="form.history_backfill"
            v-model="form.backfill_all_new"
            >全部作为本次新安排</el-checkbox
          >
        </div>
        <el-form-item label="会议名称" required
          ><el-input
            v-model="form.title"
            maxlength="500"
            aria-label="导入会议名称"
        /></el-form-item>
        <div class="sheet-form-grid">
          <el-form-item label="召开日期" required
            ><el-date-picker
              v-model="form.meeting_date"
              type="date"
              value-format="YYYY-MM-DD"
              aria-label="导入召开日期"
          /></el-form-item>
          <el-form-item label="会议类型" required
            ><el-select
              v-model="form.kind"
              :disabled="!!fixedSeriesKind"
              aria-label="导入会议类型"
              ><el-option label="部门例会" value="department" /><el-option
                label="专项会议"
                value="special" /></el-select
          ></el-form-item>
          <el-form-item label="所属部门" :required="form.kind === 'department'"
            ><el-select
              v-model="form.department_id"
              filterable
              clearable
              aria-label="导入所属部门"
              ><el-option
                v-for="d in departments"
                :key="d.id"
                :value="d.id"
                :label="d.path || d.name || departmentPath(d.id)" /></el-select
          ></el-form-item>
          <el-form-item label="主持人" required
            ><PeopleSelect
              v-model="form.host_id"
              :users="users"
              search-only
              copyable
              aria-label="导入主持人"
              placeholder="输入主持人姓名"
          /></el-form-item>
          <el-form-item v-if="state.me?.is_admin" label="编制人"
            ><PeopleSelect
              v-model="form.recorder_id"
              :users="users"
              search-only
              copyable
              aria-label="导入编制人"
          /></el-form-item>
        </div>
      </el-form>
      <details v-if="source.diagnostics.length" class="sheet-file-notes">
        <summary>文件处理说明</summary>
        <p v-for="(notice, i) in source.diagnostics" :key="i">
          {{ notice.sheet ? notice.sheet + "：" : ""
          }}{{ diagnosticMessage(notice) }}
        </p>
      </details>
      <p class="sheet-intro">
        姓名未匹配、特殊完成结果和日期冲突，会保留原文并在草稿中提示核对。上期任务不会额外重复加入本次任务。
      </p>
    </template>
    <template #footer
      ><el-button
        :disabled="creating || uploading"
        @click="emit('update:modelValue', false)"
        >{{ busy ? "先关闭" : "取消" }}</el-button
      ><el-button
        type="primary"
        :disabled="!canCreate || busy"
        :loading="creating"
        @click="create"
        >生成会议草稿</el-button
      ></template
    >
  </el-dialog>
</template>
<style>
.sheet-import-dialog {
  max-width: calc(100vw - 24px);
}
.sheet-import-dialog .el-dialog__body {
  max-height: 70vh;
  overflow-y: auto;
}
.sheet-intro {
  color: #526378;
  line-height: 1.7;
  margin: 0 0 16px;
}
.sheet-file-help {
  margin-top: 12px;
  font-size: 13px;
}
.sheet-file-input {
  display: none;
}
.sheet-filename {
  display: inline-block;
  margin: 8px 0 8px 12px;
  overflow-wrap: anywhere;
}
.sheet-error {
  color: #b42318;
  margin: 12px 0;
  line-height: 1.6;
}
.sheet-import-form {
  margin-top: 20px;
}
.sheet-counts {
  margin-bottom: 20px;
  font-weight: 600;
  color: #24364d;
}
.sheet-backfill-options {
  margin-bottom: 16px;
}
.sheet-backfill-options .sheet-intro {
  margin: 4px 0;
  font-size: 13px;
}
.sheet-form-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0 20px;
}
.sheet-import-form .el-select,
.sheet-import-form .el-date-editor {
  width: 100%;
}
.sheet-file-notes {
  margin-bottom: 16px;
  line-height: 1.6;
}
.sheet-file-notes summary {
  cursor: pointer;
}
@media (max-width: 560px) {
  .sheet-form-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .sheet-filename {
    margin-left: 0;
    display: block;
  }
}
</style>
