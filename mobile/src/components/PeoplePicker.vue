<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { showToast } from "vant";
import { departmentLeaf, departmentPath, state } from "../api/client";
import { isEmployeeAccount, pastedPeople, withDirectoryDetails } from "../api/people";

const props = withDefaults(
  defineProps<{
    modelValue: string | string[] | null | undefined;
    users?: any[];
    multiple?: boolean;
    placeholder?: string;
    title?: string;
    disabled?: boolean;
    allowInactive?: boolean;
    clearable?: boolean;
  }>(),
  {
    multiple: false,
    placeholder: "点击选择人员",
    title: "选择人员",
    disabled: false,
    allowInactive: false,
    clearable: true,
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: any];
  change: [value: any];
}>();

const open = ref(false);
const keyword = ref("");
const pasteBox = ref(false);
const pasteText = ref("");
const includeInactive = ref(false);
const draftIds = ref<string[]>([]);

const sourceUsers = computed(() => {
  const base = props.users ?? state.directory.users ?? [];
  return withDirectoryDetails(base, state.directory.users || []);
});

const selectedIds = computed<string[]>(() => {
  if (props.multiple)
    return Array.isArray(props.modelValue) ? props.modelValue : [];
  return props.modelValue ? [String(props.modelValue)] : [];
});

const userMap = computed(
  () => new Map(sourceUsers.value.map((u: any) => [u.id, u])),
);

const selectedUsers = computed(() =>
  selectedIds.value.map((id) => {
    const found = userMap.value.get(id);
    if (found) return found;
    const fromDir = (state.directory.users || []).find((u: any) => u.id === id);
    return fromDir || { id, name: "未知人员", active: true };
  }),
);

const candidateUsers = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  return sourceUsers.value
    .filter((u: any) => {
      if (!isEmployeeAccount(u)) return false;
      const active = u.active !== false;
      if (!active && !(props.allowInactive && includeInactive.value))
        return false;
      if (!q) return true;
      const dept = u.department_id ? departmentPath(u.department_id) : "";
      return (
        String(u.name || "").toLowerCase().includes(q) ||
        dept.toLowerCase().includes(q)
      );
    })
    .slice(0, 120);
});

watch(open, (visible) => {
  if (visible) {
    draftIds.value = [...selectedIds.value];
    keyword.value = "";
    pasteBox.value = false;
    pasteText.value = "";
  }
});

function toggle(id: string) {
  if (!props.multiple) {
    emit("update:modelValue", id);
    emit("change", id);
    open.value = false;
    return;
  }
  const idx = draftIds.value.indexOf(id);
  if (idx >= 0) draftIds.value.splice(idx, 1);
  else draftIds.value.push(id);
}

function removeChip(id: string) {
  if (props.disabled) return;
  if (!props.multiple) {
    emit("update:modelValue", "");
    emit("change", "");
    return;
  }
  const next = selectedIds.value.filter((x) => x !== id);
  emit("update:modelValue", next);
  emit("change", next);
}

function applyPaste() {
  const pool = sourceUsers.value.filter(
    (u: any) =>
      isEmployeeAccount(u) &&
      (u.active !== false || (props.allowInactive && includeInactive.value)),
  );
  const result = pastedPeople(pasteText.value, pool);
  if (!result.ids.length) {
    showToast("未匹配到可用员工姓名");
    return;
  }
  if (props.multiple) {
    draftIds.value = [...new Set([...draftIds.value, ...result.ids])];
  } else {
    draftIds.value = [result.ids[0]];
  }
  const warns: string[] = [];
  if (result.unresolved.length)
    warns.push(`未找到：${result.unresolved.join("、")}`);
  if (result.ambiguous.length)
    warns.push(`重名需手选：${result.ambiguous.join("、")}`);
  showToast(
    warns.length
      ? `已匹配 ${result.ids.length} 人；${warns.join("；")}`
      : `已匹配 ${result.ids.length} 人`,
  );
  pasteText.value = "";
  pasteBox.value = false;
}

function confirmSelection() {
  const val = props.multiple ? [...draftIds.value] : draftIds.value[0] || "";
  emit("update:modelValue", val);
  emit("change", val);
  open.value = false;
}

function clearSelection() {
  const val = props.multiple ? [] : "";
  emit("update:modelValue", val);
  emit("change", val);
  open.value = false;
}
</script>

<template>
  <div class="pp-trigger" :class="{ disabled }" @click="!disabled && (open = true)">
    <div v-if="selectedUsers.length" class="pp-chips">
      <span
        v-for="u in selectedUsers"
        :key="u.id"
        class="pp-chip"
        :class="{ inactive: u.active === false }"
      >
        {{ u.name }}{{ u.active === false ? "（已停用）" : "" }}
        <button
          v-if="!disabled && (multiple || clearable)"
          type="button"
          class="pp-chip-x"
          @click.stop="removeChip(u.id)"
        >
          ×
        </button>
      </span>
    </div>
    <span v-else class="pp-placeholder">{{ placeholder }}</span>
    <span v-if="!disabled" class="pp-arrow">›</span>
  </div>

  <van-popup
    v-model:show="open"
    position="bottom"
    round
    :style="{ height: '86vh', display: 'flex', flexDirection: 'column' }"
    teleport="body"
  >
    <div class="pp-head">
      <button type="button" class="pp-head-btn" @click="open = false">取消</button>
      <div class="pp-head-title">
        {{ title }}
        <span v-if="multiple" class="m-muted">（已选 {{ draftIds.length }}）</span>
      </div>
      <button
        v-if="multiple"
        type="button"
        class="pp-head-btn primary"
        @click="confirmSelection"
      >
        确定
      </button>
      <button
        v-else-if="clearable && selectedIds.length"
        type="button"
        class="pp-head-btn"
        @click="clearSelection"
      >
        清空
      </button>
      <span v-else style="width: 36px" />
    </div>

    <div class="pp-tools">
      <input
        v-model="keyword"
        class="m-input"
        placeholder="输入姓名或部门搜索…"
      />
      <div class="pp-tool-row">
        <button
          v-if="multiple"
          type="button"
          class="m-btn m-btn-sm m-btn-soft"
          @click="pasteBox = !pasteBox"
        >
          {{ pasteBox ? "收起粘贴" : "粘贴多人姓名" }}
        </button>
        <label v-if="allowInactive" class="pp-check">
          <input v-model="includeInactive" type="checkbox" />
          <span>包含已停用原责任人</span>
        </label>
      </div>
      <div v-if="pasteBox" class="pp-paste">
        <textarea
          v-model="pasteText"
          class="m-textarea"
          rows="2"
          placeholder="粘贴多个姓名，支持顿号、逗号或空格分隔"
        />
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-primary"
          @click="applyPaste"
        >
          批量匹配选中
        </button>
      </div>
    </div>

    <div class="pp-list">
      <div
        v-for="u in candidateUsers"
        :key="u.id"
        class="pp-item"
        :class="{ active: draftIds.includes(u.id) }"
        @click="toggle(u.id)"
      >
        <div class="pp-avatar">{{ (u.name || "?").slice(0, 1) }}</div>
        <div class="pp-info">
          <div class="pp-name">
            {{ u.name }}
            <span v-if="u.active === false" class="pp-inactive-tag">已停用</span>
          </div>
          <div class="pp-dept">
            {{ u.department_id ? departmentLeaf(u.department_id) : "未分配部门" }}
            <span v-if="u.position"> · {{ u.position }}</span>
          </div>
        </div>
        <div class="pp-tick">
          {{ draftIds.includes(u.id) ? "✓" : "" }}
        </div>
      </div>
      <div v-if="!candidateUsers.length" class="m-empty">未找到匹配员工</div>
    </div>
  </van-popup>
</template>

<style scoped>
.pp-trigger {
  min-height: 44px;
  border: 1px solid var(--m-input-line);
  border-radius: 9px;
  background: var(--m-surface);
  padding: 6px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  cursor: pointer;
}
.pp-trigger.disabled {
  background: #f4f4f5;
  cursor: not-allowed;
}
.pp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1;
}
.pp-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--m-primary-soft);
  color: var(--m-primary-hover);
  border: 1px solid var(--m-primary-border);
  font-size: 12px;
  font-weight: 500;
}
.pp-chip.inactive {
  background: #f4f4f5;
  color: #52525b;
  border-color: #d4d4d8;
}
.pp-chip-x {
  border: none;
  background: transparent;
  color: inherit;
  font-size: 14px;
  line-height: 1;
  padding: 0 2px;
  cursor: pointer;
}
.pp-placeholder {
  color: #a1a1aa;
  font-size: 14px;
}
.pp-arrow {
  color: #a1a1aa;
  font-size: 18px;
}
.pp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--m-line);
  flex-shrink: 0;
}
.pp-head-title {
  font-size: 15px;
  font-weight: 700;
}
.pp-head-btn {
  border: none;
  background: transparent;
  font-size: 14px;
  color: var(--m-text-secondary);
  padding: 4px 6px;
  cursor: pointer;
}
.pp-head-btn.primary {
  color: var(--m-primary);
  font-weight: 700;
}
.pp-tools {
  padding: 10px 14px;
  border-bottom: 1px solid var(--m-line);
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}
.pp-tool-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pp-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--m-text-secondary);
}
.pp-paste {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pp-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0 calc(16px + env(safe-area-inset-bottom));
}
.pp-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #f4f4f5;
  cursor: pointer;
}
.pp-item.active {
  background: #eff6ff;
}
.pp-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #e4e4e7;
  color: #27272a;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.pp-item.active .pp-avatar {
  background: var(--m-primary);
  color: #ffffff;
}
.pp-info {
  flex: 1;
  min-width: 0;
}
.pp-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--m-ink);
}
.pp-inactive-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  background: #f4f4f5;
  color: #71717a;
}
.pp-dept {
  font-size: 12px;
  color: var(--m-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pp-tick {
  width: 24px;
  text-align: center;
  color: var(--m-primary);
  font-weight: 700;
  font-size: 16px;
}
</style>
