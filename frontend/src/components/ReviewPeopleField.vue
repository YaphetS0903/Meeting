<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { person } from "../api";
import PeopleSelect from "./PeopleSelect.vue";

const props = defineProps<{
  modelValue: string | string[] | null;
  multiple?: boolean;
  originalNames?: string[];
  needsAttention?: boolean;
  placeholder?: string;
  suggestions?: any[];
}>();
const emit = defineEmits(["update:modelValue"]);
const editing = ref(false);
const ids = computed<string[]>(() =>
  Array.isArray(props.modelValue)
    ? props.modelValue
    : props.modelValue
      ? [props.modelValue]
      : [],
);
const visibleSuggestions = computed(() =>
  (props.suggestions || []).filter(
    (suggestion: any) =>
      suggestion.user_id &&
      suggestion.name &&
      !ids.value.includes(suggestion.user_id),
  ),
);
watch(
  () => props.needsAttention,
  (value) => {
    if (value && !ids.value.length) editing.value = true;
  },
  { immediate: true },
);
function update(value: any) {
  emit("update:modelValue", value);
  if (value && (!Array.isArray(value) || value.length)) editing.value = false;
}
function acceptSuggestion(suggestion: any) {
  update(
    props.multiple
      ? [...new Set([...ids.value, suggestion.user_id])]
      : suggestion.user_id,
  );
}
</script>

<template>
  <div
    class="review-people-field"
    :class="{ 'needs-attention': needsAttention }"
  >
    <div v-if="ids.length && !editing" class="review-people-value">
      <span>{{ ids.map(person).join("、") }}</span>
      <el-button link type="primary" @click="editing = true">修改</el-button>
    </div>
    <PeopleSelect
      v-else
      :model-value="modelValue"
      :multiple="multiple"
      :placeholder="placeholder"
      @update:model-value="update"
    />
    <div
      v-if="needsAttention && visibleSuggestions.length"
      class="smart-person-suggestions"
    >
      <div
        v-for="suggestion in visibleSuggestions"
        :key="`${suggestion.original_name}-${suggestion.user_id}`"
        class="smart-person-suggestion"
      >
        <div>
          <strong
            >AI 建议：{{ suggestion.original_name }} →
            {{ suggestion.name }}</strong
          >
          <small>
            {{ suggestion.department || "未标注部门" }} · 置信度
            {{ Math.round(suggestion.confidence * 100) }}% ·
            {{ suggestion.reason }}
          </small>
        </div>
        <el-button
          size="small"
          type="primary"
          plain
          @click="acceptSuggestion(suggestion)"
          >采用</el-button
        >
      </div>
    </div>
    <p v-if="needsAttention" class="field-attention">
      Excel 中填写：{{
        originalNames?.join("、") || "空白"
      }}。请选择对应的企业微信成员。
    </p>
  </div>
</template>
