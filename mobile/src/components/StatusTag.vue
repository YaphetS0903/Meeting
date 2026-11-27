<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  value?: string | null;
  tone?: "blue" | "green" | "red" | "amber" | "gray";
}>();

const resolvedTone = computed(() => {
  if (props.tone) return props.tone;
  const v = (props.value || "").trim();
  if (
    [
      "已完成",
      "已通过",
      "已发布",
      "已关闭",
      "关闭",
      "已发送",
      "正常",
      "在职",
      "在用",
      "启用",
      "已配置",
      "已开启",
      "已创建",
      "已关联待办",
      "待办已完成",
      "成功",
      "识别完成",
    ].includes(v)
  )
    return "green";
  if (
    [
      "未完成",
      "已驳回",
      "失败",
      "发送失败",
      "需处理",
      "异常",
      "责任人已停用",
    ].includes(v)
  )
    return "red";
  if (
    [
      "待验证",
      "待确认",
      "待确认派发",
      "待核实",
      "创建结果待核实",
      "部分完成",
      "修订中",
      "已提交申请 · 待验证",
      "待绑定",
    ].includes(v)
  )
    return "amber";
  if (
    [
      "未到期",
      "草稿",
      "到期未验证",
      "未到期验证",
      "部门例会",
      "直接派发",
      "系统管理员",
    ].includes(v)
  )
    return "blue";
  return "gray";
});
</script>

<template>
  <span class="m-status-tag" :class="`tone-${resolvedTone}`">
    <slot>{{ value || "—" }}</slot>
  </span>
</template>

<style scoped>
.m-status-tag {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.35;
  white-space: nowrap;
  flex-shrink: 0;
}
.tone-blue {
  background: var(--m-primary-soft);
  color: var(--m-primary);
}
.tone-green {
  background: var(--m-success-soft);
  color: var(--m-success);
}
.tone-red {
  background: var(--m-danger-soft);
  color: var(--m-danger);
}
.tone-amber {
  background: var(--m-warning-soft);
  color: var(--m-warning);
}
.tone-gray {
  background: #f1f4f9;
  color: #52525b;
}
</style>
