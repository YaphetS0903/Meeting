<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, run, person } from "../api";
import PeopleSelect from "./PeopleSelect.vue";
const props = defineProps<{ task: any }>();
const emit = defineEmits<{ changed: [] }>();
const rule = ref<any>(null),
  form = ref<any>(null),
  busy = ref(false),
  loading = ref(false),
  loadError = ref("");
let generation = 0;
const canManage = computed(() =>
  rule.value
    ? rule.value.can_manage === true
    : (props.task.can_manage_recurrence ?? props.task.can_edit),
);
async function load() {
  const active = ++generation;
  form.value = null;
  rule.value = null;
  loading.value = true;
  loadError.value = "";
  try {
    const result = await api(`/tasks/${props.task.id}/recurrence`);
    if (active === generation) rule.value = result;
  } catch (error: any) {
    if (active === generation) {
      loadError.value = error.message || "周期信息加载失败";
      throw error;
    }
  } finally {
    if (active === generation) loading.value = false;
  }
}
onBeforeUnmount(() => {
  generation += 1;
});
watch(
  () => [props.task.id, props.task.version],
  () => run(load),
  { immediate: true },
);
function edit() {
  if (!canManage.value || loading.value || loadError.value) return;
  form.value = rule.value
    ? {
        ...rule.value.template,
        expected_version: rule.value.version,
        active: rule.value.active,
        end_date: rule.value.end_date,
        reason: "",
      }
    : {
        frequency: "weekly",
        end_date: null,
        reason: "",
        expected_version: props.task.version,
      };
}
async function save() {
  busy.value = true;
  try {
    if (rule.value)
      await api(`/recurrences/${rule.value.id}`, "PUT", form.value);
    else await api(`/tasks/${props.task.id}/recurrence`, "POST", form.value);
    await load();
    emit("changed");
    ElMessage.success("周期规则已保存");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section v-loading="loading" class="panel section-body">
    <h3>周期工作</h3>
    <p v-if="task.recurrence_period" class="muted">
      本实例所属周期起始日：{{ task.recurrence_period }}
    </p>
    <template v-if="rule">
      <p>
        {{ rule.frequency === "weekly" ? "每周" : "每月" }}生成独立任务 ·
        {{ rule.active ? "已启用" : "已暂停或结束" }}
      </p>
      <p>
        首期期限：{{ rule.anchor_date }}；下一期期限：{{
          rule.next_due_date
        }}；结束日期：{{ rule.end_date || "不限" }}
      </p>
      <p v-if="rule.template">
        后续责任人：{{
          rule.template.owner_ids.map(person).join("、")
        }}；验证人：{{ person(rule.template.verifier_id) }}
      </p>
      <p v-else class="muted">
        此处显示周期信息，后续工作安排由有权限的派发人或会议负责人维护。
      </p>
      <el-alert
        v-if="rule.last_error"
        :title="rule.last_error"
        type="warning"
        :closable="false"
      />
    </template>
    <el-alert
      v-else-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
    >
      <el-button size="small" @click="run(load)">重试加载</el-button>
    </el-alert>
    <p v-else-if="!loading" class="muted">
      尚未设置周期。可将此任务作为首期，后续每期自动建立新的待办。
    </p>
    <el-button
      v-if="
        canManage &&
        !loading &&
        !loadError &&
        !form &&
        !(!rule && task.withdrawn_at)
      "
      @click="edit"
      >{{ rule ? "维护周期规则" : "设置周期任务" }}</el-button
    >
    <el-form v-if="form" label-position="top">
      <el-alert
        title="每周按首期期限的星期生成；每月按首期日期生成，遇短月取月末。新周期开始后系统自动下发，旧期保持独立；暂停期间不补发，恢复后从当前周期继续。"
        type="info"
        :closable="false"
      />
      <el-form-item v-if="!rule" label="周期频率"
        ><el-select v-model="form.frequency" aria-label="周期频率"
          ><el-option label="每周" value="weekly" /><el-option
            label="每月"
            value="monthly" /></el-select
      ></el-form-item>
      <el-form-item label="周期结束日期（可留空）"
        ><el-date-picker
          v-model="form.end_date"
          value-format="YYYY-MM-DD"
          aria-label="周期结束日期"
      /></el-form-item>
      <template v-if="rule">
        <el-form-item label="继续生成"
          ><el-switch v-model="form.active" aria-label="继续生成"
        /></el-form-item>
        <el-form-item label="后续任务内容"
          ><el-input v-model="form.content" type="textarea"
        /></el-form-item>
        <el-form-item label="后续输出物"
          ><el-input v-model="form.deliverable"
        /></el-form-item>
        <el-form-item label="后续责任人"
          ><PeopleSelect v-model="form.owner_ids" multiple
        /></el-form-item>
        <el-form-item label="后续验证人"
          ><PeopleSelect v-model="form.verifier_id"
        /></el-form-item>
        <p class="muted">
          修改规则仅作用于尚未生成的周期。已生成任务请在该任务中单独调整。
        </p>
      </template>
      <el-form-item label="周期设置依据"
        ><el-input v-model="form.reason" aria-label="周期设置依据"
      /></el-form-item>
      <el-button @click="form = null">取消</el-button
      ><el-button
        type="primary"
        :loading="busy"
        :disabled="!form.reason.trim()"
        @click="run(save)"
        >保存周期规则</el-button
      >
    </el-form>
  </section>
</template>
