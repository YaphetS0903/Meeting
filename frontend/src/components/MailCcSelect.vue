<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { ElMessage } from "element-plus";
import { refreshDirectory, run, state } from "../api";
import { isEmployeeAccount, withDirectoryDetails } from "../people";
import { normalizeMailbox } from "../emailAddresses";

const props = defineProps<{
  userIds: string[];
  emails: string[];
  allowedIds?: string[];
  users?: any[];
}>();
const emit = defineEmits<{
  "update:userIds": [string[]];
  "update:emails": [string[]];
  open: [];
}>();
const keyword = ref("");
const expandedTags = ref(false);
const select = ref();
const chosen = computed(() => [
  ...(props.userIds || []).map((id) => "user:" + id),
  ...(props.emails || []),
]);
const eligible = computed(() =>
  withDirectoryDetails(
    props.users || state.directory.users,
    state.directory.users,
  ).filter(
    (user: any) =>
      (props.userIds || []).includes(user.id) ||
      (user.active &&
        (!props.allowedIds || props.allowedIds.includes(user.id))),
  ),
);
const options = computed(() => {
  const term = keyword.value.trim().toLocaleLowerCase();
  if (!term) return [];
  const users = eligible.value.filter(
    (user: any) =>
      isEmployeeAccount(user) &&
      `${user.name} ${user.email || ""}`.toLocaleLowerCase().includes(term),
  );
  const rank = (user: any) => {
    const name = user.name.toLocaleLowerCase();
    return name === term
      ? 0
      : name.startsWith(term)
        ? 1
        : name.includes(term)
          ? 2
          : 3;
  };
  return users.sort((a: any, b: any) => rank(a) - rank(b)).slice(0, 80);
});
function userLabel(user: any) {
  return `${user.name}${user.email ? " · " + user.email : "（未配置邮箱）"}`;
}
function selectedLabel(value: unknown, fallback: string) {
  if (typeof value !== "string" || !value.startsWith("user:")) return fallback;
  const user = eligible.value.find((user: any) => user.id === value.slice(5));
  return user ? userLabel(user) : fallback;
}
function clearSearch() {
  keyword.value = "";
  if (select.value) select.value.expanded = false;
}
function refreshPeople() {
  emit("open");
  if (!props.users) run(refreshDirectory);
}
function onSearchClick(event: MouseEvent) {
  if (keyword.value.trim()) return;
  const target = event.target as HTMLElement;
  if (
    !target.closest(".el-select__wrapper") ||
    target.closest(".el-tag, .el-button, .el-select__clear")
  )
    return;
  event.stopPropagation();
  clearSearch();
  select.value?.focus();
}
function onSearchKeydown(event: KeyboardEvent) {
  if ((event.target as HTMLElement).closest("button")) return;
  if (event.isComposing || event.keyCode === 229) {
    event.stopPropagation();
    return;
  }
  if (event.key === "Escape") clearSearch();
  const term = keyword.value.trim();
  if (event.key === "Enter" && term) {
    event.preventDefault();
    event.stopPropagation();
    if (validEmail(term)) {
      update([...chosen.value, term]);
      return;
    }
    const hovered = select.value?.hoverOption?.value;
    const user =
      options.value.find((user: any) => "user:" + user.id === hovered) ||
      options.value[0];
    if (user) update([...chosen.value, "user:" + user.id]);
    else {
      ElMessage.warning(`请输入完整邮箱地址：${term}`);
      clearSearch();
    }
    return;
  }
  if (
    !keyword.value.trim() &&
    ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)
  ) {
    event.preventDefault();
    event.stopPropagation();
    clearSearch();
  }
}
async function search(value: string) {
  keyword.value = value;
  if (!value.trim()) clearSearch();
  await nextTick();
  select.value?.scrollbarRef?.setScrollTop(0);
}
function validEmail(value: string) {
  return normalizeMailbox(value) !== null;
}
function update(values: string[]) {
  const ids: string[] = [],
    emails: string[] = [],
    invalid: string[] = [];
  for (const raw of values || []) {
    const value = raw.trim();
    if (
      value.startsWith("user:") &&
      eligible.value.some((user: any) => user.id === value.slice(5))
    )
      ids.push(value.slice(5));
    else if (validEmail(value)) {
      const email = normalizeMailbox(value)!;
      const matches = eligible.value.filter(
        (user: any) =>
          isEmployeeAccount(user) &&
          user.email?.toLowerCase() === email.toLowerCase(),
      );
      if (matches.length === 1) ids.push(matches[0].id);
      else if (
        !emails.some(
          (previous) => previous.toLowerCase() === email.toLowerCase(),
        )
      )
        emails.push(email);
    } else if (value) {
      const matches = eligible.value.filter(
        (user: any) => isEmployeeAccount(user) && user.name === value,
      );
      if (matches.length === 1) ids.push(matches[0].id);
      else if (matches.length > 1)
        ElMessage.warning(`存在多位“${value}”，请从列表中选择对应邮箱。`);
      else invalid.push(value);
    }
  }
  emit("update:userIds", [...new Set(ids)]);
  emit("update:emails", emails);
  if (invalid.length)
    ElMessage.warning(`请输入完整邮箱地址：${invalid.join("、")}`);
  clearSearch();
}
function paste(event: ClipboardEvent) {
  const values = (event.clipboardData?.getData("text/plain") || "")
    .split(/[,，;；、\r\n\t]+/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (values.length < 2) return;
  event.preventDefault();
  const additions = values.map((value) => {
    const users = eligible.value.filter(
      (user: any) => isEmployeeAccount(user) && user.name === value,
    );
    return users.length === 1 ? "user:" + users[0].id : value;
  });
  update([...chosen.value, ...additions]);
  select.value?.blur();
}
</script>

<template>
  <el-select
    ref="select"
    :model-value="chosen"
    @update:model-value="update"
    @paste.capture="paste"
    @click.capture="onSearchClick"
    @keydown.capture="onSearchKeydown"
    @focus="refreshPeople"
    @blur="clearSearch"
    @visible-change="
      (visible: boolean) => {
        if (!visible) clearSearch();
      }
    "
    multiple
    filterable
    remote
    :allow-create="validEmail(keyword.trim())"
    default-first-option
    :debounce="0"
    :remote-method="search"
    :reserve-keyword="false"
    clearable
    placeholder="输入公司员工姓名或邮箱，按回车添加"
    aria-label="邮件抄送人或邮箱"
    :popper-style="!keyword.trim() ? { display: 'none' } : undefined"
  >
    <template #tag="{ data, deleteTag, selectDisabled }">
      <el-tag
        v-for="item in expandedTags ? data : data.slice(0, 3)"
        :key="item.value"
        class="mail-cc-tag"
        size="small"
        type="info"
        :closable="!selectDisabled"
        disable-transitions
        @mousedown.stop
        @click.stop
        @close="deleteTag($event, item)"
      >
        <span class="mail-cc-label">{{
          selectedLabel(item.value, item.currentLabel)
        }}</span>
      </el-tag>
      <el-button
        v-if="data.length > 3"
        class="mail-cc-expand"
        size="small"
        :aria-expanded="expandedTags"
        :aria-label="
          expandedTags
            ? '收起邮件抄送人'
            : `展开其余${data.length - 3}位邮件抄送人`
        "
        @mousedown.prevent
        @click.stop="expandedTags = !expandedTags"
        >{{ expandedTags ? "收起" : "+" + (data.length - 3) }}</el-button
      >
    </template>
    <template #label="{ value, label }">{{
      selectedLabel(value, label)
    }}</template>
    <el-option
      v-for="user in options"
      :key="user.id"
      :value="'user:' + user.id"
      :label="userLabel(user)"
    />
  </el-select>
</template>

<style scoped>
.mail-cc-tag {
  max-width: 100%;
  height: auto;
  min-height: 24px;
  padding-block: 3px;
}
.mail-cc-label {
  white-space: normal;
  overflow-wrap: anywhere;
  user-select: text;
  cursor: text;
}
.mail-cc-expand {
  margin-left: 0;
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-5);
}
</style>
