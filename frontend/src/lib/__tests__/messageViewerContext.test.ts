import { describe, it, expect } from 'vitest';
import { snapshotViewer, snapshotWorkspace, captureViewerContext } from '../viewerContext.js';
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
    expect(captureViewerContext(0).workspace.items).toEqual([]);
  });

  it('workspace carries opaque scene ids only; local drags are counted, never sent', () => {
    const a = { id: 1, name: 'A.tif', sceneId: 'sid-a' } as ViewerRec;
    const b = { id: 2, name: 'B.tif', sceneId: 'sid-b' } as ViewerRec;
    const local = { id: 3, name: 'C.png', sceneId: null } as ViewerRec;
    const ws = snapshotWorkspace([a, b, { ...a, id: 4, name: 'A 产物' }, local], 7);
    expect(ws.version).toBe(7);
    expect(ws.items).toEqual([{ scene_id: 'sid-a', name: 'A.tif' },
                              { scene_id: 'sid-b', name: 'B.tif' }]);
    expect(ws.total).toBe(2);         // 按场景 id 去重后的景数
    expect(ws.local_only).toBe(1);    // 本地拖入的图不参与盘阵检索
    expect(ws.truncated).toBe(false);
  });

  it('workspace is bounded to 200 items and says it truncated', () => {
    const recs = Array.from({ length: 205 }, (_, i) => (
      { id: i, name: `S${i}.tif`, sceneId: `sid-${i}` } as ViewerRec));
    const ws = snapshotWorkspace(recs, 0);
    expect(ws.items).toHaveLength(200);
    expect(ws.total).toBe(205);
    expect(ws.truncated).toBe(true);
  });
});
