/**
 * stores/diff.ts — 智能差异定位的编排层
 * ------------------------------------------------------------------
 * 只做四件事：取出两张影像的预览灰度 → 采样到共同网格 → 调后端只读分析 →
 * 把结果（热力图 / 羽化掩码 / 候选区域）交给 Viewer 与侧栏。
 *
 * 口径见 `lib/diffLocate.ts` 顶部：
 * - 分析网格 = 两张图共同覆盖范围，长边 ≤ 2048；
 * - 灰度按两张图**合起来的**范围归一化，后端统一做（前端只把原始灰度送过去）；
 * - 分析结果与人工掩码分开：本 store **从不**自动写 `rec.maskRois`，
 *   只有用户点「加入掩码」才调用 `viewer.addMaskPolygon`。
 *
 * 显示方式 / 羽化只改本地的显示画布，不重新发包。
 */
import { computed, markRaw, ref, shallowRef, watch } from 'vue';
import { defineStore } from 'pinia';
import { useViewerStore } from './viewer';
import { loadSrConfig } from '../lib/scene.js';
import { apiDiffAnalyze } from '../lib/api.js';
import {
  ANALYSIS_MAX_EDGE, DEFAULT_ADVANCED, commonGrid, sampleGray, jointRange,
  quantizeToRgba, gridPointToThumb, gridRectToThumb, gridCoverageToThumb, gridRectToSource,
} from '../lib/diffLocate';
import type {
  DiffAdvanced, DiffDisplay, DiffFeather, DiffImageInput, DiffMethod, DiffRequest,
  DiffResult, DiffScope, DiffSensitivity,
} from '../lib/diffLocate';
import { visibleThumbRect } from '../lib/viewMath';
import type { ViewerRec } from './viewer';

/** 共同网格坐标里的矩形。 */
interface GridRect { x: number; y: number; w: number; h: number }

const VIEWER_MARKER_MAX = 8;
const MASK_COLOR = 'rgb(224,86,50)';
const MAX_CANVAS_EDGE = ANALYSIS_MAX_EDGE;

function fullRect(width: number, height: number): GridRect {
  return { x: 0, y: 0, w: width, h: height };
}

function clampRect(r: GridRect, width: number, height: number): GridRect {
  const x = Math.max(0, Math.min(width - 1, Math.round(r.x)));
  const y = Math.max(0, Math.min(height - 1, Math.round(r.y)));
  const w = Math.max(1, Math.min(width - x, Math.round(r.w)));
  const h = Math.max(1, Math.min(height - y, Math.round(r.h)));
  return { x, y, w, h };
}

/** `rec.src` 可能是 1 波段（稀疏条带 / 盘阵 JPG）或 3 波段（UTIF / 分块）。
 *  差异分析只吃单通道灰度，这里按显示层同样的 luma 权重压成 1 波段。
 *  3 波段缓冲按源数组身份缓存，同一张图重复分析不重算。 */
const lumaCache = new WeakMap<Float32Array, Float32Array>();

function grayBuffer(rec: ViewerRec): { src: Float32Array; w: number; h: number } | null {
  if (!rec.src || !rec.srcw || !rec.srch) return null;
  const nb = Math.max(1, rec.nbands || 1);
  if (nb === 1) return { src: rec.src, w: rec.srcw, h: rec.srch };
  let gray = lumaCache.get(rec.src);
  if (!gray) {
    const n = rec.srcw * rec.srch;
    gray = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const p = i * nb;
      gray[i] = 0.299 * rec.src[p] + 0.587 * rec.src[p + 1] + 0.114 * rec.src[p + 2];
    }
    lumaCache.set(rec.src, gray);
  }
  return { src: gray, w: rec.srcw, h: rec.srch };
}

function expandSourceRect(
  rect: [number, number, number, number], margin: number, W: number, H: number,
): { x: number; y: number; w: number; h: number } {
  const [x, y, w, h] = rect;
  const pad = Math.round(Math.max(w, h) * margin);
  const x0 = Math.max(0, Math.floor(x - pad));
  const y0 = Math.max(0, Math.floor(y - pad));
  const x1 = Math.min(W, Math.ceil(x + w + pad));
  const y1 = Math.min(H, Math.ceil(y + h + pad));
  return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
}

/** 画布生成：只在前端做，不参与后端分析。 */
function canvasFromPng(dataUrl: string): Promise<HTMLCanvasElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = Math.max(1, img.naturalWidth || img.width);
      c.height = Math.max(1, img.naturalHeight || img.height);
      c.getContext('2d')!.drawImage(img, 0, 0);
      resolve(c);
    };
    img.onerror = () => reject(new Error('差异结果图解码失败'));
    img.src = dataUrl;
  });
}

/** 连续差异图（8-bit 灰度）→ 半透明热力层：低差异透明，中琥珀，高橙红。 */
function colorizeHeat(src: HTMLCanvasElement): HTMLCanvasElement {
  const out = document.createElement('canvas');
  out.width = src.width;
  out.height = src.height;
  const octx = out.getContext('2d')!;
  const ictx = src.getContext('2d')!;
  const img = ictx.getImageData(0, 0, src.width, src.height);
  const d = img.data;
  const rgba = new Uint8ClampedArray(d.length);
  for (let i = 0; i < d.length; i += 4) {
    const v = d[i] / 255;
    if (v <= 0.04) continue;                 // 低差异完全透明
    const t = Math.max(0, Math.min(1, (v - 0.04) / 0.96));
    // 琥珀 (246,166,35) → 橙红 (226,74,45)
    const k = Math.pow(t, 0.85);
    rgba[i] = 246 + (226 - 246) * k;
    rgba[i + 1] = 166 + (74 - 166) * k;
    rgba[i + 2] = 35 + (45 - 35) * k;
    // 整体不透明度约 34%，低差异再压一档
    rgba[i + 3] = Math.round(255 * 0.34 * Math.min(1, 0.25 + v * 1.2));
  }
  octx.putImageData(
    new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, src.width, src.height), 0, 0);
  return out;
}

/** 二值掩码 → 橙红图层；羽化只作用于**显示 alpha**，源掩码本身不动。
 *
 *  后端返回的掩码 PNG 是**不透明**的黑白图，alpha 恒为 255 —— 直接把亮度当
 *  alpha 用才对。先转成「白色 + alpha=亮度」的中间图，再按需模糊，最后着色；
 *  否则整块矩形都会被填满，连掩码外的黑底也会变成橙色。 */
function colorizeMask(source: HTMLCanvasElement, feather: DiffFeather): HTMLCanvasElement {
  const w = source.width;
  const h = source.height;
  const lum = document.createElement('canvas');
  lum.width = w;
  lum.height = h;
  const lctx = lum.getContext('2d')!;
  const img = source.getContext('2d')!.getImageData(0, 0, w, h);
  const alpha = new Uint8ClampedArray(img.data.length);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = img.data[i];                    // 灰阶：白=掩码内
    alpha[i] = 255; alpha[i + 1] = 255; alpha[i + 2] = 255;
    alpha[i + 3] = v;
  }
  lctx.putImageData(new ImageData(alpha as Uint8ClampedArray<ArrayBuffer>, w, h), 0, 0);

  let alphaSource: HTMLCanvasElement = lum;
  if (feather !== 'off') {
    const longEdge = Math.max(w, h);
    const radius = feather === 'light'
      ? Math.max(1, Math.round(longEdge / 1024))
      : Math.max(2, Math.round(longEdge / 512));
    const temp = document.createElement('canvas');
    temp.width = w;
    temp.height = h;
    const tctx = temp.getContext('2d')!;
    try {
      tctx.filter = `blur(${radius}px)`;
      tctx.drawImage(lum, 0, 0);
      tctx.filter = 'none';
      alphaSource = temp;
    } catch {
      alphaSource = lum;                     // 不支持 filter 的浏览器保持精确边缘
    }
  }
  const out = document.createElement('canvas');
  out.width = w;
  out.height = h;
  const ctx = out.getContext('2d')!;
  ctx.drawImage(alphaSource, 0, 0);
  ctx.globalCompositeOperation = 'source-in';
  ctx.fillStyle = MASK_COLOR;
  ctx.fillRect(0, 0, w, h);
  return out;
}

function rgbaToPng(rgba: Uint8ClampedArray, w: number, h: number): string {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  c.getContext('2d')!.putImageData(
    new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, w, h), 0, 0);
  return c.toDataURL('image/png');
}

function cropGrid(
  g: { gray: Float32Array; valid: Uint8Array }, r: GridRect, gw: number, gh: number,
): { gray: Float32Array; valid: Uint8Array } {
  const w = Math.max(1, Math.min(gw - r.x, r.w));
  const h = Math.max(1, Math.min(gh - r.y, r.h));
  const gray = new Float32Array(w * h);
  const valid = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const src = (r.y + y) * gw + r.x;
    gray.set(g.gray.subarray(src, src + w), y * w);
    valid.set(g.valid.subarray(src, src + w), y * w);
  }
  return { gray, valid };
}

export interface DiffPairOption {
  id: number;
  name: string;
  stage: string;
  width: number;
  height: number;
}

export interface DiffLayer {
  heat: HTMLCanvasElement;
  mask: HTMLCanvasElement;
  /** 本图缩略图坐标里的覆盖矩形。 */
  coverage: [number, number, number, number];
}

interface Snapshot {
  result: DiffResult;
  heat: HTMLCanvasElement | null;
  mask: HTMLCanvasElement | null;
  maskSource: HTMLCanvasElement | null;
  paramsKey: string;
}

export const useDiffStore = defineStore('diff', () => {
  const viewer = useViewerStore();

  /* ---------------- 参数 ---------------- */
  const open = ref(false);
  const busy = ref(false);
  const error = ref('');
  /** 非错误提示（例如「参数已变更，过期结果已丢弃」）。 */
  const notice = ref('');
  /** 请求序号：只有最后一次请求允许落结果，预览与精算共用。 */
  let requestSeq = 0;
  const method = ref<DiffMethod>('absdiff');
  const sensitivity = ref<DiffSensitivity>('standard');
  const scope = ref<DiffScope>('common');
  const display = ref<DiffDisplay>('regions');
  const feather = ref<DiffFeather>('light');
  const minAreaPx = ref(64);
  const advanced = ref<DiffAdvanced>({ ...DEFAULT_ADVANCED });
  const aId = ref<number | null>(null);
  const bId = ref<number | null>(null);

  /* ---------------- 结果 ---------------- */
  const result = shallowRef<DiffResult | null>(null);
  const resultKey = ref('');
  const heatLayer = shallowRef<HTMLCanvasElement | null>(null);
  const maskLayer = shallowRef<HTMLCanvasElement | null>(null);
  const rawMask = shallowRef<HTMLCanvasElement | null>(null);
  const selectedId = ref<string | null>(null);
  const hoveredId = ref<string | null>(null);
  const lastWarnings = ref<string[]>([]);
  const baseSnapshot = shallowRef<Snapshot | null>(null);

  const regions = computed(() => result.value?.regions ?? []);
  const viewerRegions = computed(() => regions.value.slice(0, VIEWER_MARKER_MAX));
  const selectedRegion = computed(() => regions.value.find((r) => r.id === selectedId.value) ?? null);
  const showHeat = computed(() => display.value === 'heat' || display.value === 'overlay');
  const showMask = computed(() => display.value === 'overlay');
  const showMarkers = computed(() => display.value !== 'heat');
  const refined = computed(() => result.value?.analysisGrid.mode === 'source');
  const canRestore = computed(() => !!baseSnapshot.value);

  const aRec = computed<ViewerRec | null>(
    () => viewer.recs.find((r) => r.id === aId.value && !r.pending) ?? null,
  );
  const bRec = computed<ViewerRec | null>(
    () => viewer.recs.find((r) => r.id === bId.value && !r.pending) ?? null,
  );

  const pairOptions = computed<DiffPairOption[]>(() =>
    viewer.recs
      .filter((r) => !r.pending && !!r.thumb && !!r.src && r.W > 0 && r.H > 0)
      .map((r) => ({
        id: r.id,
        name: r.name,
        stage: r.stageLabel ?? '',
        width: r.W,
        height: r.H,
      })),
  );

  /** 影像组合是否满足「同宽高比 + 尺寸已知」。 */
  function compatible(a: number | null, b: number | null): boolean {
    if (a == null || b == null || a === b) return false;
    const ra = pairOptions.value.find((o) => o.id === a);
    const rb = pairOptions.value.find((o) => o.id === b);
    if (!ra || !rb) return false;
    return !commonGrid(ra.width, ra.height, rb.width, rb.height).refusal;
  }

  /* ---------------- 推荐配对 ---------------- */
  function hintRank(name: string): number {
    const n = name.toLowerCase();
    if (n.includes('pan') && !n.includes('_old')) return 0;
    if (n.includes('pan_old') || n.includes('pan-old')) return 1;
    if (n.includes('nosr')) return 2;
    if (n.includes('ori') || n.includes('original')) return 3;
    if (n.includes('_sr') || n.includes('sr_')) return 5;
    return 4;
  }

  function pairScore(a: DiffPairOption, b: DiffPairOption): number {
    const plan = commonGrid(a.width, a.height, b.width, b.height);
    if (plan.refusal) return -Infinity;
    let score = 0;
    const ra = viewer.recs.find((r) => r.id === a.id);
    const rb = viewer.recs.find((r) => r.id === b.id);
    if (ra?.sceneDir && ra.sceneDir === rb?.sceneDir) score += 8;
    if (ra?.lqPath && ra.lqPath === rb?.lqPath) score += 4;
    const ka = hintRank(a.name);
    const kb = hintRank(b.name);
    if ((ka === 0 && (kb === 1 || kb === 2 || kb === 3))
      || (kb === 0 && (ka === 1 || ka === 2 || ka === 3))) score += 6;
    if ((ka === 0 && kb !== 0) || (kb === 0 && ka !== 0)) score += 3;
    if (a.width === b.width && a.height === b.height) score += 2;
    return score;
  }

  function recommendedPair(): { a: number; b: number } | null {
    const list = pairOptions.value;
    if (list.length < 2) return null;
    const active = list.find((o) => o.id === viewer.activeRec?.id);
    if (active) {
      let best: DiffPairOption | null = null;
      let bestScore = -Infinity;
      for (const other of list) {
        if (other.id === active.id) continue;
        const s = pairScore(active, other);
        if (s > bestScore) { bestScore = s; best = other; }
      }
      if (best && Number.isFinite(bestScore)) {
        const activeFirst = hintRank(active.name) <= hintRank(best.name);
        return activeFirst ? { a: active.id, b: best.id } : { a: best.id, b: active.id };
      }
    }
    let best: { a: number; b: number } | null = null;
    let bestScore = -Infinity;
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const s = pairScore(list[i], list[j]);
        if (s > bestScore) { bestScore = s; best = { a: list[i].id, b: list[j].id }; }
      }
    }
    return Number.isFinite(bestScore) ? best : null;
  }

  /** 打开面板时补一次配对；用户已经选好的、仍然有效的组合不覆盖。 */
  function ensurePair(): void {
    const hasA = pairOptions.value.some((o) => o.id === aId.value);
    const hasB = pairOptions.value.some((o) => o.id === bId.value);
    if (hasA && hasB && compatible(aId.value, bId.value)) return;
    const rec = recommendedPair();
    if (rec) {
      aId.value = rec.a;
      bId.value = rec.b;
      return;
    }
    const first = pairOptions.value[0];
    aId.value = first?.id ?? null;
    bId.value = pairOptions.value.find((o) => o.id !== first?.id)?.id ?? null;
  }

  function setPair(a: number | null, b: number | null): void {
    aId.value = a;
    bId.value = b;
    error.value = '';
  }

  function openPanel(): void {
    ensurePair();
    open.value = true;
  }

  function closePanel(): void {
    open.value = false;
  }

  /* ---------------- 参数签名（用于「结果已过期」） ---------------- */
  /** 像素代次：记录是否存在、源修订、加载令牌与采样尺寸。
   *
   *  只带记录 ID 不够 —— 记录 ID 不变时仍可能切换预览档位、重载像素或更新源版本，
   *  旧采样上的候选不能继续覆盖当前影像。`token` 在换档/重载开始时就递增，
   *  因此旧结果会立刻失效，不必等新像素落地。 */
  function pixelKey(id: number | null): string {
    if (id == null) return 'none';
    const r = viewer.recs.find((x) => x.id === id);
    if (!r) return 'missing';
    if (r.pending || !r.src) return 'pending';
    return `${r.srcRev ?? ''}|${r.token ?? 0}|${r.srcw}x${r.srch}`;
  }

  function currentKey(): string {
    return JSON.stringify({
      a: aId.value, b: bId.value, method: method.value, sensitivity: sensitivity.value,
      scope: scope.value, minArea: minAreaPx.value, advanced: advanced.value,
      pa: pixelKey(aId.value), pb: pixelKey(bId.value),
    });
  }
  const stale = computed(() => !!result.value && resultKey.value !== currentKey());

  /* ---------------- 分析范围 ---------------- */
  function visibleGridRect(planW: number, planH: number): GridRect {
    const pane = viewer.panes.find((p) => p.active) ?? viewer.panes[0];
    const rec = pane?.rec;
    const a = aRec.value;
    const b = bRec.value;
    if (!pane || !rec?.thumb || !a || !b) return fullRect(planW, planH);
    if (rec.id !== a.id && rec.id !== b.id) return fullRect(planW, planH);
    const vr = visibleThumbRect(pane.view, pane.rect.w, pane.rect.h,
      rec.thumb.width, rec.thumb.height);
    if (!vr) return fullRect(planW, planH);
    const x = (vr.x0 / rec.thumb.width) * planW;
    const y = (vr.y0 / rec.thumb.height) * planH;
    const w = ((vr.x1 - vr.x0 + 1) / rec.thumb.width) * planW;
    const h = ((vr.y1 - vr.y0 + 1) / rec.thumb.height) * planH;
    const r = clampRect({ x, y, w, h }, planW, planH);
    // 视野太小就退回整幅：8×8 的 ROI 什么也说明不了。
    return (r.w >= 16 && r.h >= 16) ? r : fullRect(planW, planH);
  }

  /* ---------------- 结果显示层 ---------------- */
  function releaseLayers(): void {
    heatLayer.value = null;
    maskLayer.value = null;
    rawMask.value = null;
  }

  function rebuildMask(): void {
    if (!rawMask.value) return;
    maskLayer.value = markRaw(colorizeMask(rawMask.value, feather.value));
  }

  interface DecodedLayers {
    heat: HTMLCanvasElement;
    rawMask: HTMLCanvasElement;
  }

  /** 只解码与着色，不落任何 store 状态；由调用方复核身份后一次性安装。 */
  async function decodeLayers(res: DiffResult): Promise<DecodedLayers> {
    const [diffCanvas, maskCanvas] = await Promise.all([
      canvasFromPng(res.differenceMap),
      canvasFromPng(res.binaryMask),
    ]);
    return {
      heat: markRaw(colorizeHeat(diffCanvas)),
      rawMask: markRaw(maskCanvas),
    };
  }

  /** 结果发布与显示图层安装在同一处完成；羽化按**当前**档位重建。 */
  function installLayers(layers: DecodedLayers): void {
    heatLayer.value = layers.heat;
    rawMask.value = layers.rawMask;
    rebuildMask();
  }

  function applyResult(res: DiffResult, key: string, plan?: { w: number; h: number }, scopeRect?: GridRect): void {
    if (plan && scopeRect) {
      res.analysisGrid = {
        ...res.analysisGrid,
        fullWidth: plan.w,
        fullHeight: plan.h,
        scopeX: scopeRect.x,
        scopeY: scopeRect.y,
      };
    }
    result.value = res;
    resultKey.value = key;
    lastWarnings.value = [...(res.warnings ?? [])];
    selectedId.value = null;
    hoveredId.value = null;
  }

  /* ---------------- 开始分析 ---------------- */
  async function analyze(): Promise<boolean> {
    if (busy.value) return false;
    error.value = '';
    baseSnapshot.value = null;              // 新一轮预览结果不继承上一次的精算快照
    const a = aRec.value;
    const b = bRec.value;
    if (!a || !b) {
      error.value = '请选择两张已打开、像素就绪的影像';
      return false;
    }
    if (a.id === b.id) {
      error.value = '两张影像不能是同一张';
      return false;
    }
    if (!a.src || !a.thumb || !b.src || !b.thumb) {
      error.value = '影像预览尚未就绪，请先在左侧打开这两张图';
      return false;
    }
    const plan = commonGrid(a.W, a.H, b.W, b.H);
    if (plan.refusal) {
      error.value = plan.refusal;
      return false;
    }
    busy.value = true;
    notice.value = '';
    error.value = '';
    const seq = ++requestSeq;
    const key = currentKey();
    try {
      const scopeRect = scope.value === 'visible'
        ? visibleGridRect(plan.width, plan.height)
        : fullRect(plan.width, plan.height);
      const bufA = grayBuffer(a);
      const bufB = grayBuffer(b);
      if (!bufA || !bufB) {
        error.value = '影像灰度数据尚未就绪';
        return false;
      }
      // 先采样完整共同网格，再裁分析范围（保证两张图用同一套坐标与灰度范围）。
      const ga = sampleGray(bufA.src, bufA.w, bufA.h, plan.width, plan.height);
      const gb = sampleGray(bufB.src, bufB.w, bufB.h, plan.width, plan.height);
      const ca = cropGrid(ga, scopeRect, plan.width, plan.height);
      const cb = cropGrid(gb, scopeRect, plan.width, plan.height);
      const { lo, hi, hasData } = jointRange([ca, cb]);
      if (!hasData) {
        error.value = '两张影像没有共同有效像素（NoData），无法形成差异';
        return false;
      }
      if (hi - lo < 1e-6) {
        error.value = '两张影像在共同有效区的灰度几乎相同，无法形成差异';
        return false;
      }
      const pngA = rgbaToPng(quantizeToRgba(ca.gray, ca.valid, lo, hi), scopeRect.w, scopeRect.h);
      const pngB = rgbaToPng(quantizeToRgba(cb.gray, cb.valid, lo, hi), scopeRect.w, scopeRect.h);

      const specA: DiffImageInput = {
        id: a.id, name: a.name, sourceWidth: a.W, sourceHeight: a.H,
        thumbWidth: a.thumb.width, thumbHeight: a.thumb.height,
        grayPng: pngA, sceneId: a.sceneId,
      };
      const specB: DiffImageInput = {
        id: b.id, name: b.name, sourceWidth: b.W, sourceHeight: b.H,
        thumbWidth: b.thumb.width, thumbHeight: b.thumb.height,
        grayPng: pngB, sceneId: b.sceneId,
      };
      const req: DiffRequest = {
        method: method.value,
        sensitivity: sensitivity.value,
        minAreaPx: Math.max(1, Math.round(minAreaPx.value)),
        threshold: advanced.value.threshold ?? undefined,
        blur: advanced.value.blur,
        openKernel: advanced.value.openKernel,
        closeKernel: advanced.value.closeKernel,
        ssimWindow: advanced.value.ssimWindow,
        ssimGaussian: advanced.value.ssimGaussian,
        maxEdge: MAX_CANVAS_EDGE,
        image_a: specA,
        image_b: specB,
      };
      const res = await apiDiffAnalyze(loadSrConfig(), req);
      // 请求期间改了配对/方法/灵敏度：这份结果属于旧配置，不能签成当前结果。
      if (seq !== requestSeq || key !== currentKey()) {
        if (seq === requestSeq) {
          notice.value = '分析期间参数或影像已变更，旧结果已丢弃；请按当前设置重新分析。';
        }
        return false;
      }
      // 先在局部解完两张图层，再复核一次身份，最后一次性发布结果与图层；
      // 解码期间改高级参数或换影像，什么都不装，避免新区域配旧图层。
      const layers = await decodeLayers(res);
      if (seq !== requestSeq || key !== currentKey()) {
        if (seq === requestSeq) {
          notice.value = '结果解码期间参数或影像已变更，旧结果已丢弃；请按当前设置重新分析。';
        }
        return false;
      }
      applyResult(res, key, { w: plan.width, h: plan.height }, scopeRect);
      installLayers(layers);
      return true;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      return false;
    } finally {
      busy.value = false;
    }
  }

  /* ---------------- 精算（原始 ROI，可选） ---------------- */
  async function refineRegion(id: string): Promise<boolean> {
    const res = result.value;
    const reg = res?.regions.find((r) => r.id === id);
    // 精算必须绑定**产生这份结果**的两张影像，不能用后来换过的当前配对。
    const a = res ? viewer.recs.find((r) => r.id === res.imageA.id && !r.pending) : null;
    const b = res ? viewer.recs.find((r) => r.id === res.imageB.id && !r.pending) : null;
    if (!res || !reg || !a || !b) {
      if (res && (!a || !b)) {
        error.value = '结果对应的影像已不在查看器中，请重新分析';
      }
      return false;
    }
    if (busy.value) return false;
    if (stale.value) {
      error.value = '参数已修改，请先重新分析，再做原始 ROI 精算';
      return false;
    }
    if (res.analysisGrid.mode === 'source') {
      error.value = '当前已经是原始 ROI 分析结果';
      return false;
    }
    if (!a.sceneId || !b.sceneId || !a.W || !a.H) {
      error.value = '原始 ROI 分析需要两张影像都来自盘阵场景（有 sceneId 与原始 TIFF）';
      return false;
    }
    error.value = '';
    notice.value = '';
    busy.value = true;
    const seq = ++requestSeq;
    const key = currentKey();
    try {
      const [sx, sy, sw, sh] = gridRectToSource(res, reg.bbox);
      const roi = expandSourceRect([sx, sy, sw, sh], 0.2, a.W, a.H);
      const req: DiffRequest = {
        method: method.value,
        sensitivity: sensitivity.value,
        minAreaPx: Math.max(1, Math.round(minAreaPx.value)),
        threshold: advanced.value.threshold ?? undefined,
        blur: advanced.value.blur,
        openKernel: advanced.value.openKernel,
        closeKernel: advanced.value.closeKernel,
        ssimWindow: advanced.value.ssimWindow,
        ssimGaussian: advanced.value.ssimGaussian,
        maxEdge: MAX_CANVAS_EDGE,
        precision: 'source-roi',
        roi,
        image_a: {
          id: a.id, name: a.name, sourceWidth: a.W, sourceHeight: a.H,
          thumbWidth: a.thumb?.width ?? a.W, thumbHeight: a.thumb?.height ?? a.H,
          sceneId: a.sceneId,
        },
        image_b: {
          id: b.id, name: b.name, sourceWidth: b.W, sourceHeight: b.H,
          thumbWidth: b.thumb?.width ?? b.W, thumbHeight: b.thumb?.height ?? b.H,
          sceneId: b.sceneId,
        },
      };
      const refinedRes = await apiDiffAnalyze(loadSrConfig(), req);
      if (seq !== requestSeq || key !== currentKey() || result.value !== res) {
        if (seq === requestSeq) {
          notice.value = '精算期间参数或结果已变更，旧的精算结果已丢弃。';
        }
        return false;
      }
      const layers = await decodeLayers(refinedRes);
      if (seq !== requestSeq || key !== currentKey() || result.value !== res) {
        if (seq === requestSeq) {
          notice.value = '精算结果解码期间参数或结果已变更，已丢弃。';
        }
        return false;
      }
      if (!baseSnapshot.value) {
        baseSnapshot.value = {
          result: res, heat: heatLayer.value, mask: maskLayer.value,
          maskSource: rawMask.value, paramsKey: resultKey.value,
        };
      }
      applyResult(refinedRes, key);
      installLayers(layers);
      return true;
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      return false;
    } finally {
      busy.value = false;
    }
  }

  async function restorePreview(): Promise<void> {
    const snap = baseSnapshot.value;
    if (!snap) return;
    baseSnapshot.value = null;
    result.value = snap.result;
    resultKey.value = snap.paramsKey;
    lastWarnings.value = [...(snap.result.warnings ?? [])];
    heatLayer.value = snap.heat ? markRaw(snap.heat) : null;
    rawMask.value = snap.maskSource ? markRaw(snap.maskSource) : null;
    maskLayer.value = snap.mask ? markRaw(snap.mask) : null;
    selectedId.value = null;
    hoveredId.value = null;
    // 快照里的显示掩码可能对应旧羽化档位；按当前档位重建一次。
    rebuildMask();
  }

  /* ---------------- 清除 ---------------- */
  function clearResults(): void {
    requestSeq++;                          // 作废仍在途的分析/精算响应
    result.value = null;
    resultKey.value = '';
    baseSnapshot.value = null;
    releaseLayers();
    lastWarnings.value = [];
    selectedId.value = null;
    hoveredId.value = null;
    error.value = '';
    notice.value = '';
  }

  /* ---------------- Viewer 交互 ---------------- */
  function selectRegion(id: string | null): void {
    selectedId.value = id;
  }

  function hoverRegion(id: string | null): void {
    hoveredId.value = id;
  }

  /** 候选区域 → 该影像缩略图坐标矩形。 */
  function regionThumbRect(regionId: string, rec: ViewerRec): [number, number, number, number] | null {
    const res = result.value;
    const reg = res?.regions.find((r) => r.id === regionId);
    if (!res || !reg || !rec.thumb) return null;
    return gridRectToThumb(res, reg.bbox, rec.thumb.width, rec.thumb.height);
  }

  /** 点击候选：选中并把视图平滑移动到该区域。
   *
   *  只驱动**产生这份结果**的影像；若它当前不在任何格子里，先激活它，
   *  而不是把无关影像的 Camera 移走。 */
  async function locateRegion(id: string): Promise<boolean> {
    const res = result.value;
    const reg = res?.regions.find((r) => r.id === id);
    if (!res || !reg) return false;
    selectedId.value = id;
    if (viewer.drawMode) return false;
    const pairIds = [res.imageA.id, res.imageB.id].filter((v): v is number => v != null);
    if (!pairIds.length) return false;
    const shown = pairIds.find((rid) => viewer.panes.some((p) => p.rec?.id === rid));
    const wanted = shown ?? (viewer.activeRec && pairIds.includes(viewer.activeRec.id)
      ? viewer.activeRec.id : pairIds[0]);
    let pane = viewer.panes.find((p) => p.rec?.id === wanted);
    if (!pane) {
      await viewer.activate(wanted);
      pane = viewer.panes.find((p) => p.rec?.id === wanted);
    }
    if (!pane?.rec?.thumb) {
      error.value = '结果对应的影像当前不在查看器中，无法定位';
      return false;
    }
    const thumbRect = gridRectToThumb(
      res, reg.bbox, pane.rec.thumb.width, pane.rec.thumb.height);
    return viewer.focusPaneRect(pane.side, thumbRect);
  }

  /** 候选轮廓加入**人工确认**的掩码区域（不自动，只有用户显式点击才走这里）。 */
  function addRegionToMask(id: string): boolean {
    const res = result.value;
    const reg = res?.regions.find((r) => r.id === id);
    if (!res || !reg) return false;
    if (stale.value) {
      error.value = '结果已对应旧参数，请重新分析后再加入掩码';
      return false;
    }
    if (reg.contourKind === 'bbox') {
      error.value = '该候选只有外接矩形轮廓，不能直接作为精确区域加入掩码';
      return false;
    }
    if ((reg.holes ?? 0) > 0) {
      error.value = '该候选含孔洞，单多边形掩码会填掉孔洞；请手工绘制后再确认';
      return false;
    }
    if ((reg.contourKind ?? 'exact') !== 'exact') {
      error.value = '该候选轮廓简化后与真实掩码偏差较大，不能直接加入掩码；请手工绘制后再确认';
      return false;
    }
    // 必须写回结果对应的 A 图，不能用后来换过的当前配对。
    const rec = viewer.recs.find((r) => r.id === res.imageA.id && !r.pending);
    if (!rec?.thumb) {
      error.value = '结果对应的影像已不在查看器中，无法加入掩码';
      return false;
    }
    const tw = rec.thumb.width;
    const th = rec.thumb.height;
    const poly = reg.contour.map(([x, y]) => gridPointToThumb(res, x, y, tw, th));
    return viewer.addMaskPolygon(rec.id, poly);
  }

  /** 用这一对影像进入交替对比；有结果时以结果里的两张为准。 */
  function openFlicker(): boolean {
    const res = result.value;
    const a = res?.imageA.id ?? aId.value;
    const b = res?.imageB.id ?? bId.value;
    if (a == null || b == null) return false;
    if (!viewer.recs.some((r) => r.id === a) || !viewer.recs.some((r) => r.id === b)) {
      error.value = '结果对应的影像已不在查看器中，无法进入交替对比';
      return false;
    }
    return viewer.openComparePair(a, b);
  }

  /** TifCanvas 按 pane 的 rec 取显示层；坐标已换算到该 rec 的缩略图。 */
  function layerFor(rec: ViewerRec | null): DiffLayer | null {
    const res = result.value;
    if (!res || !rec?.thumb) return null;
    if (rec.id !== res.imageA.id && rec.id !== res.imageB.id) return null;
    if (!heatLayer.value || !maskLayer.value) return null;
    return {
      heat: heatLayer.value,
      mask: maskLayer.value,
      coverage: gridCoverageToThumb(res, rec.thumb.width, rec.thumb.height),
    };
  }

  /** 该 rec 在结果里是 A 还是 B（判断覆盖映射是否适用）。 */
  function recSide(recId: number | null): 'A' | 'B' | null {
    const res = result.value;
    if (!res || recId == null) return null;
    if (recId === res.imageA.id) return 'A';
    if (recId === res.imageB.id) return 'B';
    return null;
  }

  function selectPairFromActive(): void {
    const rec = viewer.activeRec;
    if (!rec) return;
    if (rec.id === bId.value) return;
    aId.value = rec.id;
    if (bId.value === rec.id) bId.value = null;
    ensurePair();
  }

  /* 图层或显示方式变化 → 让 TifCanvas 重绘 drawCanvas（它只认 renderTick）。 */
  watch([display, heatLayer, maskLayer, result], () => { viewer.renderTick++; });
  /* 羽化只改显示 alpha：从精确二值掩码重建，不重算、不改区域统计。 */
  watch(feather, () => { rebuildMask(); viewer.renderTick++; });

  return {
    // 面板
    open, openPanel, closePanel, busy, error, notice,
    method, sensitivity, scope, display, feather, minAreaPx, advanced,
    aId, bId, aRec, bRec, pairOptions, compatible, setPair,
    recommendedPair, ensurePair, selectPairFromActive,
    // 分析
    analyze, stale, currentKey, resultKey,
    // 结果
    result, regions, viewerRegions, selectedRegion, selectedId, hoveredId,
    selectRegion, hoverRegion, locateRegion, addRegionToMask, openFlicker,
    clearResults, lastWarnings, refined, canRestore, refineRegion, restorePreview,
    // 显示层
    showHeat, showMask, showMarkers, layerFor, recSide,
    heatLayer, maskLayer, rebuildMask,
  };
});
