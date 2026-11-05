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
it('requires current source revisions and accepts strict evidence in either order', () => {
 const a={id:1,srcRev:'v1',tourIdentity:{id:1,sequence:'s',role:'pan' as const,width:100,height:100,sourceRevision:'v1'}};
 const b={id:2,srcRev:'v2',tourIdentity:{id:2,sequence:'s',role:'nosr' as const,width:200,height:200,sourceRevision:'v2'}};
 expect(precisePairReason(a,b)).toBeNull();expect(precisePairReason(b,a)).toBeNull();
 expect(precisePairReason(a,{...b,srcRev:'changed'})).toBeTruthy();
 expect(precisePairReason(a,{...b,tourIdentity:{...b.tourIdentity,sequence:'other'}})).toBeTruthy();
});
