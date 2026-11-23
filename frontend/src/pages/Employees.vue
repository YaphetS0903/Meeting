<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, state, run, loadSession, departmentPath } from "../api";
import { isEmployeeAccount } from "../people";

defineProps<{ embedded?: boolean }>();
const router = useRouter();
const search = ref("");
const refreshing = ref(false);
const grants = ref<any[]>([]);
const roleOptions = ref<any[]>([]);
const rolesLoading = ref(false);
const rolesLoaded = ref(false);
const rolesError = ref("");
const selectedEmployee = ref<any>(null);
const selectedRoles = ref<string[]>([]);
const expectedRoles = ref<string[]>([]);
const roleDialog = ref(false);
const savingRoles = ref(false);
const saveError = ref("");
const roleIds = (person: any): string[] =>
  grants.value.find((g) => g.user_id === person.id)?.roles || [];
const roleNames = (person: any) =>
  roleOptions.value
    .filter((r) => roleIds(person).includes(r.id))
    .map((r) => r.name + (r.active ? "" : "（已停用）"));
const rolesChanged = computed(
  () =>
    [...selectedRoles.value].sort().join() !==
    [...expectedRoles.value].sort().join(),
);
async function loadRoles() {
  rolesLoading.value = true;
  rolesLoaded.value = false;
  rolesError.value = "";
  try {
    const [roles, assignments] = await Promise.all([
      api("/admin/roles", "GET", undefined, { background: true }),
      api("/admin/role-assignments", "GET", undefined, { background: true }),
    ]);
    roleOptions.value = roles;
    grants.value = assignments;
    rolesLoaded.value = true;
  } catch (error: any) {
    rolesError.value = error.message || "员工角色读取失败，请重试";
  } finally {
    rolesLoading.value = false;
  }
}
async function editRoles(person: any) {
  await loadRoles();
  if (!rolesLoaded.value) return;
  selectedEmployee.value = { ...person };
  selectedRoles.value = [...roleIds(person)];
  expectedRoles.value = [...selectedRoles.value];
  saveError.value = "";
  roleDialog.value = true;
}
async function saveRoles() {
  if (savingRoles.value || !rolesChanged.value) return;
  savingRoles.value = true;
  saveError.value = "";
  try {
    const saved = await api(
      `/admin/users/${selectedEmployee.value.id}/business-roles`,
      "PUT",
      {
        roles: selectedRoles.value,
        expected_roles: expectedRoles.value,
        expected_department_id: selectedEmployee.value.department_id || null,
      },
    );
    grants.value = [
      ...grants.value.filter((g) => g.user_id !== saved.user_id),
      saved,
    ];
    roleDialog.value = false;
    ElMessage.success(`${selectedEmployee.value.name}的角色已更新`);
  } catch (error: any) {
    saveError.value = error.message || "角色保存失败，请重试";
  } finally {
    savingRoles.value = false;
  }
}
function searchText(value: unknown) {
  return String(value ?? "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/\s+/g, "");
}
const users = computed(() => {
  const priority = (person: any) =>
    !person.active ? 3 : person.is_admin ? 0 : roleIds(person).length ? 1 : 2;
  const query = searchText(search.value);
  return state.directory.users
    .filter(isEmployeeAccount)
    .filter((person) =>
      [
        person.name,
        person.email,
        person.wecom_id,
        person.mobile,
        person.department_id ? departmentPath(person.department_id) : "",
      ].some((value) => searchText(value).includes(query)),
    )
    .sort((a, b) => priority(a) - priority(b));
});
async function refresh() {
  refreshing.value = true;
  try {
    await loadSession();
    await loadRoles();
  } finally {
    refreshing.value = false;
  }
}
async function changeRole(person: any) {
  const { value } = await ElMessageBox.prompt(
    `将${person.name}设为${person.is_admin ? "普通成员" : "管理员"}，请填写变更原因`,
    "调整系统角色",
    { inputValidator: (value) => !!value?.trim() || "请填写变更原因" },
  );
  await api(`/admin/users/${person.id}/role`, "PUT", {
    is_admin: !person.is_admin,
    expected_is_admin: person.is_admin,
    reason: value,
  });
  await loadSession();
  ElMessage.success("角色已更新");
  if (!state.me?.is_admin) await router.replace("/meetings");
}
onMounted(() => {
  if (state.me?.is_admin) void loadRoles();
});
</script>

<template>
  <template v-if="state.me?.is_admin">
    <section v-if="!embedded" class="page-heading">
      <div>
        <h1>员工通讯录</h1>
      </div>
      <div class="actions">
        <el-button @click="router.push('/settings')">系统管理</el-button>
        <el-button :loading="refreshing" @click="run(refresh)">刷新</el-button>
      </div>
    </section>
    <section
      class="panel section-body directory-employees"
      aria-label="员工列表"
    >
      <template v-if="embedded">
        <div class="directory-heading">
          <h2>员工通讯录</h2>
          <el-button :loading="refreshing" @click="run(refresh)"
            >刷新通讯录</el-button
          >
        </div>
      </template>
      <label class="directory-search">
        <span>搜索员工</span>
        <el-input
          v-model="search"
          clearable
          placeholder="输入姓名、部门、邮箱、手机号或成员标识"
          aria-label="搜索员工通讯录"
        />
      </label>
      <el-alert
        v-if="rolesError"
        :title="rolesError"
        type="error"
        show-icon
        :closable="false"
      >
        <el-button size="small" @click="loadRoles">重新加载角色</el-button>
      </el-alert>
      <el-table
        :data="users"
        row-key="id"
        :max-height="640"
        :empty-text="
          state.directory.users.length
            ? '没有匹配的员工，请调整搜索内容'
            : '暂无员工信息，请返回系统管理同步通讯录'
        "
      >
        <el-table-column type="expand" width="44">
          <template #default="{ row }">
            <dl
              class="employee-contact-details"
              :aria-label="`${row.name}的联系资料`"
            >
              <dt>企业微信成员标识</dt>
              <dd>{{ row.wecom_id || "—" }}</dd>
              <dt>邮箱</dt>
              <dd>{{ row.email || "—" }}</dd>
              <dt>手机号</dt>
              <dd>{{ row.mobile || "—" }}</dd>
              <dt>岗位</dt>
              <dd>{{ row.position || "—" }}</dd>
            </dl>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column label="主部门" min-width="180">
          <template #default="{ row }">{{
            row.department_id ? departmentPath(row.department_id) : "未分配"
          }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">{{
            row.active ? "在用" : "已停用"
          }}</template>
        </el-table-column>
        <el-table-column label="系统角色" min-width="195">
          <template #default="{ row }">
            <span v-if="row.is_admin">系统管理员</span>
            <span v-else-if="!rolesLoaded">{{
              rolesLoading ? "加载中…" : "角色未加载"
            }}</span>
            <div v-else class="employee-role-tags">
              <el-tag v-for="name in roleNames(row)" :key="name" size="small">{{
                name
              }}</el-tag>
              <span v-if="!roleIds(row).length">普通成员</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色管理" width="185" fixed="right">
          <template #default="{ row }">
            <div class="employee-role-actions">
              <el-button
                link
                type="primary"
                :disabled="
                  rolesLoading ||
                  !rolesLoaded ||
                  ((!row.active || row.is_admin) && !roleIds(row).length)
                "
                @click="run(() => editRoles(row))"
                >设置角色</el-button
              >
              <el-button
                link
                type="primary"
                :disabled="!row.active && !row.is_admin"
                @click="run(() => changeRole(row))"
                >{{ row.is_admin ? "设为普通成员" : "设为管理员" }}</el-button
              >
            </div>
          </template>
        </el-table-column>
      </el-table>
    </section>
    <el-dialog
      v-model="roleDialog"
      :title="`${selectedEmployee?.name || ''} · 设置角色`"
      width="min(580px, 94vw)"
      append-to-body
      :close-on-click-modal="!savingRoles"
      :close-on-press-escape="!savingRoles"
      :show-close="!savingRoles"
    >
      <p class="role-department">
        请选择该员工需要的角色。多个角色的权限叠加，各功能仍按对应范围生效。
      </p>
      <el-checkbox-group
        v-model="selectedRoles"
        class="role-choices"
        aria-label="员工角色"
      >
        <div v-for="role in roleOptions" :key="role.id" class="role-choice">
          <el-checkbox
            :value="role.id"
            :disabled="
              savingRoles ||
              ((!selectedEmployee?.active ||
                selectedEmployee?.is_admin ||
                !role.active) &&
                !expectedRoles.includes(role.id))
            "
            >{{ role.name }}{{ role.active ? "" : "（已停用）" }}</el-checkbox
          >
          <span>{{ role.description || "权限与数据范围由角色管理配置" }}</span>
        </div>
      </el-checkbox-group>
      <el-empty
        v-if="!roleOptions.length"
        description="暂无角色，请先到角色管理中新建"
      />
      <p class="muted small">
        取消勾选后保存可撤销角色。本人主持、编制会议及任务责任人的职责权限独立保留。
      </p>
      <el-alert
        v-if="saveError"
        :title="saveError"
        type="error"
        :closable="false"
        show-icon
      />
      <template #footer>
        <el-button :disabled="savingRoles" @click="roleDialog = false"
          >取消</el-button
        >
        <el-button
          type="primary"
          :loading="savingRoles"
          :disabled="!rolesChanged || savingRoles"
          @click="saveRoles"
          >保存角色</el-button
        >
      </template>
    </el-dialog>
  </template>
  <el-result v-else icon="warning" title="仅系统管理员可查看员工通讯录">
    <template #extra
      ><el-button @click="router.push('/meetings')"
        >返回会议列表</el-button
      ></template
    >
  </el-result>
</template>

<style scoped>
.directory-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}
.directory-heading h2 {
  margin: 0;
}
.directory-search {
  display: grid;
  gap: 8px;
  max-width: 560px;
  margin-bottom: 20px;
}
.directory-search > span {
  font-size: 14px;
  font-weight: 500;
}
.employee-role-tags,
.employee-role-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
}
.employee-contact-details {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 10px 16px;
  margin: 0;
  padding: 12px 24px;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.employee-contact-details dt {
  color: #526580;
}
.employee-contact-details dd {
  margin: 0;
}
.employee-role-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.employee-access-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}
.role-department {
  margin: 0 0 18px;
  color: var(--ink);
  line-height: 1.6;
}
.role-choices {
  display: grid;
  gap: 12px;
  margin-bottom: 18px;
}
.role-choice {
  display: grid;
  gap: 6px;
  font-size: 14px;
  padding: 14px;
  border: 1px solid var(--line, #dce6f1);
  border-radius: 8px;
}
.role-choice > span {
  color: #52627a;
  line-height: 1.6;
}
.role-choice :deep(.el-checkbox) {
  height: auto;
  white-space: normal;
}
.role-choice :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.5;
}
</style>
