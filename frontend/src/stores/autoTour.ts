import { TourCoverage } from '../lib/tourCoverage';
import { tourPairRefusal, tourImageFromEvidence } from '../lib/tourCompare';
import { apiSceneSiblings } from '../lib/api';
import type { SceneSiblings, SceneSibling } from '../lib/api';
import { loadSrConfig } from '../lib/scene';
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { useViewerStore } from './viewer';
import { planTour, travelFraction, TOUR_SPEEDS } from '../lib/autoTour';
import type { TourPlan, TourRoute } from '../lib/autoTour';
export const useAutoTourStore=defineStore('autoTour',()=>{
 const viewer=useViewerStore();
 const coverage=new TourCoverage(),coverageVersion=ref(0),coverageVisible=ref(false),coverageCompare=ref(false);
 function presented(){
  if(status.value!=='playing')return;
  const pane=viewer.panes[0],r=pane.rec,t=r?.thumb,v=pane.view;
  if(!t||!r)return;
  coverage.record(compare.value&&viewer.flickerShowingAlt?1:0,-v.ox/v.scale/t.width,-v.oy/v.scale/t.height,pane.rect.w/v.scale/t.width,pane.rect.h/v.scale/t.height);coverageVersion.value++;
 }
 const status=ref<'idle'|'playing'|'paused'>('idle'), reason=ref('');
 const range=ref<'all'|'visible'>('all'), speed=ref<keyof typeof TOUR_SPEEDS>('normal'), loop=ref(false);
 const route=ref<TourRoute>('rows');
 const compare=ref(false), mode=ref<'stop'|'move'>('stop'), interval=ref(1000), rounds=ref(2);
 const pairs=ref<{a:SceneSibling;b:SceneSibling;label:string;refusal:string|null}[]>([]),selected=ref(-1),pairLoading=ref(false),pairMessage=ref('');
 let pairEpoch=0;const sceneEvidence=ref<SceneSiblings|null>(null);
 const selectedPair=computed(()=>pairs.value[selected.value]);
 const availablePairs=computed(()=>pairs.value.filter(p=>!p.refusal));
 function openedSceneIds(){
  const active=viewer.activeRec,dir=active?.sceneDir??active?.lqPath;
  return [...new Set([active?.sceneId,...(viewer.recs??[]).filter(r=>dir&&(r.sceneDir??r.lqPath)===dir).map(r=>r.sceneId)].filter((id):id is string=>!!id))].slice(0,32);
 }
 async function discoverPairs(){
  const epoch=++pairEpoch,sid=viewer.activeRec?.sceneId,previous=selectedPair.value;
  pairs.value=[];selected.value=-1;sceneEvidence.value=null;pairMessage.value='';
  if(!sid){pairMessage.value='当前影像没有后端 Scene 身份，无法验证配对';return;}
  pairLoading.value=true;
  try{
   const res=await apiSceneSiblings(loadSrConfig(),sid,undefined,true,openedSceneIds());
   if(epoch!==pairEpoch)return;
   sceneEvidence.value=res;
   if(!Array.isArray(res.tourItems)){pairMessage.value='后端未返回巡览候选，请确认前后端已同包更新';return;}
   const items=res.tourItems??[],pans=items.filter(i=>i.exists&&i.tourIdentity?.role==='pan');
   const refusals:string[]=[];
   for(const a of pans)for(const b of items.filter(i=>i.exists&&['nosr','ori','old'].includes(i.tourIdentity?.role??''))){
    const why=tourPairRefusal(tourImageFromEvidence(1,a.tourIdentity!,a.srcRev),tourImageFromEvidence(2,b.tourIdentity!,b.srcRev));
    if(why)refusals.push(why);
    const role=b.tourIdentity!.role;
    pairs.value.push({a,b,label:`${a.name} ↔ ${role==='nosr'?'NOSR':role} · ${b.name}`,refusal:why});
   }
   if(previous)selected.value=pairs.value.findIndex(p=>!p.refusal&&p.a.id===previous.a.id&&p.b.id===previous.b.id);
   if(selected.value<0&&availablePairs.value.length===1)selected.value=pairs.value.findIndex(p=>!p.refusal);
   pairMessage.value=pairs.value.length
    ? availablePairs.value.length===0?`找到 ${pairs.value.length} 组候选，均不可用：${refusals[0]}`
      : availablePairs.value.length>1?'发现多个可用配对，请手动选择':refusals.length?`${refusals.length} 组候选证据不足，已禁用`:''
    : res.tourDiagnostics?.[0]??'未找到可识别的 PAN/NOSR/ori/old 候选';
  }catch(e){if(epoch===pairEpoch)pairMessage.value=(e as Error).message;}
  finally{if(epoch===pairEpoch)pairLoading.value=false;}
 }
 function cancelPreparation(){pairEpoch++;pairLoading.value=false;}
 async function preparePair(){
  const chosen=selectedPair.value,res=sceneEvidence.value;
  if(!chosen||chosen.refusal||!res||pairLoading.value)return false;
  const epoch=++pairEpoch;pairLoading.value=true;pairMessage.value='';
  const intent=viewer.userIntentSeq,active=viewer.activeRec?.id;
  const stillWanted=()=>epoch===pairEpoch&&intent===viewer.userIntentSeq&&active===viewer.activeRec?.id&&viewer.canvasAttached&&!document.hidden;
  try{
   const a=await viewer.loadTourImage(chosen.a,res,stillWanted);
   if(!stillWanted())return false;
   const b=await viewer.loadTourImage(chosen.b,res,stillWanted);
   if(!stillWanted())return false;
   // Revalidate the explicit files after preview loading, so a source changed
   // during decoding cannot inherit evidence from the earlier header snapshot.
   const fresh=await apiSceneSiblings(loadSrConfig(),res.sceneId,undefined,true,openedSceneIds());
   if(!stillWanted())return false;
   for(const item of [chosen.a,chosen.b]){
    const actual=fresh.tourItems?.find(i=>i.id===item.id);
    if(!actual?.exists||actual.srcRev!==item.srcRev||JSON.stringify(actual.tourIdentity)!==JSON.stringify(item.tourIdentity))throw new Error('配对源或配准证据已变化，请刷新配对');
   }
   if(!viewer.selectTourPair(a,b))throw new Error('预览未就绪、网格比例不一致或配准校验失败，请检查预览档位');
   return true;
  }catch(e){if(epoch===pairEpoch)pairMessage.value=(e as Error).message;return false;}
  finally{if(epoch===pairEpoch)pairLoading.value=false;}
 }
 const pairReason=computed(()=>{
  if(sceneEvidence.value&&!selectedPair.value)return pairMessage.value||'请选择影像配对';
  if(selectedPair.value?.refusal)return selectedPair.value.refusal;
  const ar=viewer.flickerRefRec,br=viewer.flickerAltRec,a=ar?.tourIdentity,b=br?.tourIdentity;
  if(sceneEvidence.value&&selectedPair.value&&(ar?.sceneId!==selectedPair.value.a.id||br?.sceneId!==selectedPair.value.b.id))return '请载入所选配对';
  return !a||!b?'缺少影像身份信息，请重新载入配对':tourPairRefusal({...a,sourceRevision:ar?.srcRev===a.sourceRevision?a.sourceRevision:''},{...b,sourceRevision:br?.srcRev===b.sourceRevision?b.sourceRevision:''})??viewer.flickerReason;
 });
 let switchElapsed=0,switchCount=0,observing=false;
 const plan=ref<TourPlan|null>(null), index=ref(0), fraction=ref(0);
 const current=ref<{x:number;y:number}|null>(null);
 const progress=computed(()=>plan.value?Math.round((index.value+fraction.value)/(plan.value.points.length-1)*100):0);
 let raf=0,last=0,elapsed=0,hold=0,direction=1,valid=true,entry=true,writing=false;
 let initial={scale:1,ox:0,oy:0};
 let entryTarget:{x:number;y:number}|null=null,resumeElapsed:number|null=null;
 const reduced=()=>typeof matchMedia!=='undefined'&&matchMedia('(prefers-reduced-motion: reduce)').matches;
 function cancel(){cancelAnimationFrame(raf);raf=0;last=0;}
 function stop(){cancel();cancelPreparation();viewer.flickerPause();status.value='idle';plan.value=null;current.value=null;reason.value='';}
 function pause(why='已暂停'){if(status.value==='playing'){cancel();status.value='paused';reason.value=why;}}
 function allowed(){return !document.hidden&&(compare.value?viewer.compareMode==='flicker'&&!pairReason.value:viewer.compareMode==='off')&&!viewer.drawMode&&!viewer.busy&&!viewer.overlay.visible&&!!viewer.panes[0]?.rec?.thumb&&viewer.canvasAttached;}
 function target(x:number,y:number){const p=plan.value!,r=viewer.panes[0].rect;return {scale:p.scale,ox:r.w/2-x*p.tw/p.width*p.scale,oy:r.h/2-y*p.th/p.height*p.scale};}
 function camera(v:{scale:number;ox:number;oy:number}){writing=true;try{if(compare.value)viewer.setTourCamera(v);else viewer.setPaneView('A',v);}finally{writing=false;}}
 function write(x:number,y:number){current.value={x,y};camera(target(x,y));}
 function schedule(){raf=requestAnimationFrame(frame);}
 function frame(now:number){
  if(status.value!=='playing')return;
  if(document.hidden||reduced()){pause(document.hidden?'已暂停 · 页面隐藏':'已暂停 · 减少动效');return;}
  const dt=last?Math.min(.1,(now-last)/1000):0;last=now;
  if(compare.value&&(!viewer.flickerReady||pairReason.value)){pause(pairReason.value||viewer.flickerReason);return;}
  const p=plan.value!;
  if(entry){elapsed+=dt;const u=Math.min(1,elapsed),t=u*u*(3-2*u),point=entryTarget??p.points[index.value];const end=target(point.x,point.y);camera({scale:initial.scale+(end.scale-initial.scale)*t,ox:initial.ox+(end.ox-initial.ox)*t,oy:initial.oy+(end.oy-initial.oy)*t});
   const v=compare.value?viewer.flickerView:viewer.panes[0].view,r=viewer.panes[0].rect;current.value={x:(r.w/2-v.ox)/v.scale*p.width/p.tw,y:(r.h/2-v.oy)/v.scale*p.height/p.th};
   if(u===1){hold=.3;observing=compare.value&&mode.value==='stop';switchCount=0;switchElapsed=0;entry=false;elapsed=resumeElapsed??0;resumeElapsed=null;entryTarget=null;} schedule();return;
  }
  if(hold>0&&!(compare.value&&mode.value==='move')){hold=Math.max(0,hold-dt);schedule();return;}
  if(compare.value&&(mode.value==='move'||observing)){
   switchElapsed+=dt;
   if(switchElapsed+1e-9>=interval.value/1000 && viewer.setTourLayer(!viewer.flickerShowingAlt)){switchElapsed=Math.max(0,switchElapsed-interval.value/1000);switchCount++;}
   if(mode.value==='stop'){
    if(switchCount<rounds.value*2){schedule();return;}
    observing=false;switchCount=0;switchElapsed=0;schedule();return;
   }
  }
  const next=index.value+direction;
  if(next<0||next>=p.points.length){if(loop.value){direction*=-1;hold=.3;schedule();}else{stop();viewer.showToast('巡览结束');}return;}
  const a=p.points[index.value],b=p.points[next],distance=Math.hypot((b.x-a.x)*p.tw/p.width,(b.y-a.y)*p.th/p.height)*p.scale;
  elapsed+=dt;const f=travelFraction(elapsed,distance,(compare.value?{slow:40,normal:60,fast:80,ultra:120}[speed.value]:TOUR_SPEEDS[speed.value])).fraction;
  fraction.value=direction*f;write(a.x+(b.x-a.x)*f,a.y+(b.y-a.y)*f);
  if(f===1){index.value=next;fraction.value=0;elapsed=0;hold=.3;observing=compare.value&&mode.value==='stop';switchCount=0;if(mode.value==='stop')switchElapsed=0;}
  schedule();
 }
 function start(){
  stop();coverage.clear();coverageVersion.value++;if(reduced()){reason.value='减少动效模式下不启用自动运动，请使用手动导航';return false;}
  if(!allowed()){reason.value=compare.value?pairReason.value||'请先载入配对并退出掩码编辑': '请打开单景影像，并退出对比或掩码编辑后巡览';return false;}
  if(compare.value){viewer.flickerPause();viewer.setTourLayer(false);}
  switchElapsed=0;switchCount=0;observing=false;
  const pane=viewer.panes[0],r=(compare.value?viewer.flickerRefRec:pane.rec)!,t=r.thumb!,v=compare.value?viewer.flickerView:pane.view;
  try{const region=range.value==='visible'?{x:-v.ox/v.scale*r.W/t.width,y:-v.oy/v.scale*r.H/t.height,w:pane.rect.w/v.scale*r.W/t.width,h:pane.rect.h/v.scale*r.H/t.height}:undefined;
   plan.value=planTour(r.W,r.H,t.width,t.height,pane.rect.w,pane.rect.h,region,route.value);
  }catch(e){reason.value=(e as Error).message;return false;}
  viewer.noteUserIntent();viewer.stopViewAnim();
  coverageVisible.value=true;coverageCompare.value=compare.value;
  initial={...v};camera(initial);index.value=0;fraction.value=0;direction=1;elapsed=0;hold=0;valid=true;entry=true;entryTarget=null;resumeElapsed=null;status.value='playing';schedule();return true;
 }
 function resume(){if(!valid||!allowed()||reduced()||document.hidden){reason.value='当前视图已变化，请结束后重新规划巡览';return;}
  if(reason.value==='已暂停 · 手动操作'){
   initial={...(compare.value?viewer.flickerView:viewer.panes[0].view)};entryTarget=current.value?{...current.value}:null;resumeElapsed=entry?0:elapsed;entry=true;elapsed=0;
  }
  status.value='playing';reason.value='';schedule();}
 watch(()=>{const v=compare.value?viewer.flickerView:viewer.panes[0]?.view;return [v?.scale,v?.ox,v?.oy];},()=>{if(!writing)pause('已暂停 · 手动操作');},{flush:'sync'});
 watch(()=>viewer.userIntentSeq,()=>pause('已暂停 · 手动操作'),{flush:'sync'});
 watch(()=>viewer.drawMode,v=>{if(v)pause('已暂停 · 手动操作');},{flush:'sync'});
 watch(()=>viewer.busy||viewer.overlay.visible,v=>{if(v)pause('已暂停 · 影像正在处理');},{flush:'sync'});
 watch(()=>[viewer.activeRec?.id,viewer.compareMode,viewer.canvasSize.w,viewer.canvasSize.h,viewer.activeRec?.thumb,viewer.flickerRefRec?.id,viewer.flickerAltRec?.id,viewer.flickerRefRec?.thumb,viewer.flickerAltRec?.thumb,viewer.flickerRefRec?.srcRev,viewer.flickerAltRec?.srcRev],()=>{coverage.clear();coverageVisible.value=false;coverageVersion.value++;if(status.value!=='idle'){pause('已暂停 · 视图已变化，请重新规划');valid=false;plan.value=null;current.value=null;}},{flush:'sync'});
 watch(()=>viewer.activeRec?.id,()=>{cancelPreparation();pairs.value=[];selected.value=-1;sceneEvidence.value=null;coverage.clear();coverageVersion.value++;},{flush:'sync'});
 return {pairs,availablePairs,selected,pairLoading,pairMessage,selectedPair,discoverPairs,preparePair,cancelPreparation,coverage,coverageVersion,coverageVisible,coverageCompare,presented,compare,mode,interval,rounds,pairReason,status,reason,range,speed,loop,route,plan,index,current,progress,start,pause,resume,stop};
});

