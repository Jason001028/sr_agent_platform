import { describe, expect, it } from 'vitest';
import { buildViewerContextSnapshot } from '../agentContext.js';
import type { ViewerContextSnap } from '../agentContext.js';

const base: ViewerContextSnap = {
  name: 'PAN.tif', sceneId: 'scene-1', W: 100, H: 80,
  dispW: 50, dispH: 40, stretch: '线性', roi: null,
};

describe('buildViewerContextSnapshot', () => {
  it('creates a versioned read-only scene snapshot', () => {
    const out = buildViewerContextSnapshot(base);
    expect(out.schema_version).toBe(1);
    expect(out.scene).toEqual({ id: 'scene-1', name: 'PAN.tif' });
    expect(out.image.width).toBe(100);
    expect(out.capabilities).toEqual({ read: true, write_original: false });
  });

  it('does not expose a local path or mutable UI object', () => {
    const out = buildViewerContextSnapshot({ ...base, sceneId: null });
    expect(out.source.kind).toBe('local');
    expect(out).not.toHaveProperty('path');
    expect(out).not.toHaveProperty('canvas');
  });
});
