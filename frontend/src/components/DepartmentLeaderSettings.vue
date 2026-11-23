<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, departmentPath, run, state } from "../api";
import { reportDepartmentOptions } from "../reportDepartments";
import PeopleSelect from "./PeopleSelect.vue";

type Grant = { user_id: string; department_ids: string[] };
const grants = ref<Grant[]>([]);
const selectedUser = ref<string | null>(null);
const selectedDepartments = ref<string[]>([]);
const departmentSelect = ref();
const expectedDepartments = ref<string[]>([]);
const loading = ref(false),
  saving = ref(false),
  loaded = ref(false),
  error = ref("");
const target = computed(() =>
  state.directory.users.find((p: any) => p.id === selectedUser.value),
);
const candidates = computed(() =>
  state.directory.users.filter(
    (p: any) =>
      (p.active && !p.is_admin) ||
      grants.value.some((grant) => grant.user_id === p.id),
  ),
);
const options = computed(() => {
  const items = reportDepartmentOptions(state.directory.departments);
  for (const id of [...selectedDepartments.value, ...expectedDepartments.value])
    if (!items.some((item) => item.id === id))
      items.push({ id, name: departmentPath(id), label: departmentPath(id) });
  return items;
});
const rows = computed(() =>
  grants.value
    .map((grant) => ({
      ...grant,
      person: state.directory.users.find((p: any) => p.id === grant.user_id),
    }))
    .sort(
      (a, b) =>
        Number(b.person?.active) - Number(a.person?.active) ||
        (a.person?.name || "").localeCompare(b.person?.name || "", "zh-CN"),
    ),
);
const changed = computed(
  () =>
    selectedDepartments.value.length !== expectedDepartments.value.length ||
    selectedDepartments.value.some(
      (id) => !expectedDepartments.value.includes(id),
    ),
);
const canSave = computed(
  () =>
    loaded.value &&
    !!selectedUser.value &&
    changed.value &&
    !loading.value &&
    !saving.value &&
    ((target.value?.active && !target.value?.is_admin) ||
      !selectedDepartments.value.length),
);

function selectUser() {
  const ids =
    grants.value.find((grant) => grant.user_id === selectedUser.value)
      ?.department_ids || [];
  selectedDepartments.value = [...ids];
  expectedDepartments.value = [...ids];
}
function edit(row: Grant) {
  selectedUser.value = row.user_id;
  selectUser();
}
async function load() {
  loading.value = true;
  loaded.value = false;
  error.value = "";
  try {
    grants.value = await api("/admin/department-leaders");
    selectUser();
    loaded.value = true;
  } catch (e: any) {
    error.value = e.message || "授权列表读取失败，请重新加载";
  } finally {
    loading.value = false;
  }
}
async function persist(userId: string, ids: string[], expected: string[]) {
  if (loading.value || saving.value || !loaded.value) return;
  saving.value = true;
  error.value = "";
  try {
    const saved: Grant = await api(
      `/admin/users/${userId}/department-leader`,
      "PUT",
      {
        department_ids: ids,
        expected_department_ids: expected,
        reason: ids.length
          ? "系统管理员调整部门负责人授权范围"
          : "系统管理员撤销手动部门负责人授权",
      },
    );
    grants.value = grants.value.filter(
      (grant) => grant.user_id !== saved.user_id,
    );
    if (saved.department_ids.length) grants.value.push(saved);
    if (selectedUser.value === saved.user_id) selectUser();
    ElMessage.success(
      saved.department_ids.length
        ? "部门负责人授权已保存"
        : "手动部门负责人授权已撤销",
    );
  } catch (e: any) {
    error.value = e.message || "保存失败，请重新加载后重试";
  } finally {
    saving.value = false;
  }
}
async function save() {
  if (canSave.value && selectedUser.value)
    await persist(
      selectedUser.value,
      [...selectedDepartments.value],
      [...expectedDepartments.value],
    );
}
async function revoke(row: Grant & { person?: any }) {
  await ElMessageBox.confirm(
    `确认撤销${row.person?.name || "该员工"}的手动部门负责人授权？`,
    "撤销授权",
    {
      confirmButtonText: "确认撤销",
      cancelButtonText: "取消",
      type: "warning",
    },
  );
  await persist(row.user_id, [], [...row.department_ids]);
}
onMounted(load);
</script>

<template>
  <section v-loading="loading" class="panel department-leader-settings">
    <div class="leader-heading">
      <h2>部门负责人授权</h2>
      <p class="muted">
        授权后，可向负责部门及下属组织派发任务，查看会议系列和员工统计报表。
      </p>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
    >
      <el-button :disabled="saving || loading" @click="load"
        >重新加载授权</el-button
      >
    </el-alert>
    <div class="leader-grant-fields">
      <label>
        <span>员工账号</span>
        <PeopleSelect
          v-model="selectedUser"
          :users="candidates"
          search-only
          include-inactive
          copyable
          :disabled="!loaded || loading || saving"
          aria-label="部门负责人账号"
          placeholder="输入员工姓名"
          @update:model-value="selectUser"
        />
      </label>
      <label>
        <span>负责部门</span>
        <el-select
          ref="departmentSelect"
          v-model="selectedDepartments"
          multiple
          filterable
          clearable
          :disabled="!selectedUser || !loaded || loading || saving"
          aria-label="部门负责人负责部门"
          placeholder="选择一个或多个部门"
          @change="departmentSelect?.blur()"
        >
          <el-option
            v-for="item in options"
            :key="item.id"
            :value="item.id"
            :label="item.label"
          />
        </el-select>
      </label>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!canSave"
        @click="run(save)"
        >保存授权</el-button
      >
    </div>
    <p class="muted small">
      清空负责部门后保存，可撤销手动授权。单独授予的报表权限继续保留；岗位或会议角色不会自动获得部门报表权限。
    </p>
    <div class="leader-heading">
      <h2>
        已手动授权人员 <span class="count">{{ rows.length }}</span>
      </h2>
      <p class="muted small">
        授权变更即时生效，员工刷新页面后可看到对应功能入口。
      </p>
    </div>
    <el-table
      :data="rows"
      max-height="520"
      empty-text="暂无手动授权，请在上方选择员工和负责部门"
    >
      <el-table-column label="姓名" min-width="130">
        <template #default="{ row }">{{
          row.person?.name || "账号已不可用"
        }}</template>
      </el-table-column>
      <el-table-column label="负责部门（含下属组织）" min-width="260">
        <template #default="{ row }">{{
          row.department_ids.map((id: string) => departmentPath(id)).join("、")
        }}</template>
      </el-table-column>
      <el-table-column label="账号状态" width="100">
        <template #default="{ row }"
          ><el-tag :type="row.person?.active ? 'success' : 'info'">{{
            row.person?.active ? "正常" : "已停用"
          }}</el-tag></template
        >
      </el-table-column>
      <el-table-column label="操作" width="190">
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            :disabled="saving || loading || !loaded"
            @click="edit(row)"
            >修改授权</el-button
          >
          <el-button
            link
            type="danger"
            :disabled="saving || loading || !loaded"
            @click="run(() => revoke(row))"
            >撤销授权</el-button
          >
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.department-leader-settings {
  --el-color-primary: #245cba;
  --el-color-danger: #b42332;
  --el-color-success: #26745a;
  --el-color-info: #596579;
  display: grid;
  gap: 20px;
  padding: 24px;
}
.department-leader-settings .muted {
  color: #52627a;
}
.leader-grant-fields
  > .el-button--primary:not(.is-link):not(.is-text):not(.is-plain) {
  background: #245cba;
  border-color: #245cba;
}
.leader-grant-fields
  > .el-button--primary.is-disabled:not(.is-link):not(.is-text):not(.is-plain) {
  color: #65738a;
  background: #eef2f7;
  border-color: #dce3ed;
  box-shadow: none;
  transform: none;
}
.department-leader-settings h2,
.department-leader-settings p {
  margin: 0;
}
.leader-heading {
  display: grid;
  gap: 8px;
}
.leader-grant-fields {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(260px, 2fr) auto;
  align-items: end;
  gap: 16px;
}
.leader-grant-fields label {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.leader-grant-fields :deep(.el-select) {
  width: 100%;
}
@media (max-width: 1000px) {
  .leader-grant-fields {
    grid-template-columns: minmax(0, 1fr);
  }
  .leader-grant-fields > .el-button {
    justify-self: start;
  }
}
@media (max-width: 760px) {
  .department-leader-settings {
    padding: 16px;
  }
}
</style>
