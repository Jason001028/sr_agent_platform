<script setup lang="ts">
/** Prefer existing lazy thumbnails; fall back to pixels already opened by the user. */
import { onBeforeUnmount, computed, ref, watch } from 'vue';
import { loadSrConfig, sceneImageUrl } from '../lib/scene';
import type { ViewerRec } from '../stores/viewer';

const props = defineProps<{ rec?: ViewerRec; size?: number; previewSrc?: string; detailSrc?: string }>();

const diskSrc = computed(() => sceneImageUrl(loadSrConfig(), props.rec?.card?.row.thumbnail_url ?? null));
const failed = ref(false);
watch(diskSrc, () => { failed.value = false; });
const showDisk = computed(() => !!diskSrc.value && !failed.value);

const BOX_H = 96;                       // 横幅形态的高度（与 CSS 一致）

const cv = ref<HTMLCanvasElement | null>(null);
const hasDrawing = ref(false);
watch(() => props.rec?.id, () => { hasDrawing.value = false; });
let ro: ResizeObserver | null = null;

function draw(): void {
  const el = cv.value;
  if (!el) return;
  const src = props.rec?.thumb as unknown as HTMLCanvasElement | null;
  // Keep the tiny rendered thumbnail after the detailed canvas is evicted.
  // No reference to the large source is retained by this component.
  if (!src || !src.width || !src.height) return;
  // 侧栏收起时 clientWidth 为 0（元素还在、只是被宽度 0 的父级压扁）：这时别改背衬
  // 尺寸，否则展开回来会是一张按错尺寸画出来的糊图。ResizeObserver 会补一次重绘。
  const boxW = el.clientWidth;
  const boxH = el.clientHeight || BOX_H;
  if (!boxW) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(boxW * dpr));
  const h = Math.max(1, Math.round(boxH * dpr));
  if (el.width !== w || el.height !== h) {
    el.width = w;
    el.height = h;
  }

  const ctx = el.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);

  // contain：整幅装进框里，不裁切 —— 预览是给判读用的（比色调、看有没有云），
  // 裁掉边缘会让人对构图产生错误印象。留白由容器底色兜着。
  const s = Math.min(w / src.width, h / src.height);
  const dw = src.width * s;
  const dh = src.height * s;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh);
  hasDrawing.value = true;
}

// thumb 出现 = 解码完成；paintedMode 变 = 换了拉伸模式（画布是原地重画的，
// 单看 thumb 的引用不会变）。两者之外这张图的像素不会再动。
watch(
  () => [props.rec?.thumb, props.rec?.paintedMode],
  () => draw(),
  { flush: 'post' },
);

// A failed image can create this canvas after mount. Rebind the observer and
// paint after Vue inserts it; a pre-flush thumb watcher would miss that frame.
watch(cv, (el) => {
  ro?.disconnect(); ro = null;
  if (el && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => draw());
    ro.observe(el);
  }
  draw();
}, { flush: 'post' });

onBeforeUnmount(() => {
  if (ro) { ro.disconnect(); ro = null; }
});
</script>

<template>
  <div
    class="ft"
    :class="{ compact: !!size }"
    :style="size ? { width: size + 'px', height: size + 'px' } : undefined"
  >
    <img v-if="showDisk" :key="diskSrc" class="ft-preview" :src="diskSrc" alt="" loading="lazy" decoding="async" draggable="false" @error="failed = true" />
    <template v-else-if="previewSrc">
      <img class="ft-preview" :src="previewSrc" alt="" draggable="false" />
      <img v-if="detailSrc" class="ft-preview ft-detail" :src="detailSrc" alt="" draggable="false" />
    </template>
    <canvas v-else-if="rec?.thumb || hasDrawing || !rec?.card" ref="cv" class="ft-cv"></canvas>
    <span v-if="!showDisk && !previewSrc && !rec?.thumb && !hasDrawing" class="ft-ph">{{ rec?.status || '未打开' }}</span>
  </div>
</template>

<style scoped>
.ft {
  position: relative;
  width: 100%;
  height: 96px;
  margin-bottom: 6px;
  border-radius: 8px;
  overflow: hidden;
  /* 中性底：缩略图两侧的留白不该有颜色倾向，否则判读明暗时会被底色带偏 */
  background: var(--surface-3);
  border: 1px solid var(--line);
}
.ft-preview { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
/* Aligned same-crop sources: retain moderate blur while restoring fine detail. */
.ft-detail { opacity: .55; }
.ft-cv { display: block; width: 100%; height: 100%; }
/* 方块形态（查看器左栏）：浅灰绿底把 contain 留出的边兜住；卡片是横向布局，
   它不能参与弹性收缩，否则栏宽不够时会被压窄。 */
.ft.compact {
  flex: none;
  margin-bottom: 0;
  border-radius: 8px;
  background: var(--chrome);
}
.ft-ph {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--ink-faint);
  text-align: center;
  padding: 0 8px;
}
/* 96px 里装不下整句状态：最多四行、超出省略。 */
.ft.compact .ft-ph {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 10px;
  line-height: 1.45;
  padding: 0 6px;
}
</style>
