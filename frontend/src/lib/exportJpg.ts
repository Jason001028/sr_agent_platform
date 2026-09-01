/**
 * exportJpg.ts — JPG 中间产物导出编排（tif-viewer.html 直译）
 * ------------------------------------------------------------------
 * 移植不重写：utifExportCollect / chunkedExport / collectForExport / canvasToJpegBlob /
 * exportToJpg 逐函数直译 HTML（941-1049 行）。与 Vue 的接线差异（唯一改动）：
 *   - 数据源：rec.file → Source 抽象 + Blob（UTIF 需整文件 arrayBuffer）。
 *   - CanvasKit 注入（Node 测试覆盖 collectForExport 稀疏/分块路径；UTIF/canvas 走浏览器 e2e）。
 *   - stretchMode / exportCap / saver 由调用方传入（HTML 用全局 curStretch / rec._exportCap / getSaver）。
 */
import UTIF from '../vendor/utif.js';
import {
  EXPORT_BUDGET,
  SAFE,
  JPG_QUALITY,
  stretchRgba,
  getSamplePlan,
  chunkedCollect,
  computeStats,
  buildThumb,
  parseStrips,
  sparseCollect,
  isSparseCandidate,
  planExport,
} from './tifDecode.js';
import type { CanvasKit, KitCanvas, BandStats, ExportPlan, ProbeInfo, StretchMode } from './tifDecode.js';
import type { Source } from './source.js';
import { getSaver, dateDirName } from './saver.js';
import type { Saver } from './saver.js';

/** collectForExport / exportToJpg 所需的 rec 最小字段（store 的 ViewerRec 满足） */
export interface ExportRecLike {
  /** UTIF 路径需整文件 arrayBuffer + name（JPG 基底名）；store 存的是真实 File */
  file: File;
  /** 缺属性时 collectForExport 内部会 reject（运行时守卫，类型上可为 null） */
  probe: ProbeInfo | null;
  exportCap?: number;
}

export interface ExportCollectResult {
  src: Float32Array;
  sw: number;
  sh: number;
  nb: number;
  stats: BandStats[];
  invert: boolean;
  W: number;
  H: number;
  plan: ExportPlan;
}

/* ---------------- 小 8bit 图：UTIF 全量重解 → 直接上画布（预算内）或 buildThumb 到目标边 ---------------- */
export function utifExportCollect(file: Blob, plan: ExportPlan, kit: CanvasKit): Promise<ExportCollectResult> {
  return file.arrayBuffer().then((buf) => {
    const ifds = UTIF.decode(buf);
    if (!ifds || !ifds.length) throw new Error('无法解析 tif 结构');
    UTIF.decodeImage(buf, ifds[0]);
    const rgba = UTIF.toRGBA8(ifds[0]);
    const W = ifds[0].width, H = ifds[0].height;
    let cv: KitCanvas;
    let sw: number, sh: number;
    if (W * H * 16 <= EXPORT_BUDGET) {
      cv = kit.createCanvas(W, H);
      cv.getContext('2d')?.putImageData(kit.createImageData(rgba, W, H), 0, 0);
      sw = W; sh = H;
    } else {
      cv = buildThumb(rgba, W, H, kit, plan.longEdge, plan.longEdge);
      sw = cv.width; sh = cv.height;
    }
    const d = (cv as unknown as HTMLCanvasElement).getContext('2d')!.getImageData(0, 0, sw, sh).data;
    const n = sw * sh;
    const src = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      src[i * 3] = d[i * 4];
      src[i * 3 + 1] = d[i * 4 + 1];
      src[i * 3 + 2] = d[i * 4 + 2];
    }
    return {
      src, sw, sh, nb: 3,
      stats: computeStats(src, sw, sh, 3, 0, 255), invert: false,
      W, H, plan,
    };
  });
}

/* ---------------- geotiff 分块导出（大图路径；与预览同一分块机制，仅目标分辨率不同） ---------------- */
export function chunkedExport(
  source: Source, probe: ProbeInfo, plan: ExportPlan, onProgress?: (f: number) => void,
): Promise<ExportCollectResult> {
  const plan2 = getSamplePlan(probe.image);
  return chunkedCollect(probe.image, probe.W, probe.H, plan2, plan.scale, plan.pw, plan.ph, onProgress)
    .then((src) => {
      return {
        src, sw: plan.pw, sh: plan.ph, nb: plan2.comps,
        stats: computeStats(src, plan.pw, plan.ph, plan2.comps),
        invert: plan2.invert, W: probe.W, H: probe.H, plan,
      };
    });
}

/* ---------------- 与预览相同路由：小8bit→UTIF；无压缩条带大图→稀疏；其余→分块 ---------------- */
export function collectForExport(
  rec: ExportRecLike, source: Source, kit: CanvasKit, onProgress?: (f: number) => void,
): Promise<ExportCollectResult> {
  const probe = rec.probe;
  if (!probe || !probe.image) return Promise.reject(new Error('缺文件属性，无法导出'));
  const W = probe.W, H = probe.H;
  const ph = probe.photometric;
  const goodPh = ph == null || ph === 0 || ph === 1 || ph === 2 || ph === 3 || ph === 5;
  const small8 = probe.sampleFormat === 1 && probe.bits === 8 && probe.spp <= 4 && goodPh &&
                 W * H * 4 <= SAFE;   // UTIF 全量 rgba 需一次性分配
  if (small8) {
    const p3 = planExport(W, H, 3, rec.exportCap);
    if (!p3) return Promise.reject(new Error('导出尺寸计算失败'));
    return utifExportCollect(rec.file, p3, kit);
  }
  const nb = probe.spp >= 3 ? 3 : 1;
  const plan = planExport(W, H, nb, rec.exportCap);
  if (!plan) return Promise.reject(new Error('导出尺寸计算失败'));
  if (isSparseCandidate(probe)) {
    return parseStrips(source).then((sp) => {
      if (sp && sp.ok) {
        return sparseCollect(source, probe, sp, plan.longEdge).then((r) => {
          return { src: r.src, sw: r.sw, sh: r.sh, nb: r.nb, stats: r.stats,
                   invert: r.invert, W: r.W, H: r.H, plan } as ExportCollectResult;
        });
      }
      return chunkedExport(source, probe, plan, onProgress);
    });
  }
  return chunkedExport(source, probe, plan, onProgress);
}

/* ---------------- RGBA → JPEG Blob（HTML canvasToJpegBlob 直译） ---------------- */
export function canvasToJpegBlob(rgba: Uint8ClampedArray, sw: number, sh: number, kit: CanvasKit): Promise<Blob> {
  return new Promise((resolve, reject) => {
    try {
      const cv = kit.createCanvas(sw, sh) as HTMLCanvasElement;
      cv.getContext('2d')!.putImageData(kit.createImageData(rgba, sw, sh) as ImageData, 0, 0);
      cv.toBlob(
        (b) => (b ? resolve(b) : reject(new Error('toBlob 返回空（画布过大或浏览器限制）'))),
        'image/jpeg',
        JPG_QUALITY,
      );
    } catch (e) { reject(e); }
  });
}

export interface ExportJobResult {
  name: string;
  dims: string;
  ms: number;
  plan: ExportPlan;
}

/* ---------------- 导出一条完整流水：collect → 拉伸烘焙 → blob → 落盘 → 追加日志 ---------------- */
export function exportToJpg(
  rec: ExportRecLike, source: Source, kit: CanvasKit,
  stretchMode: StretchMode, saver: Saver | null, onProgress?: (f: number) => void,
): Promise<ExportJobResult> {
  const t0 = Date.now();
  return collectForExport(rec, source, kit, onProgress).then((c) => {
    const rgba = stretchRgba(c.src, c.sw, c.sh, c.nb, c.stats, stretchMode, c.invert);
    (c as { src: Float32Array | null }).src = null;   // 尽早释放 Float32
    return canvasToJpegBlob(rgba, c.sw, c.sh, kit).then((blob) => {
      const s = saver || getSaver();
      if (!s) throw new Error('输出目录未授权');
      const dateDir = dateDirName();
      const base = rec.file.name.replace(/\.tiff?$/i, '') + '.jpg';
      return s.uniqueJpgName(dateDir, base).then((name) => {
        return s.writeJpg(dateDir, name, blob).then(() => {
          const entry = {
            fileName: rec.file.name,
            fileSize: rec.file.size,
            W: c.W, H: c.H,
            readAt: new Date().toISOString(),
            jpg: name,
            exportMaxDim: c.plan.longEdge,
            stretch: stretchMode,
            quality: JPG_QUALITY,
            dims: [c.sw, c.sh],
          };
          return s.appendLog(dateDir, entry).then(() => {
            return { name, dims: c.sw + '×' + c.sh, ms: Date.now() - t0, plan: c.plan };
          });
        });
      });
    });
  });
}
