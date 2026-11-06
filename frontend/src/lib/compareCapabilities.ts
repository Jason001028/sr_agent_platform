import { tourImageFromEvidence, tourPairRefusal } from './tourCompare';
import type { TourImage } from './tourCompare';
import { commonGrid } from './diffLocate';

interface PairImage { id: number; W?: number; H?: number; sceneDir?: string | null; lqPath?: string | null; srcRev?: string | null; tourIdentity?: TourImage }
/** Known scene siblings can share a proportional viewport without a tour certificate. */
export function precisePairReason(a?: PairImage | null, b?: PairImage | null): string | null {
  if (!a || !b || a.id === b.id) return '请选择两张不同影像';
  if (!a.tourIdentity || !b.tourIdentity) {
    const dir = (r: PairImage) => (r.sceneDir || r.lqPath || '').replace(/[/\\]+$/, '');
    if (dir(a) && dir(a) === dir(b) && a.W && a.H && b.W && b.H) return analysisPairReason(a as AnalysisImage, b as AnalysisImage);
    return '缺少共同场景关联；仍可进行共同网格差异分析';
  }
  if (!a.srcRev || !b.srcRev) return '缺少影像源版本';
  const x = tourImageFromEvidence(a.id, a.tourIdentity, a.srcRev);
  const y = tourImageFromEvidence(b.id, b.tourIdentity, b.srcRev);
  return x.role === 'pan' ? tourPairRefusal(x, y) : tourPairRefusal(y, x);
}

type AnalysisImage = { id: number; W: number; H: number; srcRev?: string | null; tourIdentity?: TourImage };
/** User-selected image comparison needs a usable proportional grid, not a tour certificate. */
export function analysisPairReason(a?: AnalysisImage | null, b?: AnalysisImage | null): string | null {
  if (!a || !b || a.id === b.id) return '请选择两张不同影像';
  if (![a.W, a.H, b.W, b.H].every(n => Number.isSafeInteger(n) && n > 0)) return '影像尺寸未知或无效';
  const grid = commonGrid(a.W, a.H, b.W, b.H);
  if (grid.refusal) return grid.refusal;
  const x = a.tourIdentity?.registration, y = b.tourIdentity?.registration;
  if (x && y) {
    if (x.crs !== y.crs) return '两张影像的已知坐标系不同，请先重投影';
    if ([...x.extent, ...y.extent, ...x.orientation, ...y.orientation].every(Number.isFinite)) {
      const span = Math.max(x.extent[2] - x.extent[0], x.extent[3] - x.extent[1], 1);
      const tolerance = span / Math.min(a.W, a.H, b.W, b.H); // one coarse-source pixel
      if (x.extent.some((n, i) => Math.abs(n - y.extent[i]!) > tolerance)
          || x.orientation.some((n, i) => Math.abs(n - y.orientation[i]!) > 1e-6)) return '已知覆盖范围或方向不同，请先配准';
    }
  }
  return null;
}
export function analysisPairNote(a?: AnalysisImage | null, b?: AnalysisImage | null): string {
  if (!a || !b || analysisPairReason(a, b)) return '';
  const scaling = a.W !== b.W || a.H !== b.H ? '分辨率不同，按全幅比例重采样到共同网格。' : '';
  return scaling + (!a.tourIdentity || !b.tourIdentity || precisePairReason(a, b) ? '未验证空间配准；结果为差异候选，请结合原图复核。' : '使用已知同景配对。');
}
