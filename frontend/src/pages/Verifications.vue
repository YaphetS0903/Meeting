<script setup lang="ts">
import { ref, onMounted, nextTick } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, run, person, download } from "../api";
const route = useRoute();
const requested =
  typeof route.query.request === "string" ? route.query.request : null;
const missingRequested = ref(false);
const rows = ref<any[]>([]),
  busy = ref(false),
  pending = ref(true),
  working = ref("");
async function load(background = false) {
  busy.value = true;
  try {
    rows.value = await api("/verifications", "GET", undefined, { background });
    if (requested) {
      const target = rows.value.find((row) => row.id === requested);
      missingRequested.value = !target;
      if (target && target.state !== "pending") pending.value = false;
      await nextTick();
      document
        .getElementById("verification-" + requested)
        ?.scrollIntoView({ block: "center" });
    }
  } finally {
    busy.value = false;
  }
}
async function verify(r: any, decision: string) {
  const { value } = await ElMessageBox.prompt(
    decision === "approved"
      ? "验证通过后，将统一关闭所有共同责任人的待办。请填写验收意见。"
      : "请说明未通过原因。任务将继续执行。",
    decision === "approved" ? "验证通过" : "驳回申请",
    {
      inputType: "textarea",
      inputValidator: (v) => !!v?.trim() || "请填写验证意见",
    },
  );
  working.value = r.id;
  try {
    await api("/verifications/" + r.id, "POST", { decision, opinion: value });
    ElMessage.success("验证结果已保存");
    await load(true);
  } finally {
    working.value = "";
  }
}
onMounted(() => run(load));
</script>
<template>
  <section class="page-heading">
    <div>
      <h1>待我验证</h1>
    </div>
    <el-switch v-model="pending" active-text="只看待验证" />
  </section>
  <el-alert
    v-if="missingRequested"
    title="消息中的申请当前不在您的验证范围，请查看当前待验证清单或联系会议负责人。"
    type="info"
    :closable="false"
  />
  <div v-loading="busy">
    <article
      v-for="r in rows.filter((x) => !pending || x.state === 'pending')"
      :key="r.id"
      :id="'verification-' + r.id"
      class="panel verification-card"
    >
      <div class="panel-heading">
        <h2>{{ r.task?.content || r.task_code }}</h2>
        <el-tag>{{
          {
            pending: "待验证",
            approved: "已通过",
            rejected: "已驳回",
            invalidated: "已失效",
          }[r.state]
        }}</el-tag>
      </div>
      <p v-if="r.task" class="muted">
        申请人 {{ r.applicant_name }} · 责任人
        {{ r.task.owner_ids.map(person).join("、") }} · 截止
        {{ r.task.due_date || "未设期限" }}
      </p>
      <p class="muted">
        申请对应任务版本：V{{ r.task_version }} ·
        <RouterLink
          v-if="r.source_meeting_id"
          :to="'/meetings/' + r.source_meeting_id"
          >{{ r.source_meeting_title }}</RouterLink
        >
        <span v-else>{{ r.source_meeting_title }}</span>
      </p>
      <el-descriptions v-if="r.task" :column="1" border>
        <el-descriptions-item label="输出物"
          ><span class="work-content">{{
            r.task.deliverable || "未指定"
          }}</span></el-descriptions-item
        >
      </el-descriptions>
      <el-alert
        v-else
        title="申请时的任务版本记录缺失，请联系管理员核查。"
        type="error"
        :closable="false"
      />
      <el-alert
        v-if="r.state === 'pending' && !r.can_verify"
        title="任务已变化，此申请不能继续验证，请刷新或联系会议负责人。"
        type="warning"
        :closable="false"
      />
      <blockquote>{{ r.explanation }}</blockquote>
      <el-button
        v-for="id in r.evidence_ids"
        :key="id"
        link
        type="primary"
        @click="run(() => download('/files/' + id, '完成证据'))"
        >下载完成证据</el-button
      >
      <p v-if="r.opinion">验证意见：{{ r.opinion }}</p>
      <div v-if="r.can_verify" class="actions">
        <el-button
          :loading="working === r.id"
          @click="run(() => verify(r, 'rejected'))"
          >驳回</el-button
        ><el-button
          type="primary"
          :loading="working === r.id"
          @click="run(() => verify(r, 'approved'))"
          >验证通过并关闭</el-button
        >
      </div>
    </article>
    <el-empty
      v-if="!busy && !rows.some((x) => !pending || x.state === 'pending')"
      description="没有待处理的验证申请"
    />
  </div>
</template>
