export const MSS_WATER_VERSION = 'mss-ndwi.v3';

export interface WaterReady {
  status: 'ready';
  version: typeof MSS_WATER_VERSION;
  usage: 'viewer_preview_only';
  id: string;
  profile: { threshold: number };
  sources: { pan: { preview_revision: string } };
  alignment: { status: 'unaligned'; detail: string }
    | { status: 'aligned'; width: number; height: number; method: 'exact_embedded_geotiff_footprint' };
}

export function waterOverlayMatches(result: WaterReady, sceneId: string, rec: {
  sceneId: string | null; srcRev?: string | null; route: string | null; W: number; H: number;
}): boolean {
  return result.version === MSS_WATER_VERSION && result.usage === 'viewer_preview_only' && result.alignment.status === 'aligned'
    && result.alignment.method === 'exact_embedded_geotiff_footprint'
    && rec.sceneId === sceneId && rec.route === 'jpg' && !!rec.srcRev
    && rec.srcRev === result.sources.pan.preview_revision
    && rec.W === result.alignment.width && rec.H === result.alignment.height;
}
