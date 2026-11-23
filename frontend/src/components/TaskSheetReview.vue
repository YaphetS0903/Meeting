<script setup lang="ts">
import { computed } from "vue";
import PeopleSelect from "./PeopleSelect.vue";
const props = defineProps<{
  draft: any;
  item?: any;
  issues: any[];
  users: any[];
}>();
const checkedCount = computed(
  () =>
    props.issues.filter((issue) =>
      (props.draft.import_confirmed_issues || []).includes(issue.id),
    ).length,
);
const checked = computed(
  () => props.issues.length > 0 && checkedCount.value === props.issues.length,
);
function confirm(value: any) {
  const ids = new Set<string>(props.draft.import_confirmed_issues || []);
  for (const issue of props.issues) {
    if (value) ids.add(issue.id);
    else ids.delete(issue.id);
  }
  props.draft.import_confirmed_issues = [...ids];
}
function linkContinuation(issue: any, value: string) {
  const previous = props.draft.items.find(
    (item: any) =>
      item.source?.sheet_import_item_id === issue.review_item_id &&
      item.section === "review",
  );
  if (value && previous) {
    props.item.historical_work_id = previous.historical_work_id;
    props.item.source.extended_from_item_id = previous.id;
  } else {
    props.item.historical_work_id = null;
    delete props.item.source.extended_from_item_id;
  }
}
</script>
<template>
  <div
    v-if="issues.length"
    :id="item ? `sheet-check-${item.id}` : 'sheet-check-meeting'"
    class="sheet-row-review"
    role="group"
    :aria-label="item ? '本行核对提示' : '会议信息核对提示'"
  >
    <p v-if="item?.source?.row" class="sheet-original">
      原表第{{ item.source.row }}行
    </p>
    <div v-for="issue in issues" :key="issue.id" class="sheet-row-issue">
      <p>{{ issue.message }}</p>
      <p v-if="issue.raw" class="sheet-original">原文：{{ issue.raw }}</p>
      <el-select
        v-if="item && issue.field === 'continuation'"
        :model-value="item.source?.extended_from_item_id ? 'linked' : ''"
        aria-label="核对任务延续关系"
        @change="linkContinuation(issue, $event)"
        ><el-option label="作为独立新任务" value="" /><el-option
          label="延续这条上期任务"
          value="linked"
      /></el-select>
      <PeopleSelect
        v-else-if="item?.section === 'review' && issue.field === 'verifier_id'"
        v-model="item.verifier_id"
        :users="users"
        search-only
        copyable
        aria-label="上期验证人"
      />
      <PeopleSelect
        v-else-if="!item && issue.field === 'attendee_ids'"
        v-model="draft.attendee_ids"
        :users="users"
        multiple
        search-only
        copyable
        aria-label="核对参会人员"
      />
    </div>
    <el-checkbox
      :model-value="checked"
      :indeterminate="checkedCount > 0 && !checked"
      @update:model-value="confirm"
      >{{ item ? "本行已核对" : "会议信息已核对" }}</el-checkbox
    >
  </div>
</template>
<style scoped>
.sheet-row-review {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: #87520f;
  white-space: normal;
  overflow-wrap: anywhere;
}
.sheet-row-issue + .sheet-row-issue {
  margin-top: 8px;
}
.sheet-row-issue p {
  margin: 0 0 4px;
  line-height: 1.5;
}
.sheet-original {
  margin: 0 0 4px;
  color: #526378;
  white-space: pre-wrap;
}
.sheet-row-issue .el-select {
  width: 100%;
  margin: 4px 0;
}
.sheet-row-review .el-checkbox {
  margin-right: 0;
  --el-checkbox-checked-text-color: #205eac;
}
</style>
