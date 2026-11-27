<script setup lang="ts">
import { isLowReportRate, rate } from "../api";
defineProps<{ value: number | null }>();
</script>

<template>
  <div class="report-rate-meter">
    <el-progress
      v-if="value != null"
      :percentage="value * 100"
      :show-text="false"
      :stroke-width="7"
      :color="isLowReportRate(value) ? '#cf344e' : 'var(--el-color-primary)'"
      aria-hidden="true"
    />
    <strong :class="{ 'report-rate-low': isLowReportRate(value) }">{{
      rate(value)
    }}</strong>
  </div>
</template>

<style scoped>
.report-rate-meter {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: flex-end;
  font-variant-numeric: tabular-nums;
}
.report-rate-meter .el-progress {
  flex: 1;
  min-width: 0;
}
.report-rate-meter strong {
  min-width: 46px;
}
</style>
