<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, state, departmentPath } from "../api";
import { reportDepartmentOptions } from "../reportDepartments";

const props = defineProps<{ person: any }>();
const emit = defineEmits<{ close: []; saved: [grant: any] }>();
const selectedDepartments = ref<string[]>([]);
const expectedDepartments = ref<string[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const loaded = ref(false);
const companyOptions = computed(() => {
  const departments = state.directory.departments;
  const ids = new Set(departments.map((d: any) => d.id));
  return departments
    .filter((d: any) => !d.parent_id || !ids.has(d.parent_id))
    .map((d: any) => ({ id: d.id, label: d.name + "（全部部门）" }));
});
const options = computed(() => {
  const result = [
    ...companyOptions.value,
    ...reportDepartmentOptions(state.directory.departments).filter(
      (d) => !companyOptions.value.some((c: any) => c.id === d.id),
    ),
  ];
  for (const id of expectedDepartments.value)
    if (!result.some((d) => d.id === id))
      result.push({ id, label: departmentPath(id) });
  return result;
});
const changed = computed(
  () =>
    [...selectedDepartments.value].sort().join() !==
    [...expectedDepartments.value].sort().join(),
);
const canGrant = computed(() => props.person.active && !props.person.is_admin);
const canSave = computed(
  () =>
    loaded.value &&
    changed.value &&
    !loading.value &&
    !saving.value &&
    (canGrant.value || !selectedDepartments.value.length),
);
async function load() {
  loading.value = true;
  error.value = "";
  loaded.value = false;
  try {
    const grants = await api("/admin/report-access");
    const ids =
      grants.find((g: any) => g.user_id === props.person.id)?.department_ids ||
      [];
    selectedDepartments.value = [...ids];
    expectedDepartments.value = [...ids];
    loaded.value = true;
  } catch (e: any) {
    error.value = e.message || "报表授权读取失败，请重试";
  } finally {
    loading.value = false;
  }
}
async function save() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    const saved = await api(
      `/admin/users/${props.person.id}/report-access`,
      "PUT",
      {
        department_ids: selectedDepartments.value,
        expected_department_ids: expectedDepartments.value,
        reason: selectedDepartments.value.length
          ? "系统管理员在员工通讯录中调整报表可查看部门"
          : "系统管理员在员工通讯录中撤销报表管理员授权",
      },
    );
    emit("saved", saved);
    ElMessage.success(
      saved.department_ids.length ? "报表权限已保存" : "报表管理员授权已撤销",
    );
    emit("close");
  } catch (e: any) {
    error.value = e.message || "保存失败，请重试";
  } finally {
    saving.value = false;
  }
}
defineExpose({ save, canSave, saving });
onMounted(load);
</script>

<template>
  <div class="report-grant" v-loading="loading">
    <p>可查看、导出所选部门及下属组织的常规统计和中高层会议统计。</p>
    <label class="grant-field">
      <span>可查看部门</span>
      <el-select
        v-model="selectedDepartments"
        multiple
        collapse-tags
        collapse-tags-tooltip
        :max-collapse-tags="3"
        filterable
        clearable
        :disabled="loading || saving || !loaded || !canGrant"
        aria-label="授权报表部门"
        placeholder="选择一个或多个部门"
      >
        <el-option
          v-for="d in options"
          :key="d.id"
          :value="d.id"
          :label="d.label"
        />
      </el-select>
    </label>
    <div class="grant-actions">
      <el-button
        link
        type="primary"
        :disabled="
          loading || saving || !loaded || !canGrant || !companyOptions.length
        "
        @click="selectedDepartments = companyOptions.map((d: any) => d.id)"
        >选择全部部门</el-button
      >
      <el-button
        link
        :disabled="loading || saving || !loaded || !selectedDepartments.length"
        @click="selectedDepartments = []"
        >清空部门</el-button
      >
    </div>
    <p class="muted small">
      清空后保存可撤销此项授权；“部门报表查看员”角色独立保留。
    </p>
    <p v-if="!person.active" class="muted small">
      该员工已停用，仅可清空并撤销已有授权。
    </p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
    >
      <el-button size="small" :disabled="saving || loading" @click="load"
        >重新加载授权</el-button
      >
    </el-alert>
  </div>
</template>

<style scoped>
.report-grant {
  display: grid;
  gap: 16px;
}
.report-grant p {
  margin: 0;
  line-height: 1.6;
}
.grant-field {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.grant-field :deep(.el-select) {
  width: 100%;
}
.grant-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
}
.grant-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>
