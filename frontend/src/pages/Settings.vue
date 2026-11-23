<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { beijingTime } from "../time";
import { api, state, run, loadSession } from "../api";
import Employees from "./Employees.vue";
import RoleManagement from "../components/RoleManagement.vue";
const reminders = ref<any>(null);
const nativeTodo = ref<any>(null);
const nativeAuthVisible = ref(false);
const nativeAuthQr = ref("");
const nativeMemberSearch = ref("");
const nativeMemberPage = ref(1);
const nativeMembers = computed(() => {
  const query = nativeMemberSearch.value.trim().toLowerCase();
  return (nativeTodo.value?.allowed_users || []).filter((member: any) =>
    `${member.name} ${member.email || ""}`.toLowerCase().includes(query),
  );
});
const nativeMemberRows = computed(() =>
  nativeMembers.value.slice(
    (nativeMemberPage.value - 1) * 10,
    nativeMemberPage.value * 10,
  ),
);
const router = useRouter();
const reassignments = ref<any[]>([]);
const operations = ref<any>(null);
const syncHistory = ref<any[]>([]);
const syncHistoryOpen = ref(false);
const syncHistoryLoading = ref(false);
const syncHistoryLoaded = ref(false);
const syncHistoryError = ref("");
async function loadSyncHistory(background = false) {
  if (syncHistoryLoading.value) return;
  syncHistoryLoading.value = true;
  syncHistoryError.value = "";
  try {
    syncHistory.value = await api(
      "/admin/directory-sync-history",
      "GET",
      undefined,
      { background },
    );
    syncHistoryLoaded.value = true;
  } catch (error: any) {
    syncHistoryError.value = error.message || "同步记录加载失败，请重试";
  } finally {
    syncHistoryLoading.value = false;
  }
}
function toggleSyncHistory(event: Event) {
  syncHistoryOpen.value = (event.currentTarget as HTMLDetailsElement).open;
  if (
    syncHistoryOpen.value &&
    !syncHistoryLoaded.value &&
    !syncHistoryLoading.value
  )
    void loadSyncHistory();
}
const connectionChecks = ref<any>({}),
  checking = ref("");
async function checkConnection(target: string) {
  checking.value = target;
  try {
    const result = await api("/admin/integration-checks", "POST", { target });
    connectionChecks.value = { ...connectionChecks.value, [target]: result };
  } finally {
    checking.value = "";
  }
}
async function bindNativeTodoUser(member: any) {
  const results = await api("/admin/native-todo/search", "POST", {
    keywords: [member.name, member.email].filter(Boolean),
  });
  const emailMatches = member.email
    ? results.filter(
        (item: any) => item.email?.toLowerCase() === member.email.toLowerCase(),
      )
    : [];
  const nameMatches = results.filter((item: any) => item.name === member.name);
  const candidates = emailMatches.length
    ? emailMatches
    : nameMatches.filter((item: any) => !member.email || !item.email);
  if (candidates.length !== 1)
    throw new Error(
      candidates.length
        ? `搜索到 ${candidates.length} 个同名成员，暂不能自动绑定`
        : "企业微信原生待办通讯录中没有找到该成员",
    );
  await api(`/admin/native-todo/users/${member.id}/bind`, "POST", {
    native_todo_userid: candidates[0].userid,
    reason: "管理员核对姓名及邮箱后绑定原生待办成员",
  });
  ElMessage.success(`${member.name}的原生待办成员已绑定`);
  await load(true);
}
async function startNativeAuthorization() {
  const result = await api("/admin/native-todo/auth/start", "POST");
  if (result.state === "permission_required") {
    await load(true);
    ElMessage.warning("请打开下方企业微信授权链接，恢复通讯录和待办权限");
    return;
  }
  if (result.authorized) {
    ElMessage.success("原生待办扫码授权已经有效");
    await load(true);
    return;
  }
  const response = await fetch("/api/admin/native-todo/auth/qrcode", {
    headers: {
      Authorization: `Bearer ${sessionStorage.getItem("token") || ""}`,
    },
    cache: "no-store",
  });
  if (!response.ok) throw new Error("授权二维码读取失败，请重新生成");
  if (nativeAuthQr.value) URL.revokeObjectURL(nativeAuthQr.value);
  nativeAuthQr.value = URL.createObjectURL(await response.blob());
  nativeAuthVisible.value = true;
}
async function refreshNativeAuthorization() {
  await load();
  if (nativeTodo.value?.authorized) {
    nativeAuthVisible.value = false;
    if (nativeAuthQr.value) URL.revokeObjectURL(nativeAuthQr.value);
    nativeAuthQr.value = "";
    const result = await api("/admin/native-todo/resume", "POST");
    await load(true);
    ElMessage.success(`授权有效，已恢复 ${result.queued} 条待办同步`);
  } else {
    ElMessage.warning("企业微信权限尚未恢复，请完成下方授权后再刷新");
  }
}
const deliveryLabels: Record<string, string> = {
  not_started: "监控尚未启动",
  not_configured: "告警接收端未配置",
  idle: "无需发送新告警",
  pending: "等待告警接收确认",
  sent: "告警已获接收确认",
  failed: "告警发送失败，将重试",
};
async function saveReminders() {
  const result = await api("/admin/reminders", "PUT", reminders.value);
  const { cancelled_events: cancelled, ...saved } = result;
  reminders.value = saved;
  ElMessage.success(
    `提醒规则已保存${cancelled ? `，已取消 ${cancelled} 条未发送通知` : ""}`,
  );
  await load(true);
}
async function resolve(e: any, delivered: boolean) {
  const { value } = await ElMessageBox.prompt(
    "请填写已核实的外部发送记录或未送达依据（至少五个字）",
    "核实送达结果",
    { inputValidator: (v) => v?.length >= 5 || "请填写核实依据" },
  );
  await api("/admin/notifications/" + e.id + "/resolve", "POST", {
    delivered,
    reason: value,
  });
  await load(true);
}
const tab = ref("integrations"),
  config = ref<any>(null),
  events = ref<any[]>([]),
  busy = ref(false);
async function load(background = false) {
  [
    config.value,
    events.value,
    reminders.value,
    reassignments.value,
    operations.value,
    connectionChecks.value,
    nativeTodo.value,
  ] = await Promise.all([
    api("/admin/config", "GET", undefined, { background }),
    api("/admin/notifications", "GET", undefined, { background: true }),
    api("/admin/reminders", "GET", undefined, { background: true }),
    api("/admin/reassignment", "GET", undefined, { background: true }),
    api("/admin/operations", "GET", undefined, { background: true }),
    api("/admin/integration-checks", "GET", undefined, { background: true }),
    api("/admin/native-todo", "GET", undefined, { background: true }),
  ]);
  if (syncHistoryOpen.value) await loadSyncHistory(true);
  else syncHistoryLoaded.value = false;
}
async function sync() {
  busy.value = true;
  try {
    const r = await api("/admin/wecom/sync", "POST");
    ElMessage.success(
      `已同步 ${r.users} 名成员、${r.departments} 个部门；有手机号 ${r.directory_mobile_users} 人，可派发任务的人员 ${r.direct_assignment_leaders} 人`,
    );
    await loadSession();
  } finally {
    busy.value = false;
    await load(true);
  }
}
async function cancelNotification(event: any) {
  const { value } = await ElMessageBox.prompt(
    "请说明取消原因。取消后不会再次发送，原通知和尝试记录会保留。",
    "取消通知",
    {
      inputValidator: (value: string) => !!value?.trim() || "请填写取消原因",
      confirmButtonText: "确认取消",
      cancelButtonText: "返回",
    },
  );
  await api("/admin/notifications/" + event.id + "/cancel", "POST", {
    reason: value.trim(),
  });
  await load();
}
async function retry(e: any) {
  await api("/admin/notifications/" + e.id + "/retry", "POST", {
    reason: "管理员在通知记录中发起重试",
  });
  await load(true);
}
onMounted(() => run(load));
</script>
<template>
  <section class="page-heading">
    <div>
      <h1>系统管理</h1>
    </div>
    <el-button @click="run(load)">刷新</el-button>
  </section>
  <el-tabs v-model="tab"
    ><el-tab-pane label="运行监控与告警" name="operations">
      <section class="panel section-body">
        <h2>独立监控</h2>
        <el-alert
          :title="
            operations?.running
              ? '监控进程持续运行'
              : '监控心跳缺失或已过期，请检查 monitor 服务'
          "
          :type="operations?.running ? 'success' : 'error'"
          :closable="false"
          show-icon
        />
        <p class="muted">
          最近检查：{{
            operations?.checked_at
              ? beijingTime(operations.checked_at)
              : "尚无记录"
          }}（北京时间）。每
          {{ operations?.interval_seconds || 60 }}
          秒检查，点击页面右上方刷新查看最新结果。
        </p>
        <el-alert
          v-if="!operations?.alert_configured"
          title="外部告警尚未配置。当前仅记录本机检查结果，上线前需配置企业告警接收端并验证送达。"
          type="warning"
          :closable="false"
        />
        <p>
          告警状态：{{ deliveryLabels[operations?.delivery] || "尚无记录" }}
        </p>
        <p v-if="operations?.alert_configured" class="muted">
          告警通道：{{
            operations.alert_channel === "email"
              ? "指定收件人邮件"
              : "企业 Webhook"
          }}
        </p>
        <p v-if="operations?.last_error" class="error-text">
          {{ operations.last_error }}
        </p>
        <p v-if="operations?.last_sent_at" class="muted">
          最近收到告警接收确认：{{ beijingTime(operations.last_sent_at) }}
        </p>
        <el-table
          :data="operations?.snapshot?.checks || []"
          empty-text="等待独立监控首次检查"
        >
          <el-table-column prop="label" label="检查项" min-width="170" />
          <el-table-column label="结果" width="120"
            ><template #default="{ row }"
              ><el-tag :type="row.healthy ? 'success' : 'danger'">{{
                row.healthy ? "正常" : "需处理"
              }}</el-tag></template
            ></el-table-column
          >
          <el-table-column prop="detail" label="详情" min-width="350" />
        </el-table>
        <p class="muted small">
          备份检查核对文件存在及时间，不代替附件哈希验证和恢复演练。调度与工作进程停止后仍可被独立监控发现；整台服务器故障需要企业外部监控。
        </p>
      </section> </el-tab-pane
    ><el-tab-pane label="企业微信与邮件" name="integrations"
      ><div class="two-columns">
        <section class="panel section-body">
          <h2>企业微信自建应用</h2>
          <el-tag :type="config?.wecom_configured ? 'success' : 'info'">{{
            config?.wecom_configured ? "已配置，待实际联调" : "待配置企业应用"
          }}</el-tag>
          <p>
            从企业微信同步成员和部门，以成员标识绑定身份。同步后可在员工通讯录查看新增人员及停用账号。
          </p>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="!config?.wecom_configured"
            @click="run(sync)"
            >同步通讯录</el-button
          >
          <p class="muted small">
            直接任务的应用消息独立控制；下方成员提醒开关用于会议任务和纪要邮件。
          </p>
        </section>
        <section class="panel section-body">
          <h2>企业微信消息与原生待办</h2>
          <div class="actions">
            <el-tag
              :type="nativeTodo?.direct_messages_enabled ? 'success' : 'info'"
              >应用消息：{{
                nativeTodo?.direct_messages_enabled ? "已开启" : "未开启"
              }}</el-tag
            >
            <el-tag
              :type="
                nativeTodo?.app_enabled && nativeTodo?.direct_enabled
                  ? 'success'
                  : 'info'
              "
              >自建应用原生待办：{{
                nativeTodo?.app_enabled && nativeTodo?.direct_enabled
                  ? "已开启"
                  : "未开启"
              }}</el-tag
            >
            <el-tag :type="nativeTodo?.meeting_enabled ? 'success' : 'info'"
              >会议待办：{{
                nativeTodo?.meeting_enabled ? "已开启" : "未开启"
              }}</el-tag
            >
          </div>
          <p>
            开启对应通知开关后，直接派发任务会排队发送应用消息。开启原生待办并绑定本人企微身份后，可在企微内确认创建待办；取消确认不影响系统任务。实际消息送达情况以通知记录为准，责任人也可登录系统处理任务。
          </p>
          <p class="muted small">
            自建应用凭证由服务器自动刷新，无需机器人每周续授权。截止日期可留空；系统调整内容或日期后原生待办会提示差异，应用消息显示最新安排。原生待办需逐次确认创建。
          </p>
          <p>
            会议发布时勾选“发布后派发企微待办”，发布者可在企业微信内逐项确认，每项任务发送给该项全部责任人。此流程使用自建应用，无需下方旧机器人授权；责任人须为已绑定企业微信的在用员工。已有待办会跳过，取消确认后可从会议详情继续。
          </p>
          <p class="muted small">
            本应用原生待办：{{
              nativeTodo?.app_links || 0
            }}；原机器人活动待办：{{
              nativeTodo?.legacy_links || 0
            }}。原生待办完成不代替系统的完成申请与验证。
          </p>
          <el-collapse>
            <el-collapse-item
              title="旧机器人待办 · 授权和成员（保留）"
              name="legacy"
            >
              <p class="muted small">
                此模块保留，用于处理已有机器人待办，授权过期时需续授权。新会议的企微确认派发与直接派发任务使用自建应用通道，不依赖此授权。
              </p>
              <div class="actions">
                <el-tag
                  :type="nativeTodo?.cli_available ? 'success' : 'danger'"
                >
                  {{ nativeTodo?.cli_available ? "组件已安装" : "组件不可用" }}
                </el-tag>
                <el-tag :type="nativeTodo?.authorized ? 'success' : 'warning'">
                  {{
                    nativeTodo?.authorized
                      ? "企业微信权限有效"
                      : "企业微信待授权"
                  }}
                </el-tag>
                <el-tag :type="nativeTodo?.direct_enabled ? 'success' : 'info'">
                  直接派发：{{
                    nativeTodo?.direct_enabled ? "已开启" : "未开启"
                  }}
                </el-tag>
                <el-tag
                  :type="nativeTodo?.meeting_enabled ? 'success' : 'info'"
                >
                  会议任务：{{
                    nativeTodo?.meeting_enabled ? "已开启" : "未开启"
                  }}
                </el-tag>
              </div>
              <p>
                原生待办仅作为系统任务的同步副本。成员仍需在本系统提交已完成申请，验证通过后系统才会整体完成原生待办。
              </p>
              <el-alert
                v-for="permission in (nativeTodo?.permissions || []).filter(
                  (item: any) => !item.authorized,
                )"
                :key="permission.scope"
                :title="permission.error || `${permission.scope}权限尚未就绪`"
                type="warning"
                :closable="false"
                show-icon
              >
                <el-link
                  v-if="permission.authorization_url"
                  :href="permission.authorization_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  type="primary"
                >
                  在企业微信中打开，授权{{ permission.scope }}权限
                </el-link>
                <span v-else>请检查企业微信机器人授权或重新扫码。</span>
              </el-alert>
              <div class="actions">
                <el-button
                  :disabled="!nativeTodo?.cli_available"
                  @click="run(startNativeAuthorization)"
                  >{{
                    nativeTodo?.authorized ? "重新检查授权" : "生成授权二维码"
                  }}</el-button
                >
                <el-button @click="run(refreshNativeAuthorization)"
                  >刷新授权并恢复同步</el-button
                >
              </div>
              <p class="muted small">
                {{
                  nativeTodo?.all_active_users
                    ? "同步范围：全部在用企业微信员工，首次派发时自动匹配成员。"
                    : "同步范围：服务器配置的指定成员。"
                }}
                已绑定 {{ nativeTodo?.mapped_users || 0 }} /
                {{ nativeTodo?.allowed_users?.length || 0 }} 人。
                姓名或邮箱无法唯一匹配时暂停同步，请管理员核对。
              </p>
              <el-input
                v-model="nativeMemberSearch"
                placeholder="搜索成员姓名或邮箱"
                clearable
                @input="nativeMemberPage = 1"
              />
              <el-table
                :data="nativeMemberRows"
                empty-text="没有匹配的同步成员"
              >
                <el-table-column prop="name" label="成员" min-width="110" />
                <el-table-column prop="email" label="邮箱" min-width="210" />
                <el-table-column label="成员映射" min-width="150">
                  <template #default="{ row }">
                    <el-tag
                      :type="row.native_todo_userid ? 'success' : 'warning'"
                    >
                      {{ row.native_todo_userid ? "已绑定" : "待绑定" }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="130">
                  <template #default="{ row }">
                    <el-button
                      link
                      type="primary"
                      :disabled="!nativeTodo?.authorized"
                      @click="run(() => bindNativeTodoUser(row))"
                      >{{
                        row.native_todo_userid ? "重新核对" : "查找并绑定"
                      }}</el-button
                    >
                  </template>
                </el-table-column>
              </el-table>
              <el-pagination
                v-model:current-page="nativeMemberPage"
                :page-size="10"
                :total="nativeMembers.length"
                layout="prev, pager, next, total"
              />
              <p class="muted small">
                此成员映射用于原机器人待办。当前活动原生待办映射：{{
                  nativeTodo?.active_links || 0
                }}。旧待办验证通过后按原通道完成。
              </p>
            </el-collapse-item>
          </el-collapse>
        </section>
        <section class="panel section-body">
          <h2>纪要邮件</h2>
          <el-tag :type="config?.smtp_configured ? 'success' : 'info'">{{
            config?.smtp_configured ? "SMTP 已配置" : "SMTP 待配置"
          }}</el-tag>
          <p>
            发布前确认收件人与抄送人，邮件附件包含完整纪要图片及 Excel 任务单。
          </p>
          <p class="muted small">
            凭据通过服务器环境配置管理，页面不读取或展示密钥。
          </p>
        </section>
      </div>
      <section class="panel section-body">
        <h2>历史数据 AI 识别</h2>
        <el-tag :type="config?.ai_configured ? 'success' : 'info'">{{
          config?.ai_configured
            ? `已配置 · ${config.ai_model}`
            : "OpenAI 兼容接口待配置"
        }}</el-tag>
        <p>
          用于分析历史纪要中的统计分区、非标准状态、文字期限和未匹配姓名。姓名建议只允许从有效企业微信通讯录的少量候选中选择，不上传整份原文件。
        </p>
        <p class="muted small">
          AI
          结果不会自动保存、确认入库或修改报表；管理员必须对照原文应用建议并填写确认依据。Base
          URL、API Key 和模型仅通过服务器环境变量管理，页面不展示密钥。
        </p>
      </section>
      <section class="panel section-body">
        <h2>企业连接检查</h2>
        <p class="muted">
          检查应用访问、部门读取和 SMTP
          连接；不会同步人员、发送消息或邮件。连接通过后仍需真实账号与收件人验证送达。
        </p>
        <div class="actions">
          <el-button
            :loading="checking === 'wecom'"
            :disabled="!!checking && checking !== 'wecom'"
            @click="run(() => checkConnection('wecom'))"
            >检查企业微信连接</el-button
          >
          <el-button
            :loading="checking === 'smtp'"
            :disabled="!!checking && checking !== 'smtp'"
            @click="run(() => checkConnection('smtp'))"
            >检查 SMTP 连接</el-button
          >
        </div>
        <article
          v-for="target in ['wecom', 'smtp'].filter(
            (target) => connectionChecks[target],
          )"
          :key="target"
          class="section-body"
        >
          <h3>{{ target === "wecom" ? "企业微信" : "SMTP" }}检查</h3>
          <template v-if="connectionChecks[target]">
            <p class="muted small">
              最近检查：{{
                beijingTime(connectionChecks[target].checked_at)
              }}（北京时间）。配置变更后请重新检查。
            </p>
            <el-alert
              v-for="item in connectionChecks[target].checks"
              :key="item.label"
              :title="item.label + '：' + item.message"
              :type="
                item.state === 'ready'
                  ? 'success'
                  : item.state === 'failed'
                    ? 'error'
                    : 'warning'
              "
              :closable="false"
            />
          </template>
        </article>
      </section>
      <section class="panel section-body">
        <h2>提醒规则</h2>
        <el-alert
          v-if="reminders && !reminders.enabled"
          title="会议提醒及纪要邮件已关闭：关闭期间的会议通知直接跳过，不会补发。直接任务应用消息按服务器独立开关发送。"
          :closable="false"
          type="warning"
          show-icon
        />
        <el-alert
          v-else-if="reminders"
          title="会议发布通知已开启：按发布时选择的名单发送企业微信通知和纪要邮件。"
          :closable="false"
          type="success"
          show-icon
        />
        <el-form v-if="reminders" label-position="top" class="meta-grid"
          ><el-form-item class="span-2" label="会议提醒及纪要邮件开关"
            ><el-switch
              v-model="reminders.enabled"
              active-text="允许发送"
              inactive-text="暂停会议及邮件" /></el-form-item
          ><el-form-item class="span-2" label="任务截止日期提醒"
            ><el-switch
              v-model="reminders.deadline_enabled"
              active-text="上午9点按规则提醒"
              inactive-text="关闭"
            />
            <p class="muted small">
              发布后次日到期：到期当天提醒；两天后到期：前一天和当天各提醒一次；三天及以上到期：前两天提醒一次。适用于会议和直接派发任务。
            </p> </el-form-item
          ><el-form-item class="span-2" label="每日任务提醒"
            ><el-switch
              v-model="reminders.daily_enabled"
              active-text="开启"
              inactive-text="关闭" /></el-form-item
          ><el-form-item
            v-if="reminders.daily_enabled"
            label="每日提醒时间（小时，北京时间）"
            ><el-input-number
              v-model="reminders.hour"
              :min="0"
              :max="23" /></el-form-item
          ><el-form-item v-if="reminders.daily_enabled" label="提前几天提醒"
            ><el-input-number
              v-model="reminders.before_days"
              :min="0"
              :max="30" /></el-form-item
          ><el-form-item
            v-if="reminders.daily_enabled"
            label="申请超过几小时提醒验证人"
            ><el-input-number
              v-model="reminders.verification_hours"
              :min="1"
              :max="720" /></el-form-item
          ><el-form-item v-if="reminders.daily_enabled" label="逾期每日汇总"
            ><el-switch v-model="reminders.overdue_enabled" /></el-form-item
          ><el-button type="primary" @click="run(saveReminders)"
            >保存提醒规则</el-button
          ></el-form
        ><el-alert
          title="每日任务提醒与任务截止日期提醒独立控制。关闭每日任务提醒后，不发送逾期和待验证的每日汇总；截止日期提醒仍按上方规则执行，已完成、关闭、撤回及不统计的任务不再提醒。"
          :closable="false"
          type="info"
        /></section></el-tab-pane
    ><el-tab-pane label="通讯录同步" name="directory"
      ><section class="panel section-body">
        <details class="sync-history" @toggle="toggleSyncHistory">
          <summary>
            <h2>通讯录同步记录</h2>
            <span class="muted small">{{
              syncHistoryOpen ? "收起记录" : "展开查看"
            }}</span>
          </summary>
          <p class="muted">
            每小时自动同步企业微信人员及部门；新增人员自动加入，离职或停用人员不再供新任务选择。记录按时间倒序显示，也可点击“同步通讯录”立即更新。
          </p>
          <div v-if="syncHistoryError" class="sync-history-error" role="alert">
            <p>{{ syncHistoryError }}</p>
            <el-button :loading="syncHistoryLoading" @click="loadSyncHistory()"
              >重新加载</el-button
            >
          </div>
          <el-table
            v-if="syncHistoryOpen && !syncHistoryError"
            v-loading="syncHistoryLoading"
            :data="syncHistory"
            :max-height="420"
            empty-text="尚未执行企业通讯录同步"
          >
            <el-table-column label="时间（北京时间）" min-width="190"
              ><template #default="{ row }">{{
                beijingTime(row.created_at)
              }}</template></el-table-column
            >
            <el-table-column prop="actor_name" label="操作人" width="120" />
            <el-table-column label="结果" width="110"
              ><template #default="{ row }"
                ><el-tag
                  :type="row.state === 'succeeded' ? 'success' : 'danger'"
                  >{{ row.state === "succeeded" ? "成功" : "失败" }}</el-tag
                ></template
              ></el-table-column
            >
            <el-table-column label="说明" min-width="280"
              ><template #default="{ row }">{{
                row.state === "succeeded"
                  ? `${row.result.users} 名成员、${row.result.departments} 个部门`
                  : row.result.error
              }}</template></el-table-column
            >
          </el-table>
        </details>
      </section>
      <section class="panel section-body" v-if="reassignments.length">
        <h2>离职／停用人员任务待重新指派（{{ reassignments.length }}）</h2>
        <p class="muted">
          历史纪要保留原人员；请调整尚未关闭任务的责任人和验证人。
        </p>
        <el-table :data="reassignments">
          <el-table-column prop="content" label="任务" min-width="220" />
          <el-table-column prop="due_date" label="有效期限" width="130" />
          <el-table-column label="停用责任人" min-width="140"
            ><template #default="{ row }">{{
              row.inactive_owners.join("、") || "—"
            }}</template></el-table-column
          >
          <el-table-column label="停用验证人" min-width="140"
            ><template #default="{ row }">{{
              row.inactive_verifier || "—"
            }}</template></el-table-column
          >
          <el-table-column label="处理" width="100"
            ><template #default="{ row }"
              ><el-button
                link
                type="primary"
                @click="router.push({ path: '/tasks', query: { id: row.id } })"
                >调整任务</el-button
              ></template
            ></el-table-column
          >
        </el-table>
      </section> </el-tab-pane
    ><el-tab-pane label="员工通讯录" name="employees" lazy>
      <Employees v-if="tab === 'employees'" embedded /> </el-tab-pane
    ><el-tab-pane label="角色管理" name="roles" lazy>
      <RoleManagement v-if="tab === 'roles'" /> </el-tab-pane
    ><el-tab-pane label="通知与失败重试" name="notifications"
      ><section class="panel">
        <el-table :data="events"
          ><el-table-column label="创建时间（北京时间）" min-width="180"
            ><template #default="{ row }">{{
              beijingTime(row.created_at)
            }}</template></el-table-column
          ><el-table-column
            prop="channel"
            label="渠道"
            width="100"
          /><el-table-column label="事件" min-width="250"
            ><template #default="{ row }">{{
              row.payload.title || row.event_key
            }}</template></el-table-column
          ><el-table-column
            prop="state"
            label="状态"
            width="110"
          /><el-table-column
            prop="attempts"
            label="尝试次数"
            width="90"
          /><el-table-column
            prop="last_error"
            label="原因"
            min-width="230"
          /><el-table-column label="操作" width="150"
            ><template #default="{ row }"
              ><el-button
                v-if="['failed', 'blocked'].includes(row.state)"
                link
                type="primary"
                @click="run(() => retry(row))"
                >重试</el-button
              ><el-button
                v-if="['pending', 'blocked', 'failed'].includes(row.state)"
                link
                type="danger"
                @click="run(() => cancelNotification(row))"
                >取消通知</el-button
              ><template v-if="row.state === 'uncertain'"
                ><el-button link @click="run(() => resolve(row, true))"
                  >已核实送达</el-button
                ><el-button link @click="run(() => resolve(row, false))"
                  >已核实未送达</el-button
                ></template
              ></template
            ></el-table-column
          ></el-table
        >
      </section></el-tab-pane
    ></el-tabs
  >
  <el-dialog
    v-model="nativeAuthVisible"
    title="授权企业微信原生待办"
    width="min(92vw, 420px)"
  >
    <div class="native-auth-dialog">
      <img
        v-if="nativeAuthQr"
        :src="nativeAuthQr"
        alt="企业微信原生待办授权二维码"
      />
      <p>
        请使用原机器人绑定的企业微信账号扫码并按页面完成授权。二维码约 5
        分钟有效。
      </p>
      <el-alert
        title="扫码只恢复旧机器人授权，不改变发送开关。新会议企微确认派发不依赖此授权。"
        type="info"
        :closable="false"
      />
    </div>
    <template #footer>
      <el-button @click="nativeAuthVisible = false">稍后处理</el-button>
      <el-button type="primary" @click="run(refreshNativeAuthorization)"
        >我已扫码，刷新状态</el-button
      >
    </template>
  </el-dialog>
</template>

<style scoped>
.sync-history > summary {
  display: list-item;
  cursor: pointer;
  padding: 4px 0;
  color: var(--el-color-primary);
}
.sync-history > summary h2 {
  display: inline;
  margin: 0 16px 0 4px;
  color: inherit;
}
.sync-history > summary:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 4px;
}
.sync-history[open] > summary {
  margin-bottom: 16px;
}
.sync-history-error p {
  color: var(--el-color-danger);
}
.native-auth-dialog {
  display: grid;
  justify-items: center;
  gap: 16px;
  text-align: center;
}
.native-auth-dialog img {
  width: min(300px, 80vw);
  aspect-ratio: 1;
  object-fit: contain;
}
</style>
