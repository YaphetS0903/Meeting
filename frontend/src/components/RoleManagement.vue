<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, state, departmentPath } from "../api";

const roles = ref<any[]>([]),
  catalog = ref<any[]>([]);
const loading = ref(false),
  saving = ref(false),
  opened = ref(false);
const error = ref(""),
  saveError = ref("");
const draft = ref<any>(null);
const deleting = ref<string | null>(null);
const members = ref<any[] | null>(null),
  memberRole = ref<any>(null),
  membersLoading = ref(false),
  membersError = ref("");
async function showMembers(role: any) {
  memberRole.value = role;
  members.value = null;
  membersError.value = "";
  membersLoading.value = true;
  try {
    members.value = await api(`/admin/roles/${role.id}/members`);
  } catch (e: any) {
    membersError.value = e.message || "成员读取失败";
  } finally {
    membersLoading.value = false;
  }
}
const scopeNames: Record<string, string> = {
  self: "本人相关",
  department: "本部门及下属组织",
  selected: "指定部门及下属组织",
  all: "全公司",
};
const departments = computed(() =>
  state.directory.departments.map((d: any) => ({
    id: d.id,
    name: departmentPath(d.id),
  })),
);
async function load() {
  loading.value = true;
  error.value = "";
  try {
    [roles.value, catalog.value] = await Promise.all([
      api("/admin/roles"),
      api("/admin/role-catalog", "GET", undefined, { background: true }),
    ]);
  } catch (e: any) {
    error.value = e.message || "角色读取失败，请重试";
  } finally {
    loading.value = false;
  }
}
function edit(role?: any) {
  saveError.value = "";
  draft.value = {
    id: role?.id,
    name: role?.name || "",
    description: role?.description || "",
    active: role?.active ?? true,
    expected_version: role?.version,
    rules: catalog.value.map((m: any) => {
      const existing = role?.rules.find((r: any) => r.module === m.key);
      return {
        module: m.key,
        actions: [...(existing?.actions || [])],
        scope: existing?.scope || "department",
        department_ids: [...(existing?.department_ids || [])],
      };
    }),
  };
  opened.value = true;
}
function actionsChanged(rule: any) {
  if (
    rule.module === "meetings" &&
    rule.actions.includes("assess") &&
    !rule.actions.includes("edit")
  )
    rule.actions.push("edit");
  if (
    rule.module === "meetings" &&
    rule.actions.includes("publish") &&
    !rule.actions.includes("preview")
  )
    rule.actions.push("preview");
}
function summary(role: any) {
  return (
    role.rules
      .map(
        (r: any) =>
          `${catalog.value.find((m: any) => m.key === r.module)?.name || r.module}（${scopeNames[r.scope]}）`,
      )
      .join("、") || "未选择权限"
  );
}
async function removeRole(role: any) {
  if (deleting.value) return;
  deleting.value = role.id;
  try {
    try {
      await ElMessageBox.confirm(
        `确定删除“${role.name}”吗？该角色已分配给 ${role.member_count} 人。删除后会撤销这些员工通过该角色获得的权限，员工账号、其他角色及本人会议／任务职责保留。此操作不可撤销。`,
        "删除角色",
        {
          confirmButtonText: "确认删除",
          cancelButtonText: "取消",
          type: "warning",
        },
      );
    } catch {
      return;
    }
    await api(`/admin/roles/${role.id}`, "DELETE", {
      expected_version: role.version,
      expected_member_count: role.member_count,
    });
    roles.value = roles.value.filter((r: any) => r.id !== role.id);
    if (memberRole.value?.id === role.id) memberRole.value = null;
    ElMessage.success("角色已删除，相关角色分配已撤销");
  } catch (e: any) {
    ElMessage.error(e.message || "删除失败，请重试");
    await load();
  } finally {
    deleting.value = null;
  }
}
async function save() {
  if (saving.value) return;
  if (!draft.value.name.trim()) {
    saveError.value = "请输入角色名称";
    return;
  }
  const rules = draft.value.rules.filter((r: any) => r.actions.length);
  if (
    rules.some((r: any) => r.scope === "selected" && !r.department_ids.length)
  ) {
    saveError.value = "请为指定部门范围选择至少一个部门";
    return;
  }
  saving.value = true;
  saveError.value = "";
  try {
    const id = draft.value.id;
    const result = await api(
      id ? `/admin/roles/${id}` : "/admin/roles",
      id ? "PUT" : "POST",
      {
        name: draft.value.name.trim(),
        description: draft.value.description,
        active: draft.value.active,
        expected_version: draft.value.expected_version ?? null,
        rules,
      },
    );
    roles.value = id
      ? roles.value.map((r: any) => (r.id === id ? result : r))
      : [...roles.value, result];
    opened.value = false;
    ElMessage.success("角色已保存，已分配员工的权限同步生效");
  } catch (e: any) {
    saveError.value = e.message || "保存失败，请重试";
  } finally {
    saving.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="panel section-body role-management" v-loading="loading">
    <div class="role-management-heading">
      <div>
        <h2>角色管理</h2>
        <p class="muted">
          先创建角色并勾选功能，再到员工通讯录分配。各模块的数据范围独立设置。
        </p>
      </div>
      <el-button
        type="primary"
        :disabled="loading || !!error || !!deleting"
        @click="edit()"
        >新建角色</el-button
      >
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
      ><el-button @click="load">重新加载角色</el-button></el-alert
    >
    <el-table
      :data="roles"
      row-key="id"
      empty-text="暂无角色，点击“新建角色”开始配置"
    >
      <el-table-column prop="name" label="角色名称" min-width="190" />
      <el-table-column label="权限与范围" min-width="300"
        ><template #default="{ row }"
          ><span class="role-summary">{{ summary(row) }}</span></template
        ></el-table-column
      >
      <el-table-column label="已分配人数" width="110"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :aria-label="`查看${row.name}的成员`"
            @click="showMembers(row)"
            >{{ row.member_count }} 人</el-button
          ></template
        ></el-table-column
      >
      <el-table-column label="状态" width="85"
        ><template #default="{ row }"
          ><el-tag :type="row.active ? 'success' : 'info'">{{
            row.active ? "启用" : "停用"
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="操作" width="140" fixed="right"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :aria-label="`编辑${row.name}`"
            :disabled="!!deleting"
            @click="edit(row)"
            >编辑</el-button
          ><el-button
            link
            type="danger"
            :aria-label="`删除${row.name}`"
            :disabled="!!deleting"
            @click="removeRole(row)"
            >删除</el-button
          ></template
        ></el-table-column
      >
    </el-table>
  </section>
  <el-dialog
    :model-value="!!memberRole"
    :title="`${memberRole?.name || ''} · 已分配员工`"
    width="min(640px,94vw)"
    append-to-body
    @close="memberRole = null"
  >
    <div v-loading="membersLoading">
      <el-alert
        v-if="membersError"
        :title="membersError"
        type="error"
        :closable="false"
        ><el-button @click="showMembers(memberRole)">重试</el-button></el-alert
      >
      <el-table
        v-else
        :data="members || []"
        empty-text="尚未分配员工"
        max-height="480"
      >
        <el-table-column prop="name" label="姓名" min-width="100" />
        <el-table-column label="所属部门" min-width="170"
          ><template #default="{ row }">{{
            departmentPath(row.department_id) || "未设置"
          }}</template></el-table-column
        >
        <el-table-column label="状态" width="80"
          ><template #default="{ row }">{{
            row.active ? "在用" : "已停用"
          }}</template></el-table-column
        >
      </el-table>
    </div>
  </el-dialog>
  <el-drawer
    v-model="opened"
    :title="draft?.id ? '编辑角色' : '新建角色'"
    size="min(860px, 100vw)"
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    append-to-body
  >
    <el-form
      v-if="draft"
      label-position="top"
      class="role-editor"
      @submit.prevent="save"
    >
      <el-form-item label="角色名称" required
        ><el-input
          v-model="draft.name"
          maxlength="120"
          aria-label="角色名称"
          :disabled="saving"
          placeholder="例如：采购部会议管理"
      /></el-form-item>
      <el-form-item label="角色说明"
        ><el-input
          v-model="draft.description"
          type="textarea"
          :rows="2"
          maxlength="2000"
          aria-label="角色说明"
          :disabled="saving"
      /></el-form-item>
      <el-checkbox v-model="draft.active" :disabled="saving"
        >启用角色</el-checkbox
      >
      <p class="muted">
        停用后，已分配员工保留角色记录，权限立即停止生效。查看权限不会开放编辑或发布。
      </p>
      <section
        v-for="(module, index) in catalog"
        :key="module.key"
        class="permission-module"
        :aria-label="module.name"
      >
        <h3>{{ module.name }}</h3>
        <el-checkbox-group
          v-model="draft.rules[index].actions"
          :aria-label="`${module.name}权限`"
          :disabled="saving"
          @change="actionsChanged(draft.rules[index])"
        >
          <el-checkbox
            v-for="action in module.actions"
            :key="action.key"
            :value="action.key"
            >{{ action.name }}</el-checkbox
          >
        </el-checkbox-group>
        <div v-if="draft.rules[index].actions.length" class="permission-scope">
          <label
            ><span>数据范围</span
            ><el-select
              v-model="draft.rules[index].scope"
              :aria-label="`${module.name}数据范围`"
              :disabled="saving"
              ><el-option
                v-for="scope in module.scopes"
                :key="scope"
                :value="scope"
                :label="scopeNames[scope]" /></el-select
          ></label>
          <el-select
            v-if="draft.rules[index].scope === 'selected'"
            v-model="draft.rules[index].department_ids"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            :max-collapse-tags="2"
            :aria-label="`${module.name}指定部门`"
            placeholder="选择一个或多个部门"
            :disabled="saving"
            ><el-option
              v-for="d in departments"
              :key="d.id"
              :value="d.id"
              :label="d.name"
          /></el-select>
        </div>
        <p v-if="module.key === 'dispatch'" class="muted small">
          数据范围控制接收人；派发记录的查看、修改、转交、撤回仅限本人派发的任务。
        </p>
        <p v-if="module.key === 'meetings'" class="muted small">
          修改完成结果同时启用编辑；发布同时启用预览。已发布会议仅系统管理员可删除。
        </p>
        <p v-if="module.key === 'files'" class="muted small">
          下载、复制需能查看该会议；上传及移除附件还需会议编辑权限。
        </p>
      </section>
      <el-alert
        v-if="saveError"
        :title="saveError"
        type="error"
        :closable="false"
        show-icon
      />
    </el-form>
    <template #footer
      ><div class="role-editor-footer">
        <el-button :disabled="saving" @click="opened = false">取消</el-button
        ><el-button type="primary" :loading="saving" @click="save"
          >保存角色配置</el-button
        >
      </div></template
    >
  </el-drawer>
</template>

<style scoped>
.role-management-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.role-management-heading h2 {
  margin: 0 0 8px;
}
.role-management-heading p {
  margin: 0;
  line-height: 1.6;
}
.role-summary {
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.role-editor {
  max-width: 800px;
}
.permission-module {
  padding: 20px 0;
  border-top: 1px solid var(--line);
}
.permission-module h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
.permission-module :deep(.el-checkbox-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 22px;
}
.permission-module :deep(.el-checkbox) {
  margin: 0;
  height: auto;
  min-height: 28px;
  white-space: normal;
}
.permission-module :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.5;
}
.permission-scope {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}
.permission-scope label {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  max-width: 400px;
}
.permission-scope :deep(.el-select) {
  width: 100%;
}
.role-editor-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.role-editor-footer :deep(.el-button + .el-button) {
  margin-left: 0;
}
@media (max-width: 600px) {
  .role-management-heading {
    flex-direction: column;
  }
  .permission-module {
    padding: 18px 0;
  }
}
</style>
