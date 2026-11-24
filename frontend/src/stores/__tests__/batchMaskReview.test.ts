import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { reactive } from 'vue';
const m=vi.hoisted(()=>({viewer:null as any,batch:null as any,diff:null as any,items:vi.fn(),review:vi.fn(),siblings:vi.fn(),overlay:vi.fn()}));
vi.mock('../viewer',()=>({useViewerStore:()=>m.viewer}));
vi.mock('../batchGoal',()=>({useBatchGoalStore:()=>m.batch}));
vi.mock('../diff',()=>({useDiffStore:()=>m.diff,colorizeHeat:vi.fn()}));
vi.mock('../../lib/api',()=>({apiBatchItems:(...a:any[])=>m.items(...a),apiBatchReview:(...a:any[])=>m.review(...a),apiSceneSiblings:(...a:any[])=>m.siblings(...a)}));
vi.mock('../../lib/spatial',()=>({imageRef:()=>({image_key:'pan-v1'}),resolveSceneIdentity:async()=>{}}));
vi.mock('../../lib/batchOverlay',()=>({loadBatchOverlay:(...a:any[])=>m.overlay(...a),overlayMatchesImage:()=>true}));
import {useBatchMaskReviewStore} from '../batchMaskReview';
const item=()=>({item_id:'one',version:5,status:'MASK_READY',checkpoint:{review_required:true,target_image:{path:'/scene/PAN.tif'}}});
beforeEach(()=>{
 setActivePinia(createPinia());vi.clearAllMocks();vi.stubGlobal('requestAnimationFrame',(cb:()=>void)=>{cb();return 1;});
 const a={id:1,sceneId:'pan',W:128,H:128,thumb:{width:32,height:32}},b={id:2,sceneId:'nosr',W:64,H:64,thumb:{width:16,height:16}};
 m.viewer=reactive({activeRec:a,recs:[a,b],drawMode:false,userIntentSeq:0,compareMode:'split',split:true,
  panes:[a,b].map((rec,i)=>({side:i?'B':'A',rec,rect:{w:400,h:500},view:{scale:1,ox:0,oy:0}})),
  ensureSceneImage:vi.fn(()=>2),ensureCardPixels:vi.fn(async()=>true),openPreviewPair:vi.fn(async()=>true),
  setPaneView:vi.fn(),stopViewAnim:vi.fn(),openComparePair:vi.fn(()=>true),flickerStart:vi.fn(),setTourCamera:vi.fn(),
  withSilentIntent:(fn:()=>unknown)=>fn(),setCompareMode:vi.fn()});
 m.batch=reactive({goalId:'goal',setFollow:vi.fn(),openItem:vi.fn(async()=>''),refreshSnapshot:vi.fn(async()=>{})});
 m.diff=reactive({result:{imageA:{id:1},imageB:{id:2}},stale:false,heatLayer:{width:128,height:128}});
 m.items.mockResolvedValue({items:[item()]});m.siblings.mockResolvedValue({items:[{kind:'nosr',exists:true}]});
 m.overlay.mockResolvedValue({status:'ready',doc:{source:{width:128,height:128},candidates:[
  {candidate_id:'r1',bbox:[20,20,40,40]},{candidate_id:'r2',bbox:[70,70,90,90]}]}});
});
afterEach(()=>vi.unstubAllGlobals());
it('opens the PAN/NOSR pair, focuses the first ROI and reuses the existing heat without changing masks',async()=>{
 const r=useBatchMaskReviewStore();await r.open();
 expect(r.ready).toBe(true);expect(r.regionId).toBe('r1');expect(r.heat).toBe(m.diff.heatLayer);
 expect(m.viewer.openPreviewPair).toHaveBeenCalledWith(1,2);expect(m.viewer.splitLinked).toBe(true);
 expect(m.viewer.setPaneView.mock.calls[0][1].scale*32).toBeCloseTo(m.viewer.setPaneView.mock.calls[1][1].scale*16);
 expect(m.review).not.toHaveBeenCalled();
});
it('persists the exact item version and advances to the next pending ROI only after the response',async()=>{
 const r=useBatchMaskReviewStore();await r.open();
 m.review.mockResolvedValue({...item(),version:6,checkpoint:{...item().checkpoint,region_reviews:{r1:'approve'}}});
 await r.decide('approve');
 expect(m.review).toHaveBeenCalledWith('goal',expect.objectContaining({version:5}),'approve','r1');
 expect(r.regionId).toBe('r2');expect(r.item?.version).toBe(6);
});
it('does not submit decisions after the displayed pair changes',async()=>{
 const r=useBatchMaskReviewStore();await r.open();m.viewer.panes[1].rec={id:3};
 await r.decide('approve');expect(m.review).not.toHaveBeenCalled();
});
it('previews the same split and heat without pausing the batch follow',async()=>{
 const r=useBatchMaskReviewStore();
 const ok=await r.preview(item() as never);
 expect(ok).toBe(true);expect(r.previewing).toBe(true);
 expect(m.batch.setFollow).not.toHaveBeenCalled();
 expect(m.viewer.openPreviewPair).toHaveBeenCalledWith(1,2,true,expect.any(Function));
 const stillWanted=m.viewer.openPreviewPair.mock.calls[0][3];
 expect(stillWanted()).toBe(true);
 r.close();
 expect(stillWanted()).toBe(false);
 expect(r.previewing).toBe(false);expect(m.viewer.setCompareMode).toHaveBeenCalledWith('off',true);
});
it('discards a pending open after switching batches',async()=>{
 let resolve!:(value:any)=>void;m.items.mockReturnValueOnce(new Promise(r=>{resolve=r;}));
 const r=useBatchMaskReviewStore(),opened=r.open();m.batch.goalId='another';
 resolve({items:[item()]});await opened;
 expect(m.batch.openItem).not.toHaveBeenCalled();expect(r.pair).toBeNull();
});
