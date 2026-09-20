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
 * window keydown（Esc/Enter）、window dragover/drop（文件拖入；.txt 走待修复清单）。
 */
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import type { Pane } from '../stores/viewer';
import { useQcListStore } from '../stores/qclist';
import { mouseToThumb, thumbToScreen, paneAtX } from '../lib/viewMath';
import { dropSideAt } from '../lib/compare';
import type { Pt } from '../lib/maskgen';

const store = useViewerStore();
const qc = useQcListStore();
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
/** 分屏时把绘制上下文限制在活动格内，并把原点挪到该格左上角。
 *
 *  每格的 `ViewState` 就是**该格自己的局部屏幕坐标**，`store.view` 又是活动侧那一套，
 *  所以 translate 之后原先所有 `store.view.*` 的表达式逐字可用 —— 掩码/ROI/云叠/红叉
 *  一行都不用改，也不必给 `strokePoly` 加 view 参数。
 *
 *  单屏返回 false 且**什么都不做**：那是刻意保留的快速路径（`test-vue-viewer.js`
 *  的 D/E 段直接采样画布中心像素，多一趟 save/clip/translate 会带进亚像素差）。 */
function clipToActivePane(ctx: CanvasRenderingContext2D, ps: Pane[]): boolean {
  if (ps.length === 1) return false;
  const p = ps.find((x) => x.active) ?? ps[0];
  ctx.save();
  ctx.beginPath();
  ctx.rect(p.rect.x, p.rect.y, p.rect.w, p.rect.h);
  ctx.clip();
  ctx.translate(p.rect.x, p.rect.y);
  return true;
}

function render() {
  const vc = viewCanvasRef.value;
  const dc = drawCanvasRef.value;
  if (!vc || !dc) return;
  const ctx = vc.getContext('2d')!;
  ctx.clearRect(0, 0, vc.width, vc.height);
  const ps = store.panes;
  if (ps.length === 1) {
    // 单屏：与「一次一张」时期逐字相同的写法
    const rec = ps[0].rec;
    const v = ps[0].view;
    if (rec && rec.thumb) {
      ctx.imageSmoothingEnabled = v.scale < 4;
      ctx.drawImage(
        rec.thumb as unknown as CanvasImageSource,
        v.ox, v.oy, rec.thumb.width * v.scale, rec.thumb.height * v.scale,
      );
    }
  } else {
    for (const p of ps) {
      const rec = p.rec;
      if (!rec || !rec.thumb) continue;      // 空侧不画：占位由 CompareOverlay 出
      ctx.save();
      ctx.beginPath();
      ctx.rect(p.rect.x, p.rect.y, p.rect.w, p.rect.h);
      ctx.clip();
      ctx.translate(p.rect.x, p.rect.y);
      ctx.imageSmoothingEnabled = p.view.scale < 4;
      ctx.drawImage(
        rec.thumb as unknown as CanvasImageSource,
        p.view.ox, p.view.oy,
        rec.thumb.width * p.view.scale, rec.thumb.height * p.view.scale,
      );
      ctx.restore();
    }
  }
  renderDraw();
  // 像素定位红叉（约 7 秒，随缩放/平移保持在目标像素上）
  const m = store.marker;
  if (m) {
    const clipped = clipToActivePane(ctx, ps);
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
    if (clipped) ctx.restore();
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
  const active = store.activeRec;
  if (!active || !active.thumb) return;
  // 掩码/ROI/云叠/选中高亮**只画活动侧**：它们本来就读 store.getRois()
  // （= activeRec.maskRois）与 store.cloudOverlay，「跟随活动侧」是自然结果，
  // 不必按侧存两份。分屏时裁到活动格并平移原点，格内表达式逐字不变。
  const clipped = clipToActivePane(ctx, store.panes);
  try {
    renderDrawBody(ctx);
  } finally {
    if (clipped) ctx.restore();
  }
}

function renderDrawBody(ctx: CanvasRenderingContext2D) {
  const rois = store.getRois();
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  // 疑似云区红叠（非绘制模式，图层在图与 ROI 高亮之间；云叠 ≤2048 画布按同仿射放大上屏）。
  // 与 ROI 统计同源阈值：云叠 canvas 里已标红的像元 = 显示层 luma≥200 的高亮区（启发估算）。
  if (!store.drawMode && store.cloudShow && store.cloudOverlay) {
    const ov = store.cloudOverlay;
    ctx.drawImage(
      ov as unknown as CanvasImageSource,
      store.view.ox, store.view.oy,
      ov.width * store.view.scale, ov.height * store.view.scale,
    );
  }
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
  // 分屏下只要**任一侧**有图就该响应：活动侧恰好空着时也还能缩放另一侧。
  if (!vc || !store.panes.some((p) => p.rec && p.rec.thumb)) return;
  const r = vc.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
  store.onWheel(mx, my, f);
}

function onMouseDown(e: MouseEvent) {
  if (e.button !== 0) return;
  const ps = store.panes;
  if (ps.length > 1) {
    // 分屏：点在另一半上先把活动侧切过去（掩码/云量/任务状态跟着切）。
    // **滚轮刻意不切**：缩放时活动侧跟着闪会让人以为选错了图。
    const vc = viewCanvasRef.value;
    if (vc) {
      const r = vc.getBoundingClientRect();
      store.setActiveSide(paneAtX(e.clientX - r.left, store.splitX));
    }
  }
  if (!store.activeRec || !store.activeRec.thumb) return;
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

/** 拖放落点归哪一格。compare 关闭 → null（全窗口都是拖放目标，落点不参与决策）。 */
function dropSide(e: DragEvent): 'A' | 'B' | null {
  if (!store.compareOn) return null;
  const vc = viewCanvasRef.value;
  if (!vc) return null;
  return dropSideAt(e.clientX, e.clientY, vc.getBoundingClientRect(),
                    store.compareMode, store.splitX);
}

function onDragOver(e: DragEvent) {
  // **永远 preventDefault**：不拦的话浏览器会导航到拖进来的文件。
  e.preventDefault();
  // **dropEffect 永远 copy**：设成 'none' 能拿到系统的「禁止」光标，但按规范它同时会
  // 抑制 drop 事件 —— 而 .txt 必须在任何模式、任何位置都能进待修复清单。且 dragover
  // 阶段浏览器不暴露文件名（只在 drop 阶段有），没法按类型区分。所以视觉提示只由
  // CompareOverlay 负责，真正的门在 onDrop 里。
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  if (!store.compareOn) { store.setDragHint(false, null); return; }
  // 提示在画布外不显示（dropSide 回 null），由 store 的 500ms 定时器收尾
  store.setDragHint(true, dropSide(e));
}

function onDrop(e: DragEvent) {
  e.preventDefault();
  store.setDragHint(false, null);
  const files = e.dataTransfer?.files;
  if (!files || !files.length) return;
  // 按扩展名分流：`.txt` = 待修复清单（走 qc store），其余是影像（走解码管线）。
  // 两类可以一起拖进来，各走各的；多个 .txt 只取第一个（清单同时只有一份）。
  // **.txt 先走，且无条件**：任何模式、画布内外都照旧导入。
  const all = Array.from(files);
  const txt = all.filter((f) => /\.txt$/i.test(f.name));
  const rest = all.filter((f) => !/\.txt$/i.test(f.name));
  if (txt.length) void qc.importFile(txt[0]);
  if (!rest.length) return;
  let side: 'A' | 'B' | undefined;
  if (store.compareOn) {
    const hit = dropSide(e);
    if (hit === null) {
      store.showToast('图像对比模式下只能把影像拖到画布上');
      return;
    }
    side = hit;
  }
  store.addFiles(rest, side);
}

/** 拖出窗口 / 拖放结束 → 立刻熄掉落位提示（定时器之外的另一道手）。 */
function clearDragHint() { store.setDragHint(false, null); }

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
  window.addEventListener('dragend', clearDragHint);
  window.addEventListener('blur', clearDragHint);
  window.addEventListener('mousemove', onWindowMouseMove);
  window.addEventListener('mouseup', onWindowMouseUp);
});

onBeforeUnmount(() => {
  if (resizeObs) resizeObs.disconnect();
  window.removeEventListener('keydown', store.onKeyDown);
  window.removeEventListener('dragover', onDragOver);
  window.removeEventListener('drop', onDrop);
  window.removeEventListener('dragend', clearDragHint);
  window.removeEventListener('blur', clearDragHint);
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
