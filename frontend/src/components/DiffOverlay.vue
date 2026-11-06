<script setup lang="ts">
/**
 * DiffOverlay.vue — 差异候选的编号锚点 / 选中轮廓 / 信息浮层
 * ------------------------------------------------------------------
 * 只画**显示层**：连续热力图由 TifCanvas 在主画布按实际影像身份渲染，
 * 这里负责可交互的编号与轮廓。坐标都从分析网格换算到当前格的缩略图坐标，
 * 再经 thumbToScreen 跟随平移缩放；缩略图像素不会被当成原图坐标。
 *
 * 只显示排名靠前的少量候选（`viewerRegions`），其余在侧舱「差异候选」里；
 * 同一时刻最多展开一个区域。
 */
import DiffColorLegend from './DiffColorLegend.vue';
import { computed, onBeforeUnmount, onMounted } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useDiffStore } from '../stores/diff';
import { gridPointToThumb } from '../lib/diffLocate';
import { toneColor } from '../lib/diffTone';
import type { RegionToneStats } from '../lib/diffTone';
import { thumbToScreen } from '../lib/viewMath';

const emit = defineEmits<{ wheel: [event: WheelEvent] }>();
const viewer = useViewerStore();
const diff = useDiffStore();

interface Marker {
  id: string;
  rank: number;
  x: number;
  y: number;
  area: number;
  mean: number;
  max: number;
  relative: number;
  tone: RegionToneStats | null;
}

interface PaneView {
  side: 'A' | 'B';
  rect: { x: number; y: number; w: number; h: number };
  markers: Marker[];
  contour: string;
  contourStyle: { stroke: string; fill: string; strokeWidth: number } | null;
  selected: Marker | null;
}

const legendVisible = computed(() => diff.showHeat && viewer.panes.some(p => !!diff.layerFor(p.rec)));

const panes = computed<PaneView[]>(() => {
  const res = diff.result;
  if (!res || !diff.showMarkers || viewer.drawMode) return [];
  return viewer.panes.map((p) => {
    const rec = p.rec;
    if (!rec?.thumb || diff.recSide(rec.id) === null) {
      return {
        side: p.side, rect: p.rect, markers: [], contour: '',
        contourStyle: null, selected: null,
      };
    }
    const tw = rec.thumb.width;
    const th = rec.thumb.height;
    const markers: Marker[] = [];
    for (const reg of diff.viewerRegions) {
      const [tx, ty] = gridPointToThumb(res, reg.centroid[0], reg.centroid[1], tw, th);
      const [sx, sy] = thumbToScreen(p.view, tx, ty);
      if (sx < -16 || sx > p.rect.w + 16 || sy < -16 || sy > p.rect.h + 16) continue;
      markers.push({
        id: reg.id, rank: reg.rank, x: sx, y: sy,
        area: reg.areaPx, mean: reg.meanDiff, max: reg.maxDiff, relative: reg.relativeArea,
        tone: diff.toneWeight ? (reg.tone ?? null) : null,
      });
    }
    const sel = diff.selectedRegion;
    let contour = '';
    let contourStyle: PaneView['contourStyle'] = null;
    let selected: Marker | null = null;
    if (sel) {
      contour = sel.contour.map(([x, y]) => {
        const [tx, ty] = gridPointToThumb(res, x, y, tw, th);
        const [sx, sy] = thumbToScreen(p.view, tx, ty);
        return `${sx.toFixed(1)},${sy.toFixed(1)}`;
      }).join(' ');
      const [cx, cy] = gridPointToThumb(res, sel.centroid[0], sel.centroid[1], tw, th);
      const [sx, sy] = thumbToScreen(p.view, cx, cy);
      selected = {
        id: sel.id, rank: sel.rank, x: sx, y: sy,
        area: sel.areaPx, mean: sel.meanDiff, max: sel.maxDiff, relative: sel.relativeArea,
        tone: diff.toneWeight ? (sel.tone ?? null) : null,
      };
      if (sel.tone && diff.toneWeight) {
        const c = toneColor(sel.tone.norm);
        contourStyle = { stroke: c.stroke, fill: c.fill, strokeWidth: c.width };
      }
    }
    return { side: p.side, rect: p.rect, markers, contour, contourStyle, selected };
  }).filter((p) => p.markers.length || p.contour);
});

function pick(id: string, event: MouseEvent) {
  event.stopPropagation();
  if (diff.selectedId === id) {
    diff.selectRegion(null);
    return;
  }
  diff.selectRegion(id);
  diff.locateRegion(id);
}

function addMask(id: string, event: MouseEvent) {
  event.stopPropagation();
  diff.addRegionToMask(id);
}

function flicker(event: MouseEvent) {
  event.stopPropagation();
  diff.openFlicker();
}

function fmtArea(n: number): string {
  return n.toLocaleString('en-US');
}
function markerStyle(m: Marker): Record<string, string> {
  const style: Record<string, string> = { left: m.x + 'px', top: m.y + 'px' };
  if (m.tone) style.opacity = (0.6 + 0.4 * m.tone.norm).toFixed(3);
  return style;
}
function pct(v: number): string {
  return Math.round(v * 100) + '%';
}
function deltaText(d: number): string {
  return (d >= 0 ? '+' : '') + Math.round(d);
}

function popoverLeft(m: Marker, w: number): number {
  return Math.max(4, Math.min(w - 236, m.x + 18));
}
function popoverTop(m: Marker, h: number): number {
  return Math.max(4, Math.min(h - 132, m.y - 108));
}

function outside(e: Event) {
  if (viewer.drawMode) return;
  const el = e.target as Element | null;
  if (el?.closest?.('.diff-interactive, [data-diff-id], .diff-entry, .diff-visibility')) return;
  diff.selectRegion(null);
}
onMounted(() => window.addEventListener('pointerdown', outside));
onBeforeUnmount(() => window.removeEventListener('pointerdown', outside));
</script>

<template>
  <div
    v-if="panes.length || legendVisible"
    class="diff-overlay-root"
    :style="{ width: viewer.canvasSize.w + 'px', height: viewer.canvasSize.h + 'px' }"
  >
    <div v-if="legendVisible" class="viewer-legend"><DiffColorLegend /></div>
    <div
      v-for="p in panes"
      :key="p.side"
      class="diff-pane"
      :style="{ left: p.rect.x + 'px', top: p.rect.y + 'px', width: p.rect.w + 'px', height: p.rect.h + 'px' }"
    >
      <svg class="diff-geometry" width="100%" height="100%" aria-hidden="true">
      <polygon v-if="p.contour" class="diff-contour-halo" :points="p.contour" />
      <polygon v-if="p.contour" class="diff-contour" :points="p.contour" :style="p.contourStyle ?? undefined" />
    </svg>
    <button
      v-for="m in p.markers"
      :key="m.id"
      type="button"
      class="diff-marker diff-interactive"
      :class="{ hot: diff.hoveredId === m.id, on: diff.selectedId === m.id }"
      :style="markerStyle(m)"
      :aria-label="'差异候选 ' + m.rank + '，面积 ' + m.area + ' 像素'"
      :aria-pressed="diff.selectedId === m.id"
      :title="'#' + String(m.rank).padStart(2, '0') + ' · 面积 ' + fmtArea(m.area) + ' px²'"
      data-diff-id
      @pointerenter="diff.hoverRegion(m.id)"
      @pointerleave="diff.hoverRegion(null)"
      @focus="diff.hoverRegion(m.id)"
      @blur="diff.hoverRegion(null)"
      @wheel.prevent.stop="emit('wheel', $event)"
      @pointerdown.stop
      @click="pick(m.id, $event)"
    >
      {{ String(m.rank).padStart(2, '0') }}
    </button>
    <section
      v-if="p.selected"
      class="diff-popover-card diff-interactive"
      :style="{ left: popoverLeft(p.selected, p.rect.w) + 'px', top: popoverTop(p.selected, p.rect.h) + 'px' }"
      @pointerdown.stop
      @wheel.prevent.stop="emit('wheel', $event)"
    >
      <header>
        <strong>#{{ String(p.selected.rank).padStart(2, '0') }} 差异区域</strong>
        <button type="button" aria-label="关闭差异候选" title="关闭" @click="diff.selectRegion(null)">×</button>
      </header>
      <p>面积 {{ fmtArea(p.selected.area) }} px² · 相对面积 {{ (p.selected.relative * 100).toFixed(3) }}%</p>
      <p>平均差异 {{ p.selected.mean.toFixed(3) }} · 最大 {{ p.selected.max.toFixed(3) }}</p>
      <p v-if="p.selected.tone">
        灰 {{ Math.round(p.selected.tone.meanA) }} · 中间调 {{ pct(p.selected.tone.mid) }} ·
        平坦 {{ pct(p.selected.tone.flat) }} · 变化 {{ deltaText(p.selected.tone.delta) }} ·
        复核 ×{{ p.selected.tone.weight.toFixed(2) }}
      </p>
      <footer>
        <button type="button" @click="addMask(p.selected.id, $event)">加入掩码</button>
        <button type="button" @click="flicker($event)">交替对比</button>
      </footer>
    </section>
    </div>
  </div>
</template>

<style scoped>
.diff-overlay-root{position:absolute;top:0;left:50%;transform:translateX(-50%);z-index:4;pointer-events:none;overflow:visible}
.viewer-legend{position:absolute;left:14px;bottom:14px;width:220px;padding:9px 11px;border:1px solid var(--tb-border);border-radius:9px;background:var(--surface);box-shadow:0 2px 10px #0002;pointer-events:none}
.diff-pane{position:absolute;overflow:hidden;pointer-events:none}
.diff-geometry{position:absolute;inset:0;overflow:visible;pointer-events:none}
.diff-contour{fill:rgba(224,86,50,.12);stroke:#e05632;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.diff-contour-halo{fill:none;stroke:rgba(255,255,255,.9);stroke-width:4.5;stroke-linejoin:round}
.diff-marker{position:absolute;transform:translate(-50%,-50%);min-width:26px;height:20px;padding:0 5px;box-sizing:border-box;border:2px solid rgba(255,255,255,.95);border-radius:10px;background:#d95a33;color:#fff;font:600 10px/16px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.02em;cursor:pointer;box-shadow:0 1px 4px #0003;pointer-events:auto;transition:filter var(--motion-hover),background-color var(--motion-hover)}
.diff-marker:hover,.diff-marker.hot{filter:brightness(1.08);background:#c74a26}
.diff-marker.on{background:#a83a1c;box-shadow:0 0 0 3px rgba(224,86,50,.28),0 1px 4px #0003}
.diff-marker:focus-visible{outline:2px solid #e05632;outline-offset:2px}
.diff-popover-card{position:absolute;box-sizing:border-box;width:232px;max-height:160px;overflow:auto;background:var(--surface);border:1px solid #e2cfc8;border-radius:10px;padding:10px 11px;box-shadow:0 4px 16px #0002;color:var(--ink);font-size:11px;pointer-events:auto}
.diff-popover-card header{display:flex;align-items:center;justify-content:space-between;gap:8px}
.diff-popover-card strong{font-size:12px;font-weight:650}
.diff-popover-card header button{border:0;background:transparent;color:var(--ink-sub);font-size:16px;line-height:1;cursor:pointer;padding:0 2px}
.diff-popover-card p{margin:6px 0 0;color:var(--ink-sub);line-height:16px}
.diff-popover-card footer{display:flex;gap:6px;margin-top:9px}
.diff-popover-card footer button{flex:1;height:25px;border:1px solid #e2cfc8;border-radius:6px;background:var(--surface);color:#9d3b2f;font:inherit;font-size:10.5px;cursor:pointer}
.diff-popover-card footer button:hover{background:#fbe9e6}
@media(prefers-reduced-motion:reduce){.diff-marker{transition:none}}
</style>
