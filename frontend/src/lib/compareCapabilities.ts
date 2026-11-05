import { tourImageFromEvidence, tourPairRefusal } from './tourCompare';
import type { TourImage } from './tourCompare';

/** Viewing needs no registration. Spatial operations require current server evidence. */
export function precisePairReason(a?: { id: number; srcRev?: string | null; tourIdentity?: TourImage } | null,
  b?: { id: number; srcRev?: string | null; tourIdentity?: TourImage } | null): string | null {
  if (!a || !b || a.id === b.id) return '请选择两张不同影像';
  if (!a.tourIdentity || !b.tourIdentity) return '缺少可信配对证据';
  if (!a.srcRev || !b.srcRev) return '缺少影像源版本';
  const x = tourImageFromEvidence(a.id, a.tourIdentity, a.srcRev);
  const y = tourImageFromEvidence(b.id, b.tourIdentity, b.srcRev);
  return x.role === 'pan' ? tourPairRefusal(x, y) : tourPairRefusal(y, x);
}
