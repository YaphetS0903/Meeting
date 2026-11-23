<script setup lang="ts">
import { computed, ref, watch } from "vue";
const props = defineProps<{
  item: any;
  editable: boolean;
  users: any[];
}>();
const emit = defineEmits<{
  cancel: [];
  confirm: [results: Record<string, string>];
}>();
const results = ref<Record<string, string>>({});
const error = ref("");
const owners = computed(() =>
  (props.item?.owner_ids || []).map((id: string, index: number) => ({
    id,
    name:
      props.item.owner_names?.[index] ||
      props.users.find((person) => person.id === id)?.name ||
      id,
  })),
);
watch(
  () => props.item,
  (item) => {
    results.value = { ...(item?.owner_results || {}) };
    error.value = "";
  },
  { immediate: true },
);
const done = computed(
  () =>
    owners.value.filter((person: any) => results.value[person.id] === "已完成")
      .length,
);
function confirm() {
  if (owners.value.some((person: any) => !results.value[person.id])) {
    error.value = "请为每位责任人选择已完成或未完成";
    return;
  }
  emit(
    "confirm",
    Object.fromEntries(
      owners.value.map((person: any) => [person.id, results.value[person.id]]),
    ),
  );
}
</script>
<template>
  <el-dialog
    :model-value="!!item"
    title="按责任人评定"
    width="min(560px, 94vw)"
    @close="emit('cancel')"
  >
    <template v-if="item">
      <p class="owner-result-content">{{ item.content }}</p>
      <div class="owner-result-list">
        <div v-for="owner in owners" :key="owner.id" class="owner-result-row">
          <span class="owner-result-name">{{ owner.name }}</span>
          <el-radio-group
            v-model="results[owner.id]"
            :disabled="!editable"
            :aria-label="`${owner.name}的完成结果`"
            @change="error = ''"
          >
            <el-radio value="已完成">已完成</el-radio>
            <el-radio value="未完成">未完成</el-radio>
          </el-radio-group>
        </div>
      </div>
      <p class="owner-result-summary" role="status">
        已完成 {{ done }} 人／共 {{ owners.length }} 人
      </p>
      <p v-if="error" class="owner-result-error" role="alert">{{ error }}</p>
    </template>
    <template #footer>
      <el-button @click="emit('cancel')">{{
        editable ? "取消" : "关闭"
      }}</el-button>
      <el-button v-if="editable" type="primary" @click="confirm"
        >确认结果</el-button
      >
    </template>
  </el-dialog>
</template>
<style scoped>
.owner-result-content {
  margin: 0 0 16px;
  color: var(--text);
  overflow-wrap: anywhere;
}
.owner-result-list {
  max-height: 50vh;
  overflow-y: auto;
}
.owner-result-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.owner-result-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.owner-result-row :deep(.el-radio-group) {
  flex-shrink: 0;
}
.owner-result-row :deep(.el-radio) {
  margin-right: 16px;
}
.owner-result-row :deep(.el-radio:last-child) {
  margin-right: 0;
}
.owner-result-summary {
  margin: 16px 0 0;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.owner-result-error {
  color: var(--el-color-danger);
  margin-bottom: 0;
}
@media (max-width: 480px) {
  .owner-result-row {
    gap: 8px;
    flex-wrap: wrap;
  }
}
</style>
