<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { showImagePreview, showToast } from "vant";
import * as pdfjsLib from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker;

const props = defineProps<{
  blob: Blob | null;
  filename?: string;
}>();

const pages = ref<string[]>([]);
const loading = ref(false);
const error = ref("");
const fullImageUrl = ref("");
const generatingImage = ref(false);
let renderToken = 0;

function cleanup() {
  if (fullImageUrl.value) {
    URL.revokeObjectURL(fullImageUrl.value);
    fullImageUrl.value = "";
  }
}

async function renderPdf(blob: Blob | null) {
  const token = ++renderToken;
  pages.value = [];
  error.value = "";
  cleanup();
  if (!blob) return;
  loading.value = true;
  try {
    const buffer = await blob.arrayBuffer();
    const doc = await pdfjsLib.getDocument({ data: buffer }).promise;
    const urls: string[] = [];
    for (let p = 1; p <= doc.numPages; p += 1) {
      if (token !== renderToken) return;
      const page = await doc.getPage(p);
      const viewport = page.getViewport({ scale: 1.5 });
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      if (!ctx) continue;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      await page.render({ canvasContext: ctx, viewport, canvas }).promise;
      urls.push(canvas.toDataURL("image/png"));
    }
    if (token === renderToken) pages.value = urls;
  } catch {
    if (token === renderToken)
      error.value = "PDF 预览渲染失败，请重试或直接下载查看";
  } finally {
    if (token === renderToken) loading.value = false;
  }
}

async function generateLongImage() {
  if (!pages.value.length || generatingImage.value) return;
  generatingImage.value = true;
  try {
    const imgs = await Promise.all(
      pages.value.map(
        (src) =>
          new Promise<HTMLImageElement>((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
          }),
      ),
    );
    const width = Math.max(...imgs.map((i) => i.width));
    const height = imgs.reduce((sum, i) => sum + i.height, 0);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);
    let y = 0;
    for (const img of imgs) {
      ctx.drawImage(img, 0, y);
      y += img.height;
    }
    const pngBlob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!pngBlob) throw new Error("生成失败");
    cleanup();
    fullImageUrl.value = URL.createObjectURL(pngBlob);
    showToast("完整长图已生成，长按图片可保存或转发企微群");
  } catch {
    showToast("长图生成失败，请稍后重试");
  } finally {
    generatingImage.value = false;
  }
}

function previewPage(index: number) {
  showImagePreview({
    images: pages.value,
    startPosition: index,
    closeable: true,
  });
}

function downloadPng() {
  if (!fullImageUrl.value) return;
  const a = document.createElement("a");
  a.href = fullImageUrl.value;
  a.download = (props.filename || "会议纪要").replace(/\.pdf$/i, "") + ".png";
  a.click();
}

watch(
  () => props.blob,
  (blob) => {
    void renderPdf(blob);
  },
  { immediate: true },
);

onBeforeUnmount(cleanup);
</script>

<template>
  <div class="pdf-box">
    <div v-if="loading" class="m-empty">
      <van-loading size="22">正在渲染会议纪要预览…</van-loading>
    </div>
    <div v-else-if="error" class="m-alert m-alert-error">{{ error }}</div>
    <template v-else-if="pages.length">
      <div class="pdf-toolbar">
        <span class="m-muted">共 {{ pages.length }} 页 · 点击页面可双指缩放</span>
        <button
          type="button"
          class="m-btn m-btn-sm m-btn-soft"
          :disabled="generatingImage"
          @click="generateLongImage"
        >
          {{ generatingImage ? "生成中…" : "生成完整长图" }}
        </button>
      </div>
      <div v-if="fullImageUrl" class="m-card" style="margin-bottom: 10px">
        <div
          style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
          "
        >
          <span style="font-size: 12px; font-weight: 600"
            >完整纪要长图（长按保存或转发）</span
          >
          <button
            type="button"
            class="m-btn m-btn-sm m-btn-primary"
            @click="downloadPng"
          >
            下载 PNG
          </button>
        </div>
        <img :src="fullImageUrl" class="pdf-img" alt="完整会议纪要长图" />
      </div>
      <div class="pdf-pages">
        <img
          v-for="(src, idx) in pages"
          :key="idx"
          :src="src"
          class="pdf-img"
          :alt="`第 ${idx + 1} 页`"
          @click="previewPage(idx)"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.pdf-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pdf-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pdf-pages {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pdf-img {
  width: 100%;
  display: block;
  border-radius: 8px;
  border: 1px solid var(--m-line);
  background: #ffffff;
}
</style>
