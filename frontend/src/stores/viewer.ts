/**
 * stores/viewer.ts — 查看器状态 + 编排（tif-viewer.html 交互层 Vue 化）
 * ------------------------------------------------------------------
 * 「移植不重写」：HTML 全局状态（recs/activeRec/view/curStretch/drawMode/exportQueue/
 * outHandle/...）平移到 Pinia store；动作逐函数直译（activate/decode/paintStretch/
 * 掩码/导出/落盘），仅把「直接操作 DOM」改为「状态变更 + renderTick 驱动 TifCanvas 重绘」。
 *
 * 重字段（Float32Array src / canvas thumb / BandStats stats）用 markRaw 存放，避免深代理。
 * renderTick 计数器是 TifCanvas 的重绘信号：任何影响视图的状态变更后 ++，TifCanvas watch 后重绘
 * （语义等价 HTML 各处直接调 render()/renderDraw()）。
 */
import { defineStore } from 'pinia';
import { ref, computed, markRaw } from 'vue';
import {
  probeImage, tiffTags, layoutInfo, stretchRgba, JPG_QUALITY,
  setSparseMin as setSparseMinLib,
} from '../lib/tifDecode.js';
import type { ProbeInfo, BandStats, StretchMode, KitCanvas } from '../lib/tifDecode.js';
import type { Poly, Pt } from '../lib/maskgen.js';
import {
  floodSelect, fillRegionHoles, traceContour, simplifyPoly,
  mergeConnectedAsync, buildTiff, buildMaskTxt,
} from '../lib/maskgen.js';
import { fitView, locateView, wheelZoom, hitRoi, thumbToOrig } from '../lib/viewMath.js';
import type { ViewState } from '../lib/viewMath.js';
import { FileSource } from '../lib/source.js';
import { browserKit } from '../lib/browserKit.js';
import { decodeOne } from '../lib/decode.js';
import type { DecodedRec } from '../lib/decode.js';
import { loadSrConfig, sceneDecodePixels } from '../lib/scene.js';
import type { SceneOpenMeta } from '../lib/scene.js';
import { apiBakeMask } from '../lib/api.js';
import type { BakeMaskBody } from '../lib/api.js';
import { exportToJpg } from '../lib/exportJpg.js';
import { getSaver, fsIO, setOutDirListener, downloadBlob } from '../lib/saver.js';
import type { OutDirState } from '../lib/saver.js';
import { useQueueStore } from './queue.js';
import router from '../router/index.js';

export type DrawTool = 'rect' | 'polygon' | 'wand' | 'del';

/** 一条解码记录（HTML rec 直译；thumb/src/stats 为 markRaw 重字段） */
export interface ViewerRec {
  id: number;
  file: File;
  probe: ProbeInfo | null;
  name: string;
  size: number;
  W: number;
  H: number;
  thumb: KitCanvas | null;
  src: Float32Array | null;
  srcw: number;
  srch: number;
  nbands: number;
  invert: boolean;
  stats: BandStats[] | null;
  /** 解码路由；'jpg' = 盘阵场景（服务器烘焙 JPG，无本地 TIF 字节） */
  route: 'utif' | 'sparse' | 'chunked' | 'jpg' | null;
  /** 阶段4 不透明场景 id（route='jpg' 时必有；掩码烘焙 POST /api/masks 用）。 */
  sceneId: string | null;
  layout: string;              // 即探标签摘要
  status: string;
  statusCls: '' | 'ok' | 'err';
  paintedMode: StretchMode | null;
  maskRois: Poly[] | null;     // 缩略图坐标 ROI
  _jpgBusy: boolean;
  _jpgDone: boolean;
  _jpgToken: number;
  _exportCap?: number;
  jpgStatus: string;
  jpgCls: '' | 'ok' | 'err';
}

interface ExportJob {
  rec: ViewerRec;
  token: number;
}

const WAND_WIN = 4096;
const WAND_EDGE = 64;

/** 盘阵 JPG Blob → 全尺寸缩略图画布（服务器已 2% 拉伸，尺寸即 JPG 原生尺寸）。 */
async function decodeJpgToCanvas(blob: Blob): Promise<HTMLCanvasElement> {
  const bmp = await createImageBitmap(blob);
  try {
    const cv = browserKit.createCanvas(bmp.width, bmp.height) as unknown as HTMLCanvasElement;
    const ctx = cv.getContext('2d');
    if (!ctx) throw new Error('取不到 2d 上下文');
    ctx.drawImage(bmp, 0, 0);
    return cv;
  } finally {
    bmp.close();
  }
}

function fmtBytes(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}
function fmtTime(ms: number): string {
  if (ms >= 60000) return (ms / 60000).toFixed(1) + ' 分钟';
  if (ms >= 1000) return (ms / 1000).toFixed(1) + ' 秒';
  return ms + ' 毫秒';
}

let nextId = 1;
let markerTimer: ReturnType<typeof setTimeout> | null = null;
let toastTimer: ReturnType<typeof setTimeout> | null = null;
let errTimer: ReturnType<typeof setTimeout> | null = null;

export const useViewerStore = defineStore('viewer', () => {
  /* ---------------- 状态 ---------------- */
  const recs = ref<ViewerRec[]>([]);
  const activeId = ref<number | null>(null);
  const view = ref<ViewState>({ scale: 1, ox: 0, oy: 0 });
  const canvasSize = ref({ w: 0, h: 0 });
  const renderTick = ref(0);
  const marker = ref<{ tx: number; ty: number; until: number } | null>(null);
  const stretchMode = ref<StretchMode>('linear');
  const drawMode = ref(false);
  const drawTool = ref<DrawTool>('rect');
  const pendingRect = ref<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const pendingPts = ref<Pt[] | null>(null);
  const hoverPt = ref<Pt | null>(null);
  const hoverRoi = ref(-1);
  const flashRoi = ref(-1);
  const wandTol = ref(20);
  const merging = ref(false);
  const autoExport = ref(true);
  const outDir = ref<OutDirState>({ name: '', permission: 'prompt', ready: false });
  const exportQueue = ref<ExportJob[]>([]);
  const exportingNow = ref(false);
  const overlay = ref({ visible: false, title: '', sub: '', bar: false, progress: 0 });
  const toast = ref('');
  const error = ref('');
  const sidebarCollapsed = ref(false);
  const busy = ref(false);
  const srBusy = ref(false);       // 「提交 SR」进行中（掩码服务端烘焙）

  const activeRec = computed<ViewerRec | null>(() =>
    recs.value.find((r) => r.id === activeId.value) || null,
  );

  /* ---------------- 文件打开（HTML openFiles/openOne） ---------------- */
  function addFiles(fileList: FileList | File[] | File) {
    const files = Array.isArray(fileList)
      ? fileList
      : Array.from(fileList instanceof File ? [fileList] : fileList);
    const tiffs = files.filter(
      (f) => /\.tiff?$/i.test(f.name) || (f.type && f.type.indexOf('tiff') !== -1),
    );
    if (!tiffs.length) { showErr('没有识别到 tif/tiff 文件'); return; }
    tiffs.forEach((f) => {
      const dup = recs.value.some((r) => r.file.name === f.name && r.file.size === f.size);
      if (!dup) openOne(f);
    });
  }

  function openOne(file: File) {
    const rec: ViewerRec = {
      id: nextId++, file: markRaw(file),
      probe: null, name: file.name, size: file.size,
      W: 0, H: 0, thumb: null, src: null, srcw: 0, srch: 0, nbands: 0, invert: false,
      stats: null, route: null, sceneId: null, layout: '', status: '等待…', statusCls: '',
      paintedMode: null, maskRois: null,
      _jpgBusy: false, _jpgDone: false, _jpgToken: 0, jpgStatus: '', jpgCls: '',
    };
    recs.value.push(rec);
    // 即探：只读头部标签，秒显压缩/布局，不等待解码
    tiffTags(new FileSource(file, rec.name)).then((tags) => {
      rec.layout = layoutInfo(tags);
    }).catch(() => { /* 头部解析失败不阻塞 */ });
    void activate(rec.id);
  }

  /* ---------------- 激活 / 解码（HTML activate + decodeGeoTiff/decodeUtif） ---------------- */
  async function activate(id: number) {
    const rec = recs.value.find((r) => r.id === id);
    if (!rec) return;
    if (activeId.value === id && rec.thumb) {
      if (rec.paintedMode !== stretchMode.value) paintStretch(rec);
      renderTick.value++;
      return;
    }
    marker.value = null;
    activeId.value = id;
    if (drawMode.value) {
      pendingPts.value = null; pendingRect.value = null; hoverPt.value = null;
    }
    if (rec.thumb) {
      if (rec.paintedMode !== stretchMode.value) paintStretch(rec);
      fit();
      renderTick.value++;
      return;
    }
    await decodeRec(rec);
  }

  async function decodeRec(rec: ViewerRec) {
    busy.value = true;
    let probe: ProbeInfo | null = null;
    try { probe = await probeImage(rec.file); } catch (e) { probe = null; }
    rec.probe = probe;
    if (!probe) {
      rec.status = '解码失败：无法读取文件属性';
      rec.statusCls = 'err';
      if (activeId.value === rec.id) showErr('「' + rec.name + '」解码失败：无法读取文件属性');
      busy.value = false;
      return;
    }
    const source = new FileSource(rec.file, rec.name);
    try {
      const d = await decodeOne(source, probe, rec.file, browserKit, {
        onOverlay: (title, sub, bar) => showMask(title, sub, bar),
        onProgress: (f) => { if (activeId.value === rec.id) updateProgressUI(f); },
      });
      applyDecoded(rec, d);
    } catch (e) {
      failRec(rec, e);
    } finally {
      busy.value = false;
      hideMask();
    }
  }

  function applyDecoded(rec: ViewerRec, d: DecodedRec) {
    rec.thumb = markRaw(d.thumb);
    rec.W = d.W; rec.H = d.H;
    rec.src = markRaw(d.src);
    rec.srcw = d.srcw; rec.srch = d.srch;
    rec.nbands = d.nbands;
    rec.invert = d.invert;
    rec.stats = d.stats ? markRaw(d.stats) : null;
    rec.route = d.route;
    const routeText = d.route === 'sparse'
      ? '（稀疏条带预览：' + d.sparseInfo + '）'
      : '';
    rec.status = '完成，' + (d.route === 'utif' ? '解码' : '读取') + '耗时 ' + fmtTime(d.ms) + routeText;
    rec.statusCls = 'ok';
    paintStretch(rec);
    kickExport(rec);                 // 解码成功 → 自动生成 JPG 中间产物
    if (activeId.value === rec.id) { fit(); renderTick.value++; }
  }

  function failRec(rec: ViewerRec, e: unknown) {
    let msg = e instanceof Error ? e.message : String(e);
    if (rec.probe) {
      msg += '  [属性 ' + rec.probe.W + '×' + rec.probe.H + '，' + rec.probe.bits + 'bit/' +
        rec.probe.spp + '通道，类型' + rec.probe.sampleFormat + '，压缩' +
        (rec.probe.compression != null ? rec.probe.compression : '?') + ']';
    }
    rec.status = '解码失败：' + msg;
    rec.statusCls = 'err';
    if (activeId.value === rec.id) showErr('「' + rec.name + '」解码失败：' + msg);
  }

  /* ---------------- 盘阵场景（阶段4：读服务器烘焙 JPG，route='jpg'） ----------------
     JPG 即显示产物（稀疏采样 + 2% 线性拉伸已在服务器烤好）：不再读原始 TIF 字节、
     不做二次拉伸、不本地导出 JPG（服务器 JPG 即交付物）。掩码仍照旧 —— 缩略图坐标
     按元数据 W/H 换算回全分辨率（thumbToOrig scale 来自 rec.W/H 而非 probe）。 */
  async function openSceneJpg(meta: SceneOpenMeta, blob: Blob) {
    const dup = recs.value.find((r) => r.name === meta.name);
    if (dup) { void activate(dup.id); return; }
    busy.value = true;
    showMask('正在加载盘阵场景…', meta.name + '（服务器烘焙 JPG，元数据 ' + meta.W + '×' + meta.H + '）', false);
    try {
      const cv = await decodeJpgToCanvas(blob);
      const tw = cv.width, th = cv.height;
      const ctx = cv.getContext('2d');
      if (!ctx) throw new Error('取不到 2d 上下文');
      const img = ctx.getImageData(0, 0, tw, th);
      const d = sceneDecodePixels(img.data, tw, th);   // 固定 0..255 → linear 恒等
      const rec: ViewerRec = {
        id: nextId++,
        file: markRaw(new File([blob], meta.name + '.jpg', { type: 'image/jpeg' })),
        probe: null, name: meta.name, size: blob.size,
        W: meta.W, H: meta.H,
        thumb: markRaw(cv as unknown as KitCanvas),
        src: markRaw(d.src), srcw: d.tw, srch: d.th, nbands: d.nbands,
        invert: false, stats: d.stats ? markRaw(d.stats) : null,
        route: 'jpg', sceneId: meta.sceneId ?? null,
        layout: '盘阵 JPG（已烘焙 2% 线性拉伸）',
        status: '场景就绪：' + meta.name + ' · 元数据 ' + meta.W + '×' + meta.H + ' · JPG ' + d.tw + '×' + d.th,
        statusCls: 'ok', paintedMode: null, maskRois: null,
        _jpgBusy: false, _jpgDone: true,   // 服务器 JPG 即交付物：无本地再导出
        _jpgToken: 0, jpgStatus: '', jpgCls: '',
      };
      recs.value.push(rec);
      hideMask(); busy.value = false;
      void activate(rec.id);
      showToast('已打开盘阵场景「' + meta.name + '」（掩码按元数据 ' + meta.W + '×' + meta.H + ' 换算）');
    } catch (e) {
      hideMask(); busy.value = false;
      showErr('加载盘阵场景失败：' + (e instanceof Error ? e.message : String(e)));
    }
  }

  function removeRec(id: number) {
    const i = recs.value.findIndex((r) => r.id === id);
    if (i < 0) return;
    recs.value.splice(i, 1);
    if (activeId.value === id) {
      activeId.value = null;
      const next = recs.value[recs.value.length - 1];
      if (next) void activate(next.id);
      else {
        view.value = { scale: 1, ox: 0, oy: 0 };
        marker.value = null;
        renderTick.value++;
      }
    }
  }

  /* ---------------- 拉伸 / 视图 ---------------- */
  function paintStretch(rec: ViewerRec) {
    if (rec.route === 'jpg') {   // 盘阵 JPG 已烘焙：canvas 保持服务器原样，不二次拉伸
      rec.paintedMode = stretchMode.value;
      return;
    }
    if (!rec.src || !rec.thumb) return;
    const rgba = stretchRgba(rec.src, rec.srcw, rec.srch, rec.nbands, rec.stats, stretchMode.value, rec.invert);
    const ctx = (rec.thumb as unknown as HTMLCanvasElement).getContext('2d');
    if (!ctx) return;
    ctx.putImageData(new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, rec.srcw, rec.srch), 0, 0);
    rec.paintedMode = stretchMode.value;
  }

  function setStretch(mode: StretchMode) {
    stretchMode.value = mode;
    const rec = activeRec.value;
    if (rec && rec.thumb && rec.src) { paintStretch(rec); renderTick.value++; }
  }

  function setCanvasSize(w: number, h: number) {
    canvasSize.value = { w, h };
    const rec = activeRec.value;
    if (rec && rec.thumb) { fit(); renderTick.value++; }
  }

  function fit() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return;
    view.value = fitView(rec.thumb.width, rec.thumb.height, canvasSize.value.w, canvasSize.value.h);
  }

  function onWheel(mx: number, my: number, factor: number) {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return;
    view.value = wheelZoom(view.value, mx, my, factor);
    renderTick.value++;
  }

  function onPan(dx: number, dy: number) {
    view.value = { ...view.value, ox: view.value.ox + dx, oy: view.value.oy + dy };
    renderTick.value++;
  }

  function locatePixel(x: number | string, y: number | string) {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return; }
    const W = rec.W, H = rec.H;
    if (!W || !H) { showErr('图像尺寸未知'); return; }
    const xi = Math.floor(+x), yi = Math.floor(+y);
    if (!isFinite(xi) || !isFinite(yi) || xi < 0 || yi < 0 || xi >= W || yi >= H) {
      showErr('坐标超出范围（0 ≤ X < ' + W + '，0 ≤ Y < ' + H + '）');
      return;
    }
    const t = rec.thumb;
    const tx = xi * (t.width / W), ty = yi * (t.height / H);
    view.value = locateView(view.value.scale, tx, ty, canvasSize.value.w, canvasSize.value.h);
    showMarker(tx, ty);
    renderTick.value++;
  }

  function showMarker(tx: number, ty: number) {
    marker.value = { tx, ty, until: Date.now() + 7000 };
    if (markerTimer) clearTimeout(markerTimer);
    markerTimer = setTimeout(() => {
      if (marker.value && Date.now() >= marker.value.until) {
        marker.value = null;
        renderTick.value++;
      }
    }, 7000);
  }

  /* ---------------- 掩码绘制 ---------------- */
  function getRois(): Poly[] {
    const rec = activeRec.value;
    if (!rec) return [];
    if (!rec.maskRois) rec.maskRois = [];
    return rec.maskRois;
  }

  function enterDraw() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return; }
    drawMode.value = true;
    drawTool.value = 'rect';
    pendingRect.value = null; pendingPts.value = null; hoverPt.value = null;
    hoverRoi.value = -1; flashRoi.value = -1;
    renderTick.value++;
  }

  function exitDraw() {
    drawMode.value = false;
    pendingRect.value = null; pendingPts.value = null; hoverPt.value = null;
    hoverRoi.value = -1; flashRoi.value = -1;
    renderTick.value++;
  }

  function setDrawTool(tool: DrawTool) {
    drawTool.value = tool;
    if (tool === 'rect') { pendingPts.value = null; pendingRect.value = null; }
    else if (tool === 'polygon') { pendingRect.value = null; }
    else { pendingRect.value = null; pendingPts.value = null; hoverRoi.value = -1; }
    renderTick.value++;
  }

  function commitRect() {
    const r = pendingRect.value;
    pendingRect.value = null;
    if (!r) { renderTick.value++; return; }
    const x0 = Math.min(r.x0, r.x1), y0 = Math.min(r.y0, r.y1);
    const x1 = Math.max(r.x0, r.x1), y1 = Math.max(r.y0, r.y1);
    if (x1 - x0 < 1 || y1 - y0 < 1) { renderTick.value++; return; }   // 忽略过小的框
    getRois().push([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);
    renderTick.value++;
  }

  function closePolygon() {
    if (pendingPts.value && pendingPts.value.length >= 3) {
      getRois().push(pendingPts.value.slice());
    }
    pendingPts.value = null; hoverPt.value = null;
    renderTick.value++;
  }

  function undoRoi() {
    getRois().pop();
    pendingPts.value = null; pendingRect.value = null; hoverPt.value = null;
    renderTick.value++;
  }

  function clearRois() {
    const rec = activeRec.value;
    if (rec) rec.maskRois = [];
    pendingPts.value = null; pendingRect.value = null; hoverPt.value = null;
    renderTick.value++;
  }

  async function mergeRois() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return; }
    const rois = getRois();
    if (rois.length < 2) { showToast('少于 2 个区域，无需合并'); return; }
    const tw = rec.thumb.width, th = rec.thumb.height;
    const before = rois.length;
    const owner = rec;   // 捕获：合并期间切图则丢弃结果（同步耗时大，防止写到别的图上）
    merging.value = true;
    showMask('正在合并重叠（缩略图 ' + tw + '×' + th + '，' + before + ' 个区域）…', '准备中…', true);
    try {
      const merged = await mergeConnectedAsync(rois, tw, th, {
        onPhase: (phase) => { overlay.value.sub = phase + '…'; },
        onProgress: (f) => { overlay.value.progress = Math.round(f * 100); },
      });
      hideMask(); merging.value = false;
      if (activeId.value !== owner.id) return;   // 合并期间切了图，丢弃不写
      if (!merged.length) { showErr('合并失败：没有可保留的区域'); return; }
      owner.maskRois = merged;
      pendingPts.value = null; pendingRect.value = null;
      hoverRoi.value = -1; flashRoi.value = -1;
      renderTick.value++;
      showToast('合并重叠：' + before + ' 个区域 → ' + merged.length + ' 个连通区');
    } catch (e) {
      hideMask(); merging.value = false;
      if (activeId.value === owner.id) {
        showErr('合并失败：' + ((e instanceof Error ? e.message : e) as string));
      }
    }
  }

  /** delClick：捕获 owner，200ms 红闪后按对象身份再定位删除（闪烁期间切图/撤销不误删） */
  function delClick(tx: number, ty: number) {
    const rec = activeRec.value;
    if (!rec) return;
    const idx = hitRoi(tx, ty, getRois());
    if (idx < 0) return;
    const rois = getRois();
    if (idx >= rois.length) return;
    const target = rois[idx];
    const owner = rec;
    flashRoi.value = idx;
    renderTick.value++;
    setTimeout(() => {
      if (activeId.value === owner.id) {
        const list = owner.maskRois;
        const k = list ? list.indexOf(target) : -1;
        if (k >= 0 && list) list.splice(k, 1);
      }
      flashRoi.value = -1;
      renderTick.value++;
    }, 200);
  }

  /** 魔棒选区：预览图 RGBA 颜色容差连通生长 → 洞填充 → Moore 外轮廓 → RDP 简化 → 缩略图坐标 ROI */
  function wandSelect(sx: number, sy: number) {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('魔棒不可用'); return; }
    const thumb = rec.thumb as unknown as HTMLCanvasElement;
    const tw = thumb.width, th = thumb.height;
    if (sx < 0 || sy < 0 || sx >= tw || sy >= th) return;
    const x0 = Math.max(0, Math.min(tw - WAND_WIN, sx - (WAND_WIN >> 1)));
    const y0 = Math.max(0, Math.min(th - WAND_WIN, sy - (WAND_WIN >> 1)));
    const w = Math.min(WAND_WIN, tw - x0), h = Math.min(WAND_WIN, th - y0);
    const img = thumb.getContext('2d')!.getImageData(x0, y0, w, h);
    let tol = wandTol.value;
    if (!isFinite(tol)) tol = 20;
    tol = Math.max(0, Math.min(255, tol));
    const sel = floodSelect(w, h, img.data, sx - x0, sy - y0, tol, WAND_EDGE);
    // 加 1px 背景外框：洞填充与外轮廓追踪都要求区域四周有背景，避免贴边选区开口
    const pw = w + 2, ph = h + 2;
    let pm: Uint8Array = new Uint8Array(pw * ph);
    for (let yy = 0; yy < h; yy++) {
      for (let xx = 0; xx < w; xx++) pm[(yy + 1) * pw + (xx + 1)] = sel[yy * w + xx];
    }
    pm = fillRegionHoles(pm, pw, ph);
    let poly = traceContour(pm, pw, ph);
    if (!poly.length) { showErr('未选中区域（容差过小或该处无同色连通区）'); return; }
    poly = simplifyPoly(poly, 0.5);
    if (poly.length < 3) { showErr('选中区域过小，无法构成多边形'); return; }
    const pts: Pt[] = poly.map((p) => [p[0] - 1 + x0, p[1] - 1 + y0]);   // 填充空间 → 缩略图坐标
    getRois().push(pts);
    showToast('魔棒已添加选区（' + pts.length + ' 个顶点，容差 ' + tol + '）');
    renderTick.value++;
  }

  function buildMaskJson(): { width: number; height: number; polygons: { label: string; points: Pt[] }[] } | null {
    const rec = activeRec.value;
    if (!rec) return null;
    const W = rec.W, H = rec.H;
    const tw = rec.thumb ? rec.thumb.width : 0, th = rec.thumb ? rec.thumb.height : 0;
    const polys = getRois().map((pts, i) => ({
      label: 'roi_' + (i + 1),
      points: pts.map((p) => thumbToOrig(p[0], p[1], W, H, tw, th)),
    }));
    return { width: W, height: H, polygons: polys };
  }

  function exportMaskJson() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return; }
    const polys = getRois();
    if (!polys.length) { showErr('还没有绘制任何掩码区域（用矩形/多边形在图上绘制）'); return; }
    const json = buildMaskJson();
    if (!json) return;
    const blob = new Blob([JSON.stringify(json, null, 2)], { type: 'application/json' });
    downloadBlob(blob, rec.name.replace(/\.tiff?$/i, '') + '.mask.json');
    showToast('已导出掩码 JSON（' + polys.length + ' 个区域）。运行：python -m backend.services.mask <json文件> 掩码.tif 掩膜中心点坐标.txt；或直接点工具栏「生成掩码」在浏览器直出');
  }

  /** 浏览器直出掩码：全分辨率栅格化 → mask.tif + mask.txt 两次下载 */
  async function genMask() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return; }
    const rois = getRois();
    if (!rois.length) { showErr('还没有任何掩码区域：先「绘制掩码」，用矩形/多边形/魔棒画 ROI'); return; }
    const W = rec.W, H = rec.H;
    const tw = rec.thumb.width, th = rec.thumb.height;
    const polys = rois.map((pts) => pts.map((p) => thumbToOrig(p[0], p[1], W, H, tw, th)));
    const base = rec.name.replace(/\.tiff?$/i, '');
    showMask('正在生成掩码（全分辨率 ' + W + '×' + H + '，' + polys.length + ' 区域）…', '', true);
    try {
      const tif = await buildTiff(W, H, polys, {
        batch: 128,
        onProgress: (f) => {
          overlay.value.progress = Math.round(f * 100);
          overlay.value.sub = '栅格化 + 压缩中：' + Math.round(f * 100) + '%（' + W + '×' + H + '，请稍候）';
        },
      });
      const txt = buildMaskTxt(W, H, polys);
      hideMask();
      downloadBlob(new Blob([tif as Uint8Array<ArrayBuffer>], { type: 'image/tiff' }), base + '_mask.tif');
      downloadBlob(new Blob([txt], { type: 'text/plain;charset=utf-8' }), base + '_mask.txt');
      showToast('已生成 mask.tif + mask.txt（' + polys.length + ' 区域，分辨率 ' + W + '×' + H + '）');
    } catch (e) {
      hideMask();
      showErr('生成掩码失败：' + ((e instanceof Error ? e.message : e) as string));
    }
  }

  /**
   * 掩码 → SR 提交（阶段5，api-contract.md §3.4）：掩码服务端烘焙落原图目录，
   * 返回 task_draft 预填队列表单 → 跳 /queue 等用户确认（Slurm 是真副作用，不自动提交）。
   * 仅 route='jpg'（带 sceneId）可用；本地 TIF 路径无 sceneId → 提示先经 /scenes 打开。
   */
  async function submitSr() {
    const rec = activeRec.value;
    if (!rec || rec.route !== 'jpg' || !rec.sceneId) {
      showErr('提交 SR 仅对盘阵场景可用（先到「盘阵场景」打开一张图）');
      return;
    }
    if (srBusy.value) return;
    const json = buildMaskJson();
    if (!json || !json.polygons.length) {
      showErr('还没有任何掩码区域：先「绘制掩码」，用矩形/多边形/魔棒画 ROI');
      return;
    }
    const body: BakeMaskBody = {
      scene_id: rec.sceneId, polygons: json.polygons, W: json.width, H: json.height,
    };
    srBusy.value = true;
    const owner = rec;                       // 烘焙期间切图则丢弃（异步）
    showMask('正在把掩码烘焙到盘阵（服务端全分辨率 ' + json.width + '×' + json.height +
      ' 栅格化）…', rec.name, false);
    try {
      const cfg = loadSrConfig();
      const res = await apiBakeMask(cfg, body);
      hideMask(); srBusy.value = false;
      if (activeId.value !== owner.id) {
        showToast('掩码已烘焙到盘阵，但查看器已切走，未跳队列页');
        return;
      }
      useQueueStore().setDraft(res.task_draft);   // 预填，不自动提交
      showToast('掩码已烘焙到盘阵，去「任务队列」确认后提交 SR');
      void router.push('/queue');
    } catch (e) {
      hideMask(); srBusy.value = false;
      if (activeId.value === owner.id) {
        showErr('掩码烘焙失败：' + (e instanceof Error ? e.message : String(e)));
      }
    }
  }

  /* ---------------- 画布事件路由（TifCanvas 绑定） ---------------- */
  /** mousedown 在绘制模式下的分派：rect 起框 / polygon 加点 / wand 调 wandSelect / del 调 delClick */
  function onCanvasDownDraw(p: Pt): boolean {
    if (!drawMode.value) return false;
    if (drawTool.value === 'rect') {
      pendingRect.value = { x0: p[0], y0: p[1], x1: p[0], y1: p[1] };
    } else if (drawTool.value === 'wand') {
      wandSelect(Math.round(p[0]), Math.round(p[1]));
      return true;
    } else if (drawTool.value === 'del') {
      delClick(p[0], p[1]);
      return true;
    } else {
      if (!pendingPts.value) pendingPts.value = [];
      pendingPts.value.push(p);
      hoverPt.value = p;
    }
    renderTick.value++;
    return true;
  }

  function onCanvasMove(p: Pt) {
    if (drawMode.value && drawTool.value === 'del') {
      const hit = hitRoi(p[0], p[1], getRois());
      if (hit !== hoverRoi.value) { hoverRoi.value = hit; renderTick.value++; }
      return;
    }
    if (drawMode.value && (pendingRect.value || pendingPts.value)) {
      hoverPt.value = p;
      if (pendingRect.value) { pendingRect.value.x1 = p[0]; pendingRect.value.y1 = p[1]; }
      renderTick.value++;
      return;
    }
  }

  function onCanvasUp(p: Pt): boolean {
    if (drawMode.value && pendingRect.value) {
      pendingRect.value.x1 = p[0];
      pendingRect.value.y1 = p[1];
      commitRect();
      return true;
    }
    return false;
  }

  function onDblClick() {
    if (drawMode.value) { closePolygon(); return; }
    fit();
    renderTick.value++;
  }

  function onKeyDown(e: KeyboardEvent) {
    if (!drawMode.value) return;
    if (e.key === 'Escape') {
      pendingPts.value = null; pendingRect.value = null; hoverPt.value = null;
      hoverRoi.value = -1; flashRoi.value = -1;
      renderTick.value++;
    } else if (e.key === 'Enter' && pendingPts.value) {
      closePolygon();
    }
  }

  /* ---------------- JPG 导出（HTML 串行队列 + token 守卫 + 降档重试） ---------------- */
  function setJpgStatus(rec: ViewerRec, text: string, cls: '' | 'ok' | 'err') {
    rec.jpgStatus = text;
    rec.jpgCls = cls || '';
  }

  function kickExport(rec: ViewerRec, force?: boolean) {
    if (!force && !autoExport.value) return;
    if (!rec || !rec.W || !rec.src || rec._jpgBusy || rec._jpgDone) return;
    const s = getSaver();
    if (!s) { setJpgStatus(rec, 'JPG 未授权（点工具栏「输出目录」后自动导出）', 'err'); return; }
    rec._jpgBusy = true;
    rec._jpgToken = (rec._jpgToken || 0) + 1;
    exportQueue.value.push({ rec, token: rec._jpgToken });
    pumpExport();
  }

  function reExportJpg(id: number) {
    const rec = recs.value.find((r) => r.id === id);
    if (!rec || rec._jpgBusy) return;
    rec._jpgDone = false;
    rec._jpgBusy = false;
    kickExport(rec, true);
  }

  function scanPendingExports() {
    recs.value.forEach((r) => { if (r.W && r.src && !r._jpgDone) kickExport(r); });
  }

  function pumpExport() {
    if (exportingNow.value || !exportQueue.value.length) return;
    const job = exportQueue.value.shift()!;
    exportingNow.value = true;
    void doExportJob(job).then(() => { exportingNow.value = false; pumpExport(); });
  }

  async function doExportJob(job: ExportJob) {
    const rec = job.rec, tok = job.token;
    setJpgStatus(rec, 'JPG 生成中…', '');
    const source = new FileSource(rec.file, rec.name);
    try {
      const r = await exportToJpg(rec, source, browserKit, stretchMode.value, null, (f) => {
        if (tok === rec._jpgToken) setJpgStatus(rec, 'JPG 生成中 ' + Math.round(f * 100) + '%…', '');
      });
      if (tok !== rec._jpgToken) return;
      rec._jpgDone = true;
      const extra = r.plan && r.plan.reduced ? '（受画布/内存限制已自动降档）' : '';
      setJpgStatus(rec, 'JPG 已导出 ' + r.name + ' · ' + r.dims + ' · ' + stretchMode.value +
        ' · q' + JPG_QUALITY + extra, 'ok');
    } catch (e) {
      if (tok !== rec._jpgToken) return;
      const msg = (e instanceof Error ? e.message : String(e)) as string;
      // 个别 Edge 构建在画布面积极限上会拒绝 → 降到 4096 重试一次
      if (!rec._exportCap && /RangeError|allocation|Invalid|空|过大|enormous/i.test(msg)) {
        rec._exportCap = 4096;   // 默认 8192 导出仍遇画布/内存失败 → 降到 4096 再试一次
        rec._jpgBusy = false;
        kickExport(rec, true);
        return;
      }
      setJpgStatus(rec, 'JPG 失败: ' + msg, 'err');
    } finally {
      if (tok === rec._jpgToken) rec._jpgBusy = false;
    }
  }

  /* ---------------- 落盘 / 输出目录（HTML fsIO 接线） ---------------- */
  function initFsIO() {
    setOutDirListener((s) => { outDir.value = { ...s }; });
    void fsIO.init();   // 恢复上次授权目录（非阻塞）
  }

  async function authorizeOutDir() {
    try {
      await fsIO.authorize();
      scanPendingExports();
    } catch (e) {
      if (e && (e as { name?: string }).name === 'AbortError') return;   // 用户取消选择
      showErr('输出目录授权失败：' + ((e instanceof Error ? e.message : e) as string));
    }
  }

  /* ---------------- 遮罩 / toast / 错误 ---------------- */
  function showMask(title: string, sub: string, bar: boolean) {
    overlay.value = { visible: true, title, sub: sub || '', bar, progress: 0 };
  }
  function hideMask() {
    overlay.value.visible = false;
  }
  function updateProgressUI(f: number) {
    overlay.value.progress = Math.round(f * 100);
    overlay.value.sub = '分块读取中：' + Math.round(f * 100) + '%（边读边生成缩略图，可随时切换其他文件）';
  }
  function showToast(msg: string) {
    toast.value = msg;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.value = ''; }, 6000);
  }
  function showErr(msg: string) {
    error.value = msg;
    if (errTimer) clearTimeout(errTimer);
    errTimer = setTimeout(() => { error.value = ''; }, 6000);
  }

  /* ---------------- 测试钩子 ---------------- */
  /** 改写稀疏路径判定阈值（默认 1e8 不动；浏览器回归用小 fixture 走稀疏路由时调用） */
  function setSparseMin(n: number) {
    setSparseMinLib(n);
  }

  return {
    // 状态
    recs, activeId, activeRec, view, canvasSize, renderTick, marker,
    stretchMode, drawMode, drawTool, pendingRect, pendingPts, hoverPt, hoverRoi, flashRoi,
    wandTol, merging, autoExport, outDir, exportQueue, exportingNow, overlay, toast, error,
    sidebarCollapsed, busy, srBusy,
    // 文件 / 解码
    addFiles, removeRec, activate, openSceneJpg,
    // 拉伸 / 视图
    setStretch, setCanvasSize, fit, onWheel, onPan, locatePixel,
    // 掩码
    enterDraw, exitDraw, setDrawTool, commitRect, closePolygon, undoRoi, clearRois,
    mergeRois, delClick, wandSelect, buildMaskJson, exportMaskJson, genMask,
    getRois, submitSr,
    // 画布事件
    onCanvasDownDraw, onCanvasMove, onCanvasUp, onDblClick, onKeyDown,
    // 导出
    kickExport, reExportJpg, scanPendingExports, setJpgStatus,
    // 落盘 / UI
    initFsIO, authorizeOutDir, showMask, hideMask, showToast, showErr,
    setSparseMin,
  };
});
