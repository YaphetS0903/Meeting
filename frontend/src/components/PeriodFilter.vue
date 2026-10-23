<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { localDate } from "../api";
const emit = defineEmits(["change"]);
const props = defineProps<{ allowAll?: boolean }>();
const mode = ref(props.allowAll ? "all" : "month"),
  value = ref(localDate());
const range = computed(() => {
  if (mode.value === "all") return { start: "", end: "" };
  const d = new Date(value.value + "T12:00:00+08:00");
  if (Number.isNaN(d.getTime())) return { start: "", end: "" };
  const date = localDate(d),
    parts = date.split("-").map(Number);
  const start = new Date(Date.UTC(parts[0]!, parts[1]! - 1, parts[2]!, 4));
  const end = new Date(start);
  if (mode.value === "month") {
    start.setUTCDate(1);
    end.setUTCMonth(end.getUTCMonth() + 1, 0);
  } else {
    const n = (start.getUTCDay() + 6) % 7;
    start.setUTCDate(start.getUTCDate() - n);
    end.setTime(start.getTime());
    end.setUTCDate(end.getUTCDate() + 6);
  }
  return { start: localDate(start), end: localDate(end) };
});
watch(range, (r) => emit("change", r), { immediate: true });
</script>
<template>
  <div class="period-filter">
    <el-radio-group v-model="mode" aria-label="统计周期"
      ><el-radio-button v-if="allowAll" value="all">全部日期</el-radio-button
      ><el-radio-button value="month">按月</el-radio-button
      ><el-radio-button value="week">按周</el-radio-button></el-radio-group
    ><el-date-picker
      v-if="mode !== 'all'"
      v-model="value"
      :type="mode === 'month' ? 'month' : 'date'"
      value-format="YYYY-MM-DD"
      :clearable="false"
      aria-label="选择日期"
    /><span v-if="mode !== 'all'" class="muted small"
      >{{ range.start }} — {{ range.end }}</span
    >
  </div>
</template>
