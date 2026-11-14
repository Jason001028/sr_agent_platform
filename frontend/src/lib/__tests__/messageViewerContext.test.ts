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
  it('projects ROI coordinates and mask draft independently from the Camera', () => {
    const r={id:1,name:'A',W:1000,H:2000,thumb:{width:100,height:200},maskRois:[[[1,2],[5,2],[5,6]]],
      srcRev:'v1',token:3,spatialIdentity:'trusted',spatialIdentityToken:3} as ViewerRec;
    const snapshot=snapshotViewer({activeRec:r,recs:[r],selectedIds:[1],panes:[{side:'B',rec:r}],selRoi:0,
      drawMode:true,drawTool:'polygon',pendingPts:[[10,20],[30,40]]},0);
    expect(snapshot.roi?.bbox).toEqual([10,20,50,60]);
    expect(snapshot.masks).toMatchObject({count:1,editing:true,draft_vertex_count:2});
    expect(snapshot.selected_images[0]?.image_key).toBe('trusted');
    r.maskRois![0][0][0]=99;
    expect(snapshot.roi?.points[0]).toEqual([10,20]);
  });

  it('workspace keeps local metadata separate from scene-library ids', () => {
    const a = { id: 1, name: 'A.tif', sceneId: 'sid-a' } as ViewerRec;
    const b = { id: 2, name: 'B.tif', sceneId: 'sid-b' } as ViewerRec;
    const local = { id: 3, name: 'C.png', sceneId: null } as ViewerRec;
    const ws = snapshotWorkspace([a, b, { ...a, id: 4, name: 'A 产物' }, local], 7);
    expect(ws.version).toBe(7);
    expect(ws.items).toEqual([{ scene_id: 'sid-a', name: 'A.tif' },
                              { scene_id: 'sid-b', name: 'B.tif' }]);
    expect(ws.total).toBe(2);         // 按场景 id 去重后的景数
    expect(ws.local_only).toBe(1);    // 本地拖入的图不参与盘阵检索
    expect(ws.resources).toEqual([expect.objectContaining({id:3,name:'C.png',asset_id:null})]);
    expect(ws.truncated).toBe(false);
  });

  it('freezes uploaded identity without exposing its disk path',()=>{
    const r={id:4,name:'upload.tif',W:10,H:20,token:1,file:{},sceneId:null,
      previewAsset:{id:'asset-4',serverPath:'D:/private/upload.tif'}} as unknown as ViewerRec;
    const ws=snapshotWorkspace([r],1);
    expect(ws.resources?.[0].asset_id).toBe('asset-4');
    expect(ws.resources?.[0].image_key).toMatch(/^local-session:/);
    expect(JSON.stringify(ws)).not.toContain('D:/private');
  });
  it('bounds local resources without calling the scene list truncated', () => {
    const scenes = Array.from({ length: 20 }, (_, i) => (
      { id: i, name: `S${i}.tif`, sceneId: `sid-${i}` } as ViewerRec));
    const locals = Array.from({ length: 205 }, (_, i) => (
      { id: 1000 + i, name: `L${i}.png`, sceneId: null } as ViewerRec));
    const ws = snapshotWorkspace([...scenes, ...locals], 0);
    expect(ws.items).toHaveLength(20);
    expect(ws.truncated).toBe(false);
    expect(ws.resources_truncated).toBe(true);
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
