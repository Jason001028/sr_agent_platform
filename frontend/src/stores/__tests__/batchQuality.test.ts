import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { reactive } from 'vue';
import type { QualityDocument } from '../../lib/batchQuality';
const mocks=vi.hoisted(()=>({batch:null as any,viewer:null as any,list:vi.fn(),document:vi.fn(),decide:vi.fn()}));
vi.mock('../batchGoal',()=>({useBatchGoalStore:()=>mocks.batch}));
vi.mock('../viewer',()=>({useViewerStore:()=>mocks.viewer}));
vi.mock('../../lib/batchQuality',()=>({qualityList:(...a:any[])=>mocks.list(...a),qualityDocument:(...a:any[])=>mocks.document(...a),qualityDecide:(...a:any[])=>mocks.decide(...a)}));
import { useBatchQualityStore } from '../batchQuality';

function document(item='one'):QualityDocument{return{
  review_id:'review-'+item,goal_id:'goal',item_id:item,job_id:item==='one'?'81':'82',version:0,scene_decision:'pending',
  regions:[{region_id:'r1',decision:'pending',reason:'',reviewer:''},{region_id:'r2',decision:'pending',reason:'',reviewer:''}],
  images:[{id:'pan-'+item,srcRev:'output-v1',W:128,H:128},{id:'nosr-'+item,srcRev:'nosr-v1',W:64,H:64}] as any,
  scene_path:'/scene/'+item,binding:{mask:{width:128,height:128,sha256:'sha'},output:{image_key:'output'},nosr:{image_key:'nosr'},
    regions:[{candidate_id:'r1',kind:'bbox',bbox:[20,20,40,40],points:null},{candidate_id:'r2',kind:'bbox',bbox:[70,70,90,90],points:null}]},audit:[]};}

beforeEach(()=>{
  setActivePinia(createPinia());vi.stubGlobal('requestAnimationFrame',(cb:()=>void)=>{cb();return 1;});
  mocks.batch=reactive({goalId:'goal',snapshot:null,refreshSnapshot:vi.fn(async()=>{})});
  const v:any=reactive({recs:[] as any[],panes:[] as any[],drawMode:false,split:true,compareMode:'split',userIntentSeq:0,
    noteUserIntent(){this.userIntentSeq++;},setCtxRailOpen:vi.fn(),stopViewAnim:vi.fn(),
    setPaneView:vi.fn(),setTourCamera:vi.fn(),flickerStart:vi.fn(),openComparePair:vi.fn(()=>true),
    async loadTourImage(image:any,_siblings:any,wanted:()=>boolean){
      if(!wanted())throw new Error('cancelled');
      let r=this.recs.find((r:any)=>r.sceneId===image.id);
      if(!r){r={id:this.recs.length+1,sceneId:image.id,srcRev:image.srcRev,W:image.W,H:image.H,
        thumb:{width:image.W/4,height:image.H/4}};this.recs.push(r);}
      return r.id;
    },
    async openPreviewPair(a:number,b:number){this.panes=[a,b].map((id,i)=>({side:i?'B':'A',rect:{x:i*400,y:0,w:400,h:500},
      rec:this.recs.find((r:any)=>r.id===id),view:{scale:1,ox:0,oy:0}}));return true;},
  });mocks.viewer=v;
  mocks.list.mockReset().mockResolvedValue({reviews:[]});mocks.decide.mockReset().mockResolvedValue({});
  mocks.document.mockReset().mockImplementation(async(_goal:string,item:string)=>document(item));
});
afterEach(()=>vi.unstubAllGlobals());

describe('batch human quality UI state',()=>{
  it('opens input-bound no-candidate reviews and sends explicit whole-scene confirmation',async()=>{
    const d=document();d.job_id='';d.regions=[];d.binding={...d.binding,kind:'no-candidate',grid:{width:128,height:128},mask:undefined,regions:[]};
    mocks.document.mockResolvedValue(d);
    const q=useBatchQualityStore();await q.select('one');
    expect(q.comparisonReady).toBe(true);expect(q.doc?.binding.kind).toBe('no-candidate');
    q.reviewer='整景审核人';await q.decide('scene','approved');
    expect(mocks.decide.mock.calls[0][2]).toMatchObject({action:'scene',decision:'approved',review_id:d.review_id,reviewer:'整景审核人'});
  });
  it('opens the exact output/NOSR pair and focuses both normalized Mask regions',async()=>{
    const q=useBatchQualityStore();await q.select('one');
    expect(q.comparisonReady).toBe(true);expect(q.doc?.job_id).toBe('81');
    expect(q.regionId).toBe('r1');
    expect(mocks.viewer.loadTourImage.mock?.calls??mocks.viewer.recs.map((r:any)=>r.sceneId)).toEqual(['pan-one','nosr-one']);
    expect(mocks.viewer.setPaneView).toHaveBeenCalledTimes(2);
    const calls=mocks.viewer.setPaneView.mock.calls;
    expect(calls[0][1].scale*32).toBeCloseTo(calls[1][1].scale*16);
    q.flicker();expect(mocks.viewer.openComparePair).toHaveBeenCalledWith(1,2);
  });
  it('lands on the first pending region instead of an already approved one',async()=>{
    mocks.document.mockImplementationOnce(async(_goal:string,item:string)=>{
      const d=document(item);d.regions[0].decision='approved';return d;});
    const q=useBatchQualityStore();await q.select('one');
    expect(q.regionId).toBe('r2');
    expect(mocks.viewer.setPaneView).toHaveBeenCalledTimes(2);
  });
  it('sends explicit review identity/version/reason, and prevents confirmation on a different displayed source',async()=>{
    const q=useBatchQualityStore();await q.select('one');q.reviewer='审核人';q.reason='纹理通过';
    await q.decide('region','approved');
    expect(mocks.decide.mock.calls[0][2]).toMatchObject({review_id:'review-one',expected_version:0,region_id:'r1',reviewer:'审核人',reason:'纹理通过'});
    mocks.viewer.recs[0].srcRev='new-output';expect(q.comparisonReady).toBe(false);
    await q.decide('scene','approved');expect(mocks.decide).toHaveBeenCalledTimes(1);
  });
  it('discards a late document from a different Job selection',async()=>{
    const q=useBatchQualityStore();let release!:(d:QualityDocument)=>void;
    mocks.document.mockImplementationOnce(()=>new Promise(r=>{release=r;}));
    const old=q.select('one');await q.select('two');release(document('one'));await old;
    expect(q.doc?.job_id).toBe('82');expect(q.pair?.review).toBe('review-two');
  });
  it('does not move the camera when a newer user action interrupts preview IO',async()=>{
    const q=useBatchQualityStore();let release!:(id:number)=>void;
    mocks.viewer.loadTourImage=vi.fn(()=>new Promise(r=>{release=r;}));
    const pending=q.select('one');await Promise.resolve();
    mocks.viewer.noteUserIntent();release(1);await pending;
    expect(q.pair).toBeNull();expect(q.comparisonReady).toBe(false);
    expect(mocks.viewer.setPaneView).not.toHaveBeenCalled();
  });
});
