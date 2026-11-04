import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { useViewerStore } from './viewer';
import { planTour, travelFraction, TOUR_SPEEDS } from '../lib/autoTour';
import type { TourPlan } from '../lib/autoTour';
export const useAutoTourStore=defineStore('autoTour',()=>{
 const viewer=useViewerStore();
 const status=ref<'idle'|'playing'|'paused'>('idle'), reason=ref('');
 const range=ref<'all'|'visible'>('all'), speed=ref<keyof typeof TOUR_SPEEDS>('normal'), loop=ref(false);
 const plan=ref<TourPlan|null>(null), index=ref(0), fraction=ref(0);
 const current=ref<{x:number;y:number}|null>(null);
 const progress=computed(()=>plan.value?Math.round((index.value+fraction.value)/(plan.value.points.length-1)*100):0);
 let raf=0,last=0,elapsed=0,hold=0,direction=1,valid=true,entry=true,writing=false;
 let initial={scale:1,ox:0,oy:0};
 let entryTarget:{x:number;y:number}|null=null,resumeElapsed:number|null=null;
 const reduced=()=>typeof matchMedia!=='undefined'&&matchMedia('(prefers-reduced-motion: reduce)').matches;
 function cancel(){cancelAnimationFrame(raf);raf=0;last=0;}
 function stop(){cancel();status.value='idle';plan.value=null;current.value=null;reason.value='';}
 function pause(why='已暂停'){if(status.value==='playing'){cancel();status.value='paused';reason.value=why;}}
 function allowed(){return !document.hidden&&viewer.compareMode==='off'&&!viewer.drawMode&&!viewer.busy&&!viewer.overlay.visible&&!!viewer.panes[0]?.rec?.thumb&&viewer.canvasAttached;}
 function target(x:number,y:number){const p=plan.value!,r=viewer.panes[0].rect;return {scale:p.scale,ox:r.w/2-x*p.tw/p.width*p.scale,oy:r.h/2-y*p.th/p.height*p.scale};}
 function camera(v:{scale:number;ox:number;oy:number}){writing=true;try{viewer.setPaneView('A',v);}finally{writing=false;}}
 function write(x:number,y:number){current.value={x,y};camera(target(x,y));}
 function schedule(){raf=requestAnimationFrame(frame);}
 function frame(now:number){
  if(status.value!=='playing')return;
  if(document.hidden||reduced()){pause(document.hidden?'已暂停 · 页面隐藏':'已暂停 · 减少动效');return;}
  const dt=last?Math.min(.1,(now-last)/1000):0;last=now;
  const p=plan.value!;
  if(entry){elapsed+=dt;const u=Math.min(1,elapsed),t=u*u*(3-2*u),point=entryTarget??p.points[index.value];const end=target(point.x,point.y);camera({scale:initial.scale+(end.scale-initial.scale)*t,ox:initial.ox+(end.ox-initial.ox)*t,oy:initial.oy+(end.oy-initial.oy)*t});
   const v=viewer.panes[0].view,r=viewer.panes[0].rect;current.value={x:(r.w/2-v.ox)/v.scale*p.width/p.tw,y:(r.h/2-v.oy)/v.scale*p.height/p.th};
   if(u===1){entry=false;elapsed=resumeElapsed??0;resumeElapsed=null;entryTarget=null;} schedule();return;
  }
  if(hold>0){hold=Math.max(0,hold-dt);schedule();return;}
  const next=index.value+direction;
  if(next<0||next>=p.points.length){if(loop.value){direction*=-1;hold=.3;schedule();}else{stop();viewer.showToast('巡览结束');}return;}
  const a=p.points[index.value],b=p.points[next],distance=Math.hypot((b.x-a.x)*p.tw/p.width,(b.y-a.y)*p.th/p.height)*p.scale;
  elapsed+=dt;const f=travelFraction(elapsed,distance,TOUR_SPEEDS[speed.value]).fraction;
  fraction.value=direction*f;write(a.x+(b.x-a.x)*f,a.y+(b.y-a.y)*f);
  if(f===1){index.value=next;fraction.value=0;elapsed=0;hold=.3;}
  schedule();
 }
 function start(){
  stop();if(reduced()){reason.value='减少动效模式下不启用自动运动，请使用手动导航';return false;}
  if(!allowed()){reason.value='请打开单景影像，并退出对比或掩码编辑后巡览';return false;}
  const pane=viewer.panes[0],r=pane.rec!,t=r.thumb!,v=pane.view;
  try{const region=range.value==='visible'?{x:-v.ox/v.scale*r.W/t.width,y:-v.oy/v.scale*r.H/t.height,w:pane.rect.w/v.scale*r.W/t.width,h:pane.rect.h/v.scale*r.H/t.height}:undefined;
   plan.value=planTour(r.W,r.H,t.width,t.height,pane.rect.w,pane.rect.h,region);
  }catch(e){reason.value=(e as Error).message;return false;}
  viewer.noteUserIntent();viewer.stopViewAnim();
  initial={...v};camera(initial);index.value=0;fraction.value=0;direction=1;elapsed=0;hold=0;valid=true;entry=true;entryTarget=null;resumeElapsed=null;status.value='playing';schedule();return true;
 }
 function resume(){if(!valid||!allowed()||reduced()||document.hidden){reason.value='当前视图已变化，请结束后重新规划巡览';return;}
  if(reason.value==='已暂停 · 手动操作'){
   initial={...viewer.panes[0].view};entryTarget=current.value?{...current.value}:null;resumeElapsed=entry?0:elapsed;entry=true;elapsed=0;
  }
  status.value='playing';reason.value='';schedule();}
 watch(()=>{const v=viewer.panes[0]?.view;return [v?.scale,v?.ox,v?.oy];},()=>{if(!writing)pause('已暂停 · 手动操作');},{flush:'sync'});
 watch(()=>viewer.userIntentSeq,()=>pause('已暂停 · 手动操作'),{flush:'sync'});
 watch(()=>viewer.drawMode,v=>{if(v)pause('已暂停 · 手动操作');},{flush:'sync'});
 watch(()=>viewer.busy||viewer.overlay.visible,v=>{if(v)pause('已暂停 · 影像正在处理');},{flush:'sync'});
 watch(()=>[viewer.activeRec?.id,viewer.compareMode,viewer.canvasSize.w,viewer.canvasSize.h,viewer.activeRec?.thumb],()=>{if(status.value!=='idle'){pause('已暂停 · 视图已变化，请重新规划');valid=false;plan.value=null;current.value=null;}},{flush:'sync'});
 return {status,reason,range,speed,loop,plan,index,current,progress,start,pause,resume,stop};
});

