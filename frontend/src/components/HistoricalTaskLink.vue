<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api";

const props = defineProps<{ item: any; draft: any; readonly: boolean }>();
const emit = defineEmits<{ change: [reference: any] }>();
const visible = ref(false),
  loading = ref(false),
  error = ref(""),
  keyword = ref("");
const options = ref<any[]>([]);
let generation = 0;
async function search() {
  const current = ++generation;
  loading.value = true;
  error.value = "";
  options.value = [];
  try {
    const params = new URLSearchParams({
      series_id: props.draft.series_id || "",
      meeting_date: props.draft.meeting_date || "",
      q: keyword.value.trim(),
    });
    const result = await api(`/backfill-task-options?${params}`);
    if (current === generation && visible.value) options.value = result.items;
  } catch (e: any) {
    if (current === generation)
      error.value = e.message || "读取失败，请重新查询";
  } finally {
    if (current === generation) loading.value = false;
  }
}
function open() {
  keyword.value = "";
  visible.value = true;
  void search();
}
function choose(row: any) {
  const { meeting_id, version, item_id, task_id, meeting_title, meeting_date } =
    row;
  emit("change", {
    meeting_id,
    version,
    item_id,
    task_id,
    meeting_title,
    meeting_date,
  });
  visible.value = false;
}
watch(visible, (value) => {
  if (!value) generation += 1;
});
watch(
  () => [props.draft.series_id, props.draft.meeting_date, props.item.id],
  () => {
    visible.value = false;
  },
);
onBeforeUnmount(() => {
  generation += 1;
});
</script>

<template>
  <div class="history-task-link">
    <template v-if="item.backfill_reference">
      <router-link :to="`/meetings/${item.backfill_reference.meeting_id}`">
        已关联：{{ item.backfill_reference.meeting_date }}
        {{ item.backfill_reference.meeting_title }}
      </router-link>
      <el-button v-if="!readonly" link @click="emit('change', null)"
        >取消关联</el-button
      >
    </template>
    <el-button v-else-if="!readonly" type="primary" link @click="open"
      >关联已发布任务</el-button
    >
  </div>
  <el-dialog
    v-model="visible"
    title="关联后续已发布任务"
    width="min(900px,94vw)"
    append-to-body
    destroy-on-close
  >
    <p class="history-link-context">
      历史工作：{{ item.content || "尚未填写" }}
    </p>
    <p class="history-link-note">
      仅记录对应关系，保留历史填写内容和后续会议的发布结果。
    </p>
    <form class="history-link-search" @submit.prevent="search">
      <el-input
        v-model="keyword"
        clearable
        placeholder="搜索任务、责任人或会议名称"
        aria-label="搜索已发布任务"
      />
      <el-button native-type="submit" :loading="loading">查询</el-button>
    </form>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
    />
    <div v-loading="loading" class="history-link-options" :aria-busy="loading">
      <p
        v-if="!loading && !error && !options.length"
        class="history-link-note"
        role="status"
      >
        暂无匹配任务，请核对会议系列、补录日期或搜索条件。
      </p>
      <article
        v-for="row in options"
        :key="`${row.meeting_id}:${row.item_id}`"
        class="history-link-option"
      >
        <div>
          <p class="history-option-meeting">
            {{ row.meeting_date }} · {{ row.meeting_title }}
          </p>
          <p class="history-option-content">{{ row.content }}</p>
          <p class="history-link-note">
            {{ row.owner_names.join("、") }} · 计划完成：{{
              row.due_date || "未填写"
            }}
            · {{ row.section === "new" ? "本次安排" : row.status }}
          </p>
        </div>
        <el-button type="primary" plain @click="choose(row)"
          >确认关联</el-button
        >
      </article>
    </div>
    <template #footer
      ><el-button @click="visible = false">关闭</el-button></template
    >
  </el-dialog>
</template>

<style scoped>
.history-task-link {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
}
.history-task-link a {
  color: var(--el-color-primary);
  overflow-wrap: anywhere;
}
.history-link-context,
.history-option-content {
  margin: 0 0 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.history-link-note,
.history-option-meeting {
  margin: 0 0 8px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}
.history-link-search {
  display: flex;
  gap: 8px;
  margin: 16px 0;
}
.history-link-options {
  min-height: 100px;
  max-height: 52vh;
  overflow-y: auto;
}
.history-link-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  border-bottom: 1px solid var(--el-border-color-light);
  padding: 16px 0;
}
.history-link-option > div {
  min-width: 0;
}
@media (max-width: 600px) {
  .history-link-option {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }
}
</style>
