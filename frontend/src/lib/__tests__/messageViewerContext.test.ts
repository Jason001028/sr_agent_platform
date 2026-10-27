import { describe, it, expect } from 'vitest';
import { snapshotViewer, captureViewerContext } from '../viewerContext.js';
import type { ViewerRec } from '../../stores/viewer.js';

describe('message Viewer observation', () => {
  it('copies metadata and distinguishes active image, checkboxes and panes', () => {
    const a = { id: 1, name: 'A.tif', W: 100, H: 200, nbands: 1, probe: { bits: 16 }, thumb: null } as ViewerRec;
    const b = { ...a, id: 2, name: 'B.tif', thumb: { width: 50, height: 50 } } as ViewerRec;
    const viewer = { activeRec: a, selectedIds: [2], panes: [{ side: 'A', rec: b }] };
    const frozen = snapshotViewer(viewer, 0);
    a.name = 'changed'; viewer.selectedIds.push(3);
    expect(frozen.current?.name).toBe('A.tif');
    expect(frozen.displayed[0].image?.name).toBe('B.tif');
    expect(frozen.selected_ids).toEqual([2]);
    expect(frozen.attachment_count).toBe(0);
    expect(JSON.stringify(frozen)).not.toContain('thumb');
  });
  it('standalone chat has explicit absent current image', () => {
    expect(captureViewerContext(0).current).toBeNull();
  });
});
