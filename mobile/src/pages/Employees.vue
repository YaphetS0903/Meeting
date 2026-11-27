<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { showSuccessToast } from "vant";
import { api, departmentPath, run, state } from "../api/client";
import StatusTag from "../components/StatusTag.vue";

const router = useRouter();
const q = ref("");
const visibleCount = ref(30);
const roleOptions = ref<any[]>([]);
const grants = ref<any[]>([]);
const expandedId = ref("");

const roleSheetOpen = ref(false);
const activePerson = ref<any>(null);
const selectedRoles = ref<string[]>([]);
const expectedRoles = ref<string[]>([]);
const saving = ref(false);

const roleIds = (u: any): string[] =>
  grants.value.find((g) => g.user_id === u.id)?.roles || [];

const roleNames = (u: any) =>
  roleOptions.value
    .filter((r) => roleIds(u).includes(r.id))
    .map((r) => r.name);

const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase();
  const list = state.directory.users || [];
  if (!kw) return list;
  return list.filter((u: any) => {
    const dept = u.department_id ? departmentPath(u.department_id) : "";
    return `${u.name || ""} ${dept} ${u.email || ""} ${u.mobile || ""} ${u.position || ""}`
      .toLowerCase()
      .includes(kw);
  });
});

async function loadRoles() {
  if (!state.me?.is_admin) return;
  const [roles, assignments] = await Promise.all([
    api("/admin/roles", "GET", undefined, { background: true }),
    api("/admin/role-assignments", "GET", undefined, { background: true }),
  ]);
  roleOptions.value = roles || [];
  grants.value = assignments || [];
}

function openRoles(u: any) {
  activePerson.value = u;
  const current = [...roleIds(u)];
  selectedRoles.value = current;
  expectedRoles.value = [...current];
  roleSheetOpen.value = true;
}

function toggleRole(id: string) {
  const idx = selectedRoles.value.indexOf(id);
  if (idx >= 0) selectedRoles.value.splice(idx, 1);
  else selectedRoles.value.push(id);
}

async function saveRoles() {
  if (!activePerson.value) return;
  saving.value = true;
  try {
    const saved = await api(
      `/admin/users/${activePerson.value.id}/business-roles`,
      "PUT",
      {
        roles: selectedRoles.value,
        expected_roles: expectedRoles.value,
        expected_department_id: activePerson.value.department_id || null,
      },
    );
    grants.value = [
      ...grants.value.filter((g) => g.user_id !== saved.user_id),
      saved,
    ];
    roleSheetOpen.value = false;
    showSuccessToast(`${activePerson.value.name} 的角色已更新`);
  } finally {
    saving.value = false;
  }
}

onMounted(() => run(loadRoles));
</script>

<template>
  <div class="m-page-sub">
    <header class="m-sticky-header">
      <div class="m-header-bar">
        <button
          type="button"
          class="m-btn m-btn-sm"
          style="border: none; padding: 0 6px"
          @click="router.back()"
        >
          ‹ 返回
        </button>
        <h1 class="m-header-title">员工通讯录</h1>
        <span class="m-muted">{{ filtered.length }} 人</span>
      </div>
      <div style="padding: 0 14px 10px">
        <input
          v-model="q"
          class="m-input"
          style="padding: 8px 10px; font-size: 13px"
          placeholder="搜索姓名、部门、职位、邮箱或手机号"
          @input="visibleCount = 30"
        />
      </div>
    </header>

    <section class="m-section">
      <article
        v-for="u in filtered.slice(0, visibleCount)"
        :key="u.id"
        class="m-card"
        style="display: flex; flex-direction: column; gap: 6px"
        @click="expandedId = expandedId === u.id ? '' : u.id"
      >
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px">
          <div style="font-size: 15px; font-weight: 700">
            {{ u.name }}
            <span v-if="u.position" class="m-muted" style="font-weight: 400">
              · {{ u.position }}
            </span>
          </div>
          <StatusTag
            :value="u.active === false ? '已停用' : '在用'"
            :tone="u.active === false ? 'gray' : 'green'"
          />
        </div>
        <div class="m-muted">
          {{ u.department_id ? departmentPath(u.department_id) : "未分配部门" }}
        </div>
        <div
          v-if="roleNames(u).length"
          style="display: flex; flex-wrap: wrap; gap: 4px"
        >
          <StatusTag
            v-for="rn in roleNames(u)"
            :key="rn"
            :value="rn"
            tone="blue"
          />
        </div>
        <div
          v-if="expandedId === u.id"
          style="
            padding-top: 8px;
            border-top: 1px solid #f4f4f5;
            display: flex;
            flex-direction: column;
            gap: 4px;
            font-size: 12px;
          "
        >
          <div>邮箱：{{ u.email || "未配置" }}</div>
          <div>手机：{{ u.mobile || "未配置" }}</div>
          <div v-if="state.me?.is_admin" style="margin-top: 6px">
            <button
              type="button"
              class="m-btn m-btn-sm m-btn-soft"
              @click.stop="openRoles(u)"
            >
              设置业务角色
            </button>
          </div>
        </div>
      </article>

      <button
        v-if="visibleCount < filtered.length"
        type="button"
        class="m-btn"
        style="width: 100%"
        @click="visibleCount += 40"
      >
        加载更多（已显示 {{ visibleCount }}/{{ filtered.length }}）
      </button>
    </section>

    <van-popup v-model:show="roleSheetOpen" position="bottom" round>
      <div
        v-if="activePerson"
        style="
          padding: 16px 16px calc(18px + env(safe-area-inset-bottom));
          display: flex;
          flex-direction: column;
          gap: 10px;
        "
      >
        <div style="display: flex; justify-content: space-between">
          <span style="font-size: 16px; font-weight: 700">
            {{ activePerson.name }} · 设置角色
          </span>
          <button
            type="button"
            style="border: none; background: transparent; color: var(--m-muted)"
            @click="roleSheetOpen = false"
          >
            取消
          </button>
        </div>
        <div style="max-height: 48vh; overflow-y: auto; display: flex; flex-direction: column; gap: 6px">
          <label
            v-for="r in roleOptions.filter((x) => x.active)"
            :key="r.id"
            class="m-card"
            style="padding: 10px; display: flex; align-items: center; gap: 10px"
          >
            <input
              type="checkbox"
              :checked="selectedRoles.includes(r.id)"
              @change="toggleRole(r.id)"
            />
            <span style="font-weight: 600">{{ r.name }}</span>
          </label>
        </div>
        <button
          type="button"
          class="m-btn m-btn-primary"
          style="width: 100%"
          :disabled="saving"
          @click="run(saveRoles)"
        >
          {{ saving ? "保存中…" : "保存角色设置" }}
        </button>
      </div>
    </van-popup>
  </div>
</template>
