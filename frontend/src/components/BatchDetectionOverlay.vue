<script setup lang="ts">
/**
 * BatchDetectionOverlay.vue — 批量检测叠加层（PR D / P1 3–5）
 * ------------------------------------------------------------------
 * 入口：查看器 URL 带 `batch_goal` + `batch_item` 查询参数时，向
 * `GET /api/goals/{goal}/items/{item}/overlay` 取已发布的 overlay.json，
 * 再把 overlay.png 与候选轮廓画到**每一格自己的影像坐标变换**上。
 *
 * 三条硬规矩：
 * 1. 只画身份匹配的格子：`source.image_key === imageRef(rec).image_key`，
 *    换了图、换了格、分屏另一侧都不会串；身份不匹配就整格不画。
 * 2. 位置每次从 `pane.view` + 该格缩略图尺寸现算（`overlayPaneRect`），
 *    缩放/平移/分屏拖分隔线自然跟随，不缓存屏幕坐标。
 * 3. 加载失败只显示一条原因，不画任何“看起来像检测结果”的东西；
 *    Mock 来源必须明确标注“非真实结果”。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { imageRef } from '../lib/spatial';
import {
  candidateScreenPoints, loadBatchOverlay, overlayFailureText, overlayImageUrl,
  overlayMatchesImage, overlayPaneRect, parseBatchOverlayParams, provenanceLabel,
} from '../lib/batchOverlay';
import type { BatchOverlayDoc } from '../lib/batchOverlay';
import type { PaneRect } from '../lib/viewMath';

const viewer = useViewerStore();
/** URL is only the **initial** target (old links keep working).  The Batch
 *  Panel / auto-follow can point the overlay at another Item without a page
 *  reload; validation still happens in the overlay document itself. */
const urlParams = parseBatchOverlayParams(
  typeof window === 'undefined' ? '' : window.location.search);
const params = computed(() => {
  if (viewer.batchOverlayOff) return null;
  const target = viewer.batchOverlayTarget;
  if (target) return { goalId: target.goalId, itemId: target.itemId };
  return urlParams;
});
const doc = ref<BatchOverlayDoc | null>(null);
const failure = ref<string | null>(null);
const loading = ref(false);
/** PNG 是独立资源：文档成功 ≠ 叠加层能显示。加载状态必须和文档状态分开。 */
const pngState = ref<'loading' | 'ready' | 'error'>('loading');
const pngEpoch = ref(0);
let controller: AbortController | null = null;
let requestSeq = 0;

async function refresh() {
  const target = params.value;
  if (!target) return;
  controller?.abort();
  controller = new AbortController();
  const seq = ++requestSeq;
  loading.value = true;
  const result = await loadBatchOverlay(target, { signal: controller.signal });
  if (seq !== requestSeq) return;
  loading.value = false;
  if (result.status === 'ready') {
    doc.value = result.doc;
    failure.value = null;
    return;
  }
  if (result.reason === 'aborted') return;
  doc.value = null;
  failure.value = result.reason;
}

watch(params, (target) => {
  requestSeq += 1;
  controller?.abort();
  controller = null;
  doc.value = null;
  failure.value = null;
  pngState.value = 'loading';
  if (target) void refresh();
}, { immediate: true });
onBeforeUnmount(() => {
  requestSeq += 1;
  controller?.abort();
  controller = null;
});

/** 图片内容哈希当缓存键：同一 Item 重新生成叠加层后刷新生效。 */
const imgSrc = computed(() => (doc.value
  ? overlayImageUrl(
    doc.value, undefined,
    `${doc.value.overlay.sha256 || doc.value.item_version}-r${pngEpoch.value}`)
  : ''));

// src 变化就是一次新的图片加载；同一次刷新（src 不变）不应把已就绪状态打回 loading。
watch(imgSrc, (value) => {
  if (value) pngState.value = 'loading';
});

function onPngLoad() {
  pngState.value = 'ready';
}

function onPngError() {
  pngState.value = 'error';
}

function retryPng() {
  pngEpoch.value += 1;
  pngState.value = 'loading';
}

const overlays = computed(() => {
  const current = doc.value;
  // 格子本身与 PNG 加载状态无关：<img> 必须先挂在 DOM 里才会触发 load/error。
  // 候选轮廓由模板按 `pngState === 'ready'` 单独控制，PNG 失败时整格隐藏。
  if (!current) return [];
  return viewer.panes.map((pane) => {
    const rec = pane.rec;
    if (!rec?.thumb) return null;
    const identity = imageRef(rec);
    if (!overlayMatchesImage(current, identity)) return null;
    // 每格先按 `pane.rect` 裁出一个窗口（`.batch-pane`），窗口内部才是
    // 「整幅影像」的矩形。所以影像矩形与候选点都用**格内坐标**：只含
    // 该格自己的 view 平移/缩放，绝不再叠加 pane.rect —— 那份偏移由外层
    // 裁剪窗口承担。否则分屏右格会把 pane.rect 加两遍。
    const local: PaneRect = {
      x: 0, y: 0, w: pane.rect.w, h: pane.rect.h,
    };
    const area = overlayPaneRect(
      identity!, rec.thumb.width, rec.thumb.height, pane.view, local);
    // 候选点画在 `.batch-image` 内。这个盒子本身已经按 view.ox/oy 定位，
    // 所以点只需要「缩略图 × scale」的影像内坐标，不能再带一次 view 平移。
    const imageLocalView = { scale: pane.view.scale, ox: 0, oy: 0 };
    const candidates = current.candidates.map((candidate) => {
      const points = candidateScreenPoints(
        candidate, identity!, rec.thumb!.width, rec.thumb!.height,
        imageLocalView, local);
      return points
        ? { id: candidate.candidate_id, points: points.map((p) => p.join(',')).join(' ') }
        : null;
    }).filter((item): item is { id: string; points: string } => item !== null);
    return {
      side: pane.side,
      clip: { left: pane.rect.x, top: pane.rect.y, width: pane.rect.w, height: pane.rect.h },
      area,
      candidates,
    };
  }).filter((item): item is NonNullable<typeof item> => item !== null);
});

const provenance = computed(() => doc.value?.provenance ?? null);
</script>

<template>
  <div v-if="params" class="batch-overlay" data-e2e="batch-overlay">
    <template v-if="pngState !== 'error'">
      <div
        v-for="pane in overlays"
        :key="pane.side"
        class="batch-pane"
        :style="{ left: pane.clip.left + 'px', top: pane.clip.top + 'px', width: pane.clip.width + 'px', height: pane.clip.height + 'px' }"
        :data-pane="pane.side"
      >
        <div
          class="batch-image"
          :style="{ left: pane.area.left + 'px', top: pane.area.top + 'px', width: pane.area.width + 'px', height: pane.area.height + 'px' }"
        >
          <img
            v-if="imgSrc"
            :src="imgSrc"
            class="batch-overlay-img"
            :data-sha256="doc?.overlay.sha256 || ''"
            data-e2e="batch-overlay-img"
            alt=""
            draggable="false"
            @load="onPngLoad"
            @error="onPngError"
          >
          <svg
            v-if="pngState === 'ready' && pane.candidates.length"
            class="batch-candidate-svg"
            width="100%"
            height="100%"
          >
            <polygon
              v-for="candidate in pane.candidates"
              :key="candidate.id"
              :points="candidate.points"
              class="batch-candidate"
              data-e2e="batch-overlay-candidate"
            />
          </svg>
        </div>
      </div>
    </template>

    <div
      v-if="provenance && pngState === 'ready'"
      class="batch-chip"
      :class="provenance"
      data-e2e="batch-overlay-chip"
      :data-provenance="provenance"
    >
      <span>检测叠加层 · {{ provenanceLabel(provenance) }}</span>
      <span v-if="doc" class="batch-chip-meta">{{ doc.candidates.length }} 个候选</span>
      <button type="button" class="batch-chip-btn" :disabled="loading" @click="refresh">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <div v-if="failure" class="batch-chip error" role="status" data-e2e="batch-overlay-error">
      <span>检测叠加层加载失败：{{ overlayFailureText(failure) }}</span>
      <button type="button" class="batch-chip-btn" :disabled="loading" @click="refresh">重试</button>
    </div>
    <div
      v-else-if="doc && pngState === 'error'"
      class="batch-chip error"
      role="status"
      data-e2e="batch-overlay-error"
    >
      <span>叠加层图片加载失败</span>
      <button type="button" class="batch-chip-btn" @click="retryPng">重试</button>
    </div>
  </div>
</template>

<style scoped>
.batch-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 6;
  overflow: hidden;
}
.batch-pane {
  position: absolute;
  overflow: hidden;
}
.batch-image {
  position: absolute;
}
.batch-overlay-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0.55;
  mix-blend-mode: screen;
}
.batch-candidate-svg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.batch-candidate {
  fill: none;
  stroke: var(--warn);
  stroke-width: 1.25;
  vector-effect: non-scaling-stroke;
}
.batch-chip {
  pointer-events: auto;
  position: absolute;
  left: 8px;
  bottom: 8px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border-radius: var(--r-pill);
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink-sub);
  font-size: 11px;
  box-shadow: var(--shadow-card);
}
.batch-chip.mock {
  border-color: var(--warn-line);
  background: var(--warn-bg);
  color: var(--warn);
}
.batch-chip.real {
  border-color: var(--ok-line);
  background: var(--ok-bg);
  color: var(--ok);
}
.batch-chip.error {
  bottom: 40px;
  border-color: var(--warn-line);
  color: var(--warn);
}
.batch-chip-meta { opacity: 0.75; }
.batch-chip-btn {
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
}
.batch-chip-btn:disabled { cursor: default; opacity: 0.6; }
</style>
