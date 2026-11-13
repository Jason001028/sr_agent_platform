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
import { apiDiffAnalyze, apiSceneSiblings } from '../lib/api.js';
import {
  ANALYSIS_MAX_EDGE, DEFAULT_ADVANCED, commonGrid, sampleGray, jointRange,
  quantizeToRgba, gridPointToThumb, gridRectToThumb, gridCoverageToThumb, gridRectToSource,
} from '../lib/diffLocate';
import type {
  DiffAdvanced, DiffDisplay, DiffFeather, DiffImageInput, DiffMethod, DiffRegion,
  DiffRequest, DiffResult, DiffScope, DiffSensitivity,
  SmartOptions,
} from '../lib/diffLocate';
import { regionTone, toneWeightMap } from '../lib/diffTone';
import type { ToneGrid, ToneRect } from '../lib/diffTone';
import { colorizeHeatBytes, colorizeHeatPixels } from '../lib/diffRender';
import { analysisPairReason, analysisPairNote, precisePairReason } from '../lib/compareCapabilities';
import { registerViewerObservation } from '../lib/viewerContext';
import { visibleThumbRect, sourceToMaskThumb, thumbToOrig } from '../lib/viewMath';
import type { OwnedPoly } from '../lib/maskHistory';
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

/** 固定连续色表；有效像素不透明，NoData 由显示背景承接。 */
export function colorizeHeat(src: HTMLCanvasElement, valid?: HTMLCanvasElement | null): HTMLCanvasElement {
  const out = document.createElement('canvas');
  out.width = src.width;
  out.height = src.height;
  const img = src.getContext('2d')!.getImageData(0, 0, src.width, src.height);
  const validity = valid?.getContext('2d')!.getImageData(0, 0, src.width, src.height).data;
  const rgba = colorizeHeatPixels(img.data, validity);
  out.getContext('2d')!.putImageData(
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

/** 解码后的差异强度（单通道）与有效位，逐像素加权重建热力图时用。 */
interface HeatData {
  diff: Uint8Array;
  /** null = 全部有效（旧服务没有 validityMap）。 */
  valid: Uint8Array | null;
  w: number;
  h: number;
}

/** 逐像素归因的输入：A/B 两侧同一网格的显示灰度。 */
interface ToneSource {
  a: ToneGrid;
  b: ToneGrid;
}

/** 当前结果的逐像素权重图（norm 0..255），按需计算。 */
interface ToneMapData {
  bytes: Uint8Array;
  w: number;
  h: number;
}

interface Snapshot {
  result: DiffResult;
  heat: HTMLCanvasElement | null;
  mask: HTMLCanvasElement | null;
  maskSource: HTMLCanvasElement | null;
  paramsKey: string;
  rawHeat: HeatData | null;
  toneSource: ToneSource | null;
  toneMap: ToneMapData | null;
}

export const useDiffStore = defineStore('diff', () => {
  const viewer = useViewerStore();

  /* ---------------- 参数 ---------------- */
  const open = ref(false);
  const resultVisible = ref(true);
  const opacity = ref(0.45);
  const busy = ref(false);
  const error = ref('');
  /** 非错误提示（例如「参数已变更，过期结果已丢弃」）。 */
  const notice = ref('');
  /** 请求序号：只有最后一次请求允许落结果，预览与精算共用。 */
  let requestSeq = 0;
  let analysisController: AbortController | null = null;
  const analysisState = ref('待分析');
  const displayThreshold = ref(0);
  const boundariesVisible = ref(true);
  const parallelCompare = ref(false);
  const comparisonView = ref(false);
  const comparisonKey = ref('');
  const smartPrecision = ref<'source-grid' | 'preview'>('source-grid');
  const smartOptions = ref<SmartOptions>({ window: 15, sensor: 'generic', frequencyReview: false, sceneSuppression: true });
  const comparison = shallowRef<DiffResult['comparison']>(undefined);
  let savedViews: { side:'A'|'B'; id:number; view:typeof viewer.view }[] | null = null;
  /** 「延续到下一景」正在切景：抑制活动景 watcher 的 ensurePair，别覆盖刚按角色挑好的组合。 */
  const continuing = ref(false);
  const method = ref<DiffMethod>('absdiff');
  const sensitivity = ref<DiffSensitivity>('standard');
  const scope = ref<DiffScope>('common');
  const display = ref<DiffDisplay>('regions');
  const feather = ref<DiffFeather>('light');
  /** 亮度归因权重：连续非线性先验，只影响候选排序与轮廓冷暖，不改差异数值。默认关闭。 */
  const toneWeight = ref(false);
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
  const selectedRegionIds = ref<string[]>([]);
  const hoveredId = ref<string | null>(null);
  const lastWarnings = ref<string[]>([]);
  const baseSnapshot = shallowRef<Snapshot | null>(null);
  /** 当前结果的原始差异字节；热力图的着色/加权都从它重建，不重复解码 PNG。 */
  const rawHeat = shallowRef<HeatData | null>(null);
  /** 当前结果可用的灰度来源；逐像素权重在第一次需要时才计算。 */
  const toneSource = shallowRef<ToneSource | null>(null);
  const toneMap = shallowRef<ToneMapData | null>(null);

  const regions = computed(() => result.value?.regions ?? []);
  /** 复核排序分 = 强度 × 亮度归因权重；关闭归因时就是后端 rankScore。 */
  function reviewScore(reg: DiffRegion): number {
    const base = Number.isFinite(reg.rankScore)
      ? reg.rankScore
      : reg.meanDiff * Math.sqrt(Math.max(1, reg.areaPx));
    return base * (toneWeight.value ? (reg.tone?.weight ?? 1) : 1);
  }
  /** 候选展示顺序：归因开启时按复核分降序，保持稳定排序（同分仍按原顺序）。 */
  const orderedRegions = computed(() => {
    const list = regions.value;
    if (!toneWeight.value) return list;
    return [...list].sort((a, b) => reviewScore(b) - reviewScore(a));
  });
  const viewerRegions = computed(() => orderedRegions.value.slice(0, VIEWER_MARKER_MAX));
  const selectedRegion = computed(() => regions.value.find((r) => r.id === selectedId.value) ?? null);
  const showHeat = computed(() => resultVisible.value && !!result.value && display.value !== 'regions');
  const showMask = computed(() => false); // 连续热力叠加不再混入二值掩码。
  const showMarkers = computed(() => resultVisible.value && !!result.value && boundariesVisible.value
    && !(result.value?.method === 'smart' && stale.value));
  const refined = computed(() => result.value?.source === 'source-roi');
  const canRestore = computed(() => !!baseSnapshot.value);

  const aRec = computed<ViewerRec | null>(
    () => viewer.recs.find((r) => r.id === aId.value && !r.pending) ?? null,
  );
  const bRec = computed<ViewerRec | null>(
    () => viewer.recs.find((r) => r.id === bId.value && !r.pending) ?? null,
  );

  /** 左侧列表按场景目录分组，差异定位的下拉用同一把键收口：只列当前场景的图。 */
  function sceneKeyOf(r: { sceneDir?: string | null; lqPath?: string | null }): string {
    return (r.sceneDir || r.lqPath || '').replace(/[/\\]+$/, '');
  }
  function activeSceneKey(): string {
    return viewer.activeRec ? sceneKeyOf(viewer.activeRec) : '';
  }
  /** 当前场景里已打开的 Scene ID，交给后端做候选分类（不列目录）。 */
  function openedSceneIds(): string[] {
    const dir = activeSceneKey();
    return [...new Set(viewer.recs
      .filter((r) => !!r.sceneId && sceneKeyOf(r) === dir)
      .map((r) => r.sceneId!))].slice(0, 32);
  }

  /* ---------------- 逐景延续 ---------------- */
  /** 影像在场景里的角色。下一景据此把同一角色填回 A / B：
   *  A=PAN、B=NOSR 的人换景后仍拿到 PAN / NOSR，不会反过来。 */
  type DiffRole = 'pan' | 'pan_old' | 'nosr' | 'sr' | 'old' | 'ori' | 'input';

  function roleOf(r: ViewerRec | null): DiffRole | null {
    if (!r) return null;
    if (r.stageKind === 'nosr') return 'nosr';
    if (r.stageKind === 'product') {
      return (r.stageLabel ?? '').toLowerCase() === 'old' ? 'old' : 'sr';
    }
    const n = r.name.toLowerCase();
    if (n.includes('pan_old') || n.includes('pan-old')) return 'pan_old';
    if (n.includes('nosr')) return 'nosr';
    if (n.includes('pan')) return 'pan';
    if (n.includes('ori') || n.includes('original')) return 'ori';
    if (n.includes('_sr') || n.includes('sr_')) return 'sr';
    return 'input';
  }

  interface SceneGroup { key: string; recs: ViewerRec[] }

  /** 左侧列表按场景目录分组，顺序 = 首次出现的顺序。一键解析按清单行序入列，
   *  所以这个顺序就是「下一景」的自然顺序；收藏置顶不参与（那是渲染顺序）。 */
  function sceneGroups(): SceneGroup[] {
    const groups: SceneGroup[] = [];
    const byKey = new Map<string, SceneGroup>();
    for (const r of viewer.recs) {
      if (r.pending) continue;
      const key = sceneKeyOf(r);
      if (!key) continue;
      let g = byKey.get(key);
      if (!g) { g = { key, recs: [] }; byKey.set(key, g); groups.push(g); }
      g.recs.push(r);
    }
    return groups;
  }

  /** 「下一景」= 当前活动景之后的第一组；不循环，末景没有下一景。 */
  const nextSceneGroup = computed<SceneGroup | null>(() => {
    const key = activeSceneKey();
    if (!key) return null;
    const groups = sceneGroups();
    const i = groups.findIndex((g) => g.key === key);
    return i >= 0 && i + 1 < groups.length ? groups[i + 1] : null;
  });

  /** 显隐只取决于结果与下一景，不随显示/绘制/对比的临时状态摘掉按钮。
   *  续到末景时，保留到取图、分析与解码全部结束，避免操作中途消失。 */
  const showContinueScene = computed(() =>
    !!result.value && (!!nextSceneGroup.value || continuing.value));
  const continueSceneReason = computed(() => {
    if (continuing.value) return '正在延续到下一景，请稍候';
    if (busy.value) return '差异分析处理中，请稍候';
    if (!result.value) return '请先完成当前景的差异分析';
    if (!nextSceneGroup.value) return '左侧列表里没有下一景';
    if (viewer.drawMode) return '请先完成或退出掩码绘制';
    if (viewer.compareOn || viewer.panes.length !== 1) return '请先退出对比，回到单图查看';
    if (!aRec.value || !bRec.value) return '请先选择当前景的影像 A / B';
    return '';
  });
  const canContinueScene = computed(() => !continueSceneReason.value);

  function compatibleRecs(a: ViewerRec, b: ViewerRec): boolean {
    return a.id !== b.id && !commonGrid(a.W, a.H, b.W, b.H).refusal;
  }

  /** 在下一景里按角色挑 A / B；角色缺位时只按兼容性补位，不把两张同角色的图硬配。 */
  function pickRolePair(
    group: SceneGroup, roleA: DiffRole, roleB: DiffRole,
  ): { a: ViewerRec; b: ViewerRec } | null {
    const pool = group.recs.filter((r) => !r.pending && r.W > 0 && r.H > 0);
    if (pool.length < 2) return null;
    const rank = (r: ViewerRec) => hintRank(r.name);
    const byRole = (role: DiffRole, except?: ViewerRec | null) =>
      pool.filter((r) => r !== except && roleOf(r) === role)
        .sort((x, y) => rank(x) - rank(y));
    const a = byRole(roleA)[0] ?? null;
    const b = byRole(roleB, a)[0] ?? null;
    if (a && b && compatibleRecs(a, b)) return { a, b };
    // 保留能认出的那一侧，另一侧只在池子里找兼容的补位。
    const keepB = b ?? byRole(roleB)[0] ?? null;
    if (keepB) {
      const partner = pool.filter((r) => r !== keepB)
        .sort((x, y) => rank(x) - rank(y))
        .find((r) => compatibleRecs(r, keepB));
      if (partner) return { a: partner, b: keepB };
    }
    if (a) {
      const partner = pool.filter((r) => r !== a)
        .sort((x, y) => rank(x) - rank(y))
        .find((r) => compatibleRecs(a, r));
      if (partner) return { a, b: partner };
    }
    for (let i = 0; i < pool.length; i++) {
      for (let j = i + 1; j < pool.length; j++) {
        if (compatibleRecs(pool[i], pool[j])) return { a: pool[i], b: pool[j] };
      }
    }
    return null;
  }

  let sceneEpoch = 0;
  /** 拉取当前场景的候选并装成身份卡片（只建身份，不取像素）。
   *
   *  这样下拉里既有已打开的图，也有盘阵上存在、还没打开的同景图；选中未打开的
   *  那张时按需取像素（见下面 aId/bId 的 watch 与 analyze）。取不到候选不是错误：
   *  保留已打开的图，分析照常可用。 */
  async function refreshSceneImages(): Promise<void> {
    const sid = viewer.activeRec?.sceneId;
    if (!sid) { sceneEpoch++; return; }
    const epoch = ++sceneEpoch, dir = activeSceneKey();
    try {
      const res = await apiSceneSiblings(loadSrConfig(), sid, undefined, true, openedSceneIds());
      if (epoch !== sceneEpoch || activeSceneKey() !== dir) return;
      const seen = new Set<string>();
      for (const item of [...(res.tourItems ?? []), ...res.items]) {
        if (!item.id || seen.has(item.id)) continue;
        seen.add(item.id);
        viewer.ensureSceneImage(item, res);
      }
    } catch {
      // 只读候选失败时保持现状；不弹错，不挡已打开影像之间的分析。
    }
  }

  const pairOptions = computed<DiffPairOption[]>(() => {
    const dir = activeSceneKey();
    return viewer.recs
      .filter((r) => !r.pending && r.W > 0 && r.H > 0 && sceneKeyOf(r) === dir
        && ((!!r.thumb && !!r.src) || !!r.card))
      .map((r) => ({
        id: r.id,
        name: r.name,
        stage: r.stageLabel ?? '',
        width: r.W,
        height: r.H,
      }));
  });

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

  /** 自动配对只在**已取到像素**的图里挑：下拉里那些还没打开的同景图留给用户主动选，
   *  不能因为打开面板或切景就替人下载一串预览。 */
  function openOptions(): DiffPairOption[] {
    return pairOptions.value.filter((o) => {
      const r = viewer.recs.find((x) => x.id === o.id);
      return !!r?.thumb && !!r?.src;
    });
  }

  function recommendedPair(): { a: number; b: number } | null {
    const list = openOptions();
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
    const ready = openOptions();
    const pool = ready.length ? ready : pairOptions.value;
    const first = pool[0];
    aId.value = first?.id ?? null;
    bId.value = pool.find((o) => o.id !== first?.id)?.id ?? null;
  }

  function setPair(a: number | null, b: number | null): void {
    aId.value = a;
    bId.value = b;
    error.value = '';
  }

  function openPanel(): void {
    ensurePair();
    open.value = true;
    void refreshSceneImages();
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
    return `${r.sceneId ?? ''}|${r.stageKind ?? ''}|${r.W}x${r.H}|${r.thumb?.width}x${r.thumb?.height}|${r.srcRev ?? ''}|${r.token ?? 0}|${r.srcw}x${r.srch}|${JSON.stringify(r.tourIdentity)}`;
  }

  function currentKey(): string {
    return JSON.stringify({
      a: aId.value, b: bId.value, method: method.value, sensitivity: sensitivity.value,
      scope: scope.value, minArea: minAreaPx.value, advanced: advanced.value,
      smart: method.value === 'smart' || parallelCompare.value ? [smartOptions.value, smartPrecision.value, parallelCompare.value] : null,
      pa: pixelKey(aId.value), pb: pixelKey(bId.value),
    });
  }
  function comparisonSignature(): string {
    const key = JSON.parse(currentKey());
    delete key.method;
    key.smart = [smartOptions.value, smartPrecision.value, parallelCompare.value];
    return JSON.stringify(key);
  }
  function clearComparison() {
    comparison.value = undefined; comparisonKey.value = ''; comparisonView.value = false;
  }
  const stale = computed(() => !!result.value && resultKey.value !== currentKey());
  const precisionReason = computed(() => analysisPairReason(aRec.value, bRec.value));
  const alignmentNote = computed(() => analysisPairNote(aRec.value, bRec.value));
  const flickerPairReason = computed(() => precisePairReason(aRec.value, bRec.value));
  const smartPairReason = computed(() => {
    const a = aRec.value, b = bRec.value;
    if (!a || !b) return '请选择 PAN 与 NOSR';
    const roles = [a.tourIdentity?.role ?? roleOf(a), b.tourIdentity?.role ?? roleOf(b)];
    if (!roles.includes('pan') || !roles.includes('nosr')) return 'LTD需要 PAN 本体与 NOSR；不能使用 old 或 SR 产物代替';
    if (smartPrecision.value !== 'preview' && (!a.sceneId || !b.sceneId || !a.srcRev || !b.srcRev)) return '原始 DN 分析需要可访问的盘阵影像与源版本';
    const groupA=sceneKeyOf(a),groupB=sceneKeyOf(b);
    const sequenceA=a.tourIdentity?.sequence, sequenceB=b.tourIdentity?.sequence;
    if ((!groupA || groupA!==groupB) && (!sequenceA || sequenceA!==sequenceB)) return '缺少可靠的共同场景关联';
    if (!!a.tourIdentity?.registration !== !!b.tourIdentity?.registration) return '两张影像的空间配准证据不对称，请先核对';
    return analysisPairReason(a,b) ?? '';
  });
  const nativeRefineReason = computed(() => {
    if (smartPairReason.value) return smartPairReason.value;
    const a=aRec.value, b=bRec.value;
    if (!a?.sceneId || !b?.sceneId || !a.srcRev || !b.srcRev) return '原图精检需要可访问的 PAN/NOSR 源 TIFF 与版本';
    if ((a.tourIdentity?.role ?? roleOf(a)) !== 'pan') return '原图精检请将 PAN 本体选为 A、NOSR 选为 B';
    return '';
  });

  /* Automatic polygons are staged in SVG; only one atomic vector transaction
     is committed after the animation and a final identity/draft recheck. */
  const autoDrawing = shallowRef<{ imageId: number; groupId: string; polygons: OwnedPoly[]; duration: number;
    reviewing: boolean; decisions: Record<string, 'approved' | 'rejected'> } | null>(null);
  let finishReview: (() => Promise<boolean>) | null = null;
  const autoReviewBusy = ref(false);
  async function reviewAutoRegion(id: string, decision: 'approved' | 'rejected') {
    const batch = autoDrawing.value;
    if (!batch?.reviewing || autoReviewBusy.value || !batch.polygons.some(p => p.autoDiff?.region_id === id)) return;
    autoDrawing.value = { ...batch, decisions: { ...batch.decisions, [id]: decision } };
    if (batch.polygons.every(p => autoDrawing.value?.decisions[p.autoDiff!.region_id])) {
      autoReviewBusy.value = true;
      try { await finishReview?.(); } finally { autoReviewBusy.value = false; }
    }
  }
  const autoPreparing = ref(false);
  const autoSourceError = ref('');
  let autoValidation: AbortController | null = null;
  let autoTimer: ReturnType<typeof setTimeout> | null = null;
  let autoEpoch = 0;
  let autoActivating = false;
  let finishAuto: ((ok: boolean) => void) | null = null;
  function cancelAutoDraw() {
    autoEpoch++;
    autoValidation?.abort(); autoValidation = null;
    if (autoTimer !== null) clearTimeout(autoTimer);
    autoTimer = null; autoDrawing.value = null; autoPreparing.value = false;
    finishReview = null; autoReviewBusy.value = false;
    finishAuto?.(false); finishAuto = null;
  }
  const autoTarget = computed(() => {
    const res = result.value;
    if (!res) return null;
    // kind=input is the server's body identity. A tour certificate is optional
    // on normal opening paths, but an explicit conflicting role is not ignored.
    return viewer.recs.find(r => [res.imageA.id, res.imageB.id].includes(r.id)
      && r.stageKind === 'input' && (!r.tourIdentity || r.tourIdentity.role === 'pan')) ?? null;
  });
  const autoDrawReason = computed(() => {
    const res = result.value, auto = res?.autoMask, target = autoTarget.value;
    if (busy.value || autoPreparing.value || autoDrawing.value || viewer.merging) return '正在处理，请稍候';
    if (!auto) return '当前服务未提供自动绘制数据，请升级服务并重新分析';
    if (stale.value) return '分析结果已过期，请重新分析';
    if (res?.method === 'smart' && res.source !== 'source-roi') return 'LTD网格候选需先原图精检，再加入修复掩码';
    if (autoSourceError.value) return autoSourceError.value;
    if (!target) return '当前分析的 A/B 未包含已确认的 PAN 本体，请选择本体后重新分析';
    if (!target.thumb || !target.src || target.pending || !target.sceneId || !target.srcRev) return 'PAN 本体源文件或预览不可用';
    const a = viewer.recs.find(r => r.id === res!.imageA.id);
    const b = viewer.recs.find(r => r.id === res!.imageB.id);
    const reason = res?.method === 'smart' ? smartPairReason.value : precisePairReason(a, b);
    if (reason) return '影像身份不一致：' + reason;
    const source = auto.sources?.find(s => s.image_id === target.id);
    if (!source?.transform || source.pixel_key !== pixelKey(target.id)
      || source.scene_id !== target.sceneId || source.source_revision !== target.srcRev
      || source.source_size[0] !== target.W || source.source_size[1] !== target.H
      || (target.tourIdentity && (target.tourIdentity.width !== target.W || target.tourIdentity.height !== target.H))) return '源影像身份或尺寸已改变，请重新分析';
    if (auto.coordinate_space !== 'analysis_pixel' || auto.analysis_size[0] !== res!.analysisGrid.width
      || auto.analysis_size[1] !== res!.analysisGrid.height) return '分析网格不一致，请重新分析';
    if (!auto.polygons.some(p => p.coverage_ratio === 1 && p.supersample_coverage_ratio === 1
      && p.source_polygons?.some(s => s.image_id === target.id && s.coverage_ratio === 1 && s.drawable)
      && p.vertex_count === p.points.length && p.points.length >= 3 && p.points.length <= 24)) return '没有满足完整覆盖与顶点约束的候选区域';
    if (viewer.pendingPts?.length || viewer.pendingRect) return '请先完成或取消当前手绘草稿';
    return '';
  });
  async function autoDraw(regionIds?: string[]): Promise<boolean> {
    if (autoDrawReason.value) { error.value = autoDrawReason.value; return false; }
    const res = result.value!, auto = res.autoMask!, target = autoTarget.value!;
    const key = currentKey(), pixels = pixelKey(target.id), epoch = ++autoEpoch;
    const groupId = `${auto.analysis_id}:${target.id}:${auto.parameter_version}`;
    const polygons: OwnedPoly[] = [];
    for (const p of auto.polygons) {
      if (res.method === 'smart') {
        const wanted = Array.isArray(regionIds) ? regionIds : selectedRegionIds.value;
        if (!wanted.length || !p.candidate_ids?.length || !p.candidate_ids.every(id=>wanted.includes(id))) continue;
        if (p.candidate_ids.some(id=>res.regions.find(r=>r.id===id)?.maskEligible !== true)) continue;
      }
      if (p.coverage_ratio !== 1 || p.supersample_coverage_ratio !== 1 || p.points.length < 3
        || p.points.length > 24 || p.vertex_count !== p.points.length) continue;
      if (p.coordinate_space !== 'analysis_pixel' || p.analysis_size[0] !== auto.analysis_size[0]
        || p.analysis_size[1] !== auto.analysis_size[1]) continue;
      const projected = p.source_polygons?.find(s => s.image_id === target.id);
      if (!projected || projected.coordinate_space !== 'source_pixel' || projected.coverage_ratio !== 1
        || !projected.drawable || projected.points.length !== p.points.length) continue;
      // Source vertices were independently checked with the official rasterizer.
      const poly: OwnedPoly = projected.points.map(([x, y]) => sourceToMaskThumb(
        x, y, target.W, target.H, target.thumb!.width, target.thumb!.height));
      if (projected.points.some(([x,y]) => !Number.isInteger(x) || !Number.isInteger(y)
        || x < 0 || y < 0 || x >= target.W || y >= target.H) || poly.some(v => !v.every(Number.isFinite))) continue;
      poly.autoDiff = { source: 'auto_diff', analysis_id: auto.analysis_id, image_id: target.id,
        group_id: groupId, region_id: p.region_id, parameter_version: auto.parameter_version,
        initial_points: JSON.stringify(poly), needs_review: p.needs_review };
      polygons.push(poly);
    }
    if (!polygons.length) { error.value = '没有可安全绘制的多边形'; return false; }
    autoPreparing.value = true;
    error.value = ''; notice.value = '';
    const inputs = [res.imageA.id, res.imageB.id].map(id => viewer.recs.find(r => r.id === id)!);
    async function validateFiles() {
      // Existing read-only, named-sibling endpoint: no scan, preview generation,
      // mask write or SR. Check again after animation to catch source replacement.
      const controller = new AbortController();
      autoValidation = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      let siblings;
      try {
        siblings = await apiSceneSiblings(loadSrConfig(), target.sceneId!, undefined, true,
          inputs.map(r => r.sceneId!).filter(Boolean), controller.signal);
      } finally {
        clearTimeout(timeout);
        if (autoValidation === controller) autoValidation = null;
      }
      for (const r of inputs) {
        const item = [...(siblings.tourItems ?? []), ...siblings.items].find(i => (i.id === r.sceneId || i.aliases?.includes(r.sceneId!)) && i.exists);
        if (!item || item.srcRev !== r.srcRev || item.W !== r.W || item.H !== r.H
          || (r.stageKind && item.kind !== r.stageKind)
          || (r.id === target.id && (item.kind !== 'input' || (item.tourIdentity && item.tourIdentity.role !== 'pan')))
          // Normal open/drop/card paths may lack a tour certificate. Recheck
          // authoritative kind + exact scene ID/revision/size regardless; when
          // a local certificate exists it must still match the server.
          || (r.tourIdentity && (item.tourIdentity?.role !== r.tourIdentity.role
            || item.tourIdentity?.sequence !== r.tourIdentity.sequence))) {
          throw new Error('源文件不可用、版本或尺寸已改变，请重新加载影像并分析');
        }
      }
    }
    try {
      await validateFiles();
      if (epoch !== autoEpoch || result.value !== res || stale.value || currentKey() !== key
        || viewer.pendingPts?.length || viewer.pendingRect) return false;
      autoActivating = true;
      try { if (viewer.activeId !== target.id) await viewer.activate(target.id); }
      finally { autoActivating = false; }
      if (epoch !== autoEpoch || viewer.activeId !== target.id || result.value !== res || stale.value
        || currentKey() !== key || pixelKey(target.id) !== pixels || viewer.pendingPts?.length || viewer.pendingRect) return false;
      const vertices = polygons.flat();
      const xs = vertices.map(p => p[0]), ys = vertices.map(p => p[1]);
      viewer.focusPaneRect(viewer.activeSide, [Math.min(...xs), Math.min(...ys),
        Math.max(...xs)-Math.min(...xs), Math.max(...ys)-Math.min(...ys)], false);
      viewer.enterDraw();
      const before = JSON.stringify(target.maskRois ?? []);
      const duration = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 3200;
      autoDrawing.value = { imageId: target.id, groupId, polygons, duration, reviewing: false, decisions: {} };
      const commit = async () => {
        try { await validateFiles(); }
        catch (e) {
          if (epoch === autoEpoch) {
            error.value = autoSourceError.value = e instanceof Error ? e.message : String(e);
            viewer.showToast(error.value); cancelAutoDraw();
          }
          return false;
        }
        if (epoch !== autoEpoch) return false;
        if (viewer.activeId !== target.id || result.value !== res || stale.value
          || currentKey() !== key || pixelKey(target.id) !== pixels || !viewer.drawMode
          || viewer.pendingPts?.length || viewer.pendingRect || JSON.stringify(target.maskRois ?? []) !== before) {
          cancelAutoDraw(); return false;
        }
        const accepted = polygons.filter(p => autoDrawing.value?.decisions[p.autoDiff!.region_id] === 'approved').map(p => {
          const copy: OwnedPoly = p.map(([x, y]) => [x, y]);
          copy.autoDiff = { ...p.autoDiff!, reviewed: true, needs_review: false };
          return copy;
        });
        const ok = !accepted.length || viewer.addMaskPolygonBatch(target.id, accepted, auto.analysis_id);
        const skipped = auto.polygons.length - polygons.length;
        if (ok) {
          resultVisible.value = false;
          notice.value = `审批完成：通过 ${accepted.length} 个，驳回 ${polygons.length - accepted.length} 个${skipped ? `；${skipped} 个因覆盖或坐标冲突未绘制` : ''}；通过区域已转为可编辑掩码，可整批撤销 / 重做。`;
          viewer.showToast(notice.value);
        }
        autoDrawing.value = null; autoPreparing.value = false; autoTimer = null; finishReview = null;
        return ok;
      };
      const beginReview = async () => {
        try { await validateFiles(); }
        catch (e) {
          if (epoch === autoEpoch) { error.value = autoSourceError.value = e instanceof Error ? e.message : String(e); cancelAutoDraw(); }
          return false;
        }
        if (epoch !== autoEpoch || !autoDrawing.value) return false;
        if (viewer.activeId !== target.id || result.value !== res || stale.value || currentKey() !== key
          || pixelKey(target.id) !== pixels || !viewer.drawMode || viewer.pendingPts?.length || viewer.pendingRect
          || JSON.stringify(target.maskRois ?? []) !== before) { cancelAutoDraw(); return false; }
        autoDrawing.value = { ...autoDrawing.value, reviewing: true };
        finishReview = commit;
        notice.value = '请逐区点击右上角 √ / ×；全部审批后，通过区域将转为普通掩码。';
        autoPreparing.value = false;
        return true;
      };
      if (!duration) return beginReview();
      return await new Promise<boolean>(resolve => {
        finishAuto = resolve;
        autoTimer = setTimeout(() => { void beginReview().then(ok => {
          if (epoch === autoEpoch) finishAuto = null;
          resolve(ok);
        }); }, duration);
      });
    } catch (e) {
      if (epoch === autoEpoch) {
        error.value = autoSourceError.value = e instanceof Error ? e.message : String(e);
        viewer.showToast(error.value);
      }
      return false;
    } finally { if (epoch === autoEpoch) autoPreparing.value = false; }
  }
  watch([() => viewer.activeId, () => viewer.drawMode, () => viewer.pendingPts?.length,
    () => viewer.pendingRect, result, currentKey, busy], () => {
    if (autoDrawing.value) cancelAutoDraw();
  }, { flush: 'sync' });
  watch([() => viewer.activeId, result, currentKey, busy], () => {
    if (autoPreparing.value && !autoDrawing.value && !autoActivating) cancelAutoDraw();
  }, { flush: 'sync' });
  watch(result, () => { autoSourceError.value = ''; }, { flush: 'sync' });

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
    rawHeat.value = null;
    toneSource.value = null;
    toneMap.value = null;
  }

  function rebuildMask(): void {
    if (!rawMask.value) return;
    maskLayer.value = markRaw(colorizeMask(rawMask.value, feather.value));
  }

  interface DecodedLayers {
    raw: HeatData;
    rawMask: HTMLCanvasElement;
  }

  /** 只解码，不落任何 store 状态；由调用方复核身份后一次性安装。
   *  差异强度只留单通道字节，逐像素加权/关闭时都从这里重新着色。 */
  async function decodeLayers(res: DiffResult): Promise<DecodedLayers> {
    const [diffCanvas, maskCanvas, validCanvas] = await Promise.all([
      canvasFromPng(res.differenceMap),
      canvasFromPng(res.binaryMask),
      res.validityMap ? canvasFromPng(res.validityMap) : Promise.resolve(null),
    ]);
    const w = diffCanvas.width;
    const h = diffCanvas.height;
    const diff = new Uint8Array(w * h);
    const src = diffCanvas.getContext('2d')!.getImageData(0, 0, w, h).data;
    for (let i = 0; i < diff.length; i++) diff[i] = src[i * 4];
    let valid: Uint8Array | null = null;
    if (validCanvas) {
      valid = new Uint8Array(w * h);
      const v = validCanvas.getContext('2d')!.getImageData(0, 0, w, h).data;
      for (let i = 0; i < valid.length; i++) valid[i] = v[i * 4] ? 1 : 0;
    }
    return { raw: { diff, valid, w, h }, rawMask: markRaw(maskCanvas) };
  }

  /* ---------------- 逐像素归因权重（显示层） ---------------- */
  /** 权重图与热力图网格不一致时双线性放大。原始 ROI 精算的灰度来自预览缩略图，
   *  权重图比结果网格小，直接放大权重比在结果网格上重新估局部纹理更稳定。 */
  function upsampleWeights(map: ToneMapData, w: number, h: number): Uint8Array {
    if (map.w === w && map.h === h) return map.bytes;
    const out = new Uint8Array(w * h);
    const sx = map.w / w;
    const sy = map.h / h;
    for (let y = 0; y < h; y++) {
      const fy = Math.max(0, Math.min(map.h - 1, (y + 0.5) * sy - 0.5));
      const y0 = Math.floor(fy);
      const y1 = Math.min(map.h - 1, y0 + 1);
      const ty = fy - y0;
      const row0 = y0 * map.w;
      const row1 = y1 * map.w;
      for (let x = 0; x < w; x++) {
        const fx = Math.max(0, Math.min(map.w - 1, (x + 0.5) * sx - 0.5));
        const x0 = Math.floor(fx);
        const x1 = Math.min(map.w - 1, x0 + 1);
        const tx = fx - x0;
        const top = map.bytes[row0 + x0] * (1 - tx) + map.bytes[row0 + x1] * tx;
        const bottom = map.bytes[row1 + x0] * (1 - tx) + map.bytes[row1 + x1] * tx;
        out[y * w + x] = Math.round(top * (1 - ty) + bottom * ty);
      }
    }
    return out;
  }

  /** 需要时才算权重图；算完缓存到当前结果，反复开关不重算。 */
  function ensureToneMap(): ToneMapData | null {
    if (toneMap.value) return toneMap.value;
    const src = toneSource.value;
    if (!src) return null;
    const map = toneWeightMap(src.a, src.b);
    if (!map) return null;
    const data: ToneMapData = { bytes: map.norm, w: map.w, h: map.h };
    toneMap.value = data;
    return data;
  }

  /** 用原始差异 + 当前加权开关重建热力图画布。 */
  function rebuildHeat(): void {
    const raw = rawHeat.value;
    if (!raw) { heatLayer.value = null; return; }
    let weights: Uint8Array | null = null;
    if (toneWeight.value && result.value?.method !== 'smart') {
      const map = ensureToneMap();
      if (map) weights = upsampleWeights(map, raw.w, raw.h);
    }
    const rgba = colorizeHeatBytes(raw.diff, raw.w, raw.h, raw.valid, weights, {
      smart: result.value?.method === 'smart', threshold: result.value?.method === 'smart' ? displayThreshold.value : 0,
      selectedBbox: result.value?.method === 'smart' ? selectedRegion.value?.bbox : null,
    });
    const out = document.createElement('canvas');
    out.width = raw.w;
    out.height = raw.h;
    out.getContext('2d')!.putImageData(
      new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, raw.w, raw.h), 0, 0);
    heatLayer.value = markRaw(out);
  }

  /** 结果发布与显示图层安装在同一处完成；热力图按当前加权开关、
   *  掩码按当前羽化档位重建。 */
  function installLayers(layers: DecodedLayers): void {
    rawHeat.value = layers.raw;
    rawMask.value = layers.rawMask;
    rebuildHeat();
    rebuildMask();
  }

  function applyResult(res: DiffResult, key: string, plan?: { w: number; h: number }, scopeRect?: GridRect): void {
    if(res.source==='source-roi'||res.source==='source-tiled') comparisonView.value=false;
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
    resultVisible.value = true;
    resultKey.value = key;
    lastWarnings.value = [...(res.warnings ?? [])];
    selectedId.value = null;
    hoveredId.value = null;
  }

  /* ---------------- 亮度归因（仅显示层） ---------------- */
  /** 二值掩码画布 → 区域掩码（>127 属于某个区域），用于排除外接矩形里的背景。 */
  function maskFromCanvas(canvas: HTMLCanvasElement | null): { data: Uint8Array; w: number; h: number } | null {
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const { width: w, height: h } = canvas;
    if (w < 1 || h < 1) return null;
    const img = ctx.getImageData(0, 0, w, h).data;
    const data = new Uint8Array(w * h);
    for (let i = 0; i < data.length; i++) data[i] = img[i * 4] > 127 ? 255 : 0;
    return { data, w, h };
  }

  /** 预览分析：区域坐标与裁剪后的分析网格一一对应，直接按掩码统计。 */
  function attachPreviewTone(
    res: DiffResult, a: ToneGrid, b: ToneGrid, maskCanvas: HTMLCanvasElement | null,
  ): void {
    const mask = maskFromCanvas(maskCanvas);
    // 读不到掩码（画布实现缺像素、或掩码全黑）时退回外接矩形，别让归因整片缺失。
    const usable = mask && mask.w === a.w && mask.h === a.h && mask.data.some((v) => v > 0)
      ? mask : null;
    for (const reg of res.regions) {
      const rect: ToneRect = { x: reg.bbox[0], y: reg.bbox[1], w: reg.bbox[2], h: reg.bbox[3] };
      const stats = regionTone(a, b, rect, rect,
        usable ? { mask: usable.data, maskWidth: usable.w } : {});
      if (stats) reg.tone = stats;
    }
  }

  function thumbRectToBufferRect(
    rect: [number, number, number, number], tw: number, th: number, bw: number, bh: number,
  ): ToneRect {
    const sx = bw / Math.max(1, tw);
    const sy = bh / Math.max(1, th);
    return {
      x: rect[0] * sx, y: rect[1] * sy,
      w: Math.max(1, rect[2] * sx), h: Math.max(1, rect[3] * sy),
    };
  }

  /** 原始 ROI 精算：结果坐标在 ROI 里，换到预览缩略图再取两图各自的像素缓冲。 */
  function attachThumbTone(res: DiffResult): void {
    const a = viewer.recs.find((r) => r.id === res.imageA.id && !r.pending);
    const b = viewer.recs.find((r) => r.id === res.imageB.id && !r.pending);
    if (!a?.thumb || !b?.thumb) return;
    const bufA = grayBuffer(a);
    const bufB = grayBuffer(b);
    if (!bufA || !bufB) return;
    const gridA: ToneGrid = { gray: bufA.src, w: bufA.w, h: bufA.h };
    const gridB: ToneGrid = { gray: bufB.src, w: bufB.w, h: bufB.h };
    for (const reg of res.regions) {
      const ta = gridRectToThumb(res, reg.bbox, a.thumb.width, a.thumb.height);
      const tb = gridRectToThumb(res, reg.bbox, b.thumb.width, b.thumb.height);
      const stats = regionTone(
        gridA, gridB,
        thumbRectToBufferRect(ta, a.thumb.width, a.thumb.height, bufA.w, bufA.h),
        thumbRectToBufferRect(tb, b.thumb.width, b.thumb.height, bufB.w, bufB.h),
      );
      if (stats) reg.tone = stats;
    }
  }

  /** 把裁剪后的 Float32 分析网格压成 0..255 字节，长期保存给逐像素归因用。
   *  全有效的网格不存 valid，省一份内存。 */
  function quantizeToneGrid(
    g: { gray: Float32Array; valid: Uint8Array }, w: number, h: number,
  ): ToneGrid {
    const gray = new Uint8Array(w * h);
    for (let i = 0; i < gray.length; i++) {
      const v = g.gray[i];
      gray[i] = Number.isFinite(v) ? Math.max(0, Math.min(255, Math.round(v))) : 0;
    }
    let all = true;
    for (let i = 0; i < g.valid.length; i++) {
      if (!g.valid[i]) { all = false; break; }
    }
    return { gray, valid: all ? undefined : new Uint8Array(g.valid), w, h };
  }

  function previewToneSource(
    ca: { gray: Float32Array; valid: Uint8Array },
    cb: { gray: Float32Array; valid: Uint8Array },
    w: number, h: number,
  ): ToneSource {
    return { a: quantizeToneGrid(ca, w, h), b: quantizeToneGrid(cb, w, h) };
  }

  function sampleBuffer(
    buf: { src: Float32Array; w: number; h: number }, fx: number, fy: number,
  ): number {
    const x = Math.max(0, Math.min(buf.w - 1, fx));
    const y = Math.max(0, Math.min(buf.h - 1, fy));
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const x1 = Math.min(buf.w - 1, x0 + 1);
    const y1 = Math.min(buf.h - 1, y0 + 1);
    const tx = x - x0;
    const ty = y - y0;
    const p00 = buf.src[y0 * buf.w + x0];
    const p10 = buf.src[y0 * buf.w + x1];
    const p01 = buf.src[y1 * buf.w + x0];
    const p11 = buf.src[y1 * buf.w + x1];
    return p00 * (1 - tx) * (1 - ty) + p10 * tx * (1 - ty)
      + p01 * (1 - tx) * ty + p11 * tx * ty;
  }

  /** 原始 ROI 精算：前端拿不到源 TIFF，按归一化坐标把两张预览缩略图重采样到
   *  一个共同的小网格（长边 ≤ 512），作为显示层归因的输入。
   *
   *  不直接用结果网格：源 ROI 的分辨率远高于预览，把缩略图放大到结果网格后
   *  局部标准差会在块内塌成 0、块边骤升，权重反而失真。先在预览尺度算权重，
   *  再由 `upsampleWeights` 平滑放大。 */
  function refinedToneSource(res: DiffResult): ToneSource | null {
    const a = viewer.recs.find((r) => r.id === res.imageA.id && !r.pending);
    const b = viewer.recs.find((r) => r.id === res.imageB.id && !r.pending);
    if (!a?.thumb || !b?.thumb) return null;
    const bufA = grayBuffer(a);
    const bufB = grayBuffer(b);
    if (!bufA || !bufB) return null;
    const grid = res.analysisGrid;
    const ra = gridRectToThumb(res, [0, 0, grid.width, grid.height], a.thumb.width, a.thumb.height);
    const rb = gridRectToThumb(res, [0, 0, grid.width, grid.height], b.thumb.width, b.thumb.height);
    const ow = Math.max(16, Math.min(512, Math.round(Math.max(ra[2], rb[2]))));
    const oh = Math.max(16, Math.min(512, Math.round(ow * (ra[3] / Math.max(1, ra[2])))));
    const ga = new Uint8Array(ow * oh);
    const gb = new Uint8Array(ow * oh);
    for (let y = 0; y < oh; y++) {
      const gy = ((y + 0.5) / oh) * grid.height;
      for (let x = 0; x < ow; x++) {
        const gx = ((x + 0.5) / ow) * grid.width;
        const [ax, ay] = gridPointToThumb(res, gx, gy, bufA.w, bufA.h);
        const [bx, by] = gridPointToThumb(res, gx, gy, bufB.w, bufB.h);
        const va = sampleBuffer(bufA, ax - 0.5, ay - 0.5);
        const vb = sampleBuffer(bufB, bx - 0.5, by - 0.5);
        const i = y * ow + x;
        ga[i] = Number.isFinite(va) ? Math.max(0, Math.min(255, Math.round(va))) : 0;
        gb[i] = Number.isFinite(vb) ? Math.max(0, Math.min(255, Math.round(vb))) : 0;
      }
    }
    return {
      a: { gray: ga, w: ow, h: oh },
      b: { gray: gb, w: ow, h: oh },
    };
  }

  /* ---------------- 开始分析 ---------------- */
  async function analyze(): Promise<boolean> {
    if (busy.value) return false;
    if (method.value === 'smart' || parallelCompare.value) return analyzeSmart();
    clearComparison();
    cancelAutoDraw();
    autoSourceError.value = '';
    error.value = '';
    baseSnapshot.value = null;              // 新一轮预览结果不继承上一次的精算快照
    let a = aRec.value;
    let b = bRec.value;
    if (!a || !b) {
      error.value = '请选择两张已打开、像素就绪的影像';
      return false;
    }
    if (a.id === b.id) {
      error.value = '两张影像不能是同一张';
      return false;
    }
    if (precisionReason.value) { error.value = '差异分析暂不可用：' + precisionReason.value; return false; }
    if (!a.src || !a.thumb || !b.src || !b.thumb) {
      // 下拉里的同景图可能只建了身份卡（还没打开过）：按需取像素后再继续。
      // 取图期间先占住 busy，避免连点分析并发取同一份预览。
      busy.value = true;
      try {
        await Promise.all([viewer.ensureCardPixels(a.id), viewer.ensureCardPixels(b.id)]);
      } finally {
        busy.value = false;
      }
      a = aRec.value;
      b = bRec.value;
      if (!a || !b) {
        error.value = '影像预览尚未就绪，请先在左侧打开这两张图';
        return false;
      }
      if (!a.src || !a.thumb || !b.src || !b.thumb) {
        error.value = '影像预览尚未就绪，请先在左侧打开这两张图';
        return false;
      }
    }
    if (!a || !b) {
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
        grayPng: pngA, sceneId: a.sceneId, sourceRevision: a.srcRev, pixelKey: pixelKey(a.id),
      };
      const specB: DiffImageInput = {
        id: b.id, name: b.name, sourceWidth: b.W, sourceHeight: b.H,
        thumbWidth: b.thumb.width, thumbHeight: b.thumb.height,
        grayPng: pngB, sceneId: b.sceneId, sourceRevision: b.srcRev, pixelKey: pixelKey(b.id),
      };
      const req: DiffRequest = {
        autoMask: true,
        autoMaskGrid: { width: plan.width, height: plan.height, x: scopeRect.x, y: scopeRect.y },
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
      analysisController = new AbortController();
      const res = await apiDiffAnalyze(loadSrConfig(), req, analysisController.signal);
      if (alignmentNote.value) res.warnings = [...res.warnings, alignmentNote.value];
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
      // 归因统计始终附在结果上：开关默认关闭，但打开后要立即可用，不再重算。
      attachPreviewTone(res,
        { gray: ca.gray, valid: ca.valid, w: scopeRect.w, h: scopeRect.h },
        { gray: cb.gray, valid: cb.valid, w: scopeRect.w, h: scopeRect.h },
        layers.rawMask);
      // 归因输入与结果一起落盘（压成字节）；逐像素权重等开关真正打开时再算。
      toneSource.value = previewToneSource(ca, cb, scopeRect.w, scopeRect.h);
      toneMap.value = null;
      applyResult(res, key, { w: plan.width, h: plan.height }, scopeRect);
      installLayers(layers);
      return true;
    } catch (e) {
      if (seq === requestSeq) error.value = e instanceof Error ? e.message : String(e);
      return false;
    } finally {
      if (seq === requestSeq) busy.value = false;
    }
  }

  function cancelAnalysis(): void {
    requestSeq++;
    analysisController?.abort(); analysisController = null;
    busy.value = false;
    analysisState.value = '已取消';
    cancelAutoDraw();
  }

  function smartRequest(): DiffRequest {
    const a = aRec.value!, b = bRec.value!;
    const spec = (r: ViewerRec): DiffImageInput => ({id:r.id, name:r.name, sourceWidth:r.W, sourceHeight:r.H,
      thumbWidth:r.thumb?.width ?? r.W, thumbHeight:r.thumb?.height ?? r.H,
      sceneId:r.sceneId, sourceRevision:r.srcRev, pixelKey:pixelKey(r.id)});
    return {method:method.value, sensitivity:sensitivity.value, minAreaPx:minAreaPx.value,
      threshold:advanced.value.threshold ?? undefined, openKernel:advanced.value.openKernel, closeKernel:advanced.value.closeKernel,
      blur:advanced.value.blur, ssimWindow:advanced.value.ssimWindow, ssimGaussian:advanced.value.ssimGaussian,
      smartOptions:{...smartOptions.value}, referenceSide:(a.tourIdentity?.role ?? roleOf(a)) === 'nosr' ? 'A' : 'B',
      maxEdge:ANALYSIS_MAX_EDGE, autoMask:true, image_a:spec(a), image_b:spec(b), precision:smartPrecision.value,
      compare:parallelCompare.value};
  }

  async function analyzeSmart(): Promise<boolean> {
    if (smartPairReason.value) { error.value = smartPairReason.value; return false; }
    const a = aRec.value!, b = bRec.value!;
    cancelAutoDraw();
    busy.value = true; error.value = ''; notice.value = ''; analysisState.value = '读取中';
    baseSnapshot.value = null; clearComparison();
    const seq = ++requestSeq, key = currentKey();
    const controller = new AbortController(); analysisController = controller;
    try {
      const req = smartRequest();
      const plan = commonGrid(a.W, a.H, b.W, b.H);
      const rect = scope.value === 'visible' ? visibleGridRect(plan.width, plan.height) : fullRect(plan.width, plan.height);
      if (req.precision === 'preview') {
        const ba = grayBuffer(a), bb = grayBuffer(b);
        if (!ba || !bb) throw new Error('预览像素不可用，请先打开影像');
        const ca = cropGrid(sampleGray(ba.src,ba.w,ba.h,plan.width,plan.height),rect,plan.width,plan.height);
        const cb = cropGrid(sampleGray(bb.src,bb.w,bb.h,plan.width,plan.height),rect,plan.width,plan.height);
        const range = jointRange([ca,cb]);
        req.image_a.grayPng = rgbaToPng(quantizeToRgba(ca.gray,ca.valid,range.lo,range.hi),rect.w,rect.h);
        req.image_b.grayPng = rgbaToPng(quantizeToRgba(cb.gray,cb.valid,range.lo,range.hi),rect.w,rect.h);
        req.autoMaskGrid = {width:plan.width,height:plan.height,x:rect.x,y:rect.y};
        req.compare = false;
      } else if (scope.value === 'visible') req.roi = rect;
      analysisState.value = '分析中';
      const res = await apiDiffAnalyze(loadSrConfig(),req,controller.signal);
      if (seq !== requestSeq || key !== currentKey()) return false;
      const layers = await decodeLayers(res);
      if (seq !== requestSeq || key !== currentKey()) return false;
      if (res.source === 'preview') applyResult(res,key,{w:plan.width,h:plan.height},rect);
      else applyResult(res,key);
      comparison.value = res.comparison;
      comparisonKey.value = res.comparison ? comparisonSignature() : '';
      if (res.comparison?.requestedMethod && res.comparison.requestedMethod !== res.method) {
        busy.value = false; analysisController = null;
        method.value = res.method;
        resultKey.value = currentKey();
        notice.value = '所选方法检测失败，已显示成功方法；失败原因见对照摘要。';
      }
      installLayers(layers);
      display.value = 'overlay'; opacity.value = .6; toneWeight.value = false;
      analysisState.value = '已完成';
      return true;
    } catch (e) {
      if (seq === requestSeq) { error.value = e instanceof Error ? e.message : String(e); analysisState.value = controller.signal.aborted ? '已取消' : '检测失败'; }
      return false;
    } finally { if (seq === requestSeq) { busy.value = false; analysisController = null; } }
  }

  async function showComparison(next: DiffMethod): Promise<void> {
    const entry = comparison.value?.results[next];
    if (!entry || busy.value || stale.value || comparisonKey.value !== comparisonSignature()) return;
    const seq = ++requestSeq, key=currentKey();
    const layers = await decodeLayers(entry);
    if (seq !== requestSeq || key!==currentKey() || comparisonKey.value !== comparisonSignature()) return;
    // Refinement snapshots belong to another precision/result. A cached method
    // switch must not offer to restore an unrelated earlier ROI overview.
    baseSnapshot.value = null;
    method.value = next;
    applyResult(entry,currentKey()); installLayers(layers);
  }
  async function comparisonRaster(next: DiffMethod) {
    const entry=comparison.value?.results[next], key=comparisonKey.value;
    if (!entry || !key || key!==comparisonSignature()) return null;
    const layers=await decodeLayers(entry);
    return comparisonKey.value===key && key===comparisonSignature() ? layers.raw : null;
  }

  async function refineVisible(): Promise<boolean> {
    if (!result.value || !aRec.value || !bRec.value) { error.value = '请先完成LTD'; return false; }
    const plan = commonGrid(aRec.value.W,aRec.value.H,bRec.value.W,bRec.value.H);
    const rect = visibleGridRect(plan.width,plan.height);
    const a = aRec.value;
    return refineSmartRoi({x:Math.round(rect.x*a.W/plan.width),y:Math.round(rect.y*a.H/plan.height),
      w:Math.round(rect.w*a.W/plan.width),h:Math.round(rect.h*a.H/plan.height)});
  }

  async function refineDrawnRoi(): Promise<boolean> {
    const rec=viewer.activeRec,poly=viewer.selRoi,a=aRec.value;
    if(!rec?.thumb||!poly?.length||!a||![aId.value,bId.value].includes(rec.id)){
      error.value='请在当前 PAN/NOSR 影像中框选并选中一个 ROI';return false;
    }
    const points=poly.map(([x,y])=>thumbToOrig(x,y,rec.W,rec.H,rec.thumb!.width,rec.thumb!.height));
    const xs=points.map(p=>p[0]*a.W/rec.W),ys=points.map(p=>p[1]*a.H/rec.H);
    const x=Math.floor(Math.min(...xs)),y=Math.floor(Math.min(...ys));
    return refineSmartRoi({x,y,w:Math.ceil(Math.max(...xs))-x+1,h:Math.ceil(Math.max(...ys))-y+1});
  }

  async function scanNativeVisible(): Promise<boolean> {
    if (busy.value) return false;
    if (nativeRefineReason.value) { error.value=nativeRefineReason.value; return false; }
    const a=aRec.value!,b=bRec.value!,plan=commonGrid(a.W,a.H,b.W,b.H);
    const rect=visibleGridRect(plan.width,plan.height);
    const req=smartRequest();req.method='smart';req.compare=false;req.precision='source-tiled';
    req.roi={x:Math.floor(rect.x*a.W/plan.width),y:Math.floor(rect.y*a.H/plan.height),
      w:Math.ceil(rect.w*a.W/plan.width),h:Math.ceil(rect.h*a.H/plan.height)};
    const seq=++requestSeq,key=currentKey(),controller=new AbortController();analysisController=controller;
    busy.value=true;error.value='';notice.value='';analysisState.value='分析中';
    const old=result.value;
    try{
      const res=await apiDiffAnalyze(loadSrConfig(),req,controller.signal);
      const layers=await decodeLayers(res);
      if(seq!==requestSeq||key!==currentKey())return false;
      if(old && !baseSnapshot.value) baseSnapshot.value={result:old,heat:heatLayer.value,mask:maskLayer.value,
        maskSource:rawMask.value,paramsKey:resultKey.value,rawHeat:rawHeat.value,toneSource:toneSource.value,toneMap:toneMap.value};
      applyResult(res,key);installLayers(layers);display.value='overlay';opacity.value=.6;analysisState.value='已完成';return true;
    }catch(e){if(seq===requestSeq){error.value=e instanceof Error?e.message:String(e);analysisState.value='检测失败';}return false;}
    finally{if(seq===requestSeq){busy.value=false;analysisController=null;}}
  }

  async function refineSmartRoi(roi: {x:number;y:number;w:number;h:number}): Promise<boolean> {
    if (busy.value || stale.value) return false;
    if (nativeRefineReason.value) { error.value=nativeRefineReason.value; return false; }
    const old = result.value;
    if (!old) return false;
    busy.value = true; error.value = ''; analysisState.value = '分析中';
    const seq = ++requestSeq, key = currentKey();
    const controller = new AbortController(); analysisController = controller;
    try {
      const req = smartRequest(); req.precision = 'source-roi'; req.method = 'smart'; req.compare = false; req.roi = roi;
      if (old.method==='smart' && old.source!=='preview' && old.normalization) {
        req.smartOptions={...req.smartOptions!,calibrationScale:old.normalization.scale,logRatioBaseline:old.normalization.logRatioBaseline};
      }
      const res = await apiDiffAnalyze(loadSrConfig(),req,controller.signal);
      const layers = await decodeLayers(res);
      if (seq !== requestSeq || key !== currentKey() || result.value !== old) return false;
      if (!baseSnapshot.value) baseSnapshot.value = {result:old, heat:heatLayer.value,mask:maskLayer.value,
        maskSource:rawMask.value,paramsKey:resultKey.value,rawHeat:rawHeat.value,toneSource:toneSource.value,toneMap:toneMap.value};
      applyResult(res,key); installLayers(layers); analysisState.value = '已完成'; return true;
    } catch (e) {
      if (seq === requestSeq) { error.value = e instanceof Error ? e.message : String(e); analysisState.value = '检测失败'; }
      return false;
    } finally { if (seq === requestSeq) { busy.value = false; analysisController = null; } }
  }

  /* ---------------- 延续到下一景 ---------------- */
  /** 下一景只打开了本体时，向服务端问一次同景候选，把缺的角色装成身份卡再重挑。 */
  async function fillNextSceneRoles(
    group: SceneGroup, roleA: DiffRole, roleB: DiffRole,
  ): Promise<{ a: ViewerRec; b: ViewerRec } | null> {
    const sid = group.recs.find((r) => !!r.sceneId)?.sceneId;
    if (!sid) return null;
    try {
      const known = [...new Set(group.recs
        .map((r) => r.sceneId)
        .filter((v): v is string => !!v))].slice(0, 32);
      const res = await apiSceneSiblings(loadSrConfig(), sid, undefined, true, known);
      for (const item of [...(res.tourItems ?? []), ...res.items]) {
        if (!item.id) continue;
        viewer.ensureSceneImage(item, res);
      }
    } catch {
      return null;
    }
    const next = sceneGroups().find((g) => g.key === group.key) ?? group;
    return pickRolePair(next, roleA, roleB);
  }

  /** 单景看完热力结果后「延续到下一景」：沿用当前 A / B 的角色与全部显示参数，
   *  切到左侧列表里的下一景并立即重新分析。
   *
   *  顺序是「先取下一景的候选卡 → 切景 → 落 A/B → 分析」：先进场景会让活动景
   *  watcher 触发 ensurePair，把刚按角色挑好的组合换掉，所以切景期间置
   *  `continuing` 抑制它。显示方式、不透明度、羽化、方法、灵敏度等本来就在
   *  store 里，`analyze()` 原样沿用，不需要再写一遍。 */
  async function continueToNextScene(): Promise<boolean> {
    if (!canContinueScene.value) {
      if (!busy.value && !continuing.value) error.value = continueSceneReason.value;
      return false;
    }
    continuing.value = true;
    try {
      const group = nextSceneGroup.value;
      if (!group) { error.value = '左侧列表里没有下一景'; return false; }
      const roleA = roleOf(aRec.value);
      const roleB = roleOf(bRec.value);
      if (!roleA || !roleB) { error.value = '请先完成当前景的差异分析'; return false; }
      error.value = '';
      notice.value = '';
      let pair = pickRolePair(group, roleA, roleB);
      if (!pair) pair = await fillNextSceneRoles(group, roleA, roleB);
      if (!pair) {
        error.value = '下一景缺少与当前 A / B 对应的影像（如 PAN / NOSR）';
        return false;
      }
      const refusal = analysisPairReason(pair.a, pair.b);
      if (refusal) { error.value = '下一景无法延续：' + refusal; return false; }
      await viewer.activate(pair.a.id);
      aId.value = pair.a.id;
      bId.value = pair.b.id;
      return await analyze();
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e);
      return false;
    } finally {
      continuing.value = false;
    }
  }

  /* ---------------- 精算（原始 ROI，可选） ---------------- */
  async function refineRegion(id: string): Promise<boolean> {
    if (result.value?.method === 'smart') {
      const reg = result.value.regions.find(r => r.id === id);
      if (!reg) return false;
      const [x,y,w,h] = gridRectToSource(result.value,reg.bbox);
      const margin = smartOptions.value.window * 2;
      const left=Math.max(0,Math.floor(x)-margin),top=Math.max(0,Math.floor(y)-margin);
      const right=Math.min(aRec.value!.W,Math.ceil(x+w)+margin),bottom=Math.min(aRec.value!.H,Math.ceil(y+h)+margin);
      return refineSmartRoi({x:left,y:top,w:right-left,h:bottom-top});
    }
    cancelAutoDraw();
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
        autoMask: true,
        roi,
        image_a: {
          id: a.id, name: a.name, sourceWidth: a.W, sourceHeight: a.H,
          thumbWidth: a.thumb?.width ?? a.W, thumbHeight: a.thumb?.height ?? a.H,
          sceneId: a.sceneId, sourceRevision: a.srcRev, pixelKey: pixelKey(a.id),
        },
        image_b: {
          id: b.id, name: b.name, sourceWidth: b.W, sourceHeight: b.H,
          thumbWidth: b.thumb?.width ?? b.W, thumbHeight: b.thumb?.height ?? b.H,
          sceneId: b.sceneId, sourceRevision: b.srcRev, pixelKey: pixelKey(b.id),
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
      attachThumbTone(refinedRes);
      if (!baseSnapshot.value) {
        baseSnapshot.value = {
          result: res, heat: heatLayer.value, mask: maskLayer.value,
          maskSource: rawMask.value, paramsKey: resultKey.value,
          rawHeat: rawHeat.value, toneSource: toneSource.value, toneMap: toneMap.value,
        };
      }
      // 精算结果的逐像素权重来自预览缩略图（见 refinedToneSource）。
      toneSource.value = refinedToneSource(refinedRes);
      toneMap.value = null;
      applyResult(refinedRes, key);
      installLayers(layers);
      return true;
    } catch (e) {
      if (seq === requestSeq) error.value = e instanceof Error ? e.message : String(e);
      return false;
    } finally {
      if (seq === requestSeq) busy.value = false;
    }
  }

  async function restorePreview(): Promise<void> {
    const snap = baseSnapshot.value;
    if (!snap) return;
    baseSnapshot.value = null;
    result.value = snap.result;
    resultKey.value = snap.paramsKey;
    lastWarnings.value = [...(snap.result.warnings ?? [])];
    rawHeat.value = snap.rawHeat;
    toneSource.value = snap.toneSource;
    toneMap.value = snap.toneMap;
    rawMask.value = snap.maskSource ? markRaw(snap.maskSource) : null;
    maskLayer.value = snap.mask ? markRaw(snap.mask) : null;
    selectedId.value = null;
    hoveredId.value = null;
    // 快照里的热力图/掩码可能对应旧的加权开关与羽化档位，按当前状态各重建一次。
    rebuildHeat();
    if (!heatLayer.value) heatLayer.value = snap.heat ? markRaw(snap.heat) : null;
    rebuildMask();
  }

  /* ---------------- 清除 ---------------- */
  function clearResults(): void {
    analysisController?.abort(); analysisController = null;
    clearComparison();
    busy.value = false; analysisState.value = '待分析'; savedViews = null;
    requestSeq++;                          // 作废仍在途的分析/精算响应
    result.value = null;
    resultVisible.value = true;
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
  function toggleRegionSelection(id: string) {
    if (!regions.value.some(r=>r.id===id)) return;
    selectedRegionIds.value = selectedRegionIds.value.includes(id)
      ? selectedRegionIds.value.filter(x=>x!==id) : [...selectedRegionIds.value,id];
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
    if (!savedViews) savedViews = viewer.panes.filter(p=>p.rec).map(p=>({side:p.side,id:p.rec!.id,view:{...p.view}}));
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
    if (res?.method === 'smart' && reg?.maskEligible !== true) {
      error.value = '该候选尚未完成原始 DN 精检或轮廓不可靠，请先精检再加入修复掩码';
      return false;
    }
    if (res?.method === 'smart') { void autoDraw([id]); return true; }
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
    if (autoDrawing.value) return null;
    if (!resultVisible.value || !res || !rec?.thumb) return null;
    if (stale.value && res.method === 'smart') return null;
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

  /* 图层、显隐或显示方式变化 → 主画布与鹰眼同步重绘；不触发分析或 Camera 写入。 */
  watch([resultVisible, opacity, display, heatLayer, maskLayer, result, autoDrawing], () => { viewer.renderTick++; });
  /* 羽化只改显示 alpha：从精确二值掩码重建，不重算、不改区域统计。 */
  watch(feather, () => { rebuildMask(); viewer.renderTick++; });
  /* 逐像素加权只改热力图着色：从原始差异字节重建，不改差异数值与候选资格。
     第一次开启时才计算滑动窗口权重图，之后反复开关直接用缓存。 */
  watch(toneWeight, () => {
    if (!rawHeat.value) return;
    rebuildHeat();
    viewer.renderTick++;
  });
  watch(displayThreshold, () => { rebuildHeat(); viewer.renderTick++; });
  watch(selectedId, () => { if(result.value?.method==='smart'){rebuildHeat();viewer.renderTick++;} });
  watch(boundariesVisible, () => { viewer.renderTick++; });
  watch([aId,bId,method], () => {
    if (busy.value) { cancelAnalysis(); notice.value = '影像或方法已变更，旧结果已丢弃；请重新分析。'; }
  }, {flush:'sync'});
  watch(currentKey, () => {
    if (busy.value && analysisController && (method.value === 'smart' || parallelCompare.value)) {
      cancelAnalysis();
      notice.value = '检测参数或源版本已变更，旧请求已取消；请重新分析。';
    }
  }, {flush:'sync'});
  watch(result, () => { selectedRegionIds.value = []; }, {flush:'sync'});
  watch(comparisonSignature, () => {
    if (comparisonKey.value && comparisonKey.value !== comparisonSignature()) clearComparison();
  }, {flush:'sync'});
  /* 左侧换景 → 下拉收口到新场景并补候选；不在新场景里的已选组合由 ensurePair 换掉。
     「延续到下一景」切景期间跳过：A/B 已按角色落好，ensurePair 只会把它换成推荐项。 */
  watch(
    () => [viewer.activeRec?.sceneId, viewer.activeRec?.sceneDir, viewer.activeRec?.lqPath],
    () => {
      if (!open.value || continuing.value) return;
      ensurePair();
      void refreshSceneImages();
    },
    { flush: 'sync' },
  );
  /* 选到还没取过像素的同景图：立即按需取图，分析 / 交替对比 / 并排预览到手即可用。 */
  watch([aId, bId], () => {
    for (const id of [aId.value, bId.value]) {
      if (id == null) continue;
      const r = viewer.recs.find((x) => x.id === id);
      if (r && !r.thumb && !r.pending && r.card) void viewer.ensureCardPixels(id);
    }
  });
  registerViewerObservation('differences', () => {
    const res=result.value;
    if(!res)return null;
    const chosen=selectedRegionIds.value.length?selectedRegionIds.value:selectedId.value?[selectedId.value]:[];
    const items=[...regions.value].sort((a,b)=>Number(chosen.includes(b.id))-Number(chosen.includes(a.id))).slice(0,res.method==='smart'?20:80);
    return {image_a:res.imageA.id,image_b:res.imageB.id,stale:stale.value,
      precision_reason:precisionReason.value,method:res.method,source:res.source,
      completed:!busy.value, algorithm_version:res.algorithmVersion, score_semantics:res.scoreSemantics,
      comparison:comparison.value ? {requested_method:comparison.value.requestedMethod,status:comparison.value.status,
        methods:comparison.value.summary.slice(0,3).map(s=>({method:s.method,status:s.status,regions:s.regions,error:s.error?.slice(0,512)}))} : undefined,
      analysis_state:analysisState.value,
      source_identity:res.sourceIdentity ? {reference_side:res.sourceIdentity.referenceSide,
        registration:res.sourceIdentity.registration,images:res.sourceIdentity.images.slice(0,2).map(i=>({
          id:i.id,scene_id:i.sceneId,revision:i.revision,role:i.role}))} : undefined,
      mask_availability:res.maskAvailability, calibration_status:res.calibrationStatus,
      total:regions.value.length,truncated:regions.value.length>items.length,selected_ids:[...chosen].slice(0,80),
      bbox_format:'xywh',coordinate_space:'image_a_source_pixels',
      regions:items.map(r=>({id:r.id,rank:r.rank,bbox:gridRectToSource(res,r.bbox),
        area_grid_pixels:r.areaPx,relative_area:r.relativeArea,mean_diff:r.meanDiff,max_diff:r.maxDiff,
        contour_kind:r.contourKind??'exact',holes:r.holes??0,refined:res.source==='source-roi'||!!r.refined,
        ...(r.diagnostics?{evidence:{classification:r.diagnostics.classification,variance_ratio:r.diagnostics.varianceRatioMean,
          texture_loss:r.diagnostics.textureLossMean,frequency:r.diagnostics.frequency.status,
          confidence:r.diagnostics.confidence,review_status:r.diagnostics.reviewStatus}}:{})}))};
  });

  return {
    smartOptions, smartPrecision, smartPairReason, nativeRefineReason, displayThreshold, boundariesVisible, analysisState,
    parallelCompare, comparison, comparisonView, comparisonRaster, showComparison, refineVisible, refineDrawnRoi, scanNativeVisible, cancelAnalysis,
    nextRegion: (step:number) => { const list=orderedRegions.value; if(!list.length)return;
      const index=list.findIndex(r=>r.id===selectedId.value); void locateRegion(list[(index+step+list.length)%list.length].id); },
    restoreView: () => { for(const p of savedViews??[]) if(viewer.panes.some(q=>q.side===p.side&&q.rec?.id===p.id)) viewer.setPaneView(p.side,{...p.view}); savedViews=null; },
    autoDrawing, autoPreparing, autoDrawReason, autoDraw, cancelAutoDraw, autoReviewBusy, reviewAutoRegion,
    // 面板
    open, resultVisible, opacity, openPanel, closePanel, busy, error, notice,
    method, sensitivity, scope, display, feather, minAreaPx, advanced, toneWeight,
    aId, bId, aRec, bRec, pairOptions, compatible, setPair,
    recommendedPair, ensurePair, selectPairFromActive,
    // 分析
    analyze, stale, currentKey, resultKey,
    showContinueScene, canContinueScene, continueSceneReason, continuing, continueToNextScene,
    // 结果
    result, regions, orderedRegions, viewerRegions, selectedRegion, selectedId, hoveredId, precisionReason, alignmentNote, flickerPairReason, selectedRegionIds, toggleRegionSelection,
    selectRegion, hoverRegion, locateRegion, addRegionToMask, openFlicker,
    clearResults, lastWarnings, refined, canRestore, refineRegion, restorePreview,
    // 显示层
    showHeat, showMask, showMarkers, layerFor, recSide,
    heatLayer, maskLayer, rebuildMask,
  };
});
