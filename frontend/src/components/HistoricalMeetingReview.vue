<script setup lang="ts">
import { computed } from "vue";
import {
  Calendar,
  List,
  Memo,
  Tickets,
  UserFilled,
} from "@element-plus/icons-vue";
import {
  departmentPath,
  importIssueMessage,
  person,
  state,
  statuses,
} from "../api";
import PeopleSelect from "./PeopleSelect.vue";
import ReviewPeopleField from "./ReviewPeopleField.vue";
import { mergedWorkContent, updateWorkContent } from "../workContent";

const props = defineProps<{
  selected: any;
  payload: any;
  saving: boolean;
  aiBusy: boolean;
  aiConfigured: boolean;
  aiModel: string;
  aiApplied: number;
  aiFailed: number;
  aiSuggestions: Record<string, any>;
  reason: string;
  issueChecks: string[];
  confirmProblems: string[];
}>();
const emit = defineEmits([
  "close",
  "save",
  "confirm",
  "recognize",
  "download",
  "link",
  "include",
  "new-series",
  "source-issues",
  "update:reason",
  "update:issueChecks",
]);
const readonly = computed(() =>
  ["confirmed", "duplicate"].includes(props.selected.state),
);
const indexedItems = computed(() =>
  (props.payload.items || []).map((item: any, index: number) => ({
    item,
    index,
  })),
);
const review = computed(() =>
  indexedItems.value.filter((entry: any) => entry.item.section === "review"),
);
const arrangements = computed(() =>
  indexedItems.value.filter((entry: any) => entry.item.section === "new"),
);
const unclassified = computed(() =>
  indexedItems.value.filter(
    (entry: any) => !["review", "new"].includes(entry.item.section),
  ),
);
const participants = computed(() => {
  const people = new Map<string, { id: string; name: string; role: string }>();
  const add = (id: string | null, role: string) => {
    if (!id || people.has(id)) return;
    people.set(id, { id, name: person(id), role });
  };
  add(props.payload.host_id, "主持人");
  add(props.payload.recorder_id, "记录人");
  for (const id of props.payload.attendee_ids || []) add(id, "参会人员");
  return [...people.values()];
});
const activeIssues = computed(() =>
  (props.selected.issues || []).filter((issue: any) => !issue.resolved),
);
const conflictIssues = computed(() =>
  activeIssues.value.filter((issue: any) => issue.code === "meeting_conflict"),
);
function hasIssue(codes: string | string[], index?: number) {
  const wanted = Array.isArray(codes) ? codes : [codes];
  return activeIssues.value.some(
    (issue: any) =>
      wanted.includes(issue.code) &&
      (index === undefined || issue.field_path?.startsWith(`items.${index}.`)),
  );
}
function originalItem(index: number) {
  return props.selected.original.items?.[index] || {};
}
function ownerNeedsAttention(index: number) {
  if (!hasIssue(["owners_unmatched", "missing_owners"], index)) return false;
  const originalNames = new Set(
    (originalItem(index).owner_names || []).filter(Boolean),
  );
  const selectedOwners = new Set(props.payload.items?.[index]?.owner_ids || []);
  return originalNames.size
    ? selectedOwners.size < originalNames.size
    : !selectedOwners.size;
}
function meetingKindChanged(value: string) {
  if (value === "special") {
    props.payload.department_id = null;
  }
}
</script>

<template>
  <div class="historical-review-page">
    <section class="page-heading historical-review-heading">
      <div>
        <el-button link class="back-link" @click="emit('close')"
          >← 返回导入列表</el-button
        >
        <p class="eyebrow">EXCEL IMPORT</p>
        <h1>历史会议确认</h1>
        <p class="muted">
          Excel 内容已填入会议模板。浏览整份会议，确认无误后直接入库。
        </p>
      </div>
      <div class="actions">
        <el-button @click="emit('download')">查看原 Excel</el-button>
        <el-button
          v-if="aiConfigured && !readonly"
          :loading="aiBusy"
          @click="emit('recognize')"
          >重新智能分析</el-button
        >
        <el-button v-if="!readonly" :loading="saving" @click="emit('save')"
          >暂存修改</el-button
        >
        <el-button
          v-if="!readonly"
          type="primary"
          :loading="saving"
          :disabled="
            aiBusy ||
            selected.state !== 'pending' ||
            !!selected.source_issues?.length
          "
          @click="emit('confirm')"
          >确认无误并入库</el-button
        >
      </div>
    </section>

    <el-alert
      v-if="['out_of_scope', 'ignored'].includes(selected.state)"
      :title="
        selected.state === 'out_of_scope'
          ? '这场会议被旧的导入月份限制挡住了，纳入后即可继续核对。'
          : '这场会议此前已排除，纳入后即可继续核对。'
      "
      type="warning"
      :closable="false"
      show-icon
    >
      <el-button type="primary" @click="emit('include')"
        >纳入并继续核对</el-button
      >
    </el-alert>

    <el-alert
      v-if="selected.source_issues?.length"
      :title="`原文件有 ${selected.source_issues.length} 项解析问题，请先在导入文件中处理，再确认入库`"
      type="warning"
      :closable="false"
      show-icon
    >
      <p v-for="issue in selected.source_issues" :key="issue.id">
        {{ issue.original?.sheet ? issue.original.sheet + "：" : ""
        }}{{ importIssueMessage(issue) }}
      </p>
      <el-button v-if="state.me?.is_admin" @click="emit('source-issues')"
        >查看文件并记录处理结果</el-button
      >
      <p v-else>请联系管理员核对原文件并记录处理结果。</p>
    </el-alert>

    <el-alert
      v-if="aiBusy"
      title="AI 正在根据 Excel 原文分析状态、期限、任务分区和未匹配姓名，您可以先查看会议基本信息。"
      type="info"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="aiConfigured"
      :title="`系统已自动整理 Excel${aiApplied ? `，AI 分析了 ${aiApplied} 条工作` : ''}。姓名有错字时可采用 AI 给出的通讯录成员建议，也可以手动修改。${aiFailed ? `另有 ${aiFailed} 组 AI 请求未完成，可点击重新智能分析。` : ''}`"
      :type="aiFailed ? 'warning' : 'success'"
      :closable="false"
      show-icon
    />

    <el-alert
      v-if="confirmProblems.length"
      title="还有事项需要核对"
      type="error"
      :closable="false"
      show-icon
    >
      <ul>
        <li v-for="(problem, index) in confirmProblems" :key="index">
          {{ problem }}
        </li>
      </ul>
    </el-alert>

    <article class="meeting-editor">
      <el-form label-position="top" :disabled="readonly">
        <div class="meeting-overview-grid">
          <section class="panel meeting-info-card">
            <header class="section-card-heading">
              <div class="section-title">
                <span class="section-icon"
                  ><el-icon><Calendar /></el-icon
                ></span>
                <h2>会议基本信息</h2>
              </div>
              <span class="meeting-version-badge">历史导入</span>
            </header>
            <div class="meeting-meta-grid">
              <el-form-item class="span-2" label="会议名称">
                <el-input v-model="payload.title" aria-label="会议名称" />
              </el-form-item>
              <el-form-item label="会议类型">
                <el-select
                  v-model="payload.kind"
                  placeholder="请选择会议类型"
                  @change="meetingKindChanged"
                >
                  <el-option label="部门例会" value="department" />
                  <el-option label="专项会议" value="special" />
                </el-select>
              </el-form-item>
              <el-form-item v-if="payload.kind !== 'special'" label="所属部门">
                <el-select
                  v-model="payload.department_id"
                  filterable
                  placeholder="请选择所属部门"
                >
                  <el-option
                    v-for="department in state.directory.departments"
                    :key="department.id"
                    :label="departmentPath(department.id)"
                    :value="department.id"
                  />
                </el-select>
                <p
                  v-if="
                    payload.kind !== 'special' &&
                    hasIssue(['department_unmatched', 'missing_department'])
                  "
                  class="field-attention"
                >
                  Excel 中填写：{{
                    selected.original.department_name || "空白"
                  }}。请选择正确部门。
                </p>
              </el-form-item>
              <el-form-item v-else label="所属部门">
                <el-input model-value="专项会议不需要填写部门" disabled />
              </el-form-item>
              <el-form-item label="会议日期">
                <el-date-picker
                  v-model="payload.meeting_date"
                  type="date"
                  value-format="YYYY-MM-DD"
                />
              </el-form-item>
              <el-form-item label="时间">
                <el-input
                  v-model="payload.meeting_time"
                  placeholder="如 09:00-10:00"
                />
              </el-form-item>
              <el-form-item label="地点">
                <el-input
                  v-model="payload.location"
                  placeholder="请输入会议地点"
                />
              </el-form-item>
              <el-form-item label="会议系列">
                <el-select v-model="payload.series_id" clearable>
                  <el-option
                    v-for="series in state.directory.series"
                    :key="series.id"
                    :label="series.name"
                    :value="series.id"
                  />
                </el-select>
                <el-button
                  v-if="!readonly"
                  link
                  type="primary"
                  @click="emit('new-series')"
                  >新建系列</el-button
                >
              </el-form-item>
              <el-form-item label="主持人">
                <ReviewPeopleField
                  v-model="payload.host_id"
                  :original-names="
                    [selected.original.host_name].filter(Boolean)
                  "
                  :needs-attention="
                    hasIssue(['host_unmatched', 'missing_host'])
                  "
                />
              </el-form-item>
              <el-form-item label="编制人（选填）">
                <ReviewPeopleField
                  v-model="payload.recorder_id"
                  :original-names="
                    [selected.original.recorder_name].filter(Boolean)
                  "
                  :needs-attention="
                    !!selected.original.recorder_name &&
                    hasIssue('recorder_unmatched')
                  "
                  placeholder="可留空，输入姓名搜索人员"
                />
              </el-form-item>
            </div>
          </section>

          <section class="panel attendee-card">
            <header class="section-card-heading">
              <div class="section-title">
                <span class="section-icon"
                  ><el-icon><UserFilled /></el-icon
                ></span>
                <h2>参会人员</h2>
              </div>
              <span class="count">{{ participants.length }} 人</span>
            </header>
            <div class="attendee-card-body">
              <el-form-item label="参会人员">
                <PeopleSelect v-model="payload.attendee_ids" multiple />
                <p
                  v-if="hasIssue('attendees_unmatched')"
                  class="field-attention"
                >
                  Excel 原名单：{{
                    selected.original.attendee_names?.join("、") || "空白"
                  }}。请补充未对应的人员。
                </p>
              </el-form-item>
              <div v-if="participants.length" class="attendee-people">
                <div
                  v-for="participant in participants"
                  :key="participant.id"
                  class="attendee-person"
                >
                  <span class="person-avatar">{{
                    participant.name.slice(0, 1)
                  }}</span>
                  <strong>{{ participant.name }}</strong>
                  <small>{{ participant.role }}</small>
                </div>
              </div>
              <el-empty
                v-else
                description="Excel 中未识别到参会人员"
                :image-size="54"
              />
              <el-form-item label="缺席人员及原因">
                <el-input
                  v-model="payload.absentees"
                  type="textarea"
                  :autosize="{ minRows: 2 }"
                />
              </el-form-item>
            </div>
          </section>
        </div>

        <section class="panel document-section meeting-data-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><List /></el-icon
              ></span>
              <h2>上期会议任务跟踪情况</h2>
            </div>
            <span class="count">{{ review.length }} 条</span>
          </header>
          <p class="section-note">
            这部分按 Excel
            的上期任务回顾录入，并纳入该次会议达成率。日期可留空，在备注中说明待定或暂停；“不统计”也可在备注中说明原因。
          </p>
          <div class="table-scroll">
            <table class="minutes-table historical-review-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>工作内容及要求</th>
                  <th>责任人</th>
                  <th>原计划完成时间</th>
                  <th>完成结果</th>
                  <th>备注</th>
                  <th>关联</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(entry, rowIndex) in review"
                  :key="entry.item.id"
                  class="historical-review-row"
                >
                  <td>{{ Number(rowIndex) + 1 }}</td>
                  <td class="content-cell">
                    <el-input
                      :model-value="mergedWorkContent(entry.item)"
                      @update:model-value="
                        updateWorkContent(entry.item, $event)
                      "
                      type="textarea"
                      autosize
                      aria-label="工作内容及要求"
                    />
                    <small
                      >{{ entry.item.source_sheet }} · 第
                      {{ entry.item.source_row }} 行</small
                    >
                  </td>
                  <td>
                    <ReviewPeopleField
                      v-model="entry.item.owner_ids"
                      multiple
                      :original-names="originalItem(entry.index).owner_names"
                      :needs-attention="ownerNeedsAttention(entry.index)"
                      :suggestions="
                        aiSuggestions[entry.item.id]?.owner_matches || []
                      "
                    />
                  </td>
                  <td>
                    <el-date-picker
                      v-model="entry.item.due_date"
                      type="date"
                      value-format="YYYY-MM-DD"
                      placeholder="可留空，备注中说明"
                    />
                    <small
                      v-if="hasIssue('invalid_due_date', entry.index)"
                      class="field-attention"
                      >Excel：{{
                        originalItem(entry.index).raw_due_date || "空白"
                      }}</small
                    >
                  </td>
                  <td>
                    <el-select v-model="entry.item.status">
                      <el-option
                        v-for="status in statuses"
                        :key="status"
                        :label="status"
                        :value="status"
                      />
                    </el-select>
                    <small
                      v-if="
                        hasIssue(
                          ['invalid_status', 'overdue_undue'],
                          entry.index,
                        )
                      "
                      class="field-attention"
                      >Excel：{{
                        originalItem(entry.index).raw_status ||
                        originalItem(entry.index).status ||
                        "空白"
                      }}</small
                    >
                  </td>
                  <td>
                    <el-input
                      v-model="entry.item.notes"
                      type="textarea"
                      autosize
                    />
                  </td>
                  <td>
                    <el-button
                      link
                      type="primary"
                      @click="emit('link', entry.item)"
                      >关联同一工作</el-button
                    >
                    <small
                      v-if="entry.item.historical_reference"
                      class="linked-history-label"
                      >已关联：{{
                        entry.item.historical_link_label || "前期工作"
                      }}</small
                    >
                  </td>
                </tr>
                <tr v-if="!review.length">
                  <td colspan="7" class="empty-cell">
                    Excel 中没有识别到上期任务
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel document-section meeting-data-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Memo /></el-icon
              ></span>
              <h2>本次会议议定事项</h2>
            </div>
          </header>
          <div class="section-body">
            <el-input
              v-model="payload.decisions"
              type="textarea"
              :autosize="{ minRows: 3 }"
            />
          </div>
        </section>

        <section class="panel document-section meeting-data-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Tickets /></el-icon
              ></span>
              <h2>本次会议任务部署</h2>
            </div>
            <span class="count">{{ arrangements.length }} 条</span>
          </header>
          <p class="section-note">
            这部分按 Excel
            的本次工作安排录入，只归档，不补发历史待办或消息。日期可留空，在备注中说明待定或暂停。
          </p>
          <div class="table-scroll">
            <table class="minutes-table meeting-plan-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>工作内容及要求</th>
                  <th>责任人</th>
                  <th>计划完成时间</th>
                  <th>验证人</th>
                  <th>输出物</th>
                  <th>备注</th>
                  <th>关联</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(entry, rowIndex) in arrangements"
                  :key="entry.item.id"
                  class="historical-review-row"
                >
                  <td>{{ Number(rowIndex) + 1 }}</td>
                  <td class="content-cell">
                    <el-input
                      :model-value="mergedWorkContent(entry.item)"
                      @update:model-value="
                        updateWorkContent(entry.item, $event)
                      "
                      type="textarea"
                      autosize
                      aria-label="工作内容及要求"
                    />
                    <small
                      >{{ entry.item.source_sheet }} · 第
                      {{ entry.item.source_row }} 行</small
                    >
                  </td>
                  <td>
                    <ReviewPeopleField
                      v-model="entry.item.owner_ids"
                      multiple
                      :original-names="originalItem(entry.index).owner_names"
                      :needs-attention="ownerNeedsAttention(entry.index)"
                      :suggestions="
                        aiSuggestions[entry.item.id]?.owner_matches || []
                      "
                    />
                  </td>
                  <td>
                    <el-date-picker
                      v-model="entry.item.due_date"
                      type="date"
                      value-format="YYYY-MM-DD"
                      placeholder="可留空，备注中说明"
                    />
                    <small
                      v-if="hasIssue('invalid_due_date', entry.index)"
                      class="field-attention"
                      >Excel：{{
                        originalItem(entry.index).raw_due_date || "空白"
                      }}</small
                    >
                  </td>
                  <td>
                    <ReviewPeopleField
                      v-model="entry.item.verifier_id"
                      :original-names="
                        [originalItem(entry.index).verifier_name].filter(
                          Boolean,
                        )
                      "
                      :needs-attention="
                        hasIssue('verifier_unmatched', entry.index)
                      "
                      :placeholder="'默认：' + person(payload.host_id)"
                    />
                  </td>
                  <td>
                    <el-input v-model="entry.item.deliverable" />
                  </td>
                  <td>
                    <el-input
                      v-model="entry.item.notes"
                      type="textarea"
                      autosize
                    />
                  </td>
                  <td>
                    <el-button
                      link
                      type="primary"
                      @click="emit('link', entry.item)"
                      >关联同一工作</el-button
                    >
                    <small
                      v-if="entry.item.historical_reference"
                      class="linked-history-label"
                      >已关联：{{
                        entry.item.historical_link_label || "前期工作"
                      }}</small
                    >
                  </td>
                </tr>
                <tr v-if="!arrangements.length">
                  <td colspan="8" class="empty-cell">
                    Excel 中没有识别到本次工作安排
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section
          v-if="unclassified.length"
          class="panel document-section meeting-data-card attention-section"
        >
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><List /></el-icon
              ></span>
              <h2>其他内容</h2>
            </div>
            <span class="count">{{ unclassified.length }} 条</span>
          </header>
          <p class="section-note">
            如果是工作任务，请选择“上期回顾”或“本次安排”；附表说明等内容保留为“附件
            / 非任务”。
          </p>
          <div class="unclassified-list">
            <div
              v-for="entry in unclassified"
              :key="entry.item.id"
              class="unclassified-row historical-review-row"
            >
              <el-select v-model="entry.item.section" aria-label="内容归类">
                <el-option label="上期回顾" value="review" />
                <el-option label="本次安排" value="new" />
                <el-option label="附件 / 非任务" value="attachment" />
              </el-select>
              <div>
                <strong>{{ entry.item.category }}</strong>
                <p>{{ entry.item.content }}</p>
                <small
                  >{{ entry.item.source_sheet }} · 第
                  {{ entry.item.source_row }} 行</small
                >
              </div>
            </div>
          </div>
        </section>

        <section class="panel meeting-signoff-card">
          <header class="section-card-heading">
            <div class="section-title">
              <span class="section-icon"
                ><el-icon><Memo /></el-icon
              ></span>
              <h2>原文件</h2>
            </div>
          </header>
          <div class="attachment-panel-body">
            <div class="attachment-actions">
              <el-button @click="emit('download')">{{
                selected.source.filename
              }}</el-button>
              <span class="muted small"
                >{{ selected.sheet_name }} · 原始行号已随每条内容保留</span
              >
            </div>
          </div>
          <div v-if="conflictIssues.length" class="conflict-confirmation">
            <el-alert
              title="检测到同一会议可能存在另一个文件版本，请确认当前文件是需要入库的版本。"
              type="warning"
              :closable="false"
            />
            <el-checkbox-group
              :model-value="issueChecks"
              @update:model-value="emit('update:issueChecks', $event)"
            >
              <el-checkbox
                v-for="issue in conflictIssues"
                :key="issue.id"
                :value="issue.id"
                >确认使用当前文件版本</el-checkbox
              >
            </el-checkbox-group>
          </div>
          <div class="review-confirmation-note">
            <el-input
              :model-value="reason"
              type="textarea"
              :rows="2"
              aria-label="确认说明"
              @update:model-value="emit('update:reason', $event)"
            />
          </div>
        </section>
      </el-form>
    </article>

    <div v-if="selected.meeting_id" class="sticky-actions">
      <el-button
        type="primary"
        @click="$router.push('/meetings/' + selected.meeting_id)"
        >查看已入库会议</el-button
      >
    </div>
  </div>
</template>
