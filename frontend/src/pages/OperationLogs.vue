<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Refresh, Search } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { api, query, state } from "../api";
import { beijingTime } from "../time";

const allowed = computed(() => !!state.me?.can_view_operation_logs);
const keyword = ref("");
const dates = ref<string[]>([]);
const category = ref("");
const result = ref("");
const categories = ref<Record<string, string>>({
  auth: "登录与账号",
  meeting: "会议与任务单",
  task: "任务与验证",
  import: "导入与附件",
  report: "统计报表",
  permission: "权限管理",
  notification: "通知发送",
  system: "系统管理",
});
const rows = ref<any[]>([]),
  total = ref(0),
  page = ref(1),
  pageSize = ref(20);
const loading = ref(false),
  error = ref("");
const applied = ref<Record<string, string>>({});
let generation = 0;
async function load() {
  if (!allowed.value) return;
  const current = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const data = await api(
      "/admin/operation-logs?" +
        query({
          ...applied.value,
          page: page.value,
          page_size: pageSize.value,
        }),
    );
    if (current !== generation) return;
    rows.value = data.items;
    total.value = data.total;
    categories.value = data.categories;
  } catch (e: any) {
    if (current === generation)
      error.value = e.message || "加载日志失败，请重试";
  } finally {
    if (current === generation) loading.value = false;
  }
}
async function search() {
  if (dates.value?.length === 2 && dates.value[0]! > dates.value[1]!) {
    ElMessage.warning("开始日期不能晚于结束日期");
    return;
  }
  applied.value = {
    keyword: keyword.value.trim(),
    category: category.value,
    result: result.value,
    start: dates.value?.[0] || "",
    end: dates.value?.[1] || "",
  };
  page.value = 1;
  await load();
}
async function reset() {
  keyword.value = category.value = result.value = "";
  dates.value = [];
  await search();
}
const detailVisible = ref(false),
  detail = ref<any>(null),
  detailLoading = ref(false),
  detailError = ref("");
let detailGeneration = 0;
async function openDetail(row: any) {
  const current = ++detailGeneration;
  detailVisible.value = true;
  detail.value = null;
  detailLoading.value = true;
  detailError.value = "";
  try {
    const value = await api(
      "/admin/operation-logs/" + encodeURIComponent(row.id),
    );
    if (current === detailGeneration && detailVisible.value)
      detail.value = value;
  } catch (e: any) {
    if (current === detailGeneration)
      detailError.value = e.message || "加载详情失败，请重试";
  } finally {
    if (current === detailGeneration) detailLoading.value = false;
  }
}
watch(
  allowed,
  (value) => {
    if (value) void load();
    else {
      generation++;
      rows.value = [];
      detailVisible.value = false;
    }
  },
  { immediate: true },
);
const resultLabels: Record<string, string> = {
  success: "成功",
  failed: "失败",
  sent: "已发送",
  pending: "待处理",
  sending: "发送中",
  queued: "待发送",
  blocked: "发送受阻",
  uncertain: "待核实",
  skipped: "已跳过",
  historical: "历史记录未保存发送状态",
};
function resultType(value: string) {
  return ["success", "sent"].includes(value)
    ? "success"
    : ["failed", "blocked", "uncertain"].includes(value)
      ? "danger"
      : "info";
}
function recipientText(people: any[] = []) {
  return (
    people
      .map((person) =>
        person.address ? `${person.name} <${person.address}>` : person.name,
      )
      .join("；") || "无"
  );
}
const channelLabels: Record<string, string> = {
  email: "邮件",
  wecom: "企业微信",
  native_todo: "企业微信待办",
  export: "生成纪要文件",
  import: "导入文件",
};
</script>

<template>
  <div v-if="allowed" class="operation-logs-page">
    <section class="page-heading">
      <div>
        <h1>操作日志</h1>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="load"
        >刷新</el-button
      >
    </section>
    <section class="panel operation-logs-panel" aria-label="操作日志记录">
      <el-form
        class="operation-log-filters"
        label-position="top"
        @submit.prevent="search"
      >
        <el-form-item label="操作日期"
          ><el-date-picker
            v-model="dates"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            range-separator="至"
            aria-label="操作日期范围"
        /></el-form-item>
        <el-form-item label="操作分类"
          ><el-select
            v-model="category"
            clearable
            aria-label="操作分类"
            placeholder="全部分类"
            ><el-option
              v-for="(label, value) in categories"
              :key="value"
              :label="label"
              :value="value" /></el-select
        ></el-form-item>
        <el-form-item label="操作结果"
          ><el-select
            v-model="result"
            clearable
            aria-label="操作结果"
            placeholder="全部结果"
            ><el-option label="成功" value="success" /><el-option
              label="失败或待核实"
              value="failed" /><el-option
              label="待处理"
              value="pending" /></el-select
        ></el-form-item>
        <el-form-item label="关键词"
          ><el-input
            v-model="keyword"
            clearable
            maxlength="120"
            aria-label="日志关键词"
            placeholder="操作人、会议、任务或操作内容"
        /></el-form-item>
        <div class="operation-log-filter-actions">
          <el-button
            type="primary"
            :icon="Search"
            native-type="submit"
            :loading="loading"
            >查询</el-button
          ><el-button @click="reset">重置</el-button>
        </div>
      </el-form>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        role="alert"
      />
      <div v-loading="loading" class="operation-log-table" :aria-busy="loading">
        <el-table :data="rows" empty-text="暂无符合条件的操作记录" row-key="id">
          <el-table-column label="操作时间（北京时间）" min-width="175"
            ><template #default="{ row }">{{
              beijingTime(row.created_at)
            }}</template></el-table-column
          >
          <el-table-column label="操作人" min-width="125"
            ><template #default="{ row }"
              ><span>{{ row.actor_name }}</span
              ><small
                v-if="row.actor_username"
                class="operation-log-secondary"
                >{{ row.actor_username }}</small
              ></template
            ></el-table-column
          >
          <el-table-column prop="operation" label="操作内容" min-width="155" />
          <el-table-column
            prop="target"
            label="会议 / 任务 / 对象"
            min-width="220"
            show-overflow-tooltip
          />
          <el-table-column label="说明" min-width="260"
            ><template #default="{ row }">
              <span>{{ row.summary }}</span>
              <span v-if="row.sent_at" class="operation-log-secondary"
                >邮件发送：{{ beijingTime(row.sent_at) }}</span
              >
            </template></el-table-column
          >
          <el-table-column label="结果" min-width="105"
            ><template #default="{ row }"
              ><el-tag :type="resultType(row.result)">{{
                resultLabels[row.result] || row.result
              }}</el-tag></template
            ></el-table-column
          >
          <el-table-column label="详情" width="85" fixed="right"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                :aria-label="`查看${row.operation}详情`"
                @click="openDetail(row)"
                >查看</el-button
              ></template
            ></el-table-column
          >
        </el-table>
      </div>
      <div class="operation-log-footer">
        <span class="muted small">共 {{ total }} 条记录</span
        ><el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          :pager-count="5"
          layout="sizes, prev, pager, next"
          @current-change="load"
          @size-change="
            page = 1;
            load();
          "
        />
      </div>
    </section>
    <el-dialog
      v-model="detailVisible"
      title="操作详情"
      width="min(960px, 94vw)"
      class="operation-log-dialog"
      destroy-on-close
    >
      <div
        v-loading="detailLoading"
        class="operation-log-detail"
        :aria-busy="detailLoading"
      >
        <el-alert
          v-if="detailError"
          :title="detailError"
          type="error"
          :closable="false"
          role="alert"
        />
        <template v-if="detail">
          <el-descriptions :column="1" border
            ><el-descriptions-item label="操作时间">{{
              beijingTime(detail.created_at)
            }}</el-descriptions-item
            ><el-descriptions-item label="操作人"
              >{{ detail.actor_name
              }}<span v-if="detail.context.initiated_by"
                >（发起人：{{ detail.context.initiated_by }}）</span
              ></el-descriptions-item
            ><el-descriptions-item label="操作内容">{{
              detail.operation
            }}</el-descriptions-item
            ><el-descriptions-item label="操作对象">{{
              detail.target
            }}</el-descriptions-item
            ><el-descriptions-item label="结果"
              ><el-tag :type="resultType(detail.result)">{{
                resultLabels[detail.result] || detail.result
              }}</el-tag></el-descriptions-item
            ><el-descriptions-item v-if="detail.summary" label="说明">{{
              detail.summary
            }}</el-descriptions-item></el-descriptions
          >
          <section v-if="detail.meeting_record" class="operation-log-section">
            <h2>会议创建信息</h2>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="创建时间">{{
                beijingTime(detail.meeting_record.created_at)
              }}</el-descriptions-item>
              <el-descriptions-item label="创建人">{{
                detail.meeting_record.created_by
              }}</el-descriptions-item>
              <el-descriptions-item
                v-if="detail.meeting_record.meeting_date"
                label="会议召开日期"
                >{{ detail.meeting_record.meeting_date }}</el-descriptions-item
              >
            </el-descriptions>
          </section>
          <section v-if="detail.filters?.length" class="operation-log-section">
            <h2>查询条件</h2>
            <el-descriptions :column="1" border
              ><el-descriptions-item
                v-for="filter in detail.filters"
                :key="filter.field"
                :label="filter.field"
                >{{ filter.value }}</el-descriptions-item
              ></el-descriptions
            >
          </section>
          <section
            v-if="detail.deliveries?.length"
            class="operation-log-section"
          >
            <h2>通知发送情况</h2>
            <div
              v-for="(delivery, i) in detail.deliveries"
              :key="delivery.event_id || i"
              class="operation-log-delivery"
            >
              <div class="operation-log-delivery-heading">
                <strong>{{
                  channelLabels[delivery.channel] || delivery.channel
                }}</strong
                ><el-tag :type="resultType(delivery.state)">{{
                  resultLabels[delivery.state] || delivery.state
                }}</el-tag>
              </div>
              <dl>
                <template v-if="delivery.sent_at"
                  ><dt>发送时间</dt>
                  <dd>{{ beijingTime(delivery.sent_at) }}</dd></template
                >
                <dt>
                  {{
                    delivery.actual_recorded ? "实际收件人" : "计划收件人"
                  }}（{{ delivery.to?.length || 0 }}）
                </dt>
                <dd>{{ recipientText(delivery.to) }}</dd>
                <dt v-if="delivery.channel === 'email'">
                  {{
                    delivery.actual_recorded ? "实际抄送人" : "计划抄送人"
                  }}（{{ delivery.cc?.length || 0 }}）
                </dt>
                <dd v-if="delivery.channel === 'email'">
                  {{ recipientText(delivery.cc) }}
                </dd>
                <template v-if="delivery.error"
                  ><dt>发送说明</dt>
                  <dd>{{ delivery.error }}</dd></template
                >
              </dl>
              <details
                v-if="delivery.history?.length"
                class="operation-log-attempts"
              >
                <summary>发送记录（{{ delivery.history.length }} 次）</summary>
                <div
                  v-for="(attempt, index) in delivery.history || []"
                  :key="index"
                  class="operation-log-attempt"
                >
                  <span
                    >{{ beijingTime(attempt.created_at) }} ·
                    {{ resultLabels[attempt.state] || attempt.state }}</span
                  >
                  <p v-if="attempt.actual_to?.length">
                    实际收件邮箱：{{ attempt.actual_to.join("；") }}
                  </p>
                  <p v-if="attempt.actual_cc?.length">
                    实际抄送邮箱：{{ attempt.actual_cc.join("；") }}
                  </p>
                  <p v-if="attempt.error">{{ attempt.error }}</p>
                </div>
              </details>
              <p v-if="delivery.historical" class="muted small">
                未保存实际发送邮箱；计划名单按当前通讯录显示。
              </p>
            </div>
            <p class="muted small">
              “已发送”表示发送服务已接受，不代表收件人已阅读。
            </p>
          </section>
          <section v-if="detail.changes?.length" class="operation-log-section">
            <h2>记录内容 / 修改前后</h2>
            <el-table :data="detail.changes"
              ><el-table-column
                prop="field"
                label="项目"
                min-width="120"
              /><el-table-column prop="before" label="修改前" min-width="220"
                ><template #default="{ row }"
                  ><span class="operation-log-value">{{
                    row.before
                  }}</span></template
                ></el-table-column
              ><el-table-column
                prop="after"
                label="修改后 / 本次记录"
                min-width="220"
                ><template #default="{ row }"
                  ><span class="operation-log-value">{{
                    row.after
                  }}</span></template
                ></el-table-column
              ></el-table
            >
          </section>
        </template>
      </div>
      <template #footer
        ><el-button @click="detailVisible = false">关闭</el-button></template
      >
    </el-dialog>
  </div>
  <section v-else class="panel operation-logs-denied" role="alert">
    <h1>无权查看操作日志</h1>
    <p>此页面仅限 admin 账号查看。</p>
    <RouterLink to="/meetings"><el-button>返回会议页面</el-button></RouterLink>
  </section>
</template>

<style scoped>
.operation-logs-page {
  max-width: 1600px;
  margin: 0 auto;
}
.page-heading {
  align-items: center;
  gap: 16px;
}
.page-heading h1 {
  margin: 0;
  font-size: 26px;
}
.page-heading p {
  margin: 8px 0 0;
  font-size: 14px;
}
.operation-logs-panel {
  padding: 20px;
}
.operation-log-filters {
  display: grid;
  grid-template-columns:
    minmax(250px, 1.4fr) minmax(130px, 0.7fr) minmax(130px, 0.7fr)
    minmax(220px, 1.2fr) auto;
  align-items: end;
  gap: 16px;
}
.operation-log-filters .el-form-item {
  margin: 0 0 20px;
  min-width: 0;
}
.operation-log-filters :deep(.el-date-editor) {
  width: 100%;
  min-width: 0;
}
.operation-log-filter-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}
.operation-log-filter-actions .el-button + .el-button {
  margin-left: 0;
}
.operation-log-table {
  min-height: 240px;
}
.operation-log-secondary {
  display: block;
  color: var(--muted);
  margin-top: 3px;
}
.operation-log-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 20px;
}
.operation-log-detail {
  min-height: 160px;
  overflow-wrap: anywhere;
}
.operation-log-section {
  margin-top: 24px;
}
.operation-log-section h2 {
  margin: 0 0 12px;
  font-size: 16px;
}
.operation-log-delivery {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.operation-log-delivery-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.operation-log-delivery dl {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 8px 12px;
  margin: 12px 0 0;
}
.operation-log-delivery dt {
  color: var(--muted);
}
.operation-log-attempt {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  font-size: 13px;
}
.operation-log-attempts {
  margin-top: 12px;
}
.operation-log-attempts summary {
  cursor: pointer;
}
.operation-log-attempt p {
  margin: 4px 0 0;
}
.operation-log-delivery dd {
  margin: 0;
  user-select: text;
}
.operation-log-value {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  user-select: text;
}
.operation-logs-denied {
  padding: 24px;
}
@media (min-width: 768px) and (max-width: 1440px) {
  .page-heading h1 {
    font-size: 22px;
  }
  .page-heading p {
    margin-top: 4px;
    font-size: 13px;
  }
  .operation-logs-panel {
    padding: 14px 16px;
  }
  .operation-log-filters {
    grid-template-columns:
      minmax(240px, 1.4fr) repeat(2, minmax(100px, 0.65fr))
      minmax(180px, 1fr) auto;
    gap: 10px;
  }
  .operation-log-filters :deep(.el-form-item) {
    margin-bottom: 10px;
  }
}
@media (min-width: 681px) and (max-width: 1000px) {
  .operation-log-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 680px) {
  .operation-logs-panel {
    padding: 14px;
  }
  .page-heading h1 {
    font-size: 23px;
  }
  .operation-log-filters {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }
  .operation-log-filters .el-form-item {
    margin-bottom: 14px;
  }
  .operation-log-filter-actions {
    margin-bottom: 16px;
  }
  .operation-log-footer :deep(.el-pagination__sizes) {
    display: none;
  }
  .operation-log-footer {
    gap: 12px;
  }
}
</style>
