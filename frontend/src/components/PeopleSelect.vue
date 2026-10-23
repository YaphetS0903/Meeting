<script setup lang="ts">
import { computed, ref } from "vue";
import { departmentPath, state } from "../api";
const props = defineProps<{
  modelValue: any;
  multiple?: boolean;
  placeholder?: string;
  disabled?: boolean;
  includeInactive?: boolean;
  popperClass?: string;
  allowedIds?: string[];
}>();
defineEmits(["update:modelValue"]);
const keyword = ref("");
const allOptions = computed(() => {
  const nameCounts = new Map<string, number>();
  for (const user of state.directory.users)
    nameCounts.set(user.name, (nameCounts.get(user.name) || 0) + 1);
  return state.directory.users.map((user: any) => {
    const suffix =
      (nameCounts.get(user.name) || 0) > 1
        ? ` · ${user.department_id ? departmentPath(user.department_id) : user.wecom_id || user.id.slice(0, 6)}`
        : "";
    return {
      ...user,
      label: `${user.name}${!user.active ? "（已停用）" : ""}${suffix}`,
      search:
        `${user.name} ${user.department_id ? departmentPath(user.department_id) : ""} ${user.wecom_id || ""}`.toLowerCase(),
    };
  });
});
const visibleOptions = computed(() => {
  const selected = new Set(
    (Array.isArray(props.modelValue)
      ? props.modelValue
      : props.modelValue
        ? [props.modelValue]
        : []) as string[],
  );
  const term = keyword.value.trim().toLowerCase();
  const result = allOptions.value.filter(
    (item: any) =>
      selected.has(item.id) ||
      ((props.includeInactive || item.active) &&
        (!props.allowedIds || props.allowedIds.includes(item.id)) &&
        (!term || item.search.includes(term))),
  );
  const chosen = result.filter((item: any) => selected.has(item.id));
  const choices = result
    .filter((item: any) => !selected.has(item.id))
    .sort((a: any, b: any) =>
      a.id === state.me?.id ? -1 : b.id === state.me?.id ? 1 : 0,
    )
    .slice(0, 80);
  return [...chosen, ...choices];
});
</script>
<template>
  <el-select
    :model-value="modelValue"
    @update:model-value="
      $emit('update:modelValue', $event || (multiple ? [] : null))
    "
    :multiple="multiple"
    filterable
    remote
    :remote-method="(value: string) => (keyword = value)"
    remote-show-suffix
    :reserve-keyword="false"
    collapse-tags
    :max-collapse-tags="3"
    clearable
    :placeholder="placeholder || '输入姓名搜索人员'"
    :disabled="disabled"
    :popper-class="popperClass"
    ><el-option
      v-for="u in visibleOptions"
      :key="u.id"
      :value="u.id"
      :disabled="!u.active && !includeInactive"
      :label="u.label"
  /></el-select>
</template>
