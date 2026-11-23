<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import Sortable from "sortablejs";
import { departmentPath, state, refreshDirectory, run } from "../api";
import {
  isEmployeeAccount,
  pastedPeople,
  withDirectoryDetails,
} from "../people";
defineOptions({ inheritAttrs: false });
const props = defineProps<{
  modelValue: any;
  multiple?: boolean;
  collapseTags?: boolean;
  placeholder?: string;
  disabled?: boolean;
  includeInactive?: boolean;
  popperClass?: string;
  allowedIds?: string[];
  ariaLabel?: string;
  users?: any[];
  searchOnly?: boolean;
  copyable?: boolean;
  reorderable?: boolean;
}>();
const emit = defineEmits(["update:modelValue"]);
const keyword = ref("");
const select = ref();
const allOptions = computed(() => {
  const users = props.users
    ? withDirectoryDetails(props.users, state.directory.users)
    : state.directory.users;
  const nameCounts = new Map<string, number>();
  for (const user of users)
    nameCounts.set(user.name, (nameCounts.get(user.name) || 0) + 1);
  return users.map((user: any) => {
    const suffix =
      (nameCounts.get(user.name) || 0) > 1
        ? ` · ${user.department_name || (user.department_id ? departmentPath(user.department_id) : user.wecom_id || user.id.slice(0, 6))}`
        : "";
    return {
      ...user,
      label: `${user.name}${!user.active ? "（已停用）" : ""}${suffix}`,
      search:
        `${user.name} ${user.email || ""} ${user.department_name || (user.department_id ? departmentPath(user.department_id) : "")} ${user.wecom_id || ""}`.toLowerCase(),
    };
  });
});
const selectedPeople = computed(() => {
  const ids = Array.isArray(props.modelValue)
    ? props.modelValue
    : props.modelValue
      ? [props.modelValue]
      : [];
  return ids.map(
    (id: string) =>
      allOptions.value.find((user: any) => user.id === id) || {
        id,
        label: id,
      },
  );
});
const visibleOptions = computed(() => {
  const selected = new Set(
    (Array.isArray(props.modelValue)
      ? props.modelValue
      : props.modelValue
        ? [props.modelValue]
        : []) as string[],
  );
  const term = keyword.value.trim().toLowerCase();
  // Task suggestions open only after typing; tags render from the model so
  // clearing search options never hides the people who were just selected.
  if (props.searchOnly && !term) return [];
  const result = allOptions.value.filter(
    (item: any) =>
      selected.has(item.id) ||
      (isEmployeeAccount(item) &&
        (props.includeInactive || item.active) &&
        (!props.allowedIds || props.allowedIds.includes(item.id)) &&
        (!term || item.search.includes(term))),
  );
  if (term) {
    const rank = (item: any) => {
      const name = item.name.toLowerCase();
      return name === term
        ? 0
        : name.startsWith(term)
          ? 1
          : name.includes(term)
            ? 2
            : 3;
    };
    const matches = result
      .filter((item: any) => item.search.includes(term))
      .sort(
        (a: any, b: any) =>
          rank(a) - rank(b) || Number(b.active) - Number(a.active),
      )
      .slice(0, 80);
    if (props.searchOnly) return matches;
    const displayed = new Set(matches.map((item: any) => item.id));
    // Retain selected option labels without pinning unrelated names above matches.
    return [
      ...matches,
      ...result.filter(
        (item: any) => selected.has(item.id) && !displayed.has(item.id),
      ),
    ];
  }
  const chosen = result.filter((item: any) => selected.has(item.id));
  const choices = result
    .filter((item: any) => !selected.has(item.id))
    .sort((a: any, b: any) =>
      a.id === state.me?.id ? -1 : b.id === state.me?.id ? 1 : 0,
    )
    .slice(0, 80);
  return [...chosen, ...choices];
});
function update(value: any) {
  emit("update:modelValue", value || (props.multiple ? [] : null));
  clearSearch();
}
function clearSearch() {
  if (!props.searchOnly) return;
  keyword.value = "";
  if (select.value) select.value.expanded = false;
}
type NameCaret = { node: Text; offset: number };
let nameDrag: {
  root: HTMLElement;
  anchor: NameCaret;
  x: number;
  y: number;
  pointerId: number;
  moved: boolean;
} | null = null;
let suppressDragClick = false;
function nameCaretAt(
  root: HTMLElement,
  x: number,
  y: number,
): NameCaret | null {
  let nearest: HTMLElement | null = null;
  let distance = Infinity;
  for (const label of root.querySelectorAll<HTMLElement>(
    ".people-select-name",
  )) {
    const rect = label.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    const dx = Math.max(rect.left - x, 0, x - rect.right);
    const dy = Math.max(rect.top - y, 0, y - rect.bottom);
    const score = dx * dx + dy * dy;
    if (score < distance) {
      nearest = label;
      distance = score;
    }
  }
  const node = nearest?.firstChild;
  if (!node || node.nodeType !== Node.TEXT_NODE) return null;
  const range = document.createRange();
  let offset = 0;
  distance = Infinity;
  for (let i = 0; i <= (node.textContent?.length || 0); i++) {
    range.setStart(node, i);
    range.collapse(true);
    const score = Math.abs(range.getBoundingClientRect().left - x);
    if (score < distance) {
      offset = i;
      distance = score;
    }
  }
  return { node: node as Text, offset };
}
function endNameDrag() {
  nameDrag = null;
  document.removeEventListener("pointermove", moveNameDrag);
  document.removeEventListener("pointerup", endNameDrag);
  document.removeEventListener("pointercancel", endNameDrag);
  window.removeEventListener("blur", endNameDrag);
}
function moveNameDrag(event: PointerEvent) {
  const drag = nameDrag;
  if (!drag || event.pointerId !== drag.pointerId) return;
  if (
    !drag.moved &&
    Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4
  )
    return;
  if (!drag.moved) {
    drag.moved = true;
    suppressDragClick = true;
    // Keep keyboard copy inside this field, outside the empty search input.
    drag.root
      .closest<HTMLElement>(".people-select-field")
      ?.focus({ preventScroll: true });
  }
  const caret = nameCaretAt(drag.root, event.clientX, event.clientY);
  if (!caret) return;
  window
    .getSelection()
    ?.setBaseAndExtent(
      drag.anchor.node,
      drag.anchor.offset,
      caret.node,
      caret.offset,
    );
  event.preventDefault();
}
function startNameDrag(event: PointerEvent) {
  suppressDragClick = false;
  if (
    !props.copyable ||
    !props.searchOnly ||
    props.disabled ||
    keyword.value ||
    !selectedPeople.value.length ||
    event.button !== 0 ||
    event.pointerType !== "mouse"
  )
    return;
  const target = event.target as HTMLElement;
  if (target.closest(".el-tag__close, .el-select__clear")) return;
  if (target.closest(".el-tag") && !target.closest(".people-select-name"))
    return;
  if (!target.closest(".el-select__wrapper")) return;
  const root = event.currentTarget as HTMLElement;
  const names = root.querySelectorAll(".people-select-name");
  const tail = names[names.length - 1]?.firstChild;
  // The search input follows all tags, even when it wraps to a new line.
  const anchor =
    target.closest(".el-select__input-wrapper") &&
    tail?.nodeType === Node.TEXT_NODE
      ? { node: tail as Text, offset: tail.textContent?.length || 0 }
      : nameCaretAt(root, event.clientX, event.clientY);
  if (!anchor) return;
  endNameDrag();
  window.getSelection()?.removeAllRanges();
  nameDrag = {
    root,
    anchor,
    x: event.clientX,
    y: event.clientY,
    pointerId: event.pointerId,
    moved: false,
  };
  // Defer input focus until a click; starting in blank space must allow dragging.
  event.preventDefault();
  document.addEventListener("pointermove", moveNameDrag);
  document.addEventListener("pointerup", endNameDrag);
  document.addEventListener("pointercancel", endNameDrag);
  window.addEventListener("blur", endNameDrag);
}
onBeforeUnmount(endNameDrag);
function onSearchClick(event: MouseEvent) {
  const target = event.target as HTMLElement;
  const root = event.currentTarget as HTMLElement;
  // Removing a person must still work after selecting or copying their name.
  if (target.closest(".el-tag__close, .el-select__clear")) {
    suppressDragClick = false;
    return;
  }
  const selection = window.getSelection();
  if (
    props.copyable &&
    (suppressDragClick ||
      target.closest(".people-select-name") ||
      (selection?.toString() &&
        root.contains(selection.anchorNode) &&
        root.contains(selection.focusNode)))
  ) {
    suppressDragClick = false;
    // Element Plus focuses its input during wrapper click capture, which would
    // erase a mouse-selected name before the user can copy it.
    event.stopPropagation();
    return;
  }
  if (!props.searchOnly || keyword.value.trim()) return;
  if (
    !target.closest(".el-select__wrapper") ||
    target.closest(".el-tag, .el-button, .el-select__clear")
  )
    return;
  // Focusing an empty field must not toggle the select's cached popup.
  event.stopPropagation();
  clearSearch();
  select.value?.focus();
}
function onSearchKeydown(event: KeyboardEvent) {
  if (!props.searchOnly) return;
  if (event.isComposing || event.keyCode === 229) {
    event.stopPropagation();
    return;
  }
  if (event.key === "Escape") clearSearch();
  if (
    !keyword.value.trim() &&
    ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)
  ) {
    event.preventDefault();
    event.stopPropagation();
    clearSearch();
  }
}
function onVisibleChange(visible: boolean) {
  if (visible) run(refreshDirectory);
  else clearSearch();
}
function removePerson(id: string) {
  if (props.disabled) return;
  update(props.modelValue.filter((value: string) => value !== id));
}
const orderAnnouncement = ref("");
function movePerson(id: string, to: number) {
  if (props.disabled || !props.reorderable) return;
  const ids: string[] = [...props.modelValue];
  const from = ids.indexOf(id);
  if (from < 0 || to < 0 || to >= ids.length || from === to) return;
  ids.splice(to, 0, ids.splice(from, 1)[0]);
  update(ids);
  orderAnnouncement.value = `已将${selectedPeople.value.find((p: any) => p.id === id)?.label || "人员"}移至第${to + 1}位`;
}
function movePersonByKeyboard(event: KeyboardEvent, id: string) {
  const direction = ["ArrowLeft", "ArrowUp"].includes(event.key)
    ? -1
    : ["ArrowRight", "ArrowDown"].includes(event.key)
      ? 1
      : 0;
  if (!direction) return;
  event.preventDefault();
  event.stopPropagation();
  movePerson(id, props.modelValue.indexOf(id) + direction);
}
watch(
  [select, () => props.reorderable, () => props.disabled],
  ([control, reorderable, disabled], _, onCleanup) => {
    const root = control?.$el?.querySelector(".el-select__selection");
    if (!root || !reorderable || disabled || !props.multiple) return;
    let originalNextSibling: ChildNode | null = null;
    const sorter = Sortable.create(root, {
      handle: ".people-order-handle",
      draggable: ".people-sortable-tag",
      forceFallback: true,
      fallbackOnBody: true,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 0
        : 150,
      ghostClass: "people-order-ghost",
      delay: 120,
      delayOnTouchOnly: true,
      touchStartThreshold: 4,
      fallbackTolerance: 4,
      onStart(event) {
        originalNextSibling = event.item.nextSibling;
      },
      onEnd(event) {
        const { item, oldDraggableIndex: from, newDraggableIndex: to } = event;
        if (from === undefined || to === undefined || from === to) return;
        root.insertBefore(
          item,
          originalNextSibling?.parentNode === root ? originalNextSibling : null,
        );
        originalNextSibling = null;
        if (item.dataset.personId) movePerson(item.dataset.personId, to);
      },
    });
    onCleanup(() => sorter.destroy());
  },
  { flush: "post" },
);
const copiedNames = computed(() =>
  selectedPeople.value.map((user: any) => user.label).join("、"),
);
const copyRole = computed(() => props.ariaLabel || "人员");
function onCopy(event: ClipboardEvent) {
  if (!props.copyable || !copiedNames.value || keyword.value.trim()) return;
  // Respect normal partial text selections. In an empty search input, Ctrl+C
  // copies the selected owners so the next task can reuse them directly.
  const selection = window.getSelection();
  if (selection?.toString()) {
    // Browser copying across separate tags can omit separators. Keep the
    // selected name fragments separated so pasting into the next task works.
    const root = event.currentTarget as HTMLElement;
    if (
      !selection.rangeCount ||
      !root.contains(selection.anchorNode) ||
      !root.contains(selection.focusNode)
    )
      return;
    const selected = selection.getRangeAt(0);
    const pieces: string[] = [];
    for (const label of root.querySelectorAll(".people-select-name")) {
      if (!selected.intersectsNode(label)) continue;
      const part = document.createRange();
      part.selectNodeContents(label);
      if (selected.compareBoundaryPoints(Range.START_TO_START, part) > 0)
        part.setStart(selected.startContainer, selected.startOffset);
      if (selected.compareBoundaryPoints(Range.END_TO_END, part) < 0)
        part.setEnd(selected.endContainer, selected.endOffset);
      const text = part.toString().trim();
      if (text) pieces.push(text);
    }
    if (pieces.length > 1 && event.clipboardData) {
      event.clipboardData.setData("text/plain", pieces.join("、"));
      event.preventDefault();
    }
    return;
  }
  const target = event.target;
  if (
    target instanceof HTMLInputElement &&
    target.selectionStart !== target.selectionEnd
  )
    return;
  if (!event.clipboardData) return;
  event.clipboardData.setData("text/plain", copiedNames.value);
  event.preventDefault();
}
async function search(value: string) {
  keyword.value = value;
  if (props.searchOnly && !value.trim()) clearSearch();
  await nextTick();
  select.value?.scrollbarRef?.setScrollTop(0);
}
function onPaste(event: ClipboardEvent) {
  if ((!props.multiple && !props.copyable) || props.disabled) return;
  const text = event.clipboardData?.getData("text/plain");
  if (!text) return;
  const eligible = allOptions.value.filter(
    (user: any) =>
      isEmployeeAccount(user) &&
      (props.includeInactive || user.active) &&
      (!props.allowedIds || props.allowedIds.includes(user.id)),
  );
  const result = pastedPeople(text, eligible);
  if (result.names.length < 2 && !result.ids.length) return;
  event.preventDefault();
  if (!props.multiple) {
    if (result.names.length !== 1) {
      ElMessage.warning(`${copyRole.value}每项只能选择一人，请分别选择`);
      return;
    }
    emit("update:modelValue", result.ids[0]);
    clearSearch();
    select.value?.blur();
    return;
  }
  const current = Array.isArray(props.modelValue) ? props.modelValue : [];
  const next = [...new Set([...current, ...result.ids])];
  if (next.length !== current.length) emit("update:modelValue", next);
  keyword.value = "";
  // Element Plus keeps its own search query, separate from the remote keyword.
  select.value?.blur();
  const warnings: string[] = [];
  if (result.ambiguous.length)
    warnings.push(`存在重名，请分别选择：${result.ambiguous.join("、")}`);
  if (result.unresolved.length)
    warnings.push(`未匹配或不在可选范围：${result.unresolved.join("、")}`);
  const added = next.length - current.length;
  if (warnings.length)
    ElMessage.warning(
      `${added ? `已添加 ${added} 人；` : ""}${warnings.join("；")}`,
    );
  else
    ElMessage.success(added ? `已添加 ${added} 人` : "这些人员已在已选名单中");
}
</script>
<template>
  <div
    class="people-select-field"
    :class="{ 'people-select-single-copyable': copyable && !multiple }"
    :tabindex="copyable ? -1 : undefined"
    @copy.capture="onCopy"
  >
    <el-select
      v-bind="$attrs"
      ref="select"
      @paste.capture="onPaste"
      @pointerdown.capture="startNameDrag"
      @click.capture="onSearchClick"
      @keydown.capture="onSearchKeydown"
      @blur="clearSearch"
      :class="{
        'people-select-copyable': copyable,
      }"
      :model-value="modelValue"
      @update:model-value="update"
      :multiple="multiple"
      filterable
      remote
      :debounce="searchOnly ? 0 : 300"
      :remote-method="search"
      default-first-option
      :remote-show-suffix="!searchOnly"
      @visible-change="onVisibleChange"
      :reserve-keyword="false"
      :collapse-tags="collapseTags !== false"
      :max-collapse-tags="3"
      clearable
      :placeholder="placeholder || '输入姓名搜索人员'"
      :disabled="disabled"
      :aria-label="ariaLabel"
      :popper-class="popperClass"
      :popper-style="
        searchOnly && !keyword.trim() ? { display: 'none' } : undefined
      "
    >
      <template v-if="searchOnly && !multiple && selectedPeople.length" #label>
        <span class="people-select-name" @mousedown.stop @click.stop>{{
          selectedPeople[0].label
        }}</span>
      </template>
      <template v-if="(searchOnly || reorderable) && multiple" #tag>
        <el-tag
          v-for="user in selectedPeople"
          :key="user.id"
          :class="{ 'people-sortable-tag': reorderable }"
          :data-person-id="user.id"
          type="info"
          :closable="!disabled"
          :disable-transitions="true"
          @close="removePerson(user.id)"
          ><button
            v-if="reorderable"
            type="button"
            class="people-order-handle"
            :aria-label="`拖动${user.label}调整顺序`"
            :disabled="disabled"
            title="拖动调整顺序，或按方向键移动"
            @click.stop
            @keydown="movePersonByKeyboard($event, user.id)"
          >
            <svg
              width="12"
              height="16"
              viewBox="0 0 12 16"
              fill="currentColor"
              aria-hidden="true"
            >
              <circle cx="4" cy="4" r="1" />
              <circle cx="8" cy="4" r="1" />
              <circle cx="4" cy="8" r="1" />
              <circle cx="8" cy="8" r="1" />
              <circle cx="4" cy="12" r="1" />
              <circle cx="8" cy="12" r="1" />
            </svg></button
          ><span class="people-select-name" @mousedown.stop @click.stop>{{
            user.label
          }}</span></el-tag
        >
      </template>
      <el-option
        v-for="u in visibleOptions"
        v-show="isEmployeeAccount(u)"
        :key="u.id"
        :value="u.id"
        :disabled="!u.active && !includeInactive"
        :label="u.label"
    /></el-select>
    <span v-if="reorderable" class="people-order-status" aria-live="polite">{{
      orderAnnouncement
    }}</span>
  </div>
</template>

<style scoped>
.people-select-field {
  width: 100%;
  min-width: 0;
}
.people-select-field:focus {
  outline: none;
}
.people-sortable-tag :deep(.el-tag__content) {
  display: inline-flex;
  align-items: center;
}
.people-order-handle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  min-width: 20px;
  height: 22px;
  padding: 0;
  margin-left: -5px;
  margin-right: 2px;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: grab;
  touch-action: none;
}
.people-order-handle:hover {
  background: var(--el-fill-color);
  color: var(--el-text-color-primary);
}
.people-order-handle:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}
.people-order-handle:active {
  cursor: grabbing;
}
.people-order-handle:disabled {
  cursor: default;
}
.people-order-ghost {
  opacity: 0.4;
}
.people-order-status {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.people-select-copyable :deep(.el-tag__content) {
  -webkit-user-select: text;
  user-select: text;
  cursor: text;
}
.people-select-single-copyable :deep(.el-select__placeholder) {
  z-index: 1;
}
.people-select-single-copyable .people-select-name {
  pointer-events: auto;
  -webkit-user-select: text;
  user-select: text;
  cursor: text;
}
.people-select-copyable :deep(.el-select__selection) {
  gap: 4px;
}
.people-select-copyable :deep(.el-select__input-wrapper.is-hidden) {
  /* Keep the row height stable when focus moves to the next task. */
  position: relative;
  opacity: 1;
  z-index: auto;
}
.people-select-copyable :deep(.el-select__input) {
  outline: none;
}
.people-select-copyable :deep(.el-select__placeholder) {
  pointer-events: none;
}
</style>
