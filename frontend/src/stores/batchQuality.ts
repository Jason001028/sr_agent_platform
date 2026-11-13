import { computed, ref, shallowRef, watch, nextTick } from 'vue';
import { defineStore } from 'pinia';
import { useBatchGoalStore } from './batchGoal';
import { useViewerStore } from './viewer';
import { qualityList, qualityDocument, qualityDecide } from '../lib/batchQuality';
import type { QualityDocument, QualitySummary, QualityDecision } from '../lib/batchQuality';
import type { SceneSiblings } from '../lib/api';
import { spatialId } from '../lib/spatial';

export const useBatchQualityStore=defineStore('batchQuality',()=>{
  const batch=useBatchGoalStore(), viewer=useViewerStore();
  const reviews=ref<QualitySummary[]>([]), doc=shallowRef<QualityDocument|null>(null);
  const regionId=ref(''), error=ref(''), busy=ref(false), loading=ref(false);
  const reviewer=ref(''), reason=ref(''), overlay=ref<'off'|'outline'|'fill'>('fill');
  const pair=shallowRef<{output:number;nosr:number;review:string}|null>(null);
  let generation=0, listSeq=0, controller:AbortController|null=null;
  const selectedRegion=computed(()=>doc.value?.binding.regions.find(r=>r.candidate_id===regionId.value)??null);
  const comparisonReady=computed(()=>{
    const d=doc.value,p=pair.value;if(!d||!p||p.review!==d.review_id)return false;
    const records=[p.output,p.nosr].map(id=>viewer.recs.find(r=>r.id===id));
    if(records.some((r,i)=>!r?.thumb||r.srcRev!==d.images[i]?.srcRev||r.sceneId!==d.images[i]?.id||r.pending))return false;
    const ids=viewer.compareMode==='flicker'?[viewer.flickerRefRec?.id,viewer.flickerAltRec?.id]:viewer.split?viewer.panes.map(p=>p.rec?.id):[];
    return ids.includes(p.output)&&ids.includes(p.nosr)&&!viewer.drawMode;
  });
  const totals=computed(()=>reviews.value.reduce((a,r)=>({scenes:a.scenes+Number(r.status==='AWAITING_REVIEW'),
    regions:a.regions+r.counts.pending+r.counts.approved+r.counts.rejected,pending:a.pending+r.counts.pending,
    approved:a.approved+(r.current?r.counts.approved:0),rejected:a.rejected+r.counts.rejected}),
    {scenes:0,regions:0,pending:0,approved:0,rejected:0}));
  function clear(){generation++;controller?.abort();controller=null;doc.value=null;pair.value=null;regionId.value='';reviews.value=[];error.value='';loading.value=false;busy.value=false;}
  async function refresh(){
    const gid=batch.goalId,seq=++listSeq;if(!gid)return;
    try{const result=await qualityList(gid);if(batch.goalId===gid&&seq===listSeq)reviews.value=result.reviews;}
    catch(e){if(batch.goalId===gid&&seq===listSeq)error.value=String(e);}
  }
  async function select(itemId:string){
    const gid=batch.goalId;if(!gid)return;
    const gen=++generation;controller?.abort();controller=new AbortController();
    doc.value=null;pair.value=null;error.value='';loading.value=true;busy.value=false;
    try{
      const value=await qualityDocument(gid,itemId,controller.signal);
      if(gen!==generation||batch.goalId!==gid)return;
      doc.value=value;regionId.value=value.regions.find(r=>r.decision==='pending')?.region_id??value.regions[0]?.region_id??'';
      await openRegion(regionId.value);
    }catch(e){if(gen===generation){error.value=String(e);pair.value=null;}}
    finally{if(gen===generation)loading.value=false;}
  }
  function focus(){
    const d=doc.value,c=selectedRegion.value;if(!d||(!c&&d.binding.kind!=='no-candidate')||!comparisonReady.value)return;
    const {width:w,height:h}=d.binding.kind==='no-candidate'?d.binding.grid!:d.binding.mask!;
    const [x0,y0,x1,y1]=c?.bbox??[0,0,w-1,h-1],cx=(x0+x1)/2/w,cy=(y0+y1)/2/h;
    if(viewer.compareMode==='flicker'){
      const pane=viewer.panes[0],r=viewer.flickerRefRec;if(!r?.thumb||!pane)return;
      const norm=Math.min(pane.rect.w*.7/Math.max((x1-x0+1)/w,.001),pane.rect.h*.7/Math.max((y1-y0+1)/w,.001));
      viewer.setTourCamera({scale:norm/r.thumb.width,ox:pane.rect.w/2-cx*norm,oy:pane.rect.h/2-cy*norm*h/w});return;
    }
    const norm=Math.min(...viewer.panes.map(p=>Math.min(p.rect.w*.7/Math.max((x1-x0+1)/w,.001),p.rect.h*.7/Math.max((y1-y0+1)/w,.001))));
    viewer.stopViewAnim();
    for(const p of viewer.panes){if(!p.rec?.thumb)continue;viewer.setPaneView(p.side,{scale:norm/p.rec.thumb.width,ox:p.rect.w/2-cx*norm,oy:p.rect.h/2-cy*norm*h/w});}
  }
  async function openRegion(id:string){
    const d=doc.value,gid=batch.goalId;if(!d||!gid)return;
    if(viewer.drawMode){error.value='请先完成或取消手绘草稿并退出编辑';return;}
    regionId.value=id;reason.value=d.regions.find(r=>r.region_id===id)?.reason??'';
    if(comparisonReady.value){focus();return;}
    const gen=generation;viewer.noteUserIntent();const intent=viewer.userIntentSeq;
    const wanted=()=>gen===generation&&batch.goalId===gid&&doc.value===d&&viewer.userIntentSeq===intent&&!viewer.drawMode;
    const siblings={lqPath:d.scene_path} as SceneSiblings;
    try{
      const ids=[];
      for(const image of d.images){ids.push(await viewer.loadTourImage(image,siblings,wanted));if(!wanted())return;}
      const [a,b]=ids;
      const ra=viewer.recs.find(r=>r.id===a)!,rb=viewer.recs.find(r=>r.id===b)!;
      if(!ra.thumb||!rb.thumb||ra.thumb.width*rb.thumb.height!==rb.thumb.width*ra.thumb.height)throw new Error('预览网格比例不一致，不能进行精确同步验收；请使用匹配档位');
      if(!await viewer.openPreviewPair(a,b))throw new Error('对比视图已被新的操作取消');
      viewer.setCtxRailOpen(true);
      await nextTick();
      await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
      const openedIntent=viewer.userIntentSeq;
      const current=await qualityDocument(gid,d.item_id);
      if(gen!==generation||viewer.userIntentSeq!==openedIntent||doc.value!==d)return;
      if(current.review_id!==d.review_id||current.version!==d.version)throw new Error('审核对象已改变，请刷新');
      viewer.splitLinked=true;pair.value={output:a,nosr:b,review:d.review_id};error.value='';focus();
    }catch(e){if(gen===generation){error.value=String(e);pair.value=null;}}
  }
  function flicker(){const p=pair.value;if(!p||!comparisonReady.value)return;if(viewer.openComparePair(p.output,p.nosr)){focus();viewer.flickerStart();}}
  async function decide(action:'region'|'scene',decision:QualityDecision){
    const d=doc.value,gid=batch.goalId;if(!d||!gid||busy.value||!comparisonReady.value)return;
    busy.value=true;error.value='';const gen=generation;
    try{
      await qualityDecide(gid,d.item_id,{review_id:d.review_id,expected_version:d.version,request_key:spatialId(),
        action,decision,region_id:regionId.value,reviewer:reviewer.value,reason:reason.value});
      if(gen!==generation||batch.goalId!==gid)return;
      const next=await qualityDocument(gid,d.item_id);
      if(gen!==generation||batch.goalId!==gid)return;
      doc.value=next;
      await Promise.all([refresh(),batch.refreshSnapshot()]);
    }catch(e){if(gen===generation){error.value=String(e);pair.value=null;}}
    finally{if(gen===generation)busy.value=false;}
  }
  watch(()=>batch.goalId,()=>{clear();void refresh();});
  watch(()=>JSON.stringify(batch.snapshot?.quality?.items??[]),()=>{void refresh();});
  return{reviews,doc,regionId,error,busy,loading,reviewer,reason,overlay,pair,selectedRegion,comparisonReady,totals,refresh,select,openRegion,focus,flicker,decide,clear};
});
