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
import { ref, computed, markRaw, nextTick } from 'vue';
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
import { fitView, locateView, wheelZoom, hitRoi, thumbToOrig, visibleThumbRect,
  clampSplitRatio, splitRects, paneAtX, wheelZoomBoth, panBoth,
  remapViewForImage } from '../lib/viewMath.js';
import type { ViewState, Rect, PaneRect } from '../lib/viewMath.js';
import {
  parseCompareMode, loadSplitRatio, saveSplitRatio, seedCompareList,
  addCompareEntry, removeCompareEntry, pruneCompareList, DEFAULT_SPLIT_RATIO,
  loadCmpPrefetch, saveCmpPrefetch, placeInSplit,
} from '../lib/compare.js';
import type { CompareMode } from '../lib/compare.js';
import { FileSource } from '../lib/source.js';
import { browserKit } from '../lib/browserKit.js';
import { decodeOne } from '../lib/decode.js';
import type { DecodedRec } from '../lib/decode.js';
import {
  sceneDecodePixels, loadSrConfig, startStretch, loadPreviewDiv,
  savePreviewDiv, SCENE_PREVIEW_DIVS, previewDivLabel, rasterPreviewWins,
  sceneAnchors,
} from '../lib/scene.js';
import {
  apiResolveScene, apiBakeMask, apiSceneSiblings, fetchSceneJpg, fetchDropSceneJpg,
  siblingRow, previewCacheStats, clearPreviewCache, watchPreviewCache,
} from '../lib/api.js';
import type { SceneResolveResult, SceneSibling, SceneSiblings } from '../lib/api.js';
import { classifyImages, imageKindOf } from '../lib/imageFiles.js';
import type { SceneOpenMeta } from '../lib/scene.js';
import { isIntermediateStage, stageLabel, stageRefusal } from '../lib/stage.js';
import type { StageKind } from '../lib/stage.js';
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
   *  除非 tryLinkScenes 命中了盘阵目录并把它升级成 route='jpg'。 */
  lqPath: string | null;
  /** 掩码写到服务端后，服务端告知的绝对路径（`bakeMaskToServer` 成功才非空）。
   *  显示用；提交时真正的值由后端按同一规则重新推导。 */
  serverMaskPath?: string | null;
  /** 盘阵场景里的环节（盘阵 JPG 才有）：本体输入 / 本轮超分产物 / NOSR。
   *  **与 `lqPath` 正交** —— 三者都在同一个场景目录里，`lqPath` 都是同一个值；
   *  能不能修复只看这一项（见 lib/stage.ts 顶部那段）。 */
  stageKind?: StageKind;
  /** 环节标签（`PAN` / `本体` / `SR` / `NOSR`），卡片小标用。 */
  stageLabel?: string;
  /** 这一行**所属的场景目录**（知道了就一定写，与能不能提交无关）。
   *  与 `lqPath` 的区别只在中间产物上：那类的 `lqPath` 被服务端置空（不可提交），
   *  但它仍属于某个场景目录 —— 卡片上那颗「同一景共用一个序号」的小标按这一项
   *  分组。裸 .tif / 场景库之外的单张图没有场景目录，这里与 `lqPath` 同为 null。 */
  sceneDir?: string | null;
  /** 已经预热过缩放的那份**显示画布**（`=== thumb` 即热过）。存引用而不是布尔：
   *  换过像素（重新解码 / 换预览档位）就是新画布，缩放缓存不作数，得重新热。 */
  warmed?: KitCanvas | null;
  /** 已经试过反推关联盘阵目录（成功或失败都算）。每个文件只试一次 ——
   *  切来切去不该反复打同一个请求、反复弹同一条错。网络失败/超时不算「试过」，
   *  那种情况不留痕，下次激活还能再试。 */
  linkTried?: boolean;
  /** 反推关联失败时服务端给的原因（原样展示）。单独一个字段而不是写进
   *  `status`：activate 里紧接着的本地解码几百毫秒内就会覆盖 status，写在那儿
   *  等于没写；用户与 e2e 都只看得到这一份。 */
  linkNote?: string;
  /** 像素写令牌：每次「有新的写者要接管这张图的像素」时 +1（本地解码
   *  `decodeRec` / 盘阵 JPG 升级 `applySceneJpgToRec`）。落笔前比对，对不上就说
   *  明期间有更新的写者接手，自己这份已经过期 —— 真机上本地解码要几十秒，
   *  切图/升级完全可能挤在中间完成，旧解码结果绝不能盖到新图上。 */
  token: number;
  layout: string;              // 即探标签摘要
  status: string;
  statusCls: '' | 'ok' | 'err';
  paintedMode: StretchMode | null;
  maskRois: Poly[] | null;     // 缩略图坐标 ROI
}

/** 渲染器的一格（图像对比，2026-09-20）。
 *
 * `rect` 是画布内的屏幕矩形，`view` 是**这一格自己的局部坐标**（原点 = 该格左上角）——
 * 于是 fitView/locateView/visibleThumbRect/mouseToThumb 全部原样可用。
 * 单屏时 `panes` 只含一个铺满全画布的格子，渲染走与今天逐字相同的快速路径。 */
export interface Pane {
  side: 'A' | 'B';
  rect: PaneRect;
  view: ViewState;
  rec: ViewerRec | null;
  active: boolean;
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
/** 落位提示的续期定时器（每次 dragover 重置，见 setDragHint）。 */
let dragHintTimer: ReturnType<typeof setTimeout> | null = null;

/* ---------------- 右侧栏展开状态（从 ContextPanel 提升，2026-09-20） ----------------
   分屏要能自动收起它、退出时再恢复，所以这份状态不能再留在组件里。
   **key 与默认值一字不改**：老用户的「上次收起来了」继续生效。 */
const CTX_RAIL_KEY = 'sr.viewer.ctxRailOpen';
function readCtxRailOpen(): boolean {
  try {
    if (typeof localStorage === 'undefined') return false;
    return localStorage.getItem(CTX_RAIL_KEY) === '1';
  } catch {
    return false;
  }
}
function saveCtxRailOpen(open: boolean): void {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(CTX_RAIL_KEY, open ? '1' : '0');
  } catch {
    /* 忽略：存不下不影响本次会话 */
  }
}

export const useViewerStore = defineStore('viewer', () => {
  /* ---------------- 状态 ---------------- */
  const recs = ref<ViewerRec[]>([]);
  const activeId = ref<number | null>(null);
  const canvasSize = ref({ w: 0, h: 0 });
  const renderTick = ref(0);
  const marker = ref<{ tx: number; ty: number; until: number } | null>(null);
  const stretchMode = ref<StretchMode>('linear');
  /** 预览烘焙档位（各边 ÷N），平台级设置，初值从 localStorage 来。
   *  工具栏那条拖动条读写它；取图的两处调用点也读它。 */
  const previewDiv = ref(loadPreviewDiv());
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
  /** 模态提示：**不自动消失**，得用户点掉。给的是后端那些一长串、需要照着做的
   *  原因（toast 六秒既看不完也留不住）。全局只有一个，后弹的顶掉先弹的。 */
  const modal = ref({ visible: false, title: '', body: '', hint: '' });
  const sidebarCollapsed = ref(false);
  const busy = ref(false);
  const srBusy = ref(false);       // 「提交 SR」进行中（掩码服务端烘焙）

  /* ---------------- 图像对比（2026-09-20） ----------------

     分屏 = **一个画布、两个裁剪矩形**：每格一套 ViewState（viewA/viewB），下面那个
     `view` 是**可写 computed**，代理到活动侧那一套。于是全仓读 `view.value` 的地方
     （TifCanvas 与 store 内约十二处）一行都不用改。

     `activeId` 仍是「活动侧那张 rec」：`placeRec` 在 `activate` 开头把两者对齐，
     掩码 / ROI 统计 / 云量卡 / 任务状态 / 待修复清单自动选中 / StatusBar /
     Toolbar 可用性（约四十处 activeRec 消费点）全部自动跟随活动侧。

     三态与纯判据（落点归属、清单增删、比例解析）在 lib/compare.ts；分屏几何在
     lib/viewMath.ts。这里只放状态与编排。 */

  const compareMode = ref<CompareMode>('off');
  /** 工具栏下方那条区域的展开与否。**不持久化**：刷新即回关闭。 */
  const cmpStripOpen = ref(false);
  const paneA = ref<number | null>(null);
  const paneB = ref<number | null>(null);
  /** 非分屏恒为 'A'。 */
  const activeSide = ref<'A' | 'B'>('A');
  /** 分隔比例。跨会话记住 —— 只有它持久化，对比模式本身不。 */
  const splitRatio = ref(loadSplitRatio());
  const viewA = ref<ViewState>({ scale: 1, ox: 0, oy: 0 });
  const viewB = ref<ViewState>({ scale: 1, ox: 0, oy: 0 });
  /** 点选清单成员（有序 rec id）。 */
  const compareList = ref<number[]>([]);
  const dragHint = ref<{ active: boolean; side: 'A' | 'B' | null }>({ active: false, side: null });
  /** 右侧栏是否展开（从 ContextPanel 提升）。进分屏自动收起、退出时按 prev 还原。 */
  const ctxRailOpen = ref(readCtxRailOpen());
  /** 最近一次**显示过**的场景目录（`recs` 里带 `lqPath` 的那张被换上来时记一次）。
   *  拖 jpg 时它当 `anchor` 递给后端 —— 名字里没有场景身份的 jpg（RC 场景的产物
   *  `PAN_<suffix>.jpg`）反推不出目录，只有「用户当前在看哪一景」这条线索。
   *  **不是响应式 state**：它只喂请求，不上屏。 */
  let lastSceneDir: string | null = null;
  const ctxRailPrevOpen = ref<boolean | null>(null);

  /** 活动侧的视图变换。**全仓 `view.value = …` 只有 5 处，都在本文件内**，
     读点一个都不用改。Pinia 会解包 computed，`store.view.ox` 照旧可用。 */
  const view = computed<ViewState>({
    get: () => (activeSide.value === 'B' ? viewB.value : viewA.value),
    set: (v) => { if (activeSide.value === 'B') viewB.value = v; else viewA.value = v; },
  });

  /** 分屏中（关闭 / 点选对比都是单屏）。 */
  const split = computed(() => compareMode.value === 'split');
  /** 任一对比模式 —— 拖放门、只读、右栏自动收起都看它。 */
  const compareOn = computed(() => compareMode.value !== 'off');

  /** 分隔线在画布局部坐标里的 x。 */
  const splitX = computed(
    () => splitRects(splitRatio.value, canvasSize.value.w, canvasSize.value.h).a.w,
  );

  /** 某一格的视口矩形。单屏 = 铺满画布（与今天同义）。 */
  function rectForSide(side: 'A' | 'B'): PaneRect {
    const { w, h } = canvasSize.value;
    if (!split.value) return { x: 0, y: 0, w, h };
    const r = splitRects(splitRatio.value, w, h);
    return side === 'A' ? r.a : r.b;
  }

  function activePaneRect(): PaneRect {
    return rectForSide(activeSide.value);
  }

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

  /** 某一格上摆着哪张 rec。**单屏时与 `activeRec` 同源** —— 于是渲染器只有
      `panes` 一个输入，不必分「单屏走老路、分屏走新路」。 */
  function recForSide(side: 'A' | 'B'): ViewerRec | null {
    if (!split.value) return activeRec.value;
    const id = side === 'A' ? paneA.value : paneB.value;
    return id === null ? null : recs.value.find((r) => r.id === id) ?? null;
  }

  /** 渲染器的唯一输入：单屏 = 一个铺满的格子（`length === 1` 走逐字保留的快速
      路径），分屏 = 左右两格。 */
  const panes = computed<Pane[]>(() => {
    const { w, h } = canvasSize.value;
    if (!split.value) {
      return [{ side: 'A', rect: { x: 0, y: 0, w, h }, view: viewA.value, rec: recForSide('A'), active: true }];
    }
    const r = splitRects(splitRatio.value, w, h);
    return [
      { side: 'A', rect: r.a, view: viewA.value, rec: recForSide('A'), active: activeSide.value === 'A' },
      { side: 'B', rect: r.b, view: viewB.value, rec: recForSide('B'), active: activeSide.value === 'B' },
    ];
  });

  /* ---------------- 归位（placeRec）：新图进哪一格 ---------------- */

  /** 把这 id 放进它该在的格子，并让它成为活动侧。**整套落点规则就这一个函数**：
   *
   *  - 单屏（关闭 / 点选对比）→ 唯一格子，直接顶掉当前这张。于是「点选对比拖进来
   *    就覆盖当前这张」不需要任何特判。
   *  - 分屏 + 给了 side（拖放落点）→ 落那一格；
   *  - 分屏 + 没给 side（文件列表点击 / 盘阵栏 / 待修复清单「打开」）→ 落活动侧。
   *
   *  分屏的那一支（进哪一格、要不要互换、目标格空着怎么办）全在纯函数
   *  `lib/compare.placeInSplit` 里 —— 它是判据，值得单测；这里只把结果写进响应式状态。
   *
   *  返回「目标格换了一张图」（= 那一格的视口需要重新适配）。调用方据此决定要不要
   *  `fit()` —— 同一张图留在原格时不重适配，用户的缩放不该被一次多余的点选抹掉。 */
  /** 一张 rec 属于哪个场景目录（盘阵 POSIX）。中间产物的 `lqPath` 是空的 —— 它不
   *  可提交 —— 但 `sceneDir` 有，而「用户在看的这一景」两个都算。 */
  function sceneDirOf(r: ViewerRec | null | undefined): string | null {
    return r?.sceneDir ?? r?.lqPath ?? null;
  }

  /** 记下「用户当前打开的这一景」。拖 jpg 时当 `anchor` 递给后端（见 tryLinkScenes）。 */
  function noteSceneDir(r: ViewerRec | null | undefined): void {
    const d = sceneDirOf(r);
    if (d) lastSceneDir = d;
  }

  function placeRec(id: number, side?: 'A' | 'B'): boolean {
    noteSceneDir(recs.value.find((r) => r.id === id));      // 换进来的那张
    if (!split.value) {
      const changed = paneA.value !== id;
      paneA.value = id;
      paneB.value = null;
      activeSide.value = 'A';
      activeId.value = id;
      return changed;
    }
    const r = placeInSplit(paneA.value, paneB.value, id, side ?? null, activeSide.value);
    paneA.value = r.paneA;
    paneB.value = r.paneB;
    activeSide.value = r.side;
    activeId.value = id;
    return r.moved;
  }

  /** 切图/切侧时的公共重置。`activate` 与 `setActiveSide` 共用 —— 两处各写一遍
      迟早漂移，漏一个就是「云量卡还挂着上一张的数」。 */
  function switchActive(id: number, doFit = false) {
    marker.value = null;
    activeId.value = id;
    clearRoiSel();                   // 侧舱选择/统计随图失效
    clearCloud();                    // 云量数字/红叠随图失效（随后 refresh 补回）
    if (drawMode.value) {
      pendingPts.value = null;
      pendingRect.value = null;
      hoverPt.value = null;
    }
    const rec = recs.value.find((r) => r.id === id);
    if (rec && rec.thumb) {
      if (doFit) fit();
      refreshCloudStats();
    }
  }

  /** 手动切活动侧（点分屏的另一半）。
   *  **刻意不调 `activate`**：两格本来就都显示着自己的图，切的是「哪一侧在响应
   *  掩码/云量/任务状态」，不该重新解码、重新适配、惊动侧舱。 */
  function setActiveSide(side: 'A' | 'B') {
    if (!split.value || activeSide.value === side) return;
    const id = side === 'A' ? paneA.value : paneB.value;
    if (id === null) return;               // 空侧没什么可切的，忽略（点击退化为平移）
    // 绘制期间不让活动侧落到不可绘制的那张（分屏里点产物那一半就是这条路）：
    // 掩码只认活动侧（见 enterDraw），放过去就等于「在产物上画掩码」。
    // 不静默忽略 —— 点了没反应比一句说明更让人迷惑。
    const next = recs.value.find((r) => r.id === id) ?? null;
    if (drawMode.value && !canDrawOn(next)) {
      showToast('绘制掩码中：活动侧停在可绘制的那张图（' +
        (next?.stageLabel ?? '产物') + '不能画掩码），先点「完成」再切');
      return;
    }
    activeSide.value = side;
    switchActive(id);
    renderTick.value++;
  }

  /* ---------------- 分屏适配 ---------------- */

  /** 每格当前的 ViewState 是**按哪个缩略图尺寸**算出来的（null = 这格没适配过 / 现在空着）。
   *
   *  换图时靠它决定「该 remap 还是该 fit」—— 它记的就是「此前摆在这格的是多大的图」。
   *  `activate` 与 `afterPixels` 这两条换图路径是**异步**的（.jpg 要先解码），把账记在
   *  这里就不必跨 await 传「上一张是谁」。
   *
   *  **非响应式**：只参与判断，不进渲染 —— 画布尺寸、比例、视图各自有 ref 在管它。 */
  const viewFor: { A: { w: number; h: number } | null; B: { w: number; h: number } | null }
    = { A: null, B: null };

  function setSideView(side: 'A' | 'B', v: ViewState) {
    if (side === 'B') viewB.value = v; else viewA.value = v;
  }

  /** 只适配某一格（按它自己的视口矩形）。空格（没图 / 没像素）直接返回，
   *  **不记账** —— 「这格没适配过」正是「往空格里拖图该老实 fit」的判据。 */
  function fitSide(side: 'A' | 'B') {
    const rec = recForSide(side);
    if (!rec || !rec.thumb) return;
    const rect = rectForSide(side);
    const v = fitView(rec.thumb.width, rec.thumb.height, rect.w, rect.h);
    setSideView(side, v);
    viewFor[side] = { w: rec.thumb.width, h: rec.thumb.height };
  }

  /** 两格各自适配 —— 进分屏、回正、画布尺寸变化时用。半幅是新的视口，
     沿用全幅那次的适配两边都不对。 */
  function fitBoth() {
    fitSide('A');
    fitSide('B');
  }

  /** **换图**之后那一格的适配：对比模式下按相对视野搬过去，其余情况老实 fit。
   *
   *  `side` = 这张图落在哪一格（`placeRec` 之后 `activeSide` 就是它）。
   *  `viewFor[side]` 为空 = 这一格此前空着（进分屏时的右格）或从没适配过 → 老实 fit，
   *  否则用户会看到一张缩在左上角的图。 */
  function fitAfterImageChange(side: 'A' | 'B', rec: ViewerRec) {
    if (!rec.thumb) return;
    const rect = rectForSide(side);
    const size = { w: rec.thumb.width, h: rec.thumb.height };
    const prev = viewFor[side];
    const cur = side === 'B' ? viewB.value : viewA.value;
    setSideView(side, (compareOn.value && prev)
      ? remapViewForImage(cur, prev, size, rect.w, rect.h)
      : fitView(size.w, size.h, rect.w, rect.h));
    viewFor[side] = size;
  }

  /** 解码/取图完成后的收尾。旧写法是 `if (activeId === rec.id) { fit(); … }`——
     分屏下这张可能在**非活动侧**解码完成，那样那一格的 ViewState 会停在 {1,0,0}
     （图缩在左上角）。所以改成「显示着这张 rec 的格子各适配一次」。

     2026-09-20：两块都改走 `fitAfterImageChange` —— 对比模式下换图不再把用户的
     缩放与位置抹掉（按相对视野搬过去）。关闭模式 `compareOn` 为假，仍然走 `fit()`。 */
  function afterPixels(rec: ViewerRec) {
    if (split.value) {
      if (paneA.value === rec.id) fitAfterImageChange('A', rec);
      if (paneB.value === rec.id) fitAfterImageChange('B', rec);
      if (activeId.value === rec.id) refreshCloudStats();
      renderTick.value++;
      return;
    }
    if (activeId.value === rec.id) {
      if (compareOn.value) fitAfterImageChange('A', rec);
      else fit();
      refreshCloudStats();
      renderTick.value++;
    }
  }

  /* ---------------- 模式切换 ---------------- */

  /** 进分屏：自动收起右侧栏，并记住用户此前的展开状态（退出时还原）。
      只记第一次 —— 从点选切到分屏不该把已经记住的手动状态覆盖掉。 */
  function collapseRailForCompare() {
    if (ctxRailPrevOpen.value === null) ctxRailPrevOpen.value = ctxRailOpen.value;
    ctxRailOpen.value = false;
    saveCtxRailOpen(false);
  }
  function restoreRailAfterCompare() {
    if (ctxRailPrevOpen.value === null) return;
    ctxRailOpen.value = ctxRailPrevOpen.value;
    saveCtxRailOpen(ctxRailOpen.value);
    ctxRailPrevOpen.value = null;
  }

  /** 切「关闭 / 点选对比 / 分屏对比」。 */
  function setCompareMode(mode: CompareMode) {
    const next = parseCompareMode(mode);
    if (next === compareMode.value) return;
    const wasSplit = split.value;
    const wasOff = compareMode.value === 'off';
    // **刻意不再 exitDraw()**（2026-09-22）：对比模式下可以继续画掩码（活动侧那张），
    // 切模式不该把用户画了一半的框丢掉。pendingRect/pendingPts 存的是缩略图坐标，
    // 换了视口也仍然指着同一片影像区域，所以留着是对的。

    if (next === 'split') {
      if (!wasSplit) {
        // 进分屏：当前这张进左格，右格空着等拖入。两侧都是新视口 → 两边重新适配。
        paneA.value = activeId.value;
        paneB.value = null;
        viewFor.B = null;                  // 右格空着 = 没适配过（往它里面落图该老实 fit）
        activeSide.value = 'A';
      }
      compareMode.value = next;
      compareList.value = seedCompareList(recs.value.map((r) => r.id));
      collapseRailForCompare();
      fitBoth();
      if (wasOff) void maybePrefetchCompare();
      renderTick.value++;
      return;
    }

    if (wasSplit) {
      // 退出分屏：留下**此前活动侧**那张（单屏显示它），重新适配整幅。
      const keep = recForSide(activeSide.value);
      compareMode.value = next;
      paneB.value = null;
      viewFor.B = null;
      activeSide.value = 'A';
      if (keep) { paneA.value = keep.id; activeId.value = keep.id; }
      restoreRailAfterCompare();
      if (next === 'off') { compareList.value = []; stopPrefetch(); }
      fit();                               // 重记 viewFor.A（单屏视口，全幅）
      renderTick.value++;
      return;
    }

    // 关闭 ↔ 点选：单屏语义完全不变，只换模式与清单播种。
    compareMode.value = next;
    if (next === 'off') {
      compareList.value = [];
      paneB.value = null;
      viewFor.B = null;
      activeSide.value = 'A';
      stopPrefetch();
    } else {
      compareList.value = seedCompareList(recs.value.map((r) => r.id));
      if (wasOff) void maybePrefetchCompare();
    }
    renderTick.value++;
  }

  function setCmpStripOpen(open: boolean) {
    cmpStripOpen.value = !!open;
  }

  /* ---------------- 设置浮层 ---------------- */

  const settingsOpen = ref(false);
  function setSettingsOpen(open: boolean) {
    settingsOpen.value = !!open;
  }

  /** 本地预览缓存的**代数**：内容一变就 +1，设置浮层那一行靠它保持实时。
   *
   *  不做成「把 stats() 挂成响应式」：那样每次取图都要重算一次渲染。代数只在缓存
   *  一进一出时各响一次（见 lib/api.ts 的 watchPreviewCache），而**它必须响** ——
   *  改缓存的那颗按钮（预取开关）就在那一行上面，不响的话用户永远看不到自己刚触发
   *  的那一次，只会得出「预取没生效，缓存始终是 0 项」。 */
  const previewCacheRev = ref(0);
  watchPreviewCache(() => { previewCacheRev.value++; });

  /* ---------------- 对比模式后台预取（用户开关，默认关） ----------------

     进对比模式时提前把同场景另两类图的预览取到本地：真机上切图的等待几乎全在
     「取一份几 MB 的盘阵 jpg」上，提前取好就是零等待。

     **绝不触发服务端烘焙**：只取 `hasPreview && previewDiv === div` 的那几类
     （服务端已经现成烤好的那份），判据本身就把「会重烤的」挡在外面 —— 真机上
     「我什么都没点，盘阵却在读大图」是件让人不安的事。开关默认关，装好后行为
     与没有这个功能时完全一样。 */

  const cmpPrefetchOn = ref(loadCmpPrefetch());
  /** 上一次预取干了什么（设置浮层里那个开关下面一行）。**空串 = 还没跑过**。
   *
   *  为什么要说出来：预取的合格项是「盘上已有一份现成预览」，第一次打开某个场景时
   *  这个集合**本来就是空的**（另两类还没人烤过）。这时缓存行会如实停在 0 项，用户
   *  无从分辨「没东西可预取」与「预取坏了」—— 所以结果必须自己讲出来。 */
  const prefetchNote = ref('');
  /** 已经预取过的 `sceneId|档位`。同一个场景 + 同一档位只做一次。
   *  离开对比模式时清空 —— 那时 blob 缓存可能也被清过，重进该重取一遍。 */
  const prefetched = new Set<string>();
  /** 代数计数：离开对比模式 / 换场景就 +1，在飞的预取自己发现过期就收手。
   *  取图的两个 API 都不收 AbortSignal（它们要兼容静态 URL 那条支路），
   *  所以取消做成「不开始下一项」而不是「掐断在飞的那个」。 */
  let prefetchGen = 0;

  function setCmpPrefetch(on: boolean) {
    const next = !!on;
    if (next === cmpPrefetchOn.value) return;
    cmpPrefetchOn.value = next;
    saveCmpPrefetch(next);
    if (next) void maybePrefetchCompare();   // 当场打开就当场开始，不用等下次切图
    else stopPrefetch();                     // 关掉就收手，并把上一次的结果行擦掉
  }

  /** 收手：离开对比模式、或活动图不再是盘阵场景时调。 */
  function stopPrefetch() {
    prefetchGen++;
    prefetched.clear();
    prefetchNote.value = '';
  }

  /** 预取同场景另两类图的预览。三道门：对比模式 + 开关开 + 活动图有 sceneId。 */
  async function maybePrefetchCompare(): Promise<void> {
    if (!compareOn.value || !cmpPrefetchOn.value) return;
    const sid = activeSceneId();
    if (!sid) return;
    const div = previewDiv.value;
    const key = sid + '|' + div;
    if (prefetched.has(key)) return;
    prefetched.add(key);                    // 先记上：免得连着几次 activate 各起一轮
    const my = prefetchGen;
    const cfg = loadSrConfig();
    let res: SceneSiblings;
    try {
      res = await apiSceneSiblings(cfg, sid);
    } catch {
      prefetchNote.value = '预取没问成：同场景三类图没查到';
      return;                               // 预取失败不打扰用户，点的时候照旧会去取
    }
    if (my !== prefetchGen) return;
    // 只挑「服务端已有一份现成预览、且档位对得上」的那几类，且**没开着的**。
    const todo = res.items.filter((it) => it.exists && it.id && it.name
      && it.W != null && it.H != null
      && it.hasPreview && it.previewDiv === div
      && !recs.value.some((r) => r.sceneId === it.id));
    if (!todo.length) {
      prefetchNote.value = '这次没有可预取的：另两类在盘上还没有现成预览'
        + '（预取不触发烘焙，打开过一次之后就有了）';
      return;
    }
    let done = 0;
    for (const it of todo) {
      if (my !== prefetchGen) return;
      prefetchNote.value = `正在预取 ${done + 1}/${todo.length}…`;
      try {
        // 顺序取（并发 1）：真机上别同时读几份盘阵预览。
        // **不传 onPhase**：预取不该占用遮罩，用户此刻在看别的。
        await fetchSceneJpg(cfg, siblingRow(res, it), div);
        done++;
      } catch {
        /* 单张失败就跳过，接着取下一张 */
      }
    }
    if (my !== prefetchGen) return;
    prefetchNote.value = done === todo.length
      ? `已预取 ${done} 项，切图不必现取`
      : `预取了 ${done}/${todo.length} 项（其余没取到）`;
  }

  /* ---------------- 分隔比例 ---------------- */

  /** 设比例。**顺手持久化**：一次 localStorage 写 20 字节，比这次改动触发的重绘
      便宜得多，不值得为它多开一个 commit 接口。 */
  function setSplitRatio(r: number) {
    const next = clampSplitRatio(r, canvasSize.value.w);
    if (next === splitRatio.value) return;
    splitRatio.value = next;
    saveSplitRatio(next);
    renderTick.value++;
  }

  /** 回正：比例回 0.5 **且两侧重新适配**。只把线挪回中间而不重新适配，会让两侧
      各留一半旧视口，看起来像「图被裁掉了」。 */
  function resetSplit() {
    splitRatio.value = clampSplitRatio(DEFAULT_SPLIT_RATIO, canvasSize.value.w);
    saveSplitRatio(splitRatio.value);
    fitBoth();
    renderTick.value++;
  }

  /* ---------------- 落位提示 ---------------- */

  /** 落位提示的开关，**每次 dragover 续期一次**（500ms 没再来就自己熄）。
      用超时而不是 dragenter/dragleave 计数：指针穿过画布上的子元素时计数会失配，
      提示就卡住了。drop / dragend / window blur 会立即清掉它。
      **刻意不碰 renderTick** —— dragover 是高频事件，每次重绘整张画布没有必要，
      overlay 是 Vue 组件，读 dragHint 自己会重渲。 */
  const DRAG_HINT_TTL = 500;
  function setDragHint(active: boolean, side: 'A' | 'B' | null) {
    if (dragHintTimer) { clearTimeout(dragHintTimer); dragHintTimer = null; }
    if (!active) {
      if (dragHint.value.active || dragHint.value.side !== null) {
        dragHint.value = { active: false, side: null };
      }
      return;
    }
    const next = { active: true, side };
    dragHint.value = next;
    dragHintTimer = setTimeout(() => {
      dragHintTimer = null;
      dragHint.value = { active: false, side: null };
    }, DRAG_HINT_TTL);
  }

  /* ---------------- 侧栏卡片拖进画布（2026-09-21） ---------------- */

  /** 页面内拖放的载荷类型：侧栏文件卡（值为 `rec.id`）。
   *
   *  **刻意不用 `text/plain`**：画面上拖一段选中的文字、拖个链接也都是 text/plain，
   *  那两种落进画布什么都不该发生。自定义 MIME 同时也是 TifCanvas 的落图门 ——
   *  `dataTransfer.types` 在 dragover 阶段就能读到（浏览器不让读值，但类型名给读），
   *  所以落位提示能跟拖文件时一样提前亮。 */
  const REC_MIME = 'application/x-sr-rec';

  /** 卡片开始被拖：只在 dataTransfer 里放一张「票」（rec.id），**不搬像素**。
   *  落点由 TifCanvas 的 `dropSide` 算，最终还走 `activate(id, side)` 那条老路
   *  （换格 / 适配 / 重绘 / 进点选清单它都做齐了）。 */
  function startRecDrag(rec: ViewerRec, e: DragEvent): void {
    if (!e.dataTransfer) return;
    e.dataTransfer.setData(REC_MIME, String(rec.id));
    e.dataTransfer.effectAllowed = 'copy';
  }

  /** 拖放结束（含中途按 Esc、拖出窗口）：熄掉落位提示。`drop` 自己也会熄一次，
   *  这条是兜底 —— 否则提示得等 500ms 超时。 */
  function endRecDrag(): void {
    setDragHint(false, null);
  }

  /* ---------------- 同景序号（卡片小标） ---------------- */

  /** 同一景（同一个场景目录）在侧栏共用一个序号；不在场景目录里（裸 .tif、库外
   *  单张图）返回 0，卡片那边不渲染这一颗。
   *
   *  按 `sceneDir` 分组而**不是** `sceneId`：`sceneId` 是整条路径的编码，同一目录里
   *  的本体与产物是两个不同的 id，按它分组会把一景拆成三组 —— 而「它们是一景」
   *  恰恰是这颗小标要表达的唯一一件事。也**不是** `lqPath`：中间产物的 `lqPath`
   *  被服务端置空（不可提交，见 resolve 的 kind 分岔），按它分组会让产物一个号都
   *  拿不到，正是最需要「这张和本体是一景」的那一类。`sceneDir` 两者都覆盖。
   *
   *  Map 刻意**不做成响应式**（不是 ref）：它在渲染期按需发号，做成响应式等于在
   *  渲染中改状态，Vue 会警告递归更新；发号结果也不需要触发重渲（rec 列表本身
   *  变了才会有这次渲染）。只增不减 —— 移除一张再拖回来，还是原来的号。 */
  const sceneOrdinals = new Map<string, number>();
  function sceneOrdinalOf(rec: ViewerRec): number {
    const key = rec.sceneDir;
    if (!key) return 0;
    let n = sceneOrdinals.get(key);
    if (n === undefined) {
      n = sceneOrdinals.size + 1;
      sceneOrdinals.set(key, n);
    }
    return n;
  }

  /* ---------------- 点选清单 ---------------- */

  /** 新拖入的图自动加入清单（幂等）。 */
  function noteCompareEntry(id: number) {
    if (!compareOn.value) return;
    compareList.value = addCompareEntry(compareList.value, id);
  }

  /** 「清除」：**只把这一条移出清单**。文件列表条目、像素、屏幕上那张、activeId
      都不动 —— 屏幕上那张继续显示，只是不再参与清单轮换。
      文件列表点击**不会**把它加回清单（只有新拖入或重新进入对比模式才会），
      这样「清除」才是粘住的。 */
  function clearCompareEntry(id: number) {
    compareList.value = removeCompareEntry(compareList.value, id);
  }

  /** 清单里的 rec（按 id 顺序，找不到的跳过 —— removeRec 会剪枝，这里是双保险）。 */
  const cmpListRecs = computed<ViewerRec[]>(() =>
    compareList.value
      .map((id) => recs.value.find((r) => r.id === id))
      .filter((r): r is ViewerRec => !!r),
  );

  function setCtxRailOpen(open: boolean) {
    ctxRailOpen.value = !!open;
    saveCtxRailOpen(ctxRailOpen.value);
  }

  /* ---------------- 场景内快捷入口：三张图的廉价通道 ----------------

     为什么需要它：后端关联盘阵时是拿**场景输入影像的 stem** 去比用户拖进来的文件名
     （backend/api/app.py 的 `_fingerprint_mismatch`），产物叫 `<输入名>_<suffix>.tif`、
     `_NOSR` 更不必说，名字永远对不上 → 拖这两类进来必然 404，退回浏览器本地稀疏解码
     （真机大图几十秒、几百 MB，还拿不到 lqPath/sceneId，掩码没处写）。
     `/siblings` 恰好给了三类各自的 id，拿它调 `/preview` 就是服务端烤好的下采样 jpg。

     芯片绑**活动侧 rec 的 sceneId**：活动侧是本地文件（没有 sceneId）时整排禁用。 */

  /** 当前活动侧那张图的场景 id（没有 → null，芯片整排禁用）。 */
  function activeSceneId(): string | null {
    const rec = activeRec.value;
    return rec && rec.sceneId ? rec.sceneId : null;
  }

  /** 打开活动侧场景里的某一类图（input / product / nosr），落在活动侧。 */
  async function openSceneSibling(kind: SceneSibling['kind']): Promise<boolean> {
    const sid = activeSceneId();
    if (!sid) { showToast('先打开一张带盘阵关联的图，才能取同场景的其它图'); return false; }
    busy.value = true;
    showMask('正在查找同场景的图…', '三类图（输入 / 本轮超分产物 / NOSR）', false);
    try {
      const res = await apiSceneSiblings(loadSrConfig(), sid);
      const item = res.items.find((it) => it.kind === kind);
      if (!item || !item.id || !item.exists) {
        // 「找不到」如实交代：把试过哪些名字一并说出来，别只说一句「没有」。
        const kindName = kind === 'input' ? '输入影像'
          : kind === 'product' ? '本轮超分产物' : 'NOSR';
        const tried = res.productCandidates.length
          ? '（试过 ' + res.productCandidates.join(' / ') + '）' : '';
        const why = !res.suffix
          ? '—— 这个场景还没有可用的 suffix，拼不出产物名'
          : tried;
        showToast('盘阵上没有' + kindName + why);
        hideMask(); busy.value = false;
        return false;
      }
      if (item.W == null || item.H == null) {
        // 尺寸读不出来就不能开：掩码换算按 rec.W/H 建画布，0 会让落点全错。
        showErr('「' + (item.name ?? kind) + '」读不出影像尺寸，不能打开');
        hideMask(); busy.value = false;
        return false;
      }
      // 已经开着这张就别去要像素了：`/preview` 是一次可能几 MB 的往返，而这张图的
      // 像素已经在 rec 里。判据与 `openSceneJpg` 的 `findRecByMeta` 同源（认 `sceneId`）
      // —— 同一个场景若开出两条 rec，两份 maskRois 就各写各的了。
      // 于是「同一枚芯片连点两次」= 一次 `/siblings`（仍要问，才知道它对应哪条 rec）+ 零次 `/preview`。
      const opened = recs.value.find((r) => r.sceneId === item.id);
      if (opened) {
        hideMask(); busy.value = false;
        await activate(opened.id, split.value ? activeSide.value : undefined);
        return true;
      }
      const row = siblingRow(res, item);
      const blob = await fetchSceneJpg(loadSrConfig(), row, previewDiv.value, (text) => {
        showMask('正在加载同场景的图…', text, false);
      });
      hideMask(); busy.value = false;
      // 名字取 stem：与场景库那些行一个口径（列表里两个名字并排时不至于一个带后缀
      // 一个不带）。lqPath 用场景目录 —— 任务区靠它关联当前场景的队列行。
      // 环节照实带上：产物/NOSR**不是可修复对象**（掩码与 SR 都建在本体网格
      // 上），拖拽那条路进来的产物已经有这个字段了，这条入口没有的话，同一份产物
      // 就成了「拖进来不能改、芯片打开能改」两个说法。
      const stem = (item.name ?? kind).replace(/\.(tif|tiff|jpg|jpeg)$/i, '');
      await openSceneJpg({
        name: stem, W: item.W, H: item.H,
        sceneId: item.id, lqPath: res.lqPath, sceneDir: res.lqPath,
        serverMaskPath: null,
        stageKind: item.kind, stageSuffix: res.suffix,
      }, blob, split.value ? activeSide.value : undefined);
      if (res.suffixFrom !== 'query') {
        showToast('suffix 用的是'
          + (res.suffixFrom === 'task' ? '「最近跑过的任务」' : '「配置缺省」')
          + ' ' + res.suffix + '，若与实际不符请核对');
      }
      return true;
    } catch (e) {
      hideMask(); busy.value = false;
      showErr('打开失败：' + (e instanceof Error ? e.message : String(e)));
      return false;
    }
  }

  /* ---------------- 文件打开（HTML openFiles/openOne） ---------------- */
  /** 选文件 / 拖入：TIF 走解码管线，.jpg/.jpeg 走显示就绪图片管线（§4.6）。
   *  `side` 只在分屏下由拖放落点给出（落在左半是 'A'、右半是 'B'）；选文件对话框、
   *  场景快捷入口、以及任何别的调用都不给 → 落活动侧。 */
  function addFiles(fileList: FileList | File[] | File, side?: 'A' | 'B') {
    const files = Array.isArray(fileList)
      ? fileList
      : Array.from(fileList instanceof File ? [fileList] : fileList);
    const { tifs, imgs } = classifyImages(files);
    if (!tifs.length && !imgs.length) {
      showErr('没有识别到影像文件（支持 .tif/.tiff/.jpg/.jpeg）');
      return;
    }
    const dupOf = (f: File): ViewerRec | undefined =>
      recs.value.find((r) => r.file.name === f.name && r.file.size === f.size);
    tifs.forEach((f) => {
      const dup = dupOf(f);
      // 重复的仍然走一遍 activate：分屏下「把同一张图拖到另一侧」是有意义的操作，
      // 归位规则（含两格互换）在 placeRec 里。
      if (dup) void activate(dup.id, side); else openOne(f, side);
    });
    imgs.forEach((f) => {
      const dup = dupOf(f);
      if (dup) void activate(dup.id, side); else void openLocalImage(f, side);
    });
  }

  function openOne(file: File, side?: 'A' | 'B') {
    const rec: ViewerRec = {
      id: nextId++, file: markRaw(file),
      probe: null, name: file.name, size: file.size,
      W: 0, H: 0, thumb: null, src: null, srcw: 0, srch: 0, nbands: 0, invert: false,
      stats: null, route: null, sceneId: null, lqPath: null,
      layout: '', status: '等待…', statusCls: '',
      paintedMode: null, maskRois: null, token: 0,
    };
    recs.value.push(rec);
    // 即探：只读头部标签，秒显压缩/布局，不等待解码。
    // 落笔前判 route：帧头解析虽快，但若这期间 rec 已被升级成盘阵场景
    // （route='jpg'），标签摘要就会把「盘阵 JPG」那行文案盖掉。
    tiffTags(new FileSource(file, rec.name)).then((tags) => {
      if (rec.route === null) rec.layout = layoutInfo(tags);
    }).catch(() => { /* 头部解析失败不阻塞 */ });
    void activate(rec.id, side);
  }

  /* ---------------- 激活 / 解码（HTML activate + decodeGeoTiff/decodeUtif） ---------------- */
  /** 激活一条 rec。`side` 只在分屏下有意义（拖放落点算出来的那一半）；不给就是
      「落活动侧」（文件列表点击 / 盘阵栏 / 待修复清单「打开」都不给）。 */
  async function activate(id: number, side?: 'A' | 'B') {
    const rec = recs.value.find((r) => r.id === id);
    if (!rec) return;
    const already = activeId.value === id;
    const moved = placeRec(id, side);
    noteCompareEntry(id);                // 对比模式下露面的这张要进点选清单（幂等）
    if (already && rec.thumb) {
      // 已经是活动侧这张：只重画。**只有它真的换了格子才重新适配** ——
      // 同一张图留在原格时若也 fit，一次多余的点选就把用户的缩放抹了。
      // 换了格子（两格互换）也走 `fitAfterImageChange`：新格子的视口与图都换了，
      // 对比模式下按相对视野搬过去，与「换图」同一套规矩。
      if (moved) fitAfterImageChange(activeSide.value, rec);
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
    if (rec.thumb) {
      repaintOnActivate(rec);
      // 分屏（对比）下换图保住用户的缩放与位置，其余情况看全幅；见 fitAfterImageChange。
      fitAfterImageChange(activeSide.value, rec);
      refreshCloudStats();           // 切回已解码文件 → 云量随新图刷新
      void maybePrefetchCompare();   // 对比模式里换了另一张 → 可能换了场景
      renderTick.value++;
      return;
    }
    // 未解码：先试着反推盘阵目录。**命中就直接用服务端烘焙 JPG，不做本地解码** ——
    // 真机上一次全图解码要几十秒、几百 MB，而盘阵那份 1/2 预览已经在手边上；
    // 顺带也避开了「本地图先画出来、几百毫秒后又被 JPG 换掉」的闪烁。
    // 盘阵场景（route='jpg'）不走这条路：它们的目录是 resolve 按绝对路径给出的
    // 权威值，而反推只能拿裸文件名去猜，猜出来的可能是另一个目录（同名不同景），
    // 拿它提交 SR 就是错的；tryLinkScenes 里也会再挡一次。
    // 未命中（盘阵上没有 / 指纹不符 / 后端不可达）才回落本地解码，能力不变。
    if (!rec.route && await tryLinkScenes(rec)) return;
    if (!recs.value.includes(rec)) return;      // 关联期间被移除了
    await decodeRec(rec);
    // 解完才有 sceneId（本地解码的那条路不会命中盘阵，这里多半直接返回）。
    void maybePrefetchCompare();
  }

  async function decodeRec(rec: ViewerRec) {
    const my = ++rec.token;                     // 领号：本地解码要接管像素了
    busy.value = true;
    let probe: ProbeInfo | null = null;
    try { probe = await probeImage(rec.file); } catch (e) { probe = null; }
    rec.probe = probe;
    if (!probe) {
      if (rec.token !== my) return;             // 已过期：别覆盖新写者的状态
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
      applyDecoded(rec, d, my);
      // 遮罩还盖着（finally 里才收）：把首次缩放那笔一次性重采样在这里付掉
      await warmZoom(rec);
    } catch (e) {
      if (rec.token === my) failRec(rec, e);
    } finally {
      busy.value = false;
      hideMask();
    }
  }

  function applyDecoded(rec: ViewerRec, d: DecodedRec, token: number) {
    // 解码是几十秒级的异步过程：期间这张图可能已经被升级成盘阵 JPG（或又被解码
    // 一次），那份结果比这份新。令牌对不上就整份丢掉，一个字都不写。
    if (rec.token !== token) return;
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
    afterPixels(rec);                // 分屏下这张可能在非活动侧解码完成
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
  async function openLocalImage(file: File, side?: 'A' | 'B') {
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
        // 状态只留短词：文件名是卡片标题那一行，尺寸在上一行的图属性里 —— 三行里
        // 三处同名同数，2026-09-18 收掉。
        status: '已读取',
        statusCls: 'ok', paintedMode: null, maskRois: null, token: 0,
      };
      recs.value.push(rec);
      // 入列之后一律用 recs 里那份（响应式代理）：上面这个局部变量是**原始对象**，
      // 直接改它的字段不会触发渲染 —— 关联成功后工具栏那两颗按钮会一直是灰的
      // （store 里的值是对的，页面上的 DOM 不更新）。其余入口都是 recs.find(...)
      // 拿的代理，只有这里差点漏掉。
      const live = recs.value.find((r) => r.id === rec.id) ?? rec;
      const ready = live.status;
      // 先上屏再预热：预热要在「这张 rec 真的摆在格子里」时才做（否则热的是别张图
      // 的画布）。activate 对已有像素的 rec 是同步走完的，等它不吃亏。
      await activate(live.id, side);
      await warmZoom(live);
      hideMask(); busy.value = false;
      // 盘阵场景目录里那份 jpg 就是这条 rec 自己（拖进来的是生产全名，后端能反推
      // 出目录），所以顺带试一次关联：命中就按场景身份升级，拿到 lqPath 才能提交
      // SR / 保存掩码。**不 await** —— 关联要发请求，本地图该显示就先显示。
      // 没连上（盘阵上没有这个目录 / 后端不可达）就什么都不改，本地图能力不变。
      void tryLinkScenes(live).then((linked) => {
        // 关联失败时 tryLinkScenes 已经把状态改成了「正在关联盘阵目录…」，而这条
        // 路后面没有解码会去覆盖它 —— 不还原的话状态栏就永远卡在那句上。
        if (linked || !recs.value.includes(live) || live.route !== 'img') return;
        live.status = ready; live.statusCls = 'ok';
      });
    } catch (e) {
      hideMask(); busy.value = false;
      showErr('读取图片失败：' + (e instanceof Error ? e.message : String(e)));
    }
  }

  /* ---------------- 盘阵场景（阶段4：读服务器烘焙 JPG，route='jpg'） ----------------
     JPG 即显示产物（各边 ÷2…÷32、当前档位见 viewer.previewDiv 的稀疏采样 + 直方图均衡已在服务器烤好）：不再读原始
     TIF 字节、不做二次拉伸、不本地导出 JPG（服务器 JPG 即交付物）。掩码仍照旧 ——
     缩略图坐标按元数据 W/H 换算回全分辨率（thumbToOrig scale 来自 rec.W/H 而非
     probe，所以 JPG 尺寸变了也不影响掩码落点）。 */
  /** 按场景身份找已在列表里的 rec：先认 `sceneId`（权威），退回名字。
   *
   *  升级过的 rec 名字仍是用户拖进来的那个文件名（`SC.tif`），而库行给的名字是
   *  `SC` —— 只按名字找会漏，于是同一个场景会开出两条 rec、两份 maskRois。 */
  function findRecByMeta(meta: { sceneId?: string | null; name: string })
      : ViewerRec | undefined {
    return recs.value.find((r) =>
      (!!meta.sceneId && r.sceneId === meta.sceneId) || r.name === meta.name);
  }

  /** 把「服务端烘焙 JPG + 元数据」装进一条**已有**的 rec（新建 / 就地升级共用）。
   *
   *  收尾动作一个都不能少：云量卡要 `refreshCloudStats`、拉伸起手值要按新 route
   *  走 `startStretch`、画面要重绘 —— 漏一个就是「云量卡不刷 / 拉伸下拉显示错 /
   *  画面不重画」。返回是否真的装上了（令牌对不上 = 期间有更新的写者接手，没装）。
   *
   *  `rec.file`/`name`/`size` 归调用方管：拖拽升级那条路要**保持**用户拖进来的
   *  那个文件名与字节数（FileList 展示与 addFiles 查重都靠它），只换像素与身份。 */
  async function applySceneJpgToRec(rec: ViewerRec, meta: SceneOpenMeta,
                                    blob: Blob, layout?: string): Promise<boolean> {
    const my = ++rec.token;                     // 领号：JPG 要接管像素了
    const cv = await decodeJpgToCanvas(blob);
    if (rec.token !== my || !recs.value.includes(rec)) return false;
    const tw = cv.width, th = cv.height;
    const ctx = cv.getContext('2d');
    if (!ctx) throw new Error('取不到 2d 上下文');
    const img = ctx.getImageData(0, 0, tw, th);
    const d = sceneDecodePixels(img.data, tw, th);   // 固定 0..255 → linear 恒等
    rec.thumb = markRaw(cv as unknown as KitCanvas);
    rec.W = meta.W; rec.H = meta.H;
    rec.src = markRaw(d.src); rec.srcw = d.tw; rec.srch = d.th;
    rec.nbands = d.nbands;
    rec.invert = false;
    rec.stats = d.stats ? markRaw(d.stats) : null;
    rec.route = 'jpg';
    rec.sceneId = meta.sceneId ?? null;
    rec.lqPath = meta.lqPath ?? null;
    rec.sceneDir = meta.sceneDir ?? null;
    // 这一景是「用户当前打开着的」——记下来，供下一次拖没有场景身份的 jpg 时当
    // `anchor` 用（见 lastSceneDir）。中途产物那条路 lqPath 是空的，用 sceneDir。
    noteSceneDir(rec);
    // 环节（本体 / 产物 / NOSR）：两处入口（拖拽升级、快捷芯片）都在 meta 里给，
    // 缺省按本体 —— 只有盘阵场景才有这一项，本地图片 stageKind 保持 undefined。
    // 标签在这一个漏斗里算出来，卡片直接渲染，不必各自再判一遍。
    rec.stageKind = meta.stageKind ?? 'input';
    rec.stageLabel = stageLabel(rec.stageKind, meta.stageSuffix, rec.name);
    // 默认是服务端烘焙那份的口径；拖本地 jpg 升级进来的那条路自报来源
    // （它的像素是用户拖进来的原图，说「服务端已烘焙」就是假话）。
    // 尺度报当前档位 —— 取图的两处调用点都用 `previewDiv.value` 烤，所以
    // 「刚拿到手的这张是按哪一档烤的」就是它（用户若在取图途中又拖了滑块，
    // 文案最多早一拍，下次打开即对齐）。
    rec.layout = layout
      ?? `盘阵 JPG（${previewDivLabel(previewDiv.value)} 尺度 + 直方图均衡，服务端已烘焙）`;
    // 同上：名字与尺寸卡片上已有（标题行、图属性行、布局行），状态只表态
    rec.status = '场景就绪';
    rec.statusCls = 'ok';
    rec.linkNote = undefined;                   // 关联成功了，旧的失败原因留着是误导
    // 手工场景由 resolve 带着权威掩码路径进来（场景库页那条入口也走这里）；
    // 库行没有这个字段 → null，写掩码时再拿后端回的值。
    rec.serverMaskPath = meta.serverMaskPath ?? null;
    // 像素刚换过 → 一律重画（沿用已选模式，不重置用户的选择）
    paintStretch(rec, rec.paintedMode ?? startStretch(rec.route, stretchMode.value));
    afterPixels(rec);
    return true;
  }

  /** `side` 只在分屏下由调用方（场景快捷入口按活动侧 / 拖放落点）给出。 */
  async function openSceneJpg(meta: SceneOpenMeta, blob: Blob, side?: 'A' | 'B') {
    const dup = findRecByMeta(meta);
    if (dup) {
      // 已经在列表里的场景：只需重新上屏。**仍要过一遍预热** —— 重复打开往往正是
      // 换过预览档位之后（画布换了），那份旧画布的缩放缓存不作数。
      await activate(dup.id, side);
      await warmZoom(dup);
      return;
    }
    busy.value = true;
    showMask('正在加载盘阵场景…', meta.name + '（服务器烘焙 JPG，元数据 ' + meta.W + '×' + meta.H + '）', false);
    const rec: ViewerRec = {
      id: nextId++,
      file: markRaw(new File([blob], meta.name + '.jpg', { type: 'image/jpeg' })),
      probe: null, name: meta.name, size: blob.size,
      W: meta.W, H: meta.H,
      thumb: null, src: null, srcw: 0, srch: 0, nbands: 0, invert: false,
      stats: null, route: null, sceneId: null, lqPath: null,
      layout: '', status: '正在加载盘阵场景…', statusCls: '',
      paintedMode: null, maskRois: null, token: 0,
    };
    recs.value.push(rec);
    try {
      await applySceneJpgToRec(rec, meta, blob);
      await activate(rec.id, side);
      await warmZoom(rec);                 // 遮罩仍盖着：把首次缩放的代价在这里付掉
      hideMask(); busy.value = false;
      showToast('已打开盘阵场景「' + meta.name + '」（掩码按元数据 ' + meta.W + '×' + meta.H + ' 换算）');
    } catch (e) {
      // 解不开就整条撤掉：留一条空壳 rec 在列表里，点它只会再失败一次。
      const i = recs.value.indexOf(rec);
      if (i >= 0) recs.value.splice(i, 1);
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
    // 分屏的两格与点选清单都可能还指着它
    let paneCleared = false;
    if (paneA.value === id) { paneA.value = null; paneCleared = true; viewFor.A = null; }
    if (paneB.value === id) { paneB.value = null; paneCleared = true; viewFor.B = null; }
    compareList.value = pruneCompareList(compareList.value, recs.value.map((r) => r.id));
    if (activeId.value === id) {
      activeId.value = null;
      clearRoiSel();
      const next = recs.value[recs.value.length - 1];
      // 走 activate → placeRec：活动侧那张被关掉后，格子与 activeId 的不变量自动恢复
      if (next) void activate(next.id);
      else {
        activeSide.value = 'A';          // 两格都空了 → 活动侧回左，view 写回 viewA
        view.value = { scale: 1, ox: 0, oy: 0 };
        viewFor.A = null; viewFor.B = null;   // 没图了 = 没适配过
        marker.value = null;
        clearCloud();                    // 无图可显：云卡回空态，释放红叠画布
        renderTick.value++;
      }
    } else if (paneCleared) {
      // 关掉的是**非活动侧**那张：那一格当场空出来，得重画（活动侧什么都没变）
      renderTick.value++;
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
      而场景那条链路用户要的是本图独立。

      **对比模式下改一次刷两侧**：并排看的两张图若用着不同拉伸，看到的差异里混着
      拉伸差异，对比结论就是错的。关闭模式下仍是「每张图各自记」（e2e 盯着这条）。 */
  function setStretch(mode: StretchMode) {
    const rec = activeRec.value;
    if (!rec || rec.route !== 'jpg') stretchMode.value = mode;
    if (split.value) {
      let painted = false;
      for (const side of ['A', 'B'] as const) {
        const r = recForSide(side);
        if (r && r.thumb && r.src) { paintStretch(r, mode); painted = true; }
      }
      if (!painted) return;
    } else if (rec && rec.thumb && rec.src) {
      paintStretch(rec, mode);
    } else {
      return;
    }
    renderTick.value++;
    refreshRoiStats();              // 显示层像素变了 → 选中 ROI 统计随层刷新
    refreshCloudStats();            // …云量数字/红叠同理随显示层刷新
  }

  /** 切换预览烘焙档位（工具栏拖动条）。

  点选即生效：只写进 store + localStorage，**不去动已经打开的图** —— 当前这张的
  像素已经在手上了，重烤它既慢又不是用户此刻的诉求。档位在下次取图时生效
  （`fetchSceneJpg` / `fetchDropSceneJpg` 自己读它）。 */
  function setPreviewDiv(div: number) {
    if (!(SCENE_PREVIEW_DIVS as readonly number[]).includes(div)) return;
    previewDiv.value = div;
    savePreviewDiv(div);
  }

  function setCanvasSize(w: number, h: number) {
    canvasSize.value = { w, h };
    if (split.value) {
      // 半幅宽度变了 → 比例先收进新界限，再两格各自适配
      splitRatio.value = clampSplitRatio(splitRatio.value, w);
      fitBoth();
      renderTick.value++;
      return;
    }
    const rec = activeRec.value;
    if (rec && rec.thumb) { fit(); renderTick.value++; }
  }

  /** 只适配**活动格**（看全幅）。单屏时活动格就是整块画布 → 与今天完全等价。
      **写 `viewFor`**：这就是「这格当前视图是按多大的一张图算的」这笔账。 */
  function fit() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return;
    const rect = activePaneRect();
    view.value = fitView(rec.thumb.width, rec.thumb.height, rect.w, rect.h);
    viewFor[activeSide.value] = { w: rec.thumb.width, h: rec.thumb.height };
  }

  /* ---------------- 大图缩放预热（2026-09-21） ----------------

     现象（真机实测：RTX 3060 + 16012×15422 显示画布）：一张图**头两次**滚轮手势里
     各有一两帧 ~600ms，而那一帧的 drawImage/clearRect/getImageData 耗时全都≈0；
     此后永久流畅（含空闲 1.5s 之后）。所以那不是主线程的活儿，是合成器/光栅线程
     头一回按某个缩略比给这张巨图做重采样的代价。三条实测结论定它的形状：
       · 与手势次数无关：第二次手势紧接第一次，照样 ~600ms（不是「每次手势都付」）；
       · 不随空闲失效：停 1.5s 再动，最大帧 16.8ms、零掉帧（不是「空闲被驱逐」）；
       · **按尺度分档**：只有走过某些缩略比的帧才贵 —— 于是「热一次就永久好」。

     对策：把这份代价挪到装载时、解码遮罩还盖着的时候付掉 —— 用**真视图画布**按
     几档缩略比各画一帧。为什么不能用离屏小画布顶替：这份代价记在（源画布 × 目标
     画布 × 缩略比）这一组上，离屏画布热出来的缓存对真画布不作数。

     档位取 [1/4, 1/2, 1, 2, 4]：覆盖实际会用的「缩到 1/4 … 放到 4 倍」这段重采样
     区制（真机手势范围 0.26–6.93，fit 约 0.06）。确切的分档边界未知，多热一档只
     多花一次全图重采样的装载时间、不影响正确性，所以宁可多热。 */
  /** 预热档位（缩略比）。 */
  const WARM_SCALES = [0.25, 0.5, 1, 2, 4] as const;
  /** 源画布面积低于这个数就不预热：本地 TIF 那条路 buildThumb 上限 2048，
      两万像素级的图首次缩放本来就不掉帧，热它纯属白等。 */
  const WARM_MIN_PX = 2048 * 2048;

  /** 让出**两帧**。drawImage 只是「提交」，重采样在光栅线程上、晚一帧才发生 ——
      不把这一帧让出去，几档会挤进同一帧、只热了最后一档（等于没热）。
      兜底 400ms：后台页/无合成帧时 rAF 不跑，绝不能把装载流程挂死。 */
  function twoFrames(): Promise<void> {
    return new Promise((res) => {
      if (typeof requestAnimationFrame !== 'function') { setTimeout(res, 0); return; }
      let done = false;
      const fin = () => { if (!done) { done = true; res(); } };
      const t = setTimeout(fin, 400);
      requestAnimationFrame(() => requestAnimationFrame(() => { clearTimeout(t); fin(); }));
    });
  }

  /** 把 rec 的显示画布按 WARM_SCALES 各重采样一帧，付掉首次缩放的那笔一次性开销。
   *
   *  只热**画面上真的摆着这张 rec 的格子**：单屏 = 那一格（铺满画布），分屏 = 它落
   *  的那半边。两格的视口矩形不同 → 分屏下另一格要等它自己那轮预热，不在这里重复付
   *  （多热一格 = 多花一倍装载时间，而实测里分屏只是「稍稍严重一些」）。
   *
   *  调用时机一律是「rec 已经上屏、遮罩还没收」——热完把 view 逐格还原再 `renderTick++`，
   *  用户看到的仍是适配好的那一帧，中间那几帧全在遮罩底下。遮罩本来不在（例如从
   *  位置文件升级盘阵场景那条路）就自己盖一个、热完再收，不改变调用方的编排。 */
  async function warmZoom(rec: ViewerRec | null): Promise<void> {
    if (!rec) return;
    // 入列之后一律用 recs 里那份**代理**：调用方可能递来入列前的原始对象
    // （openSceneJpg 那条正是如此），拿它去比 `p.rec === rec` 永远不等，
    // 预热会被静默跳过 —— 而「静默跳过」在这里等于「功能没上」。
    const live = recs.value.find((r) => r.id === rec.id) ?? rec;
    const thumb = live.thumb as unknown as HTMLCanvasElement | null;
    if (!thumb) return;
    if (thumb.width * thumb.height < WARM_MIN_PX) return;
    if (live.warmed === live.thumb) return;            // 这份像素热过了
    if (typeof document !== 'undefined' && document.hidden) return;   // 后台页：等激活时再说
    const targets = panes.value.filter((p) => p.rec === live);
    if (!targets.length) return;                       // 没在画面上：热了也白热
    // 已经在盖遮罩的（解码 / 取图那几条路）只把文案换成实话；没盖的（从位置文件
    // 升级盘阵场景那条路）自己盖一个、热完收回 —— 调用方不必重复编排这件事。
    const mine = overlay.value.visible;
    showMask('正在预热缩放…', live.name, false);
    const keep = targets.map((p) => ({ side: p.side, view: p.view, marked: viewFor[p.side] }));
    try {
      for (const s of WARM_SCALES) {
        for (const p of targets) {
          // 以该格中心为锚：与用户滚轮缩放的落点无关，只是要让整张图都进重采样路径
          setSideView(p.side, {
            scale: s,
            ox: p.rect.w / 2 - (thumb.width * s) / 2,
            oy: p.rect.h / 2 - (thumb.height * s) / 2,
          });
        }
        renderTick.value++;
        await nextTick();                              // Vue 刷新 → watcher → render() 提交 drawImage
        await twoFrames();                             // …再等它真的光栅化完
      }
    } finally {
      for (const k of keep) { setSideView(k.side, k.view); viewFor[k.side] = k.marked; }
      renderTick.value++;
      live.warmed = live.thumb;
      if (!mine) hideMask();
    }
  }

  function onWheel(mx: number, my: number, factor: number) {
    if (split.value) {
      // 指针落在哪一格，就以那一格里的归一化位置为锚点，同一个归一化位置施加到另一格。
      // 两格等宽且都 fit 时这条规则就是逐像素锁定。
      if (!recForSide('A') && !recForSide('B')) return;
      const r = splitRects(splitRatio.value, canvasSize.value.w, canvasSize.value.h);
      const onA = paneAtX(mx, r.a.w) === 'A';
      const pPane = onA ? r.a : r.b;      // 指针所在格
      const oPane = onA ? r.b : r.a;      // 另一格
      const out = wheelZoomBoth(viewA.value, viewB.value, pPane, oPane,
        onA ? 'A' : 'B', mx, my, factor);
      viewA.value = out.a;
      viewB.value = out.b;
      renderTick.value++;
      return;
    }
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return;
    // **单屏刻意保留这条字面表达式**：改走 normAnchor/anchorAt 会多一次除法与乘法，
    // IEEE double 下不保证往返（100/1314*1314 = 99.99999999999999），
    // 而 test-vue-viewer.js 的 D/E 段在采样画布中心像素。
    view.value = wheelZoom(view.value, mx, my, factor);
    renderTick.value++;
  }

  function onPan(dx: number, dy: number) {
    if (split.value) {
      const out = panBoth(viewA.value, viewB.value, dx, dy);
      viewA.value = out.a;
      viewB.value = out.b;
      renderTick.value++;
      return;
    }
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
    // 居中到**活动格**的中心：分屏下把点定位到右半屏的中心，而不是整块画布的中心。
    const rect = activePaneRect();
    view.value = locateView(view.value.scale, tx, ty, rect.w, rect.h);
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
  /** 当前缩略图像素尺度下的可见视野矩形（thumb/view/canvas 就绪时）。
   *
   *  **分屏下必须用活动格而不是整块画布**：`visibleThumbRect` 假定「一个铺满画布的
   *  视口」，直接用整幅宽高会把分隔线另一边也当成可见区，云量数字就偏了。
   *  这是「单视口」假设在整个 store 里仅有的两个泄漏点之一（另一个见 computeCloudView）。 */
  function currentViewRect(): Rect | null {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) return null;
    const rect = activePaneRect();
    return visibleThumbRect(
      view.value, rect.w, rect.h,
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

  /** 掩码能画在谁身上：这张 rec 得有显示像素，且是**本体影像**。
   *
   *  中间产物（SR 放大结果 / NOSR）不能画：掩码是建在**本体影像**网格上的，在产物上
   *  画的坐标写到本体掩码文件上整片都是错的（工具栏那颗按钮同期置灰）。
   *  对比模式下这是**唯一**的判据 —— 见 enterDraw 的注释。 */
  function canDrawOn(rec: ViewerRec | null): boolean {
    return Boolean(rec && rec.thumb && !isIntermediateStage(rec.stageKind));
  }

  /** 进入绘制模式。**对比模式下也可以画（2026-09-22 改）**。
   *
   *  原来这里挡着「对比模式只读」，理由是「分屏里画掩码会画到哪一格、写进哪一张 rec
   *  都不明确」。那条理由已经不成立了：活动侧（`activeId`/`activeSide`）本来就是
   *  「掩码/ROI 统计/云量/任务状态跟随的那一张」，分屏里点哪半哪半就是活动侧 ——
   *  掩码画到活动侧那张 rec 上是**唯一**的结果，与其它几块数据同一条规则。
   *  剩下真正含糊的那件事（在本体影像上画、却在产物上落笔）由 canDrawOn 挡住：
   *  活动侧是产物时这里照旧拒绝，绘制期间也不让活动侧切到产物（setActiveSide）。
   *
   *  画布的坐标也要跟着改：分屏下每格的 ViewState 是**该格自己的局部坐标**，
   *  指针要先减去活动格左上角（TifCanvas.mousePos）。
   */
  function enterDraw() {
    const rec = activeRec.value;
    if (!rec || !rec.thumb) { showErr('请先打开一张图'); return; }
    if (!canDrawOn(rec)) {
      showErr(stageRefusal(rec.stageLabel ?? '产物')); return;
    }
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
    // 中间产物不提交：SR 跑的是本体（RC 场景的 PAN.tif），从产物视图提交等于让
    // 用户以为「我在修这张产物」。与绘制掩码同一句话，先于 lqPath 那道判。
    if (rec && isIntermediateStage(rec.stageKind)) {
      showErr(stageRefusal(rec.stageLabel ?? '产物')); return;
    }
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

  /** 关联成功后**后台静默**把 `<这份影像的 stem>_preview.jpg` 烤进场景目录。
   *
   *  用户口径（2026-09-21）：「后台静默烤」—— 不阻塞、不弹遮罩、不报进度，用户
   *  继续看他拖进来的原图（那张更清晰，不该被服务端缩图顶掉）。这条请求只为了
   *  **在盘阵上留下一个中间产物预览**，像素谁都不用。
   *
   *  失败也一声不吭：它不影响这次关联（rec 的像素、lqPath、掩码路径都已定下），
   *  报出来只会让用户以为关联有问题。真要暴露给用户的失败（盘阵不可写）会在
   *  用户主动「保存掩码到盘阵」时以他自己的动作报出来。
   *
   *  `rec.sceneId` 就是这条路径的权威文件（stage 自己的栅格），服务端据此落
   *  `<stem>_preview.jpg` —— 产物会落成 `<产物名>_preview.jpg`，正是这条路要的。 */
  function bakeDropPreview(rec: ViewerRec): void {
    if (!rec.sceneId) return;
    void fetchDropSceneJpg(loadSrConfig(), rec.sceneId, previewDiv.value)
      .catch(() => { /* 静默：见上 */ });
  }

  /** 本地 TIF 打开后**试着**关联盘阵目录。返回是否**命中并已升级成盘阵 JPG** ——
   *  命中时调用方（`activate`）直接返回，不要再做本地解码。
   *
   *  浏览器拿不到本地文件的绝对路径（`File` 只有 name/size/type），所以只能把
   *  **裸文件名 + 字节数**交给后端，由它按生产命名规则 + SR_SCENE_PATH_TEMPLATE
   *  反推候选目录 —— 规则唯一真源在 backend/pathguard.py，前端不自拼路径。
   *
   *  两个指纹都得给：同一个场景目录里可能躺着不止一张图（RC 场景的输入影像是
   *  `PAN.tif`），光凭文件名反推有可能认到一张**不是用户拖进来**的图，那之后画的
   *  掩码坐标会整片落在别的图上。判定在服务端做（`_fingerprint_mismatch`），前端
   *  只负责把 name + size 原样递过去 —— 拖 jpg 时后端比的是「jpg 名 == 场景目录
   *  名」，不跟输入的 TIF 比（详见那里的注释）。
   *
   *  三种「没命中」的处理各不相同：
   *  * 服务端明确 4xx（盘阵上没这个目录 / 指纹不符）：写 `rec.linkNote` 原样留着
   *    给用户看，**不留 linkTried 之外的痕迹**，本地图照常能看。这是「这张图不在
   *    盘阵上」的正常答案，不是错误，所以不弹 showErr。
   *  * 网络失败 / 超时（20s）：**清掉 linkTried**，下次激活还能再试 —— 那不是服务端
   *    的答案，留着等于「问过了、没有」，用户永远拿不到第二次机会。超时要出提示：
   *    后端答不上来时用户看得见的只有「拖完什么都没发生」。
   *  * 命中了但临时 JPG 烤不出来：报错 + 回落本地解码，不写 lqPath。
   *
   *  **route='jpg' 一律不试**：那种 rec 的目录已经由 resolve 按绝对路径定过（就是
   *  权威值）。粘单个 .tif 且父目录不是场景目录时它的 lqPath 是 null —— 这时若按
   *  文件名去反推，可能命中**另一个**目录，用户点「提交 SR」就会拿着错的 lq_path
   *  去跑，而这正是「绝不静默提交」要防的事。父目录不是场景目录，就是不能提交。
   *
   *  请求里还带一串 `anchor` = 「用户当前打开的那几景」（见 `noteSceneDir`）。产物名
   *  里**没有场景身份**时（RC 场景的 `PAN_<suffix>.jpg`）后端只能靠它 —— 名字能反推
   *  时它一个字都不起作用，所以照旧先反推、失败才用到它。 */
  async function tryLinkScenes(rec?: ViewerRec | null): Promise<boolean> {
    const r = rec ?? activeRec.value;
    // 已是盘阵场景 / 走过 resolve / 已关联过 / 已试过 —— 都直接返回
    if (!r) return false;
    if (r.route === 'jpg') return true;             // 本来就是盘阵场景，无需本地解码
    if (r.lqPath || r.linkTried) return false;
    r.linkTried = true;
    r.status = '正在关联盘阵目录…';
    r.statusCls = '';
    let res: SceneResolveResult;
    // 当前打开的那几景（分屏两块格格子各自的那一景），最近的排在最前 —— 后端按这个
    // 顺序取第一个成立的。名字能自己反推时它完全用不上（后端连 stat 都不花）。
    const anchor = sceneAnchors([
      lastSceneDir, sceneDirOf(recForSide('A')), sceneDirOf(recForSide('B')),
    ]);
    try {
      res = await apiResolveScene(loadSrConfig(),
        { name: r.name, size_bytes: r.file.size, anchor },
        { signal: AbortSignal.timeout(20000) });
    } catch (e) {
      if (!recs.value.includes(r)) return false;
      const err = e as Error;
      if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
        r.linkTried = false;                        // 不是服务端的答案，下回还能试
        r.status = '等待解码…';
        // 超时以前是**一声不吭**的：用户拖完什么都不发生，也无从知道该不该重试。
        // resolve 里要读一次输入影像的头（盘阵冷缓存时慢），20 秒仍答不上来说明
        // 后端真出问题了，得说出来。
        showToast('关联盘阵目录超时（后端 20 秒没答）—— 可以再拖一次，或稍后重试');
        return false;
      }
      r.linkNote = err instanceof Error ? err.message : String(err);
      if (imageKindOf(r.file) === 'jpg') {
        // 拖 jpg 进来的人多半就是冲着这个场景目录来的，而失败原因往往是后端那
        // 一长串「哪个目录、缺什么」，toast 六秒既看不完也留不住 —— 改弹窗。
        // 后端那句已经点明了「这份 jpg 的名字认不出一景」，这里补上入口的形状。
        showModal('这张 JPG 没有关联到盘阵目录', r.linkNote,
          '它仍按本地图片打开了，只是没有盘阵目录、不能提交 SR。能拖进来关联的 jpg 是'
          + '这一景的场景显示件（<目录名>.jpg）、它的中间产物（SC 场景是 '
          + '<目录名>_<suffix>.jpg），以及平台自己烤的那份预览'
          + '（<栅格 stem>_preview.jpg）—— 都在场景目录里，且同级要有同名栅格；'
          + '改过名、另存过、或叫 PAN.jpg 这类都不认，平台不猜目录。'
          + 'RC 场景（目录里的输入影像叫 PAN.tif）的产物叫 PAN_<suffix>.jpg，上次的产物'
          + '叫 <产物名>_NOSR —— 这两个名字里没有成像时刻，先打开那一景再拖进来才'
          + '认得出（平台照当前场景目录点同级栅格）。把该场景目录粘进上方的「盘阵场景」'
          + '栏打开，再拖一次即可；也可以直接用同场景芯片切到产物。');
      } else {
        showToast('「' + r.name + '」没有关联到盘阵场景，按本地文件查看'
          + '（要提交 SR 请在「盘阵场景」栏粘贴该场景目录）');
      }
      return false;
    }
    if (!recs.value.includes(r)) return false;      // 期间已切图/关掉
    // 命中：把这张 rec 就地升级成盘阵场景。**不新建 rec** —— 用户拖进来的那个
    // 文件就是这张图，新建一条会多出第二份 maskRois。
    // 拖进来的本来就是 jpg（且是生产全名）→ 它就是这张图的原图，**默认不去服务端
    // 烤一份预览**：用户要看的就是自己拖的那张，拿服务端缩图顶掉反而降清，
    // 还白等一次解压采样。其余情况（裸 .tif 反推命中）仍走阶段4 那条老路。
    //
    // 默认之外的**例外**：盘阵上有同名栅格、且当前档位下服务端从栅格烤出来的比
    // 这张 jpg 更清晰 → 换成服务端那份（判据与场景库页同一条 rasterPreviewWins，
    // 尺寸全在 resolve 响应里，不多一次往返）。用**盘阵那张 jpg** 的尺寸判，
    // 不用用户拖进来这份的：指纹对 jpg 行只比名字，本地那份可能另存过，
    // 平台口径是「盘阵上的才是基准」。
    const localJpg = imageKindOf(r.file) === 'jpg';
    const rp = res.row.rasterPreview;
    const useServer = localJpg && !!rp && rasterPreviewWins(rp, previewDiv.value);
    try {
      let blob: Blob;
      if (localJpg && !useServer) {
        blob = r.file;                     // File 是 Blob 子类，直接喂解码
      } else {
        // 未走服务端时那句 layout 自报来源；走服务端时传 undefined，让
        // openSceneJpg 用默认的「服务端已烘焙」——那才是实话。
        blob = await fetchDropSceneJpg(loadSrConfig(), res.row.id, previewDiv.value,
                                       (text) => {
          if (r === activeRec.value) showMask('正在关联盘阵场景…', text, false);
        });
      }
      hideMask();
      if (!recs.value.includes(r)) return false;
      const ok = await applySceneJpgToRec(r, {
        name: res.row.name, W: res.row.W as number, H: res.row.H as number,
        sceneId: res.row.id,
        lqPath: res.resolved.sr_capable ? res.resolved.dir : null,
        // 场景目录**照给**（与能否提交无关）：中间产物的 lqPath 是空的，但它仍
        // 属于这个目录 —— 卡片上那颗「同一景共用一个序号」的小标按它分组。
        sceneDir: res.resolved.dir,
        serverMaskPath: res.resolved.mask_path,
        stageKind: res.resolved.kind,
        stageSuffix: res.resolved.suffix,
      }, blob, localJpg && !useServer
        ? '盘阵场景 JPG（拖入的原图，本地解码）' : undefined);
      if (!ok) return false;
      // 就地升级：画布换了 → 旧的缩放缓存不作数。这条路上面已经把遮罩收了，
      // 由 warmZoom 自己再盖一个（见它的注释）。
      await warmZoom(r);
      // 后台静默烤一份 `<这份影像的 stem>_preview.jpg` 到场景目录（用户口径：
      // 不阻塞、不弹遮罩、不看结果）。只在**本地原图胜出**这条分支补 —— 服务端
      // 那份更清晰时上面那次 fetchDropSceneJpg 已经把同一份烤好了，再发一次是
      // 白烤一张图。它不碰 rec 的像素，所以不 await 也不会跟画面抢。
      if (localJpg && !useServer && r.sceneId) void bakeDropPreview(r);
      if (isIntermediateStage(r.stageKind)) {
        // 中间产物：如实说清它与本体的关系。此处**不能**说「可以提交 SR 了」——
        // 这一条的 lqPath 已被服务端置空（见 resolve 的 kind 分岔），提交按钮是
        // 灰的，说了就是空头支票。
        showToast('已关联盘阵场景 ' + res.resolved.dir + '（' + r.stageLabel
          + ' 是中间产物，仅用于对比，不作修复；修复请打开本体）');
      } else {
        showToast('已关联盘阵目录 ' + res.resolved.dir + '，可以提交 SR 了');
      }
      return true;
    } catch (e) {
      hideMask();
      if (!recs.value.includes(r)) return false;
      // 升级失败：字段一个都别留（半升级的 rec 既不像本地图也不像盘阵场景），
      // 回落本地解码，能力不变。
      r.statusCls = 'err';
      // 措辞对三条来源都成立：裸 .tif 那条是烤预览失败；拖 jpg 那条要么是这份
      // 本地文件解不动（本地解码也失败，所以不说「改用本地解码」），要么是它被判
      // 「服务端那份更清晰」后服务端烤失败了 —— 两种情况都是「装载像素失败」。
      showErr('关联到盘阵目录 ' + res.resolved.dir + ' 了，但装载像素失败：'
        + (e instanceof Error ? e.message : String(e)) + ' —— 仍按本地文件查看');
      return false;
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
      // 首次要服务端烘焙预览图（读一遍大图）——把遮罩文案换成这一句，
      // 否则几十秒里界面看起来像卡死了。
      const blob = await fetchSceneJpg(loadSrConfig(), res.row, previewDiv.value,
                                       (text) => {
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
        sceneDir: res.resolved.dir,
      }, blob);
      const rec = findRecByMeta({ sceneId: res.row.id, name: res.row.name });
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
    // 中间产物：**这一道必须排在 lqPath 那道前面**。产物也有 lqPath（任务区关联
    // 队列行要用），若先撞上 lqPath 空那条件就会说「这张图没有盘阵目录」—— 而它
    // 明明在场景目录里，用户按这句话去粘目录只会更糊涂。机制在 lib/stage.ts 顶部。
    if (isIntermediateStage(rec.stageKind)) {
      showErr(stageRefusal(rec.stageLabel ?? '产物')); return false;
    }
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
    // 兜底：绘制期间活动侧被换成了不可绘制的图（拖放落格、清单点选、盘阵栏都能换）。
    // 退出绘制并说明，而不是默默把 ROI 写到产物上 —— 那种错要等到掩码文件送进 SR
    // 才会暴露，且表现为「坐标整片偏了」，很难倒查回来。
    // （分屏里点另一半那条常见路径在 setActiveSide 就挡住了，这里管的是其余入口。）
    if (!canDrawOn(activeRec.value)) {
      const rec = activeRec.value;
      exitDraw();
      showErr(rec ? stageRefusal(rec.stageLabel ?? '产物') : '请先打开一张图');
      return true;
    }
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
  /** 模态提示：没有计时器，只能由用户关掉（或切换/离开页面时 hideModal）。 */
  function showModal(title: string, body: string, hint = '') {
    modal.value = { visible: true, title, body, hint };
  }
  function hideModal() {
    modal.value.visible = false;
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
    previewDiv,
    wandTol, merging, overlay, toast, error, modal,
    sidebarCollapsed, busy, srBusy,
    // 文件 / 解码
    addFiles, removeRec, activate, openSceneJpg, openLocalImage,
    // 拉伸 / 视图
    setStretch, setPreviewDiv, setCanvasSize, fit, onWheel, onPan, locatePixel,
    // 掩码
    enterDraw, exitDraw, setDrawTool, commitRect, closePolygon, undoRoi, clearRois,
    mergeRois, delClick, wandSelect, buildMaskJson, exportMaskJson, genMask,
    getRois, submitSr, bakeMaskToServer,
    // 手工盘阵场景：打开任意场景目录 / 本地图反推关联
    openScenePath, tryLinkScenes,
    // 图像对比（关闭 / 点选对比 / 分屏对比）
    compareMode, compareOn, split, setCompareMode,
    cmpStripOpen, setCmpStripOpen,
    panes, splitX, splitRatio, setSplitRatio, resetSplit,
    activeSide, setActiveSide, paneA, paneB, recForSide, rectForSide, activePaneRect,
    fitBoth, fitSide,
    compareList, cmpListRecs, clearCompareEntry,
    dragHint, setDragHint,
    // 侧栏卡片拖进画布：载荷类型 + 起手/收尾 + 同景序号（卡片小标）
    REC_MIME, startRecDrag, endRecDrag, sceneOrdinalOf,
    ctxRailOpen, ctxRailPrevOpen, setCtxRailOpen,
    activeSceneId, openSceneSibling,
    // 设置浮层（右上角）：对比模式后台预取开关 + 本地预览缓存
    settingsOpen, setSettingsOpen,
    cmpPrefetchOn, setCmpPrefetch, prefetchNote,
    previewCacheStats, clearPreviewCache, previewCacheRev,
    // 侧舱 ROI 选择 / 确定性统计
    selRoi, roiStats, roiSelIndex, selectRoi, clearRoiSel, refreshRoiStats,
    // 云量估算（整景/当前视野 + 疑似云区红叠）
    cloudScene, cloudView, cloudShow, cloudOverlay,
    refreshCloudStats, refreshCloudView, setCloudShow,
    // 画布事件
    onCanvasDownDraw, onCanvasMove, onCanvasUp, onDblClick, onKeyDown,
    // UI
    showMask, hideMask, showToast, showErr, showModal, hideModal,
    setSparseMin,
  };
});
