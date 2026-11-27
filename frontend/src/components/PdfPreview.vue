<script setup lang="ts">
import { chineseError } from "../errorMessages";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import workerUrl from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?worker&url";

GlobalWorkerOptions.workerSrc = workerUrl;
const props = withDefaults(
  defineProps<{
    src: string;
    filename: string;
    allowCopy?: boolean;
    allowDownload?: boolean;
  }>(),
  { allowCopy: true, allowDownload: true },
);
const container = ref<HTMLElement>();
const loading = ref(false),
  failed = ref(false),
  copying = ref(false),
  imageUrl = ref("");
const imageFilename = computed(
  () => props.filename.replace(/\.pdf$/i, "") + ".png",
);
const pages = ref<{ number: number; width: number; height: number }[]>([]);
let generation = 0;
let task: PDFDocumentLoadingTask | undefined;
let document: PDFDocumentProxy | undefined;
let observer: IntersectionObserver | undefined;
let resizeObserver: ResizeObserver | undefined;
const rendering = new Map<HTMLElement, RenderTask>();
const rendered = new Map<HTMLElement, number>();
const pending = new Set<HTMLElement>();
let imageBlob: Blob | undefined;
let imageTask: Promise<Blob> | undefined;
let imageRender: RenderTask | undefined;

function stopSheetRendering() {
  observer?.disconnect();
  resizeObserver?.disconnect();
  for (const render of rendering.values()) render.cancel();
  rendering.clear();
  rendered.clear();
  pending.clear();
}

function clear() {
  generation++;
  stopSheetRendering();
  imageRender?.cancel();
  imageRender = undefined;
  imageTask = undefined;
  imageBlob = undefined;
  copying.value = false;
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
  imageUrl.value = "";
  document = undefined;
  void task?.destroy().catch(() => {});
  task = undefined;
}

function assertCurrent(current: number) {
  if (current !== generation)
    throw new DOMException("纪要预览已更新", "AbortError");
}

async function renderImage(pdf: PDFDocumentProxy, current: number) {
  const dimensions = pages.value.map((page) => ({ ...page }));
  const width = Math.max(...dimensions.map((page) => page.width));
  const height = dimensions.reduce((sum, page) => sum + page.height, 0);
  const scale = Math.min(
    2.5,
    1800 / width,
    Math.sqrt(48_000_000 / (width * height)),
    32700 / height,
  );
  if (width * scale < 800)
    throw new Error("纪要页数较多，无法生成清晰长图，请下载 PDF。");
  const canvas = window.document.createElement("canvas");
  canvas.width = Math.ceil(width * scale);
  canvas.height = dimensions.reduce(
    (sum, page) => sum + Math.ceil(page.height * scale),
    0,
  );
  try {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("图片生成失败，请重新点击复制图片。");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    let top = 0;
    for (const dimension of dimensions) {
      assertCurrent(current);
      const page = await pdf.getPage(dimension.number);
      assertCurrent(current);
      const viewport = page.getViewport({ scale });
      const sheet = window.document.createElement("canvas");
      sheet.width = Math.ceil(viewport.width);
      sheet.height = Math.ceil(viewport.height);
      const render = page.render({ canvas: sheet, viewport });
      imageRender = render;
      try {
        await render.promise;
        assertCurrent(current);
        context.drawImage(
          sheet,
          Math.floor((canvas.width - sheet.width) / 2),
          top,
        );
        top += sheet.height;
      } finally {
        if (imageRender === render) imageRender = undefined;
        sheet.width = sheet.height = 0;
      }
    }
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) =>
          value
            ? resolve(value)
            : reject(new Error("图片生成失败，请重新点击复制图片。")),
        "image/png",
      ),
    );
    assertCurrent(current);
    return blob;
  } finally {
    canvas.width = canvas.height = 0;
  }
}

function fullImage() {
  if (imageBlob) return Promise.resolve(imageBlob);
  if (imageTask) return imageTask;
  if (!document || !pages.value.length)
    return Promise.reject(new Error("请等待纪要预览加载完成。"));
  const current = generation;
  imageTask = renderImage(document, current)
    .then((blob) => {
      assertCurrent(current);
      imageBlob = blob;
      return blob;
    })
    .finally(() => {
      if (current === generation) imageTask = undefined;
    });
  return imageTask;
}

async function copyImage() {
  if (copying.value || loading.value || failed.value || !pages.value.length)
    return;
  const current = generation;
  copying.value = true;
  try {
    const blob = fullImage();
    // Closing the preview can cancel rendering after clipboard access is denied.
    void blob.catch(() => {});
    if (
      window.isSecureContext &&
      navigator.clipboard?.write &&
      typeof ClipboardItem !== "undefined"
    ) {
      try {
        // Start the clipboard write during the click, before rendering awaits.
        await navigator.clipboard.write([
          new ClipboardItem({ "image/png": blob }),
        ]);
        if (current === generation)
          ElMessage.success("完整纪要图片已复制，可直接粘贴。");
        return;
      } catch {
        if (current !== generation) return;
      }
    }
    const image = await blob;
    if (current !== generation) return;
    if (!imageUrl.value) imageUrl.value = URL.createObjectURL(image);
    stopSheetRendering();
  } catch (error) {
    if (current === generation)
      ElMessage.error(
        chineseError(error, "图片生成失败，请重新点击复制图片。"),
      );
  } finally {
    if (current === generation) copying.value = false;
  }
}

async function renderSheet(sheet: HTMLElement, current: number) {
  const pdf = document;
  const width = Math.round(sheet.clientWidth);
  if (
    current !== generation ||
    !pdf ||
    !width ||
    pending.has(sheet) ||
    rendered.get(sheet) === width
  )
    return;
  pending.add(sheet);
  try {
    const page = await pdf.getPage(Number(sheet.dataset.page));
    if (generation !== current || !sheet.isConnected) return;
    const viewport = page.getViewport({
      scale: width / page.getViewport({ scale: 1 }).width,
    });
    const density = Math.min(
      window.devicePixelRatio || 1,
      2,
      Math.sqrt(3_000_000 / (viewport.width * viewport.height)),
    );
    const canvas = window.document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width * density);
    canvas.height = Math.ceil(viewport.height * density);
    canvas.setAttribute("aria-hidden", "true");
    const render = page.render({
      canvas,
      viewport,
      transform: density === 1 ? undefined : [density, 0, 0, density, 0, 0],
    });
    rendering.set(sheet, render);
    await render.promise;
    if (generation !== current || !sheet.isConnected) return;
    sheet.replaceChildren(canvas);
    rendered.set(sheet, width);
  } catch (error) {
    if (
      generation === current &&
      (error as Error).name !== "RenderingCancelledException"
    )
      failed.value = true;
  } finally {
    if (generation === current) {
      rendering.delete(sheet);
      pending.delete(sheet);
      if (Math.round(sheet.clientWidth) !== width)
        void renderSheet(sheet, current);
    }
  }
}

function observeSheets(current: number) {
  observer?.disconnect();
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const sheet = entry.target as HTMLElement;
        if (entry.isIntersecting) void renderSheet(sheet, current);
        else if (!rendering.has(sheet)) {
          sheet.replaceChildren();
          rendered.delete(sheet);
        }
      }
    },
    { rootMargin: "600px 0px" },
  );
  container.value
    ?.querySelectorAll<HTMLElement>(".pdf-preview-sheet")
    .forEach((sheet) => observer!.observe(sheet));
}

watch(
  () => props.src,
  async (src) => {
    clear();
    const current = generation;
    pages.value = [];
    failed.value = false;
    loading.value = true;
    try {
      task = getDocument({ url: src });
      const pdf = await task.promise;
      if (current !== generation) return;
      document = pdf;
      const dimensions: typeof pages.value = [];
      for (let number = 1; number <= pdf.numPages; number++) {
        const page = await pdf.getPage(number);
        if (current !== generation) return;
        const { width, height } = page.getViewport({ scale: 1 });
        dimensions.push({ number, width, height });
      }
      pages.value = dimensions;
      await nextTick();
      if (current !== generation) return;
      observeSheets(current);
      let lastWidth = container.value?.clientWidth;
      resizeObserver = new ResizeObserver(([entry]) => {
        const width = Math.round(entry.contentRect.width);
        if (width === lastWidth) return;
        lastWidth = width;
        observeSheets(current);
      });
      if (container.value) resizeObserver.observe(container.value);
    } catch {
      if (current === generation) failed.value = true;
    } finally {
      if (current === generation) loading.value = false;
    }
  },
  { immediate: true },
);
onBeforeUnmount(clear);
</script>

<template>
  <section
    ref="container"
    class="pdf-preview"
    aria-label="发布前 PDF 纪要预览"
    :aria-busy="loading"
  >
    <div class="pdf-preview-toolbar">
      <span
        >PDF 预览<span v-if="pages.length">
          · 共 {{ pages.length }} 页</span
        ></span
      >
      <div class="pdf-preview-links">
        <el-button
          v-if="allowCopy"
          link
          type="primary"
          :loading="copying"
          :disabled="loading || failed || !pages.length"
          @click="copyImage"
          >复制图片</el-button
        >
        <a :href="src" target="_blank" rel="noopener">打开 PDF</a>
        <a v-if="allowDownload" :href="src" :download="filename">下载 PDF</a>
      </div>
    </div>
    <p v-if="loading" class="pdf-preview-loading" role="status">
      正在加载纪要预览…
    </p>
    <p v-if="failed" class="pdf-preview-error" role="alert">
      预览暂时无法显示，请刷新预览，或打开 PDF 查看。
    </p>
    <p v-if="imageUrl" class="pdf-preview-copy-hint" role="status">
      已生成完整纪要图片，请右键图片选择“复制图片”；手机可长按图片复制或保存。
      <a v-if="allowDownload" :href="imageUrl" :download="imageFilename"
        >下载图片</a
      >
    </p>
    <figure v-if="imageUrl" class="pdf-preview-page">
      <img
        :src="imageUrl"
        class="pdf-preview-image"
        alt="完整会议纪要，可右键或长按复制"
      />
    </figure>
    <template v-else>
      <figure v-for="page in pages" :key="page.number" class="pdf-preview-page">
        <div
          class="pdf-preview-sheet"
          :data-page="page.number"
          :style="{ aspectRatio: `${page.width} / ${page.height}` }"
          role="img"
          :aria-label="`纪要第 ${page.number} 页`"
        />
        <figcaption>第 {{ page.number }} / {{ pages.length }} 页</figcaption>
      </figure>
    </template>
  </section>
</template>

<style scoped>
.pdf-preview {
  margin-top: 12px;
  border: 1px solid var(--line);
  background: var(--el-fill-color-light);
}
.pdf-preview-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  padding: 12px;
  color: var(--ink);
  border-bottom: 1px solid var(--line);
  background: white;
}
.pdf-preview-links {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
}
.pdf-preview-links a,
.pdf-preview-copy-hint a {
  color: var(--el-color-primary-dark-2);
  text-underline-offset: 3px;
}
.pdf-preview-page {
  margin: 12px;
}
.pdf-preview-sheet {
  width: 100%;
  background: white;
}
.pdf-preview-sheet :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}
.pdf-preview-image {
  display: block;
  width: 100%;
  height: auto;
  background: white;
}
.pdf-preview-copy-hint {
  margin: 12px;
  color: var(--ink);
  line-height: 1.6;
}
figcaption {
  padding-top: 8px;
  color: var(--muted);
  text-align: center;
  font-size: 13px;
}
.pdf-preview-loading {
  min-height: 240px;
  padding: 16px;
  color: var(--muted);
}
.pdf-preview-error {
  padding: 16px;
  color: var(--el-color-danger-dark-2);
}
</style>
