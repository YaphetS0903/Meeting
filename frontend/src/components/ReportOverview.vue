<script setup lang="ts">
import { computed } from "vue";
import { CircleCheck, Clock, Document } from "@element-plus/icons-vue";
import { isLowReportRate, rate } from "../api";
import "../reports-dashboard.css";

const props = defineProps<{
  summary: {
    items: number;
    rate: number | null;
    counts: Record<string, number>;
  };
  statuses: string[];
  rateLabel: string;
  totalLabel: string;
  taskLabel: string;
  rateHint: string;
  summaryOnlyStatuses?: boolean;
  disableTotalDetails?: boolean;
}>();
const emit = defineEmits<{ details: [status: string] }>();
const cards = [
  { status: "", icon: Document, tone: "total" },
  { status: "已完成", icon: CircleCheck, tone: "done" },
  { status: "未完成", icon: Clock, tone: "overdue" },
];
const tones: Record<string, string> = {
  已完成: "done",
  未完成: "overdue",
  未到期: "pending",
  不统计: "excluded",
  关闭: "closed",
  已关闭: "closed",
  待验证: "verification",
};
const distribution = computed(() => {
  const total = props.statuses.reduce(
    (sum, status) => sum + (props.summary.counts[status] ?? 0),
    0,
  );
  return props.statuses.map((status) => {
    const count = props.summary.counts[status] ?? 0;
    return {
      status,
      count,
      tone: tones[status],
      share: total ? count / total : 0,
    };
  });
});
const hasDistribution = computed(() =>
  distribution.value.some((entry) => entry.count > 0),
);
const percent = computed(() => (props.summary.rate ?? 0) * 100);
const low = computed(() => isLowReportRate(props.summary.rate));
</script>

<template>
  <div class="report-overview">
    <div class="stat-grid report-stat-grid">
      <article
        class="report-kpi report-kpi-rate"
        :class="{ 'rate-below-target': low }"
        :title="rateHint"
      >
        <div>
          <span>{{ rateLabel }}</span
          ><strong>{{ rate(summary.rate) }}</strong>
        </div>
        <div class="report-kpi-ring" aria-hidden="true">
          <el-progress
            type="circle"
            :percentage="percent"
            :stroke-width="8"
            :width="90"
            :show-text="false"
            :color="low ? '#ffc2cc' : '#fff'"
          />
          <span>{{ rate(summary.rate) }}</span>
        </div>
      </article>
      <article
        v-for="card in cards"
        :key="card.status"
        class="report-kpi"
        :data-tone="card.tone"
      >
        <div>
          <span>{{ card.status || totalLabel }}</span>
          <strong
            v-if="card.status ? summaryOnlyStatuses : disableTotalDetails"
            >{{
              card.status ? (summary.counts[card.status] ?? 0) : summary.items
            }}</strong
          >
          <button
            v-else
            type="button"
            class="report-kpi-value"
            :aria-label="`查看${card.status || '全部'}${taskLabel}`"
            aria-haspopup="dialog"
            @click="emit('details', card.status)"
          >
            <strong>{{
              card.status ? (summary.counts[card.status] ?? 0) : summary.items
            }}</strong>
          </button>
        </div>
        <el-icon class="report-kpi-icon" aria-hidden="true"
          ><component :is="card.icon"
        /></el-icon>
      </article>
    </div>
    <section class="panel report-distribution" aria-label="任务状态分布">
      <h2>任务状态分布</h2>
      <div class="report-distribution-bar" aria-hidden="true">
        <span
          v-for="entry in distribution.filter((item) => item.share > 0)"
          :key="entry.status"
          :data-tone="entry.tone"
          :style="{ flexGrow: entry.share }"
          :title="`${entry.status} ${rate(entry.share)}`"
        >
          <span v-if="entry.share >= 0.035">{{ rate(entry.share) }}</span>
        </span>
        <span v-if="!hasDistribution" class="report-distribution-empty"
          >本期暂无任务</span
        >
      </div>
      <ul class="report-distribution-legend">
        <li v-for="entry in distribution" :key="entry.status">
          <span v-if="summaryOnlyStatuses" class="report-distribution-label">
            <span
              class="report-distribution-swatch"
              :data-tone="entry.tone"
              aria-hidden="true"
            />
            {{ entry.status }} {{ entry.count }}
          </span>
          <button
            v-else
            type="button"
            :disabled="!entry.count"
            :aria-label="`任务状态分布：${entry.status} ${entry.count} 项，占 ${rate(entry.share)}`"
            aria-haspopup="dialog"
            @click="emit('details', entry.status)"
          >
            <span
              class="report-distribution-swatch"
              :data-tone="entry.tone"
              aria-hidden="true"
            />
            {{ entry.status }} {{ entry.count }}
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>
