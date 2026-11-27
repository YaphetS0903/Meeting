<script setup lang="ts">
import { computed, ref } from "vue";
import { departmentPath } from "../api";
import { reportClientError } from "../errorMessages";
import PeopleSelect from "./PeopleSelect.vue";

const props = defineProps<{
  draft: any;
  users: any[];
  departments: any[];
  seriesName: string;
  seriesLocked: boolean;
}>();
const emit = defineEmits<{
  confirm: [];
  cancel: [];
  kindChange: [kind: string];
}>();

const checked = ref(false);
const errors = computed(() => ({
  title: props.draft.title.trim() ? "" : "请填写会议名称",
  date: props.draft.meeting_date ? "" : "请选择会议日期",
  host: props.draft.host_id ? "" : "请选择主持人",
  department:
    props.draft.kind !== "department" || props.draft.department_id
      ? ""
      : "请选择所属部门",
}));
const onsite = computed(() =>
  props.draft.attendee_ids.filter(
    (id: string) => !props.draft.online_attendee_ids.includes(id),
  ),
);
const attendeeCount = computed(
  () =>
    new Set([props.draft.host_id, ...props.draft.attendee_ids].filter(Boolean))
      .size,
);
function updateAttendees(mode: "onsite" | "online", selected: string[]) {
  const other: string[] =
    mode === "onsite" ? props.draft.online_attendee_ids : onsite.value;
  const remaining = other.filter((id) => !selected.includes(id));
  const online = mode === "online" ? selected : remaining;
  const physical = mode === "onsite" ? selected : remaining;
  props.draft.attendee_ids = [...new Set([...physical, ...online])].filter(
    (id) => id !== props.draft.host_id,
  );
  props.draft.online_attendee_ids = online;
}
function confirm() {
  checked.value = true;
  const missing = Object.keys(errors.value).find(
    (key) => errors.value[key as keyof typeof errors.value],
  );
  if (missing) {
    reportClientError(`setup_${missing}`);
    const input = document.querySelector<HTMLElement>(
      `[id="new-meeting-${missing}"], .new-meeting-dialog .is-error input`,
    );
    input?.focus();
    return;
  }
  props.draft.title = props.draft.title.trim();
  emit("confirm");
}
</script>

<template>
  <el-dialog
    :model-value="true"
    title="新建会议"
    class="new-meeting-dialog"
    width="min(720px, 94vw)"
    align-center
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="(open: boolean) => !open && emit('cancel')"
  >
    <el-form label-position="top" @submit.prevent="confirm">
      <p class="setup-intro">填写会议信息并核对参会名单，然后进入任务单。</p>
      <div v-if="seriesName" class="setup-series">
        会议系列 · {{ seriesName }}
      </div>
      <div class="setup-grid">
        <el-form-item
          class="setup-wide"
          label="会议名称"
          required
          :error="checked ? errors.title : ''"
        >
          <el-input
            id="new-meeting-title"
            aria-label="会议名称"
            v-model="draft.title"
            maxlength="500"
            placeholder="请输入会议名称"
            @keydown.enter.prevent="confirm"
          />
        </el-form-item>
        <el-form-item
          label="会议日期"
          required
          :error="checked ? errors.date : ''"
        >
          <el-date-picker
            id="new-meeting-date"
            aria-label="会议日期"
            v-model="draft.meeting_date"
            type="date"
            value-format="YYYY-MM-DD"
          />
        </el-form-item>
        <el-form-item label="会议时间（选填）">
          <el-input v-model="draft.meeting_time" placeholder="如 09:00–10:00" />
        </el-form-item>
        <el-form-item label="会议类型">
          <el-select
            aria-label="会议类型"
            v-model="draft.kind"
            :disabled="seriesLocked"
            @change="emit('kindChange', $event)"
          >
            <el-option label="部门例会" value="department" />
            <el-option label="专项会议" value="special" />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="draft.kind === 'department'"
          label="所属部门"
          required
          :error="checked ? errors.department : ''"
        >
          <el-select
            id="new-meeting-department"
            aria-label="会议所属部门"
            v-model="draft.department_id"
            filterable
            placeholder="请选择所属部门"
          >
            <el-option
              v-for="department in departments"
              :key="department.id"
              :label="departmentPath(department.id, departments)"
              :value="department.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="会议地点（选填）" class="setup-wide">
          <el-input v-model="draft.location" placeholder="请输入会议地点" />
        </el-form-item>
        <el-form-item
          label="主持人"
          required
          :error="checked ? errors.host : ''"
        >
          <PeopleSelect
            v-model="draft.host_id"
            :users="users"
            aria-label="主持人"
          />
        </el-form-item>
        <el-form-item label="编制人（选填）">
          <PeopleSelect
            v-model="draft.recorder_id"
            :users="users"
            aria-label="编制人"
          />
        </el-form-item>
      </div>
      <div class="setup-attendees">
        <h3>
          参会人员 <span>共 {{ attendeeCount }} 人</span>
        </h3>
        <p class="setup-intro">主持人自动计入参会人员，请补充其他参会人员。</p>
        <div class="setup-grid">
          <el-form-item label="现场参会人员">
            <PeopleSelect
              :model-value="onsite"
              :users="users"
              multiple
              :collapse-tags="false"
              open-above
              aria-label="现场参会人员"
              @update:model-value="updateAttendees('onsite', $event)"
            />
          </el-form-item>
          <el-form-item label="线上参会人员">
            <PeopleSelect
              :model-value="draft.online_attendee_ids"
              :users="users"
              multiple
              :collapse-tags="false"
              open-above
              aria-label="线上参会人员"
              @update:model-value="updateAttendees('online', $event)"
            />
          </el-form-item>
          <el-form-item label="缺席人员及原因（选填）" class="setup-wide">
            <el-input v-model="draft.absentees" type="textarea" :rows="2" />
          </el-form-item>
        </div>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="emit('cancel')">取消</el-button>
      <el-button type="primary" @click="confirm">确认并填写任务单</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.setup-intro,
.setup-series {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
}
.setup-series {
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.setup-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
.setup-grid :deep(.el-date-editor),
.setup-grid :deep(.el-select) {
  width: 100%;
}
.setup-wide {
  grid-column: 1 / -1;
}
.setup-attendees {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 12px;
}
.setup-attendees h3 {
  margin: 0 0 6px;
  font-size: 15px;
  color: var(--el-text-color-primary);
}
.setup-attendees h3 span {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  font-weight: normal;
}
@media (max-width: 520px) {
  .setup-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
<style>
.new-meeting-dialog .el-dialog__body {
  max-height: min(72dvh, 680px);
  overflow: auto;
}
</style>
