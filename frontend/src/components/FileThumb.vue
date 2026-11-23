<script setup lang="ts">
/** Prefer existing lazy thumbnails; fall back to pixels already opened by the user. */
import { onBeforeUnmount, computed, ref, watch } from 'vue';
import { loadSrConfig, sceneImageUrl } from '../lib/scene';
import type { ViewerRec } from '../stores/viewer';

const props = defineProps<{
  rec?: ViewerRec;
  size?: number;
  previewSrc?: string;
  detailSrc?: string;
  standbyPhase?: 'resolving' | 'standby' | 'loading' | 'failed';
  scanActive?: boolean;
}>();

const diskSrc = computed(() => sceneImageUrl(loadSrConfig(), props.rec?.card?.row.thumbnail_url ?? null));
const failed = ref(false);
watch(diskSrc, () => { failed.value = false; });
const showDisk = computed(() => !props.standbyPhase && !!diskSrc.value && !failed.value);

const BOX_H = 96;                       // 横幅形态的高度（与 CSS 一致）

const cv = ref<HTMLCanvasElement | null>(null);
const hasDrawing = ref(false);
watch(() => props.rec?.id, () => { hasDrawing.value = false; });
let ro: ResizeObserver | null = null;
const phase = computed(() => props.standbyPhase && (hasDrawing.value ? 'ready' : props.standbyPhase));

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
    :data-phase="phase"
    :class="{ compact: !!size, scanning: scanActive }"
    :style="size ? { width: size + 'px', height: size + 'px' } : undefined"
  >
    <img v-if="showDisk" :key="diskSrc" class="ft-preview" :src="diskSrc" alt="" loading="lazy" decoding="async" draggable="false" @error="failed = true" />
    <template v-else-if="previewSrc">
      <img class="ft-preview" :src="previewSrc" alt="" draggable="false" />
      <img v-if="detailSrc" class="ft-preview ft-detail" :src="detailSrc" alt="" draggable="false" />
    </template>
    <canvas v-else-if="rec?.thumb || hasDrawing || !rec?.card" ref="cv" class="ft-cv"></canvas>
    <div v-if="standbyPhase" class="ft-standby" aria-hidden="true">
      <i class="ft-frame" />
      <i class="ft-corners" />
      <i class="ft-search" />
      <i class="ft-film" />
      <i class="ft-scan" />
    </div>
    <i v-if="standbyPhase && hasDrawing" class="ft-ready" aria-hidden="true" />
    <span v-if="!standbyPhase && !showDisk && !previewSrc && !rec?.thumb && !hasDrawing" class="ft-ph">{{ rec?.status || '未打开' }}</span>
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
/* Viewer sidebar only. All layers are positioned inside the fixed-size thumbnail. */
.ft[data-phase] {
  --standby-ease: cubic-bezier(.22,1,.36,1);
  --standby-ink: #678878;
  background: color-mix(in srgb, #93aa98 23%, var(--chrome));
  transition: border-color 240ms var(--standby-ease);
}
.ft[data-phase="standby"]:hover {
  border-color: color-mix(in srgb, var(--standby-ink) 60%, var(--line));
}
.ft-standby, .ft-standby > i, .ft-ready { position: absolute; pointer-events: none; }
.ft-standby { inset: 0; overflow: hidden; }
.ft-frame, .ft-corners {
  inset: 10px;
  border: 1px solid rgb(103 136 120 / .24);
  border-radius: 4px;
  transition: opacity 360ms var(--standby-ease), transform 360ms var(--standby-ease);
}
.ft-corners {
  inset: 16px;
  border: 0;
  --corner: linear-gradient(var(--standby-ink), var(--standby-ink));
  background:
    var(--corner) left top / 8px 1px, var(--corner) left top / 1px 8px,
    var(--corner) right top / 8px 1px, var(--corner) right top / 1px 8px,
    var(--corner) left bottom / 8px 1px, var(--corner) left bottom / 1px 8px,
    var(--corner) right bottom / 8px 1px, var(--corner) right bottom / 1px 8px;
  background-repeat: no-repeat;
  opacity: .64;
}
.ft-search, .ft-film {
  top: 50%;
  left: 50%;
  color: var(--standby-ink);
  transition: transform 360ms var(--standby-ease), opacity 360ms var(--standby-ease);
}
.ft-search {
  width: 18px;
  height: 18px;
  border: 1.5px solid currentColor;
  border-radius: 50%;
  transform: translate(-57%, -57%) scale(.75);
  opacity: 0;
}
.ft-search::after {
  content: '';
  position: absolute;
  width: 8px;
  height: 1.5px;
  right: -6px;
  bottom: -2px;
  background: currentColor;
  transform: rotate(45deg);
}
.ft-film {
  width: 30px;
  height: 22px;
  border: 1px solid currentColor;
  border-radius: 3px;
  background:
    repeating-linear-gradient(180deg, transparent 0 3px, currentColor 3px 5px) left 2px top / 2px 100% no-repeat,
    repeating-linear-gradient(180deg, transparent 0 3px, currentColor 3px 5px) right 2px top / 2px 100% no-repeat;
  transform: translate(-50%, -50%);
  opacity: .76;
}
.ft-film::after {
  content: '';
  position: absolute;
  inset: 4px 6px;
  background: currentColor;
  opacity: .45;
  clip-path: polygon(0 100%, 0 82%, 38% 33%, 59% 61%, 76% 42%, 100% 76%, 100% 100%);
}
.ft[data-phase="resolving"] .ft-search { opacity: .85; transform: translate(-57%, -57%); }
.ft[data-phase="resolving"] .ft-film { opacity: 0; transform: translate(-50%, -50%) scale(.8); }
.ft[data-phase="resolving"] .ft-corners { transform: scale(1.17); }
.ft[data-phase="loading"] :is(.ft-frame, .ft-corners, .ft-film) { transition-duration: 240ms; }
.ft[data-phase="loading"] :is(.ft-frame, .ft-corners) { opacity: 0; }
.ft[data-phase="loading"] .ft-film { opacity: .3; transform: translate(-50%, -50%) scale(.86); }
.ft[data-phase="failed"] .ft-film { opacity: .36; }
.ft-scan {
  inset: 7px 6px;
  opacity: 0;
  transition: opacity 240ms var(--standby-ease);
}
.ft[data-phase="loading"] .ft-scan { opacity: 1; }
.ft-scan::before {
  content: '';
  position: absolute;
  width: 100%;
  height: 1px;
  top: 50%;
  background: linear-gradient(90deg, transparent, #70bbaa 50%, transparent);
  box-shadow: 0 0 8px 2px rgb(112 187 170 / .28);
}
.ft[data-phase="loading"] .ft-scan::before {
  animation: ft-scan 2.3s var(--standby-ease) infinite paused;
}
.ft[data-phase="loading"].scanning .ft-scan::before { animation-play-state: running; }
@keyframes ft-scan {
  0% { top: 5%; opacity: 0; }
  20% { opacity: .72; }
  84% { opacity: .6; }
  100% { top: 95%; opacity: 0; }
}
.ft[data-phase="ready"] :is(.ft-frame, .ft-corners, .ft-film) { opacity: 0; }
.ft[data-phase="ready"] .ft-cv { animation: ft-reveal 620ms var(--standby-ease); }
@keyframes ft-reveal {
  from { clip-path: inset(0 0 100%); opacity: 0; scale: 1.035; }
  to { clip-path: inset(0); opacity: 1; scale: 1; }
}
.ft-ready {
  inset: 1px;
  border: 1px solid #83b5a0;
  border-radius: 6px;
  opacity: 0;
  animation: ft-ready 480ms var(--standby-ease) 440ms;
}
@keyframes ft-ready {
  0%, 100% { opacity: 0; }
  35% { opacity: .55; }
}
@media (prefers-reduced-motion: reduce) {
  .ft[data-phase], .ft-standby > i { transition: none; }
  .ft[data-phase="loading"] .ft-scan::before,
  .ft[data-phase="ready"] .ft-cv, .ft-ready { animation: none; }
  .ft-scan::before { top: 50%; }
}
</style>
