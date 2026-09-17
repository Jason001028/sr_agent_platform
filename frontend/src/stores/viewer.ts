/**
 * stores/viewer.ts — 查看器状态 + 编排（tif-viewer.html 交互层 Vue 化）
 * ------------------------------------------------------------------
 * 「移植不重写」：HTML 全局状态（recs/activeRec/view/curStretch/drawMode/...）平移到
 * Pinia store；动作逐函数直译（activate/decode/paintStretch/掩码），仅把「直接操作 DOM」
 * 改为「状态变更 + renderTick 驱动 TifCanvas 重绘」。
 * 最小原型删去了 HTML 的 browser JPG 导出/输出目录授权一条链路（前端不再落盘）。
 *
 * 重字段（Float32Array src / canvas thumb / BandStats stats）用 markRaw 存放，避免深代理。
 * renderTick 计数器是 TifCanvas 的重绘信号：任何影响视图的状态变更后 ++，TifCanvas watch 后重绘
 * （语义等价 HTML 各处直接调 render()/renderDraw()）。
 */
import { defineStore } from 'pinia';
import { ref, computed, markRaw } from 'vue';
import {
  probeImage, tiffTags, layoutInfo, stretchRgba,
  setSparseMin as setSparseMinLib,
} from '../lib/tifDecode.js';
import type { ProbeInfo, BandStats, StretchMode, KitCanvas } from '../lib/tifDecode.js';
import type { Poly, Pt } from '../lib/maskgen.js';
import {
  floodSelect, fillRegionHoles, traceContour, simplifyPoly,
  mergeConnectedAsync, buildTiff, buildMaskTxt,
} from '../lib/maskgen.js';
import { fitView, locateView, wheelZoom, hitRoi, thumbToOrig, visibleThumbRect } from '../lib/viewMath.js';
import type { ViewState, Rect } from '../lib/viewMath.js';
import { FileSource } from '../lib/source.js';
import { browserKit } from '../lib/browserKit.js';
import { decodeOne } from '../lib/decode.js';
import type { DecodedRec } from '../lib/decode.js';
import { sceneDecodePixels, loadSrConfig, startStretch } from '../lib/scene.js';
import { apiResolveScene, apiBakeMask, fetchSceneJpg } from '../lib/api.js';
import type { SceneResolveResult } from '../lib/api.js';
import { classifyImages } from '../lib/imageFiles.js';
import type { SceneOpenMeta } from '../lib/scene.js';
import { buildStats, luma, STAT_HI } from '../lib/roiStats.js';
import type { RoiStats } from '../lib/roiStats.js';
import { downloadBlob } from '../lib/saver.js';
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
  /** 解码路由；'jpg' = 盘阵场景（服务器烘焙 JPG，可提交 SR）；
      'img' = 本地打开的 .jpg/.jpeg（同一像素管线，但无 sceneId/lqPath → 不可提交）。 */
  route: 'utif' | 'sparse' | 'chunked' | 'jpg' | 'img' | null;
  /** 阶段4 不透明场景 id（route='jpg' 时必有；掩码烘焙 POST /api/masks 用）。 */
  sceneId: string | null;
  /** 阶段6 scene 文件父目录绝对路径（= run_sr 目录语义，与 /api/queue
   *  params.lq_path 同值）；任务区用它关联当前场景的队列行。本地文件恒 null，
   *  除非 tryLinkScenes 把它关联到了盘阵目录。 */
  lqPath: string | null;
  /** 掩码写到服务端后，服务端告知的绝对路径（`bakeMaskToServer` 成功才非空）。
   *  显示用；提交时真正的值由后端按同一规则重新推导。 */
  serverMaskPath?: string | null;
  /** 已经试过反推关联盘阵目录（成功或失败都算）。每个文件只试一次 ——
   *  切来切去不该反复打同一个请求、反复弹同一条错。 */
  linkTried?: boolean;
  layout: string;              // 即探标签摘要
  status: string;
  statusCls: '' | 'ok' | 'err';
  paintedMode: StretchMode | null;
  maskRois: Poly[] | null;     // 缩略图坐标 ROI
}

const WAND_WIN = 4096;
const WAND_EDGE = 64;
/** 云叠画布长边上限：整幅降到 ≤2048（RGBA ≤16MB），避免 8192² 全幅叠加再撞画布/内存上限。 */
const CLOUD_OVERLAY_MAX = 2048;

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

/* 选中 ROI 在「当前显示层（stretch 后 thumb 画布 8bit）」上的确定性统计（阶段6）。
   实现 = ROI bbox 局部 getImageData + 多边形平移进局部坐标系 → buildStats。
   平移保持像元集合不变（区域内 (c,r) 相对坐标 = 全局坐标 − bbox 原点），局部裁剪让
   大图不整张拷贝，只读 ROI 覆盖的行列；无 canvas / 多边形在画布外 → null。 */
function roiStatsOnCanvas(rec: ViewerRec, poly: Poly): RoiStats | null {
  const cv = rec.thumb as unknown as HTMLCanvasElement | null;
  if (!cv) return null;
  const tw = cv.width, th = cv.height;
  const ctx = cv.getContext('2d');
  if (!ctx || tw <= 0 || th <= 0 || poly.length < 3) return null;
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  for (let i = 0; i < poly.length; i++) {
    const x = poly[i][0], y = poly[i][1];
    if (x < minx) minx = x;
    if (x > maxx) maxx = x;
    if (y < miny) miny = y;
    if (y > maxy) maxy = y;
  }
  if (!isFinite(minx) || !isFinite(miny)) return null;
  const bx0 = Math.max(0, Math.floor(minx));
  const by0 = Math.max(0, Math.floor(miny));
  const bx1 = Math.min(tw - 1, Math.max(0, Math.ceil(maxx)));
  const by1 = Math.min(th - 1, Math.max(0, Math.floor(maxy)));
  const bw = bx1 - bx0 + 1, bh = by1 - by0 + 1;
  if (bw < 1 || bh < 1) return null;
  const region = ctx.getImageData(bx0, by0, bw, bh);
  const shifted = poly.map((p) => [p[0] - bx0, p[1] - by0] as Pt);
  return buildStats(region, shifted);
}

/* ---------------- 云量估算（阶段6 侧栏云量卡：数字 + 疑似云区红叠，纯前端启发） ----------------
   语义与 ROI 统计同源：整景/视野矩形多边形喂 buildStats，阈值 STAT_HI(=200) 高亮占比即
   「疑似云占比」——PAN 无真云掩膜时这是亮度启发估算，亮雪/亮建筑会同样计入，非入库云量口径。 */

/** 整景云量 +（可选）疑似云区红叠：同一份整幅 getImageData 喂 buildStats 与 makeCloudOverlay，
    避免两次 256MB 级瞬态副本；整幅读失败（内存/画布上限）→ 双 null（UI 显 —，不崩）。 */
function buildSceneCloud(rec: ViewerRec, wantOverlay: boolean): { stats: RoiStats | null; overlay: HTMLCanvasElement | null } {
  const cv = rec.thumb as unknown as HTMLCanvasElement | null;
  if (!cv) return { stats: null, overlay: null };
  const tw = cv.width, th = cv.height;
  if (tw <= 0 || th <= 0) return { stats: null, overlay: null };
  const ctx = cv.getContext('2d');
  if (!ctx) return { stats: null, overlay: null };
  let img: ImageData;
  try { img = ctx.getImageData(0, 0, tw, th); } catch { return { stats: null, overlay: null }; }
  const stats = buildStats(img, [[0, 0], [tw, 0], [tw, th], [0, th]]);
  let overlay: HTMLCanvasElement | null = null;
  if (wantOverlay) {
    try { overlay = makeCloudOverlay(img, tw, th); } catch { overlay = null; }
  }
  return { stats, overlay };
}

/** 把整幅 RGBA 降采样成 ≤CLOUD_OVERLAY_MAX 的半透明红叠画布：源像元 luma≥STAT_HI → 红。
    逐目标像元按 step 抽源点（云是大片高亮区，抽样已足够）→ 输出画布 ≤2048² RGBA（≤16MB）。 */
function makeCloudOverlay(img: ImageData, tw: number, th: number): HTMLCanvasElement {
  const step = Math.max(1, Math.ceil(Math.max(tw, th) / CLOUD_OVERLAY_MAX));
  const ow = Math.max(1, Math.ceil(tw / step));
  const oh = Math.max(1, Math.ceil(th / step));
  const data = img.data;
  const out = new Uint8ClampedArray(ow * oh * 4);
  for (let ry = 0; ry < oh; ry++) {
    const sy = Math.min(th - 1, ry * step);
    for (let cx = 0; cx < ow; cx++) {
      const sx = Math.min(tw - 1, cx * step);
      const o4 = (sy * tw + sx) * 4;
      const Y = luma(data[o4], data[o4 + 1], data[o4 + 2]);
      if (Y >= STAT_HI) {
        const p = (ry * ow + cx) * 4;
        out[p] = 255; out[p + 1] = 70; out[p + 2] = 70; out[p + 3] = 130;
      }
    }
  }
  const cv = browserKit.createCanvas(ow, oh) as unknown as HTMLCanvasElement;
  const ctx = cv.getContext('2d');
  if (!ctx) throw new Error('取不到 2d 上下文');
  ctx.putImageData(new ImageData(out, ow, oh), 0, 0);
  return cv;
}

/** 整数像素矩形精确裁剪 → 全幅 buildStats（避免多边形 gap 端点 off-by-one；缩略图坐标）。 */
function statsOnRect(rec: ViewerRec, r: Rect): RoiStats | null {
  const cv = rec.thumb as unknown as HTMLCanvasElement | null;
  if (!cv) return null;
  const tw = cv.width, th = cv.height;
  if (tw <= 0 || th <= 0) return null;
  const x0 = Math.max(0, r.x0), y0 = Math.max(0, r.y0);
  const x1 = Math.min(tw - 1, r.x1), y1 = Math.min(th - 1, r.y1);
  if (x0 > x1 || y0 > y1) return null;
  const w = x1 - x0 + 1, h = y1 - y0 + 1;
  const ctx = cv.getContext('2d');
  if (!ctx) return null;
  let img: ImageData;
  try { img = ctx.getImageData(x0, y0, w, h); } catch { return null; }
  return buildStats(img, [[0, 0], [w, 0], [w, h], [0, h]]);
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
  /** 阶段6 侧舱选中的 ROI（按对象身份指向 rec.maskRois 里的多边形；切图/删改失效即清）。 */
  const selRoi = ref<Poly | null>(null);
  /** 选中 ROI 在「当前显示层」上的确定性统计缓存（stretch/选区变化后由 refresh 重算）。 */
  const roiStats = ref<RoiStats | null>(null);
  /** 云量估算（阶段6 启发卡，同源 buildStats/STAT_HI=200）：整景 / 当前视野 的亮像元统计。 */
  const cloudScene = ref<RoiStats | null>(null);
  const cloudView = ref<RoiStats | null>(null);
  /** 疑似云区红叠开关（默认关，保持现行为；开启时图上亮云区半透明红）。 */
  const cloudShow = ref(false);
  /** 疑似云区红叠画布（≤2048，draw 时放大；整幅读失败/关闭 → null）。markRaw 重字段。 */
  const cloudOverlay = ref<HTMLCanvasElement | null>(null);
  const wandTol = ref(20);
  const merging = ref(false);
  const overlay = ref({ visible: false, title: '', sub: '', bar: false, progress: 0 });
  const toast = ref('');
  const error = ref('');
  const sidebarCollapsed = ref(false);
  const busy = ref(false);
  const srBusy = ref(false);       // 「提交 SR」进行中（掩码服务端烘焙）

  const activeRec = computed<ViewerRec | null>(() =>
    recs.value.find((r) => r.id === activeId.value) || null,
  );

  /** 当前这张图**实际**的拉伸模式 —— 工具栏下拉显示它。
     不是 stretchMode：那份全局值只决定「新打开的图用什么起手」（盘阵场景另有
      起手值，见 startStretch），一张图画过之后就以它自己的 paintedMode 为准。
      没画过（paintedMode 还是 null）才回退到起手值，下拉不至于空着。 */
  const activeStretch = computed<StretchMode>(() => {
    const rec = activeRec.value;
    if (!rec) return stretchMode.value;
    return rec.paintedMode ?? startStretch(rec.route, stretchMode.value);
  });

  /* ---------------- 文件打开（HTML openFiles/openOne） ---------------- */
  /** 选文件 / 拖入：TIF 走解码管线，.jpg/.jpeg 走显示就绪图片管线（§4.6）。 */
  function addFiles(fileList: FileList | File[] | File) {
    const files = Array.isArray(fileList)
      ? fileList
      : Array.from(fileList instanceof File ? [fileList] : fileList);
    const { tifs, imgs } = classifyImages(files);
    if (!tifs.length && !imgs.length) {
      showErr('没有识别到影像文件（支持 .tif/.tiff/.jpg/.jpeg）');
      return;
    }
    const isDup = (f: File): boolean =>
      recs.value.some((r) => r.file.name === f.name && r.file.size === f.size);
    tifs.forEach((f) => { if (!isDup(f)) openOne(f); });
    imgs.forEach((f) => { if (!isDup(f)) void openLocalImage(f); });
  }

  function openOne(file: File) {
    const rec: ViewerRec = {
      id: nextId++, file: markRaw(file),
      probe: null, name: file.name, size: file.size,
      W: 0, H: 0, thumb: null, src: null, srcw: 0, srch: 0, nbands: 0, invert: false,
      stats: null, route: null, sceneId: null, lqPath: null,
      layout: '', status: '等待…', statusCls: '',
      paintedMode: null, maskRois: null,
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
      repaintOnActivate(rec);
      renderTick.value++;
      return;
    }
    marker.value = null;
    activeId.value = id;
    clearRoiSel();                      // 切换图像：侧舱选择/统计随图失效
    clearCloud();                       // 云量数字/红叠随图失效（解码/缓存分支随后 refresh 补回）
    if (drawMode.value) {
      pendingPts.value = null; pendingRect.value = null; hoverPt.value = null;
    }
    // 打开/切到本地文件时试着反推它的盘阵目录（不阻塞解码，每个文件只试一次）。
    // 盘阵场景（route='jpg'）不走这条路：它们的目录是 POST /api/scenes/resolve
    // 按**绝对路径**给出的权威值，而反推只能拿裸文件名去猜，猜出来的可能是另一个
    // 目录（同名不同景），拿它提交 SR 就是错的；tryLinkScenes 里也会再挡一次。
    void tryLinkScenes(rec);
    if (rec.thumb) {
      repaintOnActivate(rec);
      fit();
      refreshCloudStats();           // 切回已解码文件 → 云量随新图刷新
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
    // 像素刚换过 → 一律重画。已选过模式的（同一 rec 重复解码）沿用原模式，不重置。
    paintStretch(rec, rec.paintedMode ?? startStretch(rec.route, stretchMode.value));
    if (activeId.value === rec.id) {
      fit();
      refreshCloudStats();           // 首次解码完 → 云量卡/红叠就绪
      renderTick.value++;
    }
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

  /* ---------------- 本地影像（.jpg/.jpeg，route='img'） ----------------
     本地 JPG 与盘阵 JPG 都是 8bit 显示就绪图，共用同一条像素管线
     （createImageBitmap → 画布 RGBA → sceneDecodePixels 单波段 0..255）。
     差别只在来源：本地图没有 sceneId/lqPath，故不能提交 SR，也不打「盘阵」徽标 —
     所以给它独立的 route='img'，让「盘阵场景」相关的判断（徽标 / 拉伸禁用 /
     ScenesPage 的 W/H 提示 / 提交 SR）都继续只认 route==='jpg'。
     有 src + stats，因此拉伸模式、掩码绘制、云量卡、ROI 统计照常可用。 */
  async function openLocalImage(file: File) {
    busy.value = true;
    showMask('正在读取图片…', file.name, false);
    try {
      const cv = await decodeJpgToCanvas(file);
      const tw = cv.width, th = cv.height;
      const ctx = cv.getContext('2d');
      if (!ctx) throw new Error('取不到 2d 上下文');
      const d = sceneDecodePixels(ctx.getImageData(0, 0, tw, th).data, tw, th);
      const rec: ViewerRec = {
        id: nextId++, file: markRaw(file),
        probe: null, name: file.name, size: file.size,
        W: tw, H: th,
        thumb: markRaw(cv as unknown as KitCanvas),
        src: markRaw(d.src), srcw: d.tw, srch: d.th, nbands: d.nbands,
        invert: false, stats: d.stats ? markRaw(d.stats) : null,
        route: 'img', sceneId: null, lqPath: null,
        layout: '本地 JPG（8bit 显示就绪，' + tw + '×' + th + '）',
        status: '已读取：' + file.name + ' · ' + tw + '×' + th,
        statusCls: 'ok', paintedMode: null, maskRois: null,
      };
      recs.value.push(rec);
      hideMask(); busy.value = false;
      void activate(rec.id);
    } catch (e) {
      hideMask(); busy.value = false;
      showErr('读取图片失败：' + (e instanceof Error ? e.message : String(e)));
    }
  }

  /* ---------------- 盘阵场景（阶段4：读服务器烘焙 JPG，route='jpg'） ----------------
     JPG 即显示产物（各边 1/2 的稀疏采样 + 直方图均衡已在服务器烤好）：不再读原始
     TIF 字节、不做二次拉伸、不本地导出 JPG（服务器 JPG 即交付物）。掩码仍照旧 ——
     缩略图坐标按元数据 W/H 换算回全分辨率（thumbToOrig scale 来自 rec.W/H 而非
     probe，所以 JPG 尺寸变了也不影响掩码落点）。 */
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
        route: 'jpg', sceneId: meta.sceneId ?? null, lqPath: meta.lqPath ?? null,
        layout: '盘阵 JPG（1/2 尺度 + 直方图均衡，服务端已烘焙）',
        status: '场景就绪：' + meta.name + ' · 元数据 ' + meta.W + '×' + meta.H + ' · JPG ' + d.tw + '×' + d.th,
        statusCls: 'ok', paintedMode: null, maskRois: null,
        // 手工场景由 resolve 带着权威掩码路径进来（场景库页那条入口也走这里）；
        // 库行没有这个字段 → null，写掩码时再拿后端回的值。
        serverMaskPath: meta.serverMaskPath ?? null,
      };
      recs.value.push(rec);
      hideMask(); busy.value = false;
      void activate(rec.id);
      showToast('已打开盘阵场景「' + meta.name + '」（掩码按元数据 ' + meta.W + '×' + meta.H + ' 换算）');
    } catch (e) {
      hideMask(); busy.value = false;
      // 就地抛出，不自己弹错：唯一调用方是 scenes.open（/scenes 页），它把原因写进
      // scenes.error 才显示得出来；viewer 的错误条挂在 /viewer，写进去等于没报。
      throw e;
    }
  }

  function removeRec(id: number) {
    const i = recs.value.findIndex((r) => r.id === id);
    if (i < 0) return;
    recs.value.splice(i, 1);
    if (activeId.value === id) {
      activeId.value = null;
      clearRoiSel();
      const next = recs.value[recs.value.length - 1];
      if (next) void activate(next.id);
      else {
        view.value = { scale: 1, ox: 0, oy: 0 };
        marker.value = null;
        clearCloud();                    // 无图可显：云卡回空态，释放红叠画布
        renderTick.value++;
      }
    }
  }

  /* ---------------- 拉伸 / 视图 ---------------- */
  /** 激活一张**已经解码过**的图：决定要不要重画。

      没画过（paintedMode 为 null）→ 按起手值画一次：盘阵场景 = 直方图均衡
      （见 lib/scene.startStretch），本地图 = 工具栏当前模式。

      画过 → 只有「本地图 + 工具栏模式变了」才跟一次。盘阵场景不跟随全局：用户在
      某张场景图上选过的模式是那张图的属性，不该被另一张图上的操作改掉。 */
  function repaintOnActivate(rec: ViewerRec) {
    if (rec.paintedMode === null) {
      paintStretch(rec, startStretch(rec.route, stretchMode.value));
    } else if (rec.route !== 'jpg' && rec.paintedMode !== stretchMode.value) {
      paintStretch(rec);
    }
  }

  /** 按 mode 重画 rec 的显示画布，并把「这张图现在是这个模式」记在它自己身上。
      盘阵场景同样走这条路：服务器烤的直方图均衡只是**底图**，显示层照常可二次
      拉伸（均衡是不可逆的，拉不回来 —— 这一点在工具栏 title 里说明）。 */
  function paintStretch(rec: ViewerRec, mode: StretchMode = stretchMode.value) {
    if (!rec.src || !rec.thumb) return;
    const rgba = stretchRgba(rec.src, rec.srcw, rec.srch, rec.nbands, rec.stats, mode, rec.invert);
    const ctx = (rec.thumb as unknown as HTMLCanvasElement).getContext('2d');
    if (!ctx) return;
    ctx.putImageData(new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, rec.srcw, rec.srch), 0, 0);
    rec.paintedMode = mode;
  }

  /** 工具栏改拉伸：改的是**当前这张图**（记在 rec.paintedMode 上），顺带把全局
      模式更新成同一个值，作为之后新打开的本地图的起手。

      盘阵场景不写回全局：那会让「看过一张场景图」默默改掉本地 TIF 的起手模式，
      而场景那条链路用户要的是本图独立。 */
  function setStretch(mode: StretchMode) {
    const rec = activeRec.value;
    if (!rec || rec.route !== 'jpg') stretchMode.value = mode;
    if (rec && rec.thumb && rec.src) {
      paintStretch(rec, mode);
      renderTick.value++;
      refreshRoiStats();              // 显示层像素变了 → 选中 ROI 统计随层刷新
      refreshCloudStats();            // …云量数字/红叠同理随显示层刷新
    }
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

  /* ---------------- 侧舱 ROI 选择 / 确定性统计（阶段6，纯前端 L1） ----------------
     选中 = 按对象身份引用 rec.maskRois 里的多边形（新增/删除其它 ROI 不破坏当前选择；
     切图 / 清空 / 合并 / 删除自身 → refresh 检测失效自动清空）。统计只认当前显示层
     thumb 画布像素（stretch 后），paintStretch/切图/选区变化触发 refreshRoiStats()。 */
  function roiSelIndex(): number {
    const rec = activeRec.value;
    const sel = selRoi.value;
    if (!rec || !sel) return -1;
    const rois = rec.maskRois || [];
    return rois.indexOf(sel);
  }

  /** 点 ROI 列表行 i（1-based 显示为 i+1）：锁定该对象并算一次确定性统计。 */
  function selectRoi(i: number) {
    const rec = activeRec.value;
    const rois = getRois();
    if (rec && i >= 0 && i < rois.length) {
      selRoi.value = rois[i];
      roiStats.value = roiStatsOnCanvas(rec, rois[i]);
    } else {
      selRoi.value = null;
      roiStats.value = null;
    }
    renderTick.value++;                 // 通知画布重绘选中高亮
  }

  function clearRoiSel() {
    selRoi.value = null;
    roiStats.value = null;
    renderTick.value++;
  }

  /** 重算选中 ROI 统计；选中对象已失效（删/合并/清空/切图）→ 清空选择。 */
  function refreshRoiStats() {
    const rec = activeRec.value;
    const sel = selRoi.value;
    if (!rec || !sel) { roiStats.value = null; return; }
    const rois = rec.maskRois || [];
    if (rois.indexOf(sel) < 0) { selRoi.value = null; roiStats.value = null; return; }
    roiStats.value = roiStatsOnCanvas(rec, sel);
  }

  /* ---------------- 云量估算卡（阶段6 启发，纯前端） ---------------- */
  /** 当前缩略图像素尺度下的可见视野矩形（thumb/view/canvas 就绪时）。 */
  function currentViewRect(): Rect | null {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return null;
    return visibleThumbRect(
      view.value, canvasSize.value.w, canvasSize.value.h,
      rec.thumb.width, rec.thumb.height,
    );
  }

  /**
   * 当前视野云量：视野≈整景（scale<1 整体适配，或可见面积 ≥99.5%）→ 直接复用整景结果
   * （免对整幅再 getImageData）；否则按可见矩形局部统计（预算小，pan/zoom 停稳可反复算）。
   */
  function computeCloudView(scene: RoiStats | null): RoiStats | null {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return null;
    const tw = rec.thumb.width, th = rec.thumb.height;
    if (tw <= 0 || th <= 0) return null;
    const r = currentViewRect();
    if (view.value.scale < 1 || !r) return scene;
    const area = (r.x1 - r.x0 + 1) * (r.y1 - r.y0 + 1);
    if (area >= tw * th * 0.995) return scene;
    return statsOnRect(rec, r);
  }

  /** 清空云量四态（切图/删图时随图失效；缓寸分支会在同 tick refreshCloudStats 补回）。 */
  function clearCloud() {
    cloudScene.value = null;
    cloudView.value = null;
    cloudOverlay.value = null;
  }

  /** 开图/换拉伸后重算：整景（+红叠，若开关开）与当前视野。thumb 未就绪 → 四态置空。 */
  function refreshCloudStats() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) {
      cloudScene.value = null;
      cloudView.value = null;
      cloudOverlay.value = null;
      return;
    }
    const sc = buildSceneCloud(rec, cloudShow.value);
    cloudScene.value = sc.stats;
    cloudOverlay.value = sc.overlay ? markRaw(sc.overlay) : null;
    cloudView.value = computeCloudView(sc.stats);
  }

  /** 仅刷当前视野（pan/zoom 停稳后 RoiToolsTab 防抖调用，不动整景/红叠）。 */
  function refreshCloudView() {
    cloudView.value = computeCloudView(cloudScene.value);
  }

  /** 疑似云区红叠开关：开时补齐 overlay（此前关闭未算）；关时释放画布内存。 */
  function setCloudShow(v: boolean) {
    if (cloudShow.value === v) return;
    cloudShow.value = v;
    if (v) {
      const rec = activeRec.value;
      if (rec && rec.thumb) {
        const sc = buildSceneCloud(rec, true);
        cloudScene.value = sc.stats;                       // 顺带校正整景数字
        cloudOverlay.value = sc.overlay ? markRaw(sc.overlay) : null;
        cloudView.value = computeCloudView(sc.stats);
      }
    } else {
      cloudOverlay.value = null;                            // 释放
    }
    renderTick.value++;                                     // 通知画布加/减红叠
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
    refreshRoiStats();
    renderTick.value++;
  }

  function clearRois() {
    const rec = activeRec.value;
    if (rec) rec.maskRois = [];
    pendingPts.value = null; pendingRect.value = null; hoverPt.value = null;
    refreshRoiStats();
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
      refreshRoiStats();                          // 合并重建对象 → 旧选择失效即清
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
        refreshRoiStats();          // 删除的就是选中对象 → 选择自动失效
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
   * 场景 → SR 提交（最小原型 §4.3）：不再烘焙掩码，也不再要求先画掩码。
   * 只把「这张图所在的原图目录」填进队列表单，掩码由后端按
   * `<lq_path>/<输入影像 stem>_mask.tif` 推导并校验存在性 —— 前端不猜掩码
   * 文件名，那条规则只有一处实现（services/scene_search.derived_mask_path）。
   * 表单显示用的 mask_path 取后端已经回给这张图的权威值（resolve 响应或写掩码
   * 响应），而不是前端那份无 stat 的镜像 —— PAN.tif（RC）场景两者不同名。
   * 不自动提交：提交 SR 是真副作用，跳 /queue 等人工确认。
   * 仅 route='jpg'（盘阵场景）可用 —— 本地 TIF 没有盘阵路径，无从提交。
   */
  function submitSr() {
    const rec = activeRec.value;
    // 判据是「这张图有没有盘阵目录」，不是「它是怎么打开的」：盘阵 JPG 场景与
    // 反推关联上的本地 TIF 都能提交；纯本地文件（没有 lqPath）不行。
    if (!rec || !rec.lqPath) {
      showErr('提交 SR 需要盘阵目录：请在「盘阵场景」栏填写该场景目录并打开，'
        + '或让本地文件按文件名反推关联（文件名里要有成像时间戳）');
      return;
    }
    if (srBusy.value) return;
    srBusy.value = true;
    useQueueStore().setSrDraft(rec.lqPath, rec.serverMaskPath ?? null);
    showToast('已带入目录 ' + rec.lqPath + '，去「任务队列」确认后提交 SR');
    void router.push('/queue');
    srBusy.value = false;
  }

  /* ---------------- 手工盘阵场景（查看器侧入口） ---------------- */
  /** 反推关联成功后的落账：只写 lqPath（提交 SR 用）与 serverMaskPath（显示用）。
   *
   *  **不写 sceneId**：sceneId 是「库行」的身份（/api/scenes 的相对路径 id）。
   *  手工/关联出来的场景不在库里，其 id 是 `~` 形态的绝对路径编码 —— 写进去
   *  会让 Agent 上下文与任务区按库行去查，查不到。提交与掩码都不需要它
   *  （两者只认 lqPath）。 */
  function attachSceneLink(rec: ViewerRec, res: SceneResolveResult): void {
    rec.lqPath = res.resolved.dir;
    rec.serverMaskPath = res.resolved.mask_path;
    rec.status = '已关联盘阵目录：' + res.resolved.dir;
    rec.statusCls = 'ok';
  }

  /** 本地 TIF 打开后**试着**关联盘阵目录（不阻塞打开）。
   *
   *  浏览器拿不到本地文件的绝对路径（`File` 只有 name/size/type），所以只能把
   *  **裸文件名**交给后端，由它按生产命名规则 + SR_SCENE_PATH_TEMPLATE 反推候选
   *  目录 —— 规则唯一真源在 backend/pathguard.py，前端不自拼路径。命中才写
   *  lqPath；没命中就是一条 4xx，`detail` 里写着试过哪些候选、各自为什么不行，
   *  原样展示给用户，本地图照常能看。猜错必须报错，绝不静默提交。
   *
   *  **route='jpg' 一律不试**：那种 rec 的目录已经由 resolve 按绝对路径定过（就是
   *  权威值）。粘单个 .tif 且父目录不是场景目录时它的 lqPath 是 null —— 这时若按
   *  文件名去反推，可能命中**另一个**目录，用户点「提交 SR」就会拿着错的 lq_path
   *  去跑，而这正是「绝不静默提交」要防的事。父目录不是场景目录，就是不能提交。 */
  async function tryLinkScenes(rec?: ViewerRec | null): Promise<void> {
    const r = rec ?? activeRec.value;
    // 已是盘阵场景 / 走过 resolve / 已关联过 / 已试过 —— 都直接返回
    if (!r || r.lqPath || r.linkTried || r.route === 'jpg') return;
    r.linkTried = true;
    try {
      const res = await apiResolveScene(loadSrConfig(), { name: r.name });
      if (!recs.value.includes(r)) return;            // 期间已切图/关掉
      attachSceneLink(r, res);
      showToast('已关联盘阵目录 ' + res.resolved.dir + '，可以提交 SR 了');
      if (r === activeRec.value) renderTick.value++;
    } catch (e) {
      if (!recs.value.includes(r)) return;
      const msg = e instanceof Error ? e.message : String(e);
      showErr('未能关联盘阵目录（' + r.name + '）：' + msg
        + ' —— 本地文件仍可查看；要提交 SR 请在「盘阵场景」栏粘贴该场景目录后打开。');
    }
  }

  /** 查看器工具栏的「打开盘阵场景」：resolve → 取字节 → openSceneJpg。
   *
   *  与场景库页同一条后端链路（POST /api/scenes/resolve + /preview），只是错误
   *  写进查看器的错误条（ScenesPage 写 scenes.error；写错地方就等于没报）。 */
  async function openScenePath(path: string): Promise<boolean> {
    const trimmed = String(path ?? '').trim();
    if (!trimmed) { showErr('请填写场景目录路径'); return false; }
    showMask('正在打开盘阵场景…', trimmed, false);
    try {
      const res = await apiResolveScene(loadSrConfig(), { path: trimmed });
      if (!res.resolved.writable) {
        // 不阻断：只读目录里已有掩码时仍能提交；写掩码/回写 meta.xml 会失败
        showToast('提示：服务账号对 ' + res.resolved.dir + ' 没有写权限，'
          + '保存掩码到盘阵会失败');
      }
      // 首次要服务端烘焙 1/2 预览图（读一遍大图）——把遮罩文案换成这一句，
      // 否则几十秒里界面看起来像卡死了。
      const blob = await fetchSceneJpg(loadSrConfig(), res.row, (text) => {
        showMask('正在打开盘阵场景…', text, false);
      });
      hideMask();
      // lqPath 是「能不能提交 SR」的唯一判据（见 submitSr）。粘单个 .tif 进来时
      // 它可能不在场景目录里 —— 那种图能看，但 SR 在盘阵上跑不起来，所以不写
      // lqPath（后端用 sr_capable 告诉你），让它走 submitSr 的「需要盘阵目录」分支。
      await openSceneJpg({
        name: res.row.name, W: res.row.W as number, H: res.row.H as number,
        sceneId: res.row.id,
        lqPath: res.resolved.sr_capable ? res.resolved.dir : null,
      }, blob);
      const rec = recs.value.find((x) => x.name === res.row.name);
      if (rec) {
        rec.serverMaskPath = res.resolved.mask_path;
        if (!res.resolved.sr_capable) {
          showToast('这张图不在场景目录里，只能查看，不能提交 SR');
        } else if (!res.resolved.mask_exists) {
          showToast('该场景目前没有掩码（' + res.resolved.mask_path
            + '）—— 画完点「保存掩码到盘阵」再提交');
        }
      }
      return true;
    } catch (e) {
      hideMask();
      showErr('打开失败：' + (e instanceof Error ? e.message : String(e)));
      return false;
    }
  }

  /** 把画好的掩码写进**服务端**场景目录（POST /api/masks）。
   *
   *  盘阵上大多数生产场景本来没有掩码，得现画。写出去的文件名由后端推导
   *  （`<输入名>_mask.tif`），与提交时去找的那份同源，所以写完成功就能直接提交。
   *  `genMask()` 的本地双下载保留不动：离线/纯本地 tif 仍然需要它。 */
  async function bakeMaskToServer(): Promise<boolean> {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return false; }
    if (!rec.lqPath) {
      showErr('这张图没有盘阵目录，掩码无处可写 —— 请先在「盘阵场景」栏打开对应目录');
      return false;
    }
    const rois = getRois();
    if (!rois.length) {
      showErr('还没有任何掩码区域：先「绘制掩码」，用矩形/多边形/魔棒画 ROI');
      return false;
    }
    const W = rec.W, H = rec.H;
    const tw = rec.thumb.width, th = rec.thumb.height;
    // 缩略图坐标 → 全分辨率（与 genMask 同一套换算）
    const polys = rois.map((pts) => pts.map((p) => thumbToOrig(p[0], p[1], W, H, tw, th)));
    if (srBusy.value) return false;
    srBusy.value = true;
    const t0 = Date.now();
    showMask('正在把掩码写入盘阵（' + W + '×' + H + '，' + polys.length + ' 区域）…',
      rec.lqPath, true);
    try {
      // 栅格化发生在服务端，前端拿不到真实进度：只让进度条走起来，文案写明是等待
      let f = 0;
      const tick = window.setInterval(() => {
        f = Math.min(f + 0.02, 0.95);
        overlay.value.progress = Math.round(f * 100);
        overlay.value.sub = '服务端栅格化中，请稍候';
      }, 200);
      try {
        const res = await apiBakeMask(loadSrConfig(),
          { lq_path: rec.lqPath, W, H, polygons: polys });
        rec.serverMaskPath = res.mask_path;
        hideMask();
        showToast('掩码已写入盘阵：' + res.mask_path
          + '（' + ((Date.now() - t0) / 1000).toFixed(1) + 's）—— 现在可以提交 SR 了');
      } finally {
        window.clearInterval(tick);
      }
      return true;
    } catch (e) {
      hideMask();
      showErr('写入掩码失败：' + (e instanceof Error ? e.message : String(e)));
      return false;
    } finally {
      srBusy.value = false;
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
    stretchMode, activeStretch, drawMode, drawTool, pendingRect, pendingPts, hoverPt, hoverRoi, flashRoi,
    wandTol, merging, overlay, toast, error,
    sidebarCollapsed, busy, srBusy,
    // 文件 / 解码
    addFiles, removeRec, activate, openSceneJpg, openLocalImage,
    // 拉伸 / 视图
    setStretch, setCanvasSize, fit, onWheel, onPan, locatePixel,
    // 掩码
    enterDraw, exitDraw, setDrawTool, commitRect, closePolygon, undoRoi, clearRois,
    mergeRois, delClick, wandSelect, buildMaskJson, exportMaskJson, genMask,
    getRois, submitSr, bakeMaskToServer,
    // 手工盘阵场景：打开任意场景目录 / 本地图反推关联
    openScenePath, tryLinkScenes, attachSceneLink,
    // 侧舱 ROI 选择 / 确定性统计
    selRoi, roiStats, roiSelIndex, selectRoi, clearRoiSel, refreshRoiStats,
    // 云量估算（整景/当前视野 + 疑似云区红叠）
    cloudScene, cloudView, cloudShow, cloudOverlay,
    refreshCloudStats, refreshCloudView, setCloudShow,
    // 画布事件
    onCanvasDownDraw, onCanvasMove, onCanvasUp, onDblClick, onKeyDown,
    // UI
    showMask, hideMask, showToast, showErr,
    setSparseMin,
  };
});
