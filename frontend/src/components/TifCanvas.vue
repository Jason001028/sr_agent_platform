<script setup lang="ts">
/**
 * TifCanvas.vue — 查看器双画布 + 全部鼠标/滚轮/拖放交互（tif-viewer.html 直译）
 * ------------------------------------------------------------------
 * viewCanvas：渲染当前缩略图（按 view 仿射）+ 像素定位红叉；drawCanvas：掩码绘制叠加层
 * （pointer-events:none，与 viewCanvas 同几何）。渲染由 store.renderTick 驱动
 * （HTML 各处直接调 render()/renderDraw() → Vue watch renderTick 后重绘，语义等价）。
 *
 * 事件：wheel 缩放、mousedown/move/up（绘制模式分派 rect/polygon/wand/del，否则平移）、
 * dblclick（drawMode→closePolygon，否则 fit）、contextmenu（drawMode→closePolygon）、
 * window keydown（Esc/Enter）、window dragover/drop（文件拖入）。
 */
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { mouseToThumb, thumbToScreen } from '../lib/viewMath';
import type { Pt } from '../lib/maskgen';

const store = useViewerStore();
const stageRef = ref<HTMLDivElement | null>(null);
const viewCanvasRef = ref<HTMLCanvasElement | null>(null);
const drawCanvasRef = ref<HTMLCanvasElement | null>(null);
const dragging = ref(false);
let lastX = 0;
let lastY = 0;
let resizeObs: ResizeObserver | null = null;

/* ---------------- 设计令牌 → canvas 覆盖层色（阶段6 侧舱选中 ROI 高亮） ----------------
   canvas 不能直接写 var(--x)，这里运行期从根元素读令牌。仅新增覆盖层用令牌取色；
   既有影像判读专用覆盖色（ROI 青绿 / 魔棒琥珀 / 删除红等，tif-viewer 直译）不在改造范围。 */
function tokenColor(name: string, fallback: string): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}
function hexToRgba(hex: string, a: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
}
let selPalette: { line: string; halo: string; fill: string } | null = null;
function selectedPalette() {
  if (!selPalette) {
    const line = tokenColor('--accent-2', '#3DA9A4');
    selPalette = { line, halo: 'rgba(255,255,255,0.9)', fill: hexToRgba(line, 0.12) };
  }
  return selPalette;
}

/* ---------------- 尺寸同步 ---------------- */
function resize() {
  const stage = stageRef.value;
  const vc = viewCanvasRef.value;
  const dc = drawCanvasRef.value;
  if (!stage || !vc || !dc) return;
  const w = stage.clientWidth, h = stage.clientHeight;
  if (vc.width === w && vc.height === h) return;
  vc.width = w; vc.height = h;
  dc.width = w; dc.height = h;
  store.setCanvasSize(w, h);
}

/* ---------------- 渲染 ---------------- */
function render() {
  const vc = viewCanvasRef.value;
  const dc = drawCanvasRef.value;
  if (!vc || !dc) return;
  const ctx = vc.getContext('2d')!;
  ctx.clearRect(0, 0, vc.width, vc.height);
  const rec = store.activeRec;
  if (rec && rec.thumb) {
    ctx.imageSmoothingEnabled = store.view.scale < 4;
    ctx.drawImage(
      rec.thumb as unknown as CanvasImageSource,
      store.view.ox, store.view.oy,
      rec.thumb.width * store.view.scale, rec.thumb.height * store.view.scale,
    );
  }
  renderDraw();
  // 像素定位红叉（约 7 秒，随缩放/平移保持在目标像素上）
  const m = store.marker;
  if (m) {
    const sx = store.view.ox + m.tx * store.view.scale;
    const sy = store.view.oy + m.ty * store.view.scale;
    const L = 14;
    ctx.strokeStyle = '#ff4040';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(sx - L, sy); ctx.lineTo(sx + L, sy);
    ctx.moveTo(sx, sy - L); ctx.lineTo(sx, sy + L);
    ctx.stroke();
    ctx.fillStyle = '#ff4040';
    ctx.fillRect(sx - 1.5, sy - 1.5, 3, 3);
  }
}

function strokePoly(pts: Pt[], fill: string, stroke: string, lw: number, open: boolean) {
  if (!pts || pts.length < 2) return;
  const dc = drawCanvasRef.value;
  if (!dc) return;
  const ctx = dc.getContext('2d')!;
  const a = thumbToScreen(store.view, pts[0][0], pts[0][1]);
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  for (let i = 1; i < pts.length; i++) {
    const p = thumbToScreen(store.view, pts[i][0], pts[i][1]);
    ctx.lineTo(p[0], p[1]);
  }
  if (!open && pts.length >= 3) ctx.closePath();
  if (!open && pts.length >= 3 && fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw || 2; ctx.stroke(); }
}

/* 侧舱选中 ROI 高亮（非绘制模式也可见）：白 halo 分离影像 → 青绿主线 + 极浅填充 */
function drawSelectedOutline(pts: Pt[]) {
  if (!pts || pts.length < 3) return;
  const dc = drawCanvasRef.value;
  if (!dc) return;
  const ctx = dc.getContext('2d')!;
  const p = selectedPalette();
  ctx.globalAlpha = 0.9;
  strokePoly(pts, '', p.halo, 5, false);
  ctx.globalAlpha = 0.14;
  strokePoly(pts, p.fill, '', 0, false);
  ctx.globalAlpha = 1;
  strokePoly(pts, '', p.line, 2, false);
  ctx.globalAlpha = 1;
}

function renderDraw() {
  const dc = drawCanvasRef.value;
  if (!dc) return;
  const ctx = dc.getContext('2d')!;
  ctx.clearRect(0, 0, dc.width, dc.height);
  if (!store.activeRec || !store.activeRec.thumb) return;
  const rois = store.getRois();
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  if (!store.drawMode) {
    // 非绘制模式：只画侧舱选中 ROI 高亮（默认无选择 → 与原行为一致：无叠加层）
    const si = store.roiSelIndex();
    if (si >= 0 && si < rois.length) drawSelectedOutline(rois[si]);
    return;
  }
  for (let i = 0; i < rois.length; i++) {
    strokePoly(rois[i], 'rgba(45,164,162,0.22)', '#2da4a2', 2, false);
  }
  if (store.drawTool === 'del') {
    // 删除工具叠加层：悬停柔和红边 + 待删区域柔和红闪（盖在普通蓝色之上，不辣眼）
    if (store.hoverRoi >= 0 && store.hoverRoi < rois.length) {
      strokePoly(rois[store.hoverRoi], 'rgba(216,90,80,0.14)', '#d8605a', 1.5, false);
    }
    if (store.flashRoi >= 0 && store.flashRoi < rois.length) {
      strokePoly(rois[store.flashRoi], 'rgba(216,90,80,0.5)', '#d8605a', 2.5, false);
    }
  }
  if (store.pendingPts && store.pendingPts.length) {
    strokePoly(store.pendingPts, 'rgba(255,176,46,0.15)', '#ffb02e', 2, true);
    const last = store.pendingPts[store.pendingPts.length - 1];
    if (store.hoverPt) {
      const a = thumbToScreen(store.view, last[0], last[1]);
      const b = thumbToScreen(store.view, store.hoverPt[0], store.hoverPt[1]);
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = '#ffb02e';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    for (const p of store.pendingPts) {
      const pt = thumbToScreen(store.view, p[0], p[1]);
      ctx.fillStyle = '#ffb02e';
      ctx.fillRect(pt[0] - 3, pt[1] - 3, 6, 6);
    }
  }
  if (store.pendingRect) {
    const ra = thumbToScreen(store.view, store.pendingRect.x0, store.pendingRect.y0);
    const rb = thumbToScreen(store.view, store.pendingRect.x1, store.pendingRect.y1);
    const rx = Math.min(ra[0], rb[0]), ry = Math.min(ra[1], rb[1]);
    const rw = Math.abs(rb[0] - ra[0]), rh = Math.abs(rb[1] - ra[1]);
    ctx.fillStyle = 'rgba(45,164,162,0.22)';
    ctx.strokeStyle = '#2da4a2';
    ctx.lineWidth = 2;
    ctx.fillRect(rx, ry, rw, rh);
    ctx.strokeRect(rx, ry, rw, rh);
  }
}

/* ---------------- 交互事件 ---------------- */
function mousePos(e: MouseEvent): Pt {
  const vc = viewCanvasRef.value!;
  const rect = vc.getBoundingClientRect();
  return mouseToThumb(store.view, e.clientX, e.clientY, { left: rect.left, top: rect.top });
}

function onWheel(e: WheelEvent) {
  const vc = viewCanvasRef.value;
  if (!vc || !store.activeRec || !store.activeRec.thumb) return;
  const r = vc.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
  store.onWheel(mx, my, f);
}

function onMouseDown(e: MouseEvent) {
  if (e.button !== 0 || !store.activeRec || !store.activeRec.thumb) return;
  const p = mousePos(e);
  if (store.onCanvasDownDraw(p)) return;   // 绘制模式已处理
  dragging.value = true;
  lastX = e.clientX;
  lastY = e.clientY;
}

function onWindowMouseMove(e: MouseEvent) {
  if (store.drawMode) { store.onCanvasMove(mousePos(e)); return; }
  if (!dragging.value) return;
  store.onPan(e.clientX - lastX, e.clientY - lastY);
  lastX = e.clientX;
  lastY = e.clientY;
}

function onWindowMouseUp(e: MouseEvent) {
  if (store.drawMode && store.pendingRect) {
    store.onCanvasUp(mousePos(e));
    return;
  }
  dragging.value = false;
}

function onDblClick() {
  store.onDblClick();
}

function onContextMenu(e: MouseEvent) {
  if (store.drawMode) { e.preventDefault(); store.closePolygon(); }
}

function onDragOver(e: DragEvent) { e.preventDefault(); }

function onDrop(e: DragEvent) {
  e.preventDefault();
  if (e.dataTransfer && e.dataTransfer.files) store.addFiles(e.dataTransfer.files);
}

/* ---------------- 生命周期：重绘信号 + 尺寸 + window 事件 ---------------- */
watch(
  () => store.renderTick,
  () => render(),
);

onMounted(() => {
  resize();
  if (stageRef.value) {
    resizeObs = new ResizeObserver(() => resize());
    resizeObs.observe(stageRef.value);
  }
  window.addEventListener('keydown', store.onKeyDown);
  window.addEventListener('dragover', onDragOver);
  window.addEventListener('drop', onDrop);
  window.addEventListener('mousemove', onWindowMouseMove);
  window.addEventListener('mouseup', onWindowMouseUp);
});

onBeforeUnmount(() => {
  if (resizeObs) resizeObs.disconnect();
  window.removeEventListener('keydown', store.onKeyDown);
  window.removeEventListener('dragover', onDragOver);
  window.removeEventListener('drop', onDrop);
  window.removeEventListener('mousemove', onWindowMouseMove);
  window.removeEventListener('mouseup', onWindowMouseUp);
});
</script>

<template>
  <div
    ref="stageRef"
    class="stage"
    @dblclick="onDblClick"
    @contextmenu="onContextMenu"
  >
    <canvas
      ref="viewCanvasRef"
      class="view-canvas tif-canvas"
      :class="{ dragging, crosshair: store.drawMode }"
      @wheel="onWheel"
      @mousedown="onMouseDown"
    ></canvas>
    <canvas ref="drawCanvasRef" class="draw-canvas"></canvas>
    <slot></slot>
  </div>
</template>

<style scoped>
.stage {
  position: relative;
  flex: 1;
  overflow: hidden;
  /* 画布井位：浅中性面（影像区不铺莫兰迪绿，避免干扰调色判读） */
  background: #e9edeb;
}

.view-canvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
}
.view-canvas.dragging {
  cursor: grabbing;
}
.view-canvas.crosshair {
  cursor: crosshair;
}

.draw-canvas {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
