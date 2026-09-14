/**
 * decode.ts — 解码编排（tif-viewer.html activate/decodeUtif/chunkedFull/稀疏分支 直译）
 * ------------------------------------------------------------------
 * 移植不重写：needGeo 决策、UTIF 全量解码、分块读取、稀疏条带、分配失败回退，
 * 全部逐行直译 HTML（757-878 行）。与 Vue 的接线差异（唯一改动）：
 *   - rec 变更 / paintStretch / fit / render / 遮罩 DOM → 由 store 编排
 *     （本文件经 DecodeCallbacks 回调进度/遮罩文案，store 决定是否刷新 UI）。
 *   - 数据源：稀疏/分块走 Source 抽象，UTIF 走 Blob（整文件 arrayBuffer）。
 *
 * 产出统一 DecodedRec，store 填充 rec 字段后自行 paintStretch。
 */
import UTIF from '../vendor/utif.js';
import {
  PREVIEW_MAX,
  SPARSE_PREVIEW_MAX,
  SAFE,
  probeImage,
  getSamplePlan,
  chunkedCollect,
  computeStats,
  buildThumb,
  parseStrips,
  sparseCollect,
  isSparseCandidate,
} from './tifDecode.js';
import type { CanvasKit, KitCanvas, BandStats, ProbeInfo } from './tifDecode.js';
import type { Source } from './source.js';

export interface DecodeCallbacks {
  /** 遮罩文案（HTML showMask）；store 负责真正展示/隐藏 */
  onOverlay?: (title: string, sub: string, bar: boolean) => void;
  /** 进度 0~1（HTML updateProgressUI）；store 负责 `rec===activeRec` 守卫后刷新 */
  onProgress?: (f: number) => void;
}

export interface DecodedRec {
  W: number;
  H: number;
  thumb: KitCanvas;
  src: Float32Array;
  srcw: number;
  srch: number;
  nbands: number;
  invert: boolean;
  stats: BandStats[] | null;
  route: 'utif' | 'sparse' | 'chunked';
  /** 解码耗时 ms（status 文案用） */
  ms: number;
  /** 稀疏条带抽样摘要（status 文案用，如「抽样 12 条条带，读 3.2 MB / 1.1 GB」） */
  sparseInfo?: string;
}

/** 让出主线程一帧（HTML `tick()`，30ms） */
function tick(): Promise<void> {
  return new Promise((r) => setTimeout(r, 30));
}

/** 格式化字节（HTML fmtBytes 直译） */
function fmtBytes(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

/** 格式化耗时（HTML fmtTime 直译） */
function fmtTime(ms: number): string {
  if (ms >= 60000) return (ms / 60000).toFixed(1) + ' 分钟';
  if (ms >= 1000) return (ms / 1000).toFixed(1) + ' 秒';
  return ms + ' 毫秒';
}

/** HTML activate() 的 needGeo 决策：只有「小的 8bit 无符号整数」走 UTIF 快速路径。
 *  修正（本阶段唯一的行为偏差，附理由）：BigTIFF（probe.big）即使 8bit 也强制走 geotiff
 *  分块——vendored UTIF 不能解 BigTIFF（decode 后 width/height 缺失），HTML 原版在同一条
 *  件下会产出 0×0 缩略图；bigtiff_strips fixture 是迁移回归新增，必须正确解码。 */
export function needGeo(probe: ProbeInfo): boolean {
  const decodeBytes = probe.W * probe.H * probe.spp * (probe.bits / 8);
  const rgbaBytes = probe.W * probe.H * 4;
  const ph = probe.photometric;
  const goodPh = ph == null || ph === 0 || ph === 1 || ph === 2 || ph === 3 || ph === 5;
  const small8 = probe.sampleFormat === 1 && probe.bits === 8 && probe.spp <= 4 && goodPh && !probe.big;
  return !small8 || decodeBytes > SAFE || rgbaBytes > SAFE;
}

/* ---------------- UTIF 全量解码（小 8bit 图；分配失败回退在 decodeOne） ----------------
   HTML decodeUtif 直译：读整文件 → UTIF.decode → decodeImage → toRGBA8 → buildThumb(≤2048)
   → 8bit 原像素即自然值（0~255）存 src → 按固定 0~255 统计（线性=原样）。 */
export async function decodeUtif(file: Blob, kit: CanvasKit, cb?: DecodeCallbacks): Promise<DecodedRec> {
  const t0 = Date.now();
  cb?.onOverlay?.('正在读取文件…', '「' + (file as unknown as { name?: string }).name + '」 ' + fmtBytes(file.size), false);
  await tick();
  const buf = await file.arrayBuffer();
  cb?.onOverlay?.(
    '正在完整解码…',
    '已读入 ' + fmtBytes(buf.byteLength) + '。这一步会一次性解码全部像素，' +
      '大图可能需要几分钟，期间页面短暂无响应属正常。',
    false,
  );
  await tick();
  const ifds = UTIF.decode(buf);
  if (!ifds || !ifds.length) throw new Error('无法解析 tif 结构');
  UTIF.decodeImage(buf, ifds[0]);
  const rgba = UTIF.toRGBA8(ifds[0]);
  const W = ifds[0].width, H = ifds[0].height;
  const thumb = buildThumb(rgba, W, H, kit);
  const d = (thumb as unknown as HTMLCanvasElement).getContext('2d')!.getImageData(0, 0, thumb.width, thumb.height).data;
  const sw = thumb.width, sh = thumb.height, n = sw * sh;
  const src = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    src[i * 3] = d[i * 4];
    src[i * 3 + 1] = d[i * 4 + 1];
    src[i * 3 + 2] = d[i * 4 + 2];
  }
  return {
    W, H, thumb, src, srcw: sw, srch: sh, nbands: 3, invert: false,
    stats: computeStats(src, sw, sh, 3, 0, 255),
    route: 'utif', ms: Date.now() - t0,
  };
}

/* ---------------- geotiff 分块整图（大图路径，有进度条） ---------------- */
export function chunkedDecode(source: Source, probe: ProbeInfo, kit: CanvasKit, cb?: DecodeCallbacks): Promise<DecodedRec> {
  const W = probe.W, H = probe.H;
  const plan = getSamplePlan(probe.image);
  const ps = Math.min(1, PREVIEW_MAX / Math.max(W, H));
  const pw = Math.max(1, Math.round(W * ps)), ph = Math.max(1, Math.round(H * ps));
  const cv = kit.createCanvas(pw, ph);
  const t0 = Date.now();
  cb?.onOverlay?.('正在分块读取整图…', '', true);
  cb?.onProgress?.(0);
  return chunkedCollect(probe.image, W, H, plan, ps, pw, ph, (f) => cb?.onProgress?.(f)).then((src) => {
    return {
      W, H, thumb: cv, src, srcw: pw, srch: ph, nbands: plan.comps,
      invert: plan.invert, stats: computeStats(src, pw, ph, plan.comps),
      route: 'chunked' as const, ms: Date.now() - t0,
    };
  });
}

/* ---------------- 稀疏条带预览（无压缩+条带+单波段大图快路径，秒级） ---------------- */
export function sparseDecode(source: Source, probe: ProbeInfo, kit: CanvasKit, cb?: DecodeCallbacks): Promise<DecodedRec> {
  const t0 = Date.now();
  cb?.onOverlay?.('稀疏条带预览：只抽样读少数条带，跳过全文件…', '', true);
  cb?.onProgress?.(0);
  return parseStrips(source).then((sp) => {
    if (!sp || !sp.ok) throw new Error('稀疏条带布局解析失败');
    return sparseCollect(source, probe, sp, SPARSE_PREVIEW_MAX, (f) => cb?.onProgress?.(f)).then((r) => {
      const cv = kit.createCanvas(r.sw, r.sh);
      return {
        W: sp.W, H: sp.H, thumb: cv, src: r.src, srcw: r.sw, srch: r.sh,
        nbands: 1, invert: r.invert, stats: r.stats,
        route: 'sparse' as const, ms: Date.now() - t0,
        sparseInfo: '抽样 ' + r.stripsTouched + ' 条条带，读 ' + fmtBytes(r.bytesRead) + ' / ' + fmtBytes(source.size),
      };
    });
  });
}

/** HTML decodeGeoTiff 直译：优先稀疏条带，解析失败回退分块 */
export function decodeGeoTiff(source: Source, probe: ProbeInfo, kit: CanvasKit, cb?: DecodeCallbacks): Promise<DecodedRec> {
  const plan = getSamplePlan(probe.image);
  if (isSparseCandidate(probe)) {
    return parseStrips(source).then((sp) => {
      if (sp && sp.ok) return sparseDecode(source, probe, kit, cb);
      return chunkedDecode(source, probe, kit, cb);
    });
  }
  return chunkedDecode(source, probe, kit, cb);
}

/** 统一解码入口（HTML activate 解码分派直译）：needGeo → 分块/稀疏，否则 UTIF（失败回退分块） */
export async function decodeOne(
  source: Source, probe: ProbeInfo, file: Blob, kit: CanvasKit, cb?: DecodeCallbacks,
): Promise<DecodedRec> {
  if (needGeo(probe)) {
    return decodeGeoTiff(source, probe, kit, cb);
  }
  try {
    return await decodeUtif(file, kit, cb);
  } catch (e) {
    // 内存/分配不足（如 Array buffer allocation failed）→ 自动回退到分块读取
    if (e && /array buffer/i.test((e as Error).message)) {
      const probe2 = await probeImage(file).catch(() => null);
      if (probe2) return decodeGeoTiff(source, probe2, kit, cb);
    }
    throw e;
  }
}
