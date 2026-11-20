import { describe, expect, it } from 'vitest';
import { waterOverlayMatches, type WaterReady } from '../mssWater';

const result: WaterReady = {
  status: 'ready', version: 'mss-ndwi.v3', usage: 'viewer_preview_only', id: 'water-key', profile: { threshold: 0.3 },
  sources: { pan: { preview_revision: 'pan-rev-1' } },
  alignment: { status: 'aligned', width: 8000, height: 4000, method: 'exact_embedded_geotiff_footprint' },
};
const record = { route: 'jpg', sceneId: 'pan-A', srcRev: 'pan-rev-1', W: 8000, H: 4000 };

describe('MSS water overlay', () => {
  it('renders only against the exact trusted Viewer source', () => {
    expect(waterOverlayMatches(result, 'pan-A', record)).toBe(true);
    for (const change of [{ sceneId: 'pan-B' }, { srcRev: 'stale' }, { srcRev: null }, { route: 'geo' }, { W: 8001 }]) {
      expect(waterOverlayMatches(result, 'pan-A', { ...record, ...change })).toBe(false);
    }
  });

  it('does not treat matching source dimensions as alignment evidence', () => {
    const unaligned: WaterReady = { ...result, alignment: { status: 'unaligned', detail: '未完成空间对齐' } };
    expect(waterOverlayMatches(unaligned, 'pan-A', record)).toBe(false);
  });
});
