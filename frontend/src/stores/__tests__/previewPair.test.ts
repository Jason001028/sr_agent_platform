// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { markRaw } from 'vue';
vi.mock('../../router/index.js', () => ({ default: {} }));
import { useViewerStore, type ViewerRec } from '../viewer';
import { precisePairReason } from '../../lib/compareCapabilities';
beforeEach(() => setActivePinia(createPinia()));
it('opens unrelated image grids and pans/zooms independently without enabling precision', async () => {
 const v=useViewerStore(); v.setCanvasSize(800,600);
 for(const id of [1,2]) { const thumb=document.createElement('canvas');thumb.width=id*100;thumb.height=100;
  v.recs.push({id,name:`local${id}`,W:thumb.width,H:100,thumb:markRaw(thumb),maskRois:[]} as unknown as ViewerRec); }
 v.activeId=1;
 expect(await v.openPreviewPair(1,2)).toBe(true);expect(v.compareMode).toBe('split');
 expect(v.splitSynchronized).toBe(false);expect(v.splitPrecisionReason).toBeTruthy();
 const left={...v.view};v.setActiveSide('B');const right={...v.view};v.onPan(10,20);
 expect(v.panes[0].view).toEqual(left);expect(v.view.ox).toBe(right.ox+10);
 v.onWheel(100,100,1.2);expect(v.view.scale).toBe(right.scale);
 expect(v.panes[0].view.scale).toBeCloseTo(left.scale*1.2);
 expect(v.selectTourPair(1,2)).toBe(false);expect(v.openComparePair(1,2)).toBe(false);
});
it('accepts a PAN body / NOSR pair even when rounded preview sizes differ by a pixel', () => {
 const v=useViewerStore(); v.setCanvasSize(800,600);
 const mk=(id:number,w:number,h:number,role:'pan'|'nosr')=>{
  const thumb=document.createElement('canvas');thumb.width=Math.round(w/4);thumb.height=Math.round(h/4);
  return {id,name:role==='pan'?'PAN.tif':'scene_NOSR.tif',W:w,H:h,srcRev:'v1',sceneDir:'/scenes/s1',
   stageKind:role==='pan'?'input':'nosr',thumb:markRaw(thumb),maskRois:[],
   tourIdentity:{id,sequence:'s1',role,width:w,height:h,sourceRevision:'v1'}} as unknown as ViewerRec;
 };
 // 生产实测 SC 本体/NOSR ≈ 1.96；预览各自取整后宽高比仍一致。
 v.recs.push(mk(1,30831,30948,'pan'),mk(2,15768,15823,'nosr'));v.activeId=1;
 expect(v.selectTourPair(1,2)).toBe(true);
});
it('requires current source revisions and accepts strict evidence in either order', () => {
 const a={id:1,srcRev:'v1',tourIdentity:{id:1,sequence:'s',role:'pan' as const,width:100,height:100,sourceRevision:'v1'}};
 const b={id:2,srcRev:'v2',tourIdentity:{id:2,sequence:'s',role:'nosr' as const,width:200,height:200,sourceRevision:'v2'}};
 expect(precisePairReason(a,b)).toBeNull();expect(precisePairReason(b,a)).toBeNull();
 expect(precisePairReason(a,{...b,srcRev:'changed'})).toBeTruthy();
 expect(precisePairReason(a,{...b,tourIdentity:{...b.tourIdentity,sequence:'other'}})).toBeTruthy();
});
