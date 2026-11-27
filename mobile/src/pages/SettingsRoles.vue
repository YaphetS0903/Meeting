<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { showConfirmDialog, showSuccessToast } from "vant";
import { api, departmentPath, run, state } from "../api/client";
import { chineseError } from "../api/errorMessages";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const roles = ref<any[]>([]);
const catalog = ref<any[]>([]);
const busy = ref(false);
const error = ref("");

const editorOpen = ref(false);
const draft = ref<any>(null);
const saving = ref(false);
const saveError = ref("");

const memberOpen = ref(false);
const memberRole = ref<any>(null);
const members = ref<any[]>([]);
const memberBusy = ref(false);

const scopeNames: Record<string, string> = {
  self: "本人相关",
  department: "本部门及下属组织",
  selected: "指定部门及下属组织",
  all: "全公司",
};

const departments = computed(() =>
  (state.directory.departments || []).map((d: any) => ({
    id: d.id,
    name: departmentPath(d.id),
  })),
);

async function load() {
  if (!state.me?.is_admin) return;
  busy.value = true;
  error.value = "";
  try {
    [roles.value, catalog.value] = await Promise.all([
      api("/admin/roles"),
      api("/admin/role-catalog", "GET", undefined, { background: true }),
    ]);
  } catch (e: any) {
    error.value = chineseError(e, "角色读取失败");
  } finally {
    busy.value = false;
  }
}

function roleSummary(role: any) {
  return (
    (role.rules || [])
      .map(
        (r: any) =>
          `${catalog.value.find((m: any) => m.key === r.module)?.name || r.module} · ${scopeNames[r.scope] || r.scope}`,
      )
      .join(" / ") || "未选择权限"
  );
}

function openEdit(role?: any) {
  saveError.value = "";
  draft.value = {
    id: role?.id,
    name: role?.name || "",
    description: role?.description || "",
    active: role?.active ?? true,
    expected_version: role?.version ?? null,
    member_count: role?.member_count ?? 0,
    rules: catalog.value.map((m: any) => {
      const existing = (role?.rules || []).find((r: any) => r.module === m.key);
      return {
        module: m.key,
        actions: [...(existing?.actions || [])],
        scope: existing?.scope || "department",
        department_ids: [...(existing?.department_ids || [])],
      };
    }),
  };
  editorOpen.value = true;
}

function toggleAction(rule: any, actionKey: string) {
  const idx = rule.actions.indexOf(actionKey);
  if (idx >= 0) rule.actions.splice(idx, 1);
  else rule.actions.push(actionKey);
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

async function saveRole() {
  if (!draft.value || saving.value) return;
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
    const saved = await api(
      id ? `/admin/roles/${id}` : "/admin/roles",
      id ? "PUT" : "POST",
      {
        name: draft.value.name.trim(),
        description: draft.value.description,
        active: draft.value.active,
        expected_version: draft.value.expected_version,
        rules,
      },
    );
    roles.value = id
      ? roles.value.map((r) => (r.id === id ? saved : r))
      : [...roles.value, saved];
    editorOpen.value = false;
    showSuccessToast("角色已保存");
  } catch (e: any) {
    saveError.value = chineseError(e, "保存失败");
  } finally {
    saving.value = false;
  }
}

async function deleteRole() {
  if (!draft.value?.id) return;
  await showConfirmDialog({
    title: "删除角色",
    message: `确定删除「${draft.value.name}」吗？该角色已分配给 ${draft.value.member_count} 人，删除后不可恢复。`,
  });
  await api(`/admin/roles/${draft.value.id}`, "DELETE", {
    expected_version: draft.value.expected_version,
    expected_member_count: draft.value.member_count,
  });
  roles.value = roles.value.filter((r) => r.id !== draft.value.id);
  editorOpen.value = false;
  showSuccessToast("角色已删除");
}

async function openMembers(role: any) {
  memberRole.value = role;
  members.value = [];
  memberOpen.value = true;
  memberBusy.value = true;
  try {
    members.value = await api(`/admin/roles/${role.id}/members`);
  } finally {
    memberBusy.value = false;
  }
}

onMounted(() => run(load));
</script>

<template>
  <div class="m-page">
    <header class="m-sticky-header-white">
      <div class="m-header-bar">
        <button type="button" class="m-back-btn" @click="router.push('/settings')">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <h1 class="m-header-title" style="flex: 1">角色管理</h1>
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-primary"
          :disabled="busy"
          @click="openEdit()"
        >
          ＋ 新建角色
        </button>
      </div>
    </header>

    <section class="m-section" style="padding-top: 12px">
      <div v-if="error" class="m-alert m-alert-error">{{ error }}</div>
      <div v-if="busy && !roles.length" class="m-empty">
        <van-loading size="22">读取角色配置…</van-loading>
      </div>

      <template v-else-if="roles.length">
        <div class="m-section-heading"><h2>权限与范围</h2></div>
        <article
          v-for="role in roles"
          :key="role.id"
          class="m-card"
          style="display: flex; flex-direction: column; gap: 8px"
        >
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
            <span style="font-size: 15px; font-weight: 700">{{ role.name }}</span>
            <StatusTag
              :value="role.active ? '启用' : '停用'"
              :tone="role.active ? 'green' : 'gray'"
            />
          </div>
          <div class="m-muted" style="line-height: 1.5">
            {{ roleSummary(role) }}
          </div>
          <div
            style="
              display: flex;
              justify-content: space-between;
              align-items: center;
              padding-top: 6px;
              border-top: 1px solid #eef2f7;
            "
          >
            <button
              type="button"
              class="m-link-btn"
              style="color: var(--m-muted); font-weight: 500"
              @click="run(() => openMembers(role))"
            >
              已分配 {{ role.member_count ?? 0 }} 人
            </button>
            <button
              type="button"
              class="m-link-btn"
              @click="openEdit(role)"
            >
              编辑
            </button>
          </div>
        </article>
      </template>

      <div v-else class="m-empty">暂无自定义角色</div>
    </section>

    <!-- Role Members Sheet -->
    <van-popup v-model:show="memberOpen" position="bottom" round :style="{ maxHeight: '72vh' }">
      <div style="padding: 16px; display: flex; flex-direction: column; gap: 10px">
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-size: 15px; font-weight: 700">
            {{ memberRole?.name }} · 已分配员工
          </span>
          <button type="button" style="border: none; background: transparent; color: var(--m-muted)" @click="memberOpen = false">关闭</button>
        </div>
        <div v-if="memberBusy" class="m-empty"><van-loading size="20" /></div>
        <div v-else-if="members.length" style="display: flex; flex-direction: column; gap: 8px; max-height: 55vh; overflow-y: auto">
          <div v-for="m in members" :key="m.id" class="m-card" style="padding: 10px 12px; display: flex; justify-content: space-between">
            <div>
              <div style="font-weight: 600">{{ m.name }}</div>
              <div class="m-muted">{{ departmentPath(m.department_id) || "未设置" }}</div>
            </div>
            <StatusTag :value="m.active ? '在用' : '已停用'" :tone="m.active ? 'green' : 'gray'" />
          </div>
        </div>
        <div v-else class="m-empty" style="padding: 20px">尚未分配员工</div>
      </div>
    </van-popup>

    <!-- Role Editor Sheet -->
    <van-popup
      v-model:show="editorOpen"
      position="bottom"
      round
      :style="{ height: '88vh', display: 'flex', flexDirection: 'column' }"
    >
      <div v-if="draft" style="display: flex; flex-direction: column; height: 100%; background: var(--m-canvas)">
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; background: var(--m-surface); border-bottom: 1px solid var(--m-line)">
          <span style="font-size: 16px; font-weight: 700">{{ draft.id ? "编辑角色" : "新建角色" }}</span>
          <button type="button" style="border: none; background: transparent; color: var(--m-muted)" @click="editorOpen = false">取消</button>
        </div>
        <div style="flex: 1; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 12px">
          <div class="m-card" style="display: flex; flex-direction: column; gap: 10px">
            <div>
              <label class="m-label m-label-req">角色名称</label>
              <input v-model="draft.name" class="m-input" placeholder="例如：部门任务管理员" />
            </div>
            <div>
              <label class="m-label">角色说明</label>
              <textarea v-model="draft.description" class="m-textarea" rows="2" />
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-size: 13px; font-weight: 600">启用角色</span>
              <button type="button" class="m-switch" :class="{ on: draft.active }" @click="draft.active = !draft.active" />
            </div>
          </div>

          <div
            v-for="(mod, idx) in catalog"
            :key="mod.key"
            class="m-card"
            style="display: flex; flex-direction: column; gap: 10px"
          >
            <div style="font-size: 14px; font-weight: 700">{{ mod.name }}</div>
            <div style="display: flex; flex-wrap: wrap; gap: 8px">
              <button
                v-for="act in mod.actions"
                :key="act.key"
                type="button"
                class="m-chip m-chip-soft"
                :class="{ active: draft.rules[idx].actions.includes(act.key) }"
                @click="toggleAction(draft.rules[idx], act.key)"
              >
                {{ act.name }}
              </button>
            </div>
            <div v-if="draft.rules[idx].actions.length">
              <label class="m-label">数据范围</label>
              <select v-model="draft.rules[idx].scope" class="m-select">
                <option v-for="sc in mod.scopes" :key="sc" :value="sc">
                  {{ scopeNames[sc] || sc }}
                </option>
              </select>
              <select
                v-if="draft.rules[idx].scope === 'selected'"
                v-model="draft.rules[idx].department_ids"
                multiple
                class="m-select"
                style="margin-top: 8px; min-height: 90px"
              >
                <option v-for="d in departments" :key="d.id" :value="d.id">
                  {{ d.name }}
                </option>
              </select>
            </div>
          </div>

          <div v-if="saveError" class="m-alert m-alert-error">{{ saveError }}</div>
          <button
            v-if="draft.id"
            type="button"
            class="m-btn m-btn-danger-soft"
            style="width: 100%"
            @click="run(deleteRole)"
          >
            删除此角色
          </button>
        </div>

        <div style="padding: 10px 16px calc(12px + env(safe-area-inset-bottom)); background: var(--m-surface); border-top: 1px solid var(--m-line)">
          <button
            type="button"
            class="m-btn m-btn-primary"
            style="width: 100%"
            :disabled="saving"
            @click="run(saveRole)"
          >
            {{ saving ? "保存中…" : "保存角色配置" }}
          </button>
        </div>
      </div>
    </van-popup>
  </div>
</template>
