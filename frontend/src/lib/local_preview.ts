import { getPreviewTrueMaxEdge } from './previewLevel';
import type { PreviewPlan } from './previewPlan';

export const LOCAL_PREVIEW_DN_BYTES = 256 * 1024 * 1024;
export const LARGE_LOCAL_TIFF_BYTES = 1024 ** 3;

export function localTiffPreviewEdge(file: File) {
  const max = getPreviewTrueMaxEdge();
  return file.size >= LARGE_LOCAL_TIFF_BYTES ? Math.min(4096, max) : max;
}

/** Bound browser preview buffers independently of source size and selected level.
 *
 *  长边先按平台设置的「真分辨率」上限封顶（默认 32768，可调 4096/8192/16384）；
 *  256MB 的 Float32 预算仍可能更早封顶（多波段尤其明显），这是内存保护，
 *  不是档位合并 —— 状态栏会报实际长边与比例。 */
export function localPreviewSize(w: number, h: number, bands: number, div: number,
                                 maxEdge = getPreviewTrueMaxEdge()) {
  return localPreviewBudget(w, h, bands, div, maxEdge).size;
}

export function localPreviewBudget(w: number, h: number, bands: number, div: number,
                                   maxEdge = getPreviewTrueMaxEdge()) {
  const maxPixels = LOCAL_PREVIEW_DN_BYTES / (4 * Math.max(1, bands));
  const edgeScale = maxEdge / Math.max(w, h);
  const memoryScale = Math.sqrt(maxPixels / (w * h));
  const scale = Math.min(1 / div, edgeScale, memoryScale);
  const limits: PreviewPlan['limits'] = [];
  if (edgeScale < 1 / div) limits.push('long-edge');
  if (memoryScale < Math.min(1 / div, edgeScale)) limits.push('memory');
  return { size: { w: Math.max(1, Math.floor(w * scale)), h: Math.max(1, Math.floor(h * scale)) }, limits };
}

async function sampleFingerprint(file: File) {
  const length = 4096;
  const offsets = [0, Math.max(0, Math.floor((file.size - length) / 2)), Math.max(0, file.size - length)];
  const samples = await Promise.all(offsets.map(async offset => {
    const bytes = new Uint8Array(await file.slice(offset, offset + length).arrayBuffer());
    return btoa(String.fromCharCode(...bytes));
  }));
  return { name: file.name, size: file.size, samples };
}

const fingerprints = new WeakMap<File, ReturnType<typeof sampleFingerprint>>();

export function sourceFingerprint(file: File) {
  let sampled = fingerprints.get(file);
  if (!sampled) {
    sampled = sampleFingerprint(file).catch(error => {
      fingerprints.delete(file);
      throw error;
    });
    fingerprints.set(file, sampled);
  }
  return sampled;
}
