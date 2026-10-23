<script setup lang="ts">
defineProps<{ value: string }>();
</script>
<template>
  <span
    class="status-tag"
    :class="{
      'is-success': ['已完成', '关闭'].includes(value),
      'is-danger': value === '未完成',
      'is-muted': value === '不统计',
    }"
    >{{ value }}</span
  >
</template>
