<script setup lang="ts">
import { computed,ref,shallowRef,watch,nextTick,onBeforeUnmount } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { STRETCH_LABELS } from '../lib/histogramStretch';
import type { CurvePoint, StretchMode } from '../lib/tifDecode';
const store=useViewerStore(),open=ref(false),entry=ref<HTMLButtonElement>(),panel=ref<HTMLElement>();
const snapshot=shallowRef<ReturnType<typeof store.histogramSnapshot>>(null);
const position=ref({left:'8px',top:'48px'});
const modes:StretchMode[]=['linear','linear2','equal','sqrt','log'];
const logHistogram=ref(false);
const label=computed(()=>{
  const rec=open.value?store.histogramRec:store.activeRec;
  return rec?.displayStretch?.curve?'曲线':rec?.displayStretch?.custom?'自定义':STRETCH_LABELS[rec?.paintedMode??store.activeStretch];
});
const targets=computed(()=>store.compareMode==='flicker'?[store.flickerRefRec,store.flickerAltRec]:
  [store.recs.find(r=>r.id===store.paneA),store.recs.find(r=>r.id===store.paneB)]);
const extent=computed(()=>{
  const s=snapshot.value;if(!s)return {min:0,max:255};
  const min=Math.min(s.stats[0].min,s.low),max=Math.max(s.stats[0].max,s.high);
  return Number.isFinite(min)&&Number.isFinite(max)?{min,max:max>min?max:min+1}:{min:0,max:255};
});
const dragRange=ref<{low:number;high:number}|null>(null);
const range=computed(()=>dragRange.value??snapshot.value??{low:0,high:255});
const x=(value:number)=>Math.max(0,Math.min(300,(value-extent.value.min)/(extent.value.max-extent.value.min)*300));
const bars=computed(()=>{
  const s=snapshot.value?.stats[0];if(!s)return [];
  const values=Array.from({length:128},(_,i)=>s.hist.subarray(i*8,(i+1)*8).reduce((a,b)=>a+b,0));
  const peak=Math.max(1,...values);
  if(!s.total&&Number.isFinite(s.min))return [{x:x(s.min),width:2,height:75}];
  return values.map((v,i)=>({x:x(s.min+i/128*(s.max-s.min)),width:Math.max(.5,(s.max-s.min)/(extent.value.max-extent.value.min)*300/128),height:(logHistogram.value?Math.log1p(v)/Math.log1p(peak):v/peak)*83}));
});
const number=(v:number)=>Number.isFinite(v)?Number(v.toPrecision(7)).toString():'—';
let frame=0,statsFrame=0,pointer:number|null=null,dragId=0,handle:'low'|'high'='low';
function refresh(){snapshot.value=store.histogramSnapshot();}
function place(){
  const rect=entry.value?.getBoundingClientRect(),p=panel.value;if(!rect||!p)return;
  position.value={left:Math.max(8,Math.min(rect.left,innerWidth-p.offsetWidth-8))+'px',
    top:Math.max(8,rect.bottom+8+p.offsetHeight<=innerHeight?rect.bottom+8:rect.top-p.offsetHeight-8)+'px'};
}
async function toggle(){if(open.value){close();return;}open.value=true;refresh();await nextTick();place();panel.value?.focus();}
function commit(){if(!dragId)return;store.finishStretchInteraction(dragId);dragId=0;}
function close(){cancelAnimationFrame(frame);frame=0;if(curveDraft&&store.histogramRec?.id===dragId)store.setStretchCurve(curveDraft);commit();pointer=null;curvePointer=null;curveDraft=null;dragRange.value=null;open.value=false;store.histogramTarget='active';entry.value?.focus();}
function outside(e:PointerEvent){if(open.value&&!panel.value?.contains(e.target as Node)&&!entry.value?.contains(e.target as Node))close();}
function keyOutside(e:KeyboardEvent){if(e.key==='Escape'&&open.value){e.preventDefault();close();}}
function update(low:number,high:number,preview=false){
  if(!snapshot.value||snapshot.value.id!==store.histogramRec?.id)return;
  if(!Number.isFinite(low)||!Number.isFinite(high)||high<=low)return;
  store.setStretchBounds(low,high,preview);refresh();
}
function move(e:PointerEvent){
  if(e.pointerId!==pointer||!snapshot.value||store.histogramRec?.id!==dragId)return;
  const svg=(e.currentTarget as SVGElement).closest('svg')!;
  const rect=svg.getBoundingClientRect(),t=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));
  const value=extent.value.min+t*(extent.value.max-extent.value.min),gap=(extent.value.max-extent.value.min)/1e6;
  const old=dragRange.value??range.value;
  dragRange.value=handle==='low'?{low:Math.min(value,old.high-gap),high:old.high}:{low:old.low,high:Math.max(value,old.low+gap)};
  if(!frame)frame=requestAnimationFrame(()=>{frame=0;if(dragRange.value&&store.histogramRec?.id===dragId)update(dragRange.value.low,dragRange.value.high,true);});
}
function down(e:PointerEvent,which:'low'|'high'){
  if(e.button!==0||!snapshot.value)return;e.preventDefault();
  pointer=e.pointerId;dragId=snapshot.value.id;handle=which;
  dragRange.value={low:range.value.low,high:range.value.high};
  const svg=(e.currentTarget as SVGElement).closest('svg')!;svg.setPointerCapture(e.pointerId);
}
function end(e:PointerEvent){if(pointer!==e.pointerId)return;
  cancelAnimationFrame(frame);frame=0;
  if(dragRange.value&&store.histogramRec?.id===dragId)update(dragRange.value.low,dragRange.value.high,false);
  commit();pointer=null;dragRange.value=null;refresh();
}
function sliderKey(e:KeyboardEvent,which:'low'|'high'){
  const step=(extent.value.max-extent.value.min)/1024*(e.shiftKey?10:1),r=range.value;
  let value=r[which];
  if(['ArrowRight','ArrowUp'].includes(e.key))value+=step;
  else if(['ArrowLeft','ArrowDown'].includes(e.key))value-=step;
  else if(e.key==='Home')value=extent.value.min;
  else if(e.key==='End')value=extent.value.max;else return;
  e.preventDefault();value=Math.max(extent.value.min,Math.min(extent.value.max,value));
  update(which==='low'?Math.min(value,r.high-step/10):r.low,which==='high'?Math.max(value,r.low+step/10):r.high);
}
function choose(mode:StretchMode){store.setStretch(mode);refresh();}
const curvePoints=computed(()=>snapshot.value?.curve??[]);
let curvePointer:number|null=null,curveIndex=0,curveDraft:CurvePoint[]|null=null;
const curveX=(p:CurvePoint)=>x(range.value.low+p.x*(range.value.high-range.value.low));
function chooseCurve(){store.setStretchCurve([{x:0,y:0},{x:1,y:1}]);refresh();nextTick(place);}
function curveCoords(e:PointerEvent){
  const rect=(e.currentTarget as SVGElement).closest('svg')!.getBoundingClientRect();
  const dn=extent.value.min+(e.clientX-rect.left)/rect.width*(extent.value.max-extent.value.min);
  return {x:Math.max(0,Math.min(1,(dn-range.value.low)/(range.value.high-range.value.low))),
    y:Math.max(0,Math.min(1,1-((e.clientY-rect.top)/rect.height*112-4)/94))};
}
function curveAdd(e:PointerEvent){
  if(!snapshot.value?.curve||e.button!==0)return;
  const p=curveCoords(e),points=curvePoints.value.map(p=>({...p}));
  const near=points.findIndex(q=>Math.abs(curveX(q)-x(range.value.low+p.x*(range.value.high-range.value.low)))<5);
  if(near>=0){curveDown(e,near);return;}
  const index=points.findIndex(q=>q.x>p.x);if(index<1)return;
  points.splice(index,0,p);store.setStretchCurve(points,true);refresh();curveDown(e,index);
  curveDraft=points;
}
function curveDelete(index:number){
  if(index<=0||index>=curvePoints.value.length-1)return;
  const points=curvePoints.value.map(p=>({...p}));points.splice(index,1);store.setStretchCurve(points);refresh();
}
function curveInsertKey(){
  const points=curvePoints.value;let index=1;
  for(let i=2;i<points.length;i++)if(points[i].x-points[i-1].x>points[index].x-points[index-1].x)index=i;
  const a=points[index-1],b=points[index],next=points.map(p=>({...p}));next.splice(index,0,{x:(a.x+b.x)/2,y:(a.y+b.y)/2});
  store.setStretchCurve(next);refresh();nextTick(()=>panel.value?.querySelectorAll<SVGGElement>('.curve-point')[index]?.focus());
}
function curveDown(e:PointerEvent,index:number){
  if(e.button!==0||!snapshot.value)return;e.preventDefault();commit();curveDraft=null;
  curvePointer=e.pointerId;curveIndex=index;dragId=snapshot.value.id;
  (e.currentTarget as SVGElement).closest('svg')!.setPointerCapture(e.pointerId);
}
function curveMove(e:PointerEvent){
  if(curvePointer!==e.pointerId||store.histogramRec?.id!==dragId)return;
  const points=(curveDraft??curvePoints.value).map(p=>({...p})),i=curveIndex,p=curveCoords(e);
  if(!points[i])return;
  points[i].y=p.y;
  if(i>0&&i<points.length-1)points[i].x=Math.max(points[i-1].x+1e-6,Math.min(points[i+1].x-1e-6,p.x));
  curveDraft=points;
  if(!frame)frame=requestAnimationFrame(()=>{frame=0;if(curveDraft&&store.histogramRec?.id===dragId){store.setStretchCurve(curveDraft,true);refresh();}});
}
function curveEnd(e:PointerEvent){if(e.pointerId!==curvePointer)return;
  cancelAnimationFrame(frame);frame=0;if(curveDraft&&store.histogramRec?.id===dragId)store.setStretchCurve(curveDraft);
  commit();curvePointer=null;curveDraft=null;refresh();
}
function curveKey(e:KeyboardEvent,index:number){
  if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();curveDelete(index);return;}
  if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
  e.preventDefault();const points=curvePoints.value.map(p=>({...p})),p=points[index],step=e.shiftKey?.04:.004;
  if(e.key==='Home')p.y=0;else if(e.key==='End')p.y=1;
  else if(e.key==='ArrowUp'||e.key==='ArrowDown')p.y=Math.max(0,Math.min(1,p.y+(e.key==='ArrowUp'?step:-step)));
  else if(index>0&&index<points.length-1)p.x=Math.max(points[index-1].x+.005,Math.min(points[index+1].x-.005,p.x+(e.key==='ArrowRight'?step:-step)));
  store.setStretchCurve(points);refresh();
}
function field(which:'low'|'high',event:Event){
  const input=event.target as HTMLInputElement,value=input.valueAsNumber,r=range.value;
  if(!Number.isFinite(value)||(which==='low'?value>=r.high:value<=r.low)){input.value=number(r[which]);return;}
  update(which==='low'?value:r.low,which==='high'?value:r.high);
}
function selectTarget(side:'A'|'B'){commit();store.histogramTarget=side;if(store.compareMode==='flicker')store.setTourLayer(side==='B');refresh();}
watch(()=>[store.histogramRec?.id,store.histogramRec?.src,store.renderTick,store.histogramScope],()=>{
  if(!open.value)return;
  if(dragId&&dragId!==store.histogramRec?.id){commit();dragRange.value=null;pointer=null;}
  if(!statsFrame)statsFrame=requestAnimationFrame(()=>{statsFrame=0;refresh();});
});
watch(open,value=>{
  if(value){document.addEventListener('pointerdown',outside);document.addEventListener('keydown',keyOutside);window.addEventListener('resize',place);window.addEventListener('scroll',place,true);}
  else{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',keyOutside);window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);}
});
onBeforeUnmount(()=>{cancelAnimationFrame(frame);cancelAnimationFrame(statsFrame);commit();store.histogramTarget='active';document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',keyOutside);window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);});
</script>
<template>
  <button ref="entry" type="button" class="hist-entry" :class="{on:open}" :aria-expanded="open" aria-haspopup="dialog" aria-label="直方图与拉伸" @click="toggle">
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 13V9m3 4V5m3 8V2m3 11V6m3 7V10" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
    <span>直方图 · {{ label }}</span><span class="chevron">⌄</span>
  </button>
  <Teleport to="body">
    <section v-if="open" ref="panel" class="hist-panel" role="dialog" aria-label="灰度直方图拉伸" tabindex="-1" :style="position" @pointerdown.stop @keydown.stop @keydown.esc.prevent="close">
      <header><svg class="panel-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 16V10m5 6V3m5 13V7m4 9H2" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg><strong>灰度调整</strong><button class="close" aria-label="关闭直方图" @click="close">×</button></header>
      <div class="hist-distribution"><span>灰度分布</span><div class="scope range-scope" role="group" aria-label="直方图统计范围"><button :class="{selected:store.histogramScope==='whole'}" @click="store.histogramScope='whole';refresh()">整图</button><button :class="{selected:store.histogramScope==='view'}" @click="store.histogramScope='view';refresh()">当前视野</button></div><button class="hist-log" :class="{selected:logHistogram}" :aria-pressed="logHistogram" title="切换直方图频数的对数纵轴，不改变影像映射" @click="logHistogram=!logHistogram">▥ log</button></div>
      <template v-if="snapshot">
        <svg class="hist-plot" :class="{'curve-active':!!snapshot.curve}" viewBox="0 -4 300 112" preserveAspectRatio="none" :role="snapshot.curve?'group':'img'" :tabindex="snapshot.curve?0:undefined" aria-label="灰度直方图与拉伸曲线，回车新增控制点" @keydown.enter.self.prevent="snapshot.curve&&curveInsertKey()" @pointerdown="curveAdd" @pointermove="move($event);curveMove($event)" @pointerup="end($event);curveEnd($event)" @pointercancel="end($event);curveEnd($event)">
          <defs><linearGradient id="hist-gray"><stop stop-color="#151b19"/><stop offset="1" stop-color="#fafbfa"/></linearGradient><linearGradient id="hist-bars" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#4f7b70"/><stop offset="1" stop-color="#87948f"/></linearGradient></defs>
          <rect x="0" y="0" width="300" height="94" rx="5" fill="#eef2f3"/>
          <rect x="0" y="96" width="300" height="6" fill="url(#hist-gray)" opacity=".35"/>
          <rect :x="x(range.low)" y="0" :width="Math.max(0,x(range.high)-x(range.low))" height="94" fill="#468f8a" opacity=".09"/>
          <rect v-for="(bar,i) in bars" :key="i" :x="bar.x" :y="94-bar.height" :width="bar.width+.15" :height="bar.height" fill="url(#hist-bars)" opacity=".8"/>
          <rect x="0" y="0" :width="x(range.low)" height="94" fill="#18241f" opacity=".08"/><rect :x="x(range.high)" y="0" :width="300-x(range.high)" height="94" fill="#18241f" opacity=".08"/>
          <g v-for="which in (['low','high'] as const)" :key="which" class="hist-handle" role="slider" tabindex="0" :aria-label="which==='low'?'黑场':'白场'" :aria-valuemin="extent.min" :aria-valuemax="extent.max" :aria-valuenow="range[which]" :aria-valuetext="number(range[which])" @pointerdown.stop="down($event,which)" @keydown="sliderKey($event,which)">
            <rect :x="x(range[which])-11" y="-4" width="22" height="110" fill="transparent"/>
            <line :x1="x(range[which])" :x2="x(range[which])" y1="4" y2="102" stroke="#FAFBFA" stroke-width="3"/>
            <line :x1="x(range[which])" :x2="x(range[which])" y1="4" y2="102" stroke="#263b33" stroke-width="1"/>
            <path :d="`M${x(range[which])-4},-2 h8 v8 l-4,4 -4,-4 Z`" :fill="which==='low'?'#293832':'#FAFBFA'" stroke="#405c50" stroke-width="1"/>
          </g>
          <g v-if="snapshot.curve">
            <path :d="`M${x(range.low)} 94 L${x(range.high)} 0`" stroke="#6c8b7c" stroke-dasharray="3 3" opacity=".5" pointer-events="none"/>
            <polyline :points="[[0,(1-curvePoints[0].y)*94],...curvePoints.map(p=>[curveX(p),(1-p.y)*94]),[300,(1-curvePoints[curvePoints.length-1].y)*94]].map(p=>p.join(',')).join(' ')" fill="none" stroke="#fafbfa" stroke-width="4" pointer-events="none"/>
            <polyline :points="[[0,(1-curvePoints[0].y)*94],...curvePoints.map(p=>[curveX(p),(1-p.y)*94]),[300,(1-curvePoints[curvePoints.length-1].y)*94]].map(p=>p.join(',')).join(' ')" fill="none" stroke="#28766a" stroke-width="1.8" pointer-events="none"/>
            <g v-for="(p,i) in curvePoints" :key="i" class="curve-point" role="slider" tabindex="0" :aria-label="`曲线控制点 ${i+1} 输出灰度`" :aria-valuemin="0" :aria-valuemax="255" :aria-valuenow="Math.round(p.y*255)" @pointerdown.stop="curveDown($event,i)" @dblclick.stop="curveDelete(i)" @keydown.stop="curveKey($event,i)"><title>拖动调整；双击或 Delete 删除中间点</title><circle :cx="curveX(p)" :cy="(1-p.y)*94" r="9" fill="transparent"/><circle :cx="curveX(p)" :cy="(1-p.y)*94" r="3.5" fill="#fafbfa" stroke="#28766a" stroke-width="1.5"/></g>
          </g>
        </svg>
        <div class="hist-axis" aria-label="输入灰度刻度"><span>{{ number(extent.min) }}</span><span>{{ number((extent.min+extent.max)/2) }}</span><span>{{ number(extent.max) }}</span></div>
        <div v-if="snapshot.curve" class="curve-caption"><span>点击任意位置加点 · 双击 / Delete 删点</span><button @click="chooseCurve">重置曲线</button></div>
        <div class="hist-values"><label>黑场 <input aria-label="黑场数值" type="number" step="any" :value="number(range.low)" @change="field('low',$event)"/></label><label>白场 <input aria-label="白场数值" type="number" step="any" :value="number(range.high)" @change="field('high',$event)"/></label></div>
        <div class="hist-section-label">拉伸模式</div><div class="hist-modes" role="group" aria-label="拉伸模式"><button v-for="mode in modes" :key="mode" :class="{selected:!snapshot.custom&&snapshot.mode===mode}" :aria-pressed="!snapshot.custom&&snapshot.mode===mode" @click="choose(mode)">{{ STRETCH_LABELS[mode] }}</button></div>
        <button class="curve-entry" :class="{selected:!!snapshot.curve}" :aria-pressed="!!snapshot.curve" @click="!snapshot.curve&&chooseCurve()">曲线拉伸 <span>调整暗部、中间调与高光</span></button>
        <div v-if="store.split||store.compareMode==='flicker'" class="hist-link"><div v-if="store.stretchLink==='independent'" class="scope" role="group" aria-label="调整影像"><span>调整</span><button v-for="(side,i) in (['A','B'] as const)" :key="side" :disabled="!targets[i]" :title="targets[i]?.name" :class="{selected:store.histogramRec?.id===targets[i]?.id}" @click="selectTarget(side)">{{ side }}</button></div><span v-else>双图映射</span><div class="scope"><button :class="{selected:store.stretchLink==='independent'}" @click="store.setStretchLink('independent');refresh()">独立</button><button :disabled="!!store.sharedStretchReason" :title="store.sharedStretchReason||'同一上下限及 CDF 映射'" :class="{selected:store.stretchLink==='shared'}" @click="store.setStretchLink('shared');refresh()">共享</button></div></div>
        <p v-if="(store.split||store.compareMode==='flicker')&&store.sharedStretchReason" class="hist-note">{{ store.sharedStretchReason }}</p>
        <footer><span>{{ snapshot.curve?'曲线映射':snapshot.custom?'自定义线性映射':STRETCH_LABELS[snapshot.mode] }}<small v-if="store.stretchLink==='shared'"> · 共享范围 / CDF</small></span><button @click="store.resetHistogramStretch();refresh()"><span aria-hidden="true">↻</span> 恢复默认</button></footer>
        <p class="hist-note source-note"><span aria-hidden="true">ⓘ</span> 数据来源：{{ snapshot.source }}<br v-if="store.histogramScope==='view'"/><span v-if="store.histogramScope==='view'">视野统计来自缓存像素，不重新读取 TIFF。</span></p>
      </template>
      <p v-else class="hist-empty">当前范围暂无有效采样；请打开影像、等待概览或调整视野。</p>
    </section>
  </Teleport>
</template>
<style scoped>
.hist-entry{height:32px;display:inline-flex;align-items:center;gap:7px;padding:0 10px;white-space:nowrap;border:1px solid #d8e4df;border-radius:8px;color:#304c40;background:#fafbfa;font:12px inherit;cursor:pointer}.hist-entry:hover,.hist-entry.on{border-color:#83a397;background:#f2f5f3}.chevron{margin-left:3px;color:#82958b}
.hist-panel{position:fixed;z-index:130;width:340px;max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);overflow:auto;box-sizing:border-box;padding:0 15px 12px;background:#FAFBFA;border:1px solid #D8E4DF;border-radius:15px;box-shadow:0 3px 9px #142c2009,0 18px 40px #142c201a;color:#263c33;font-size:12px;outline:none}
.hist-panel header{display:flex;align-items:center;gap:9px;margin:0 -15px 13px;padding:12px 15px;border-bottom:1px solid #e5ece8;background:linear-gradient(110deg,#eff5f280,#fafbfa);border-radius:15px 15px 0 0}.panel-icon{color:#20634f;filter:drop-shadow(0 2px 2px #20634f26)}.hist-panel strong{font-size:15px;flex:1;font-weight:650;letter-spacing:.3px}.hist-panel button{font:inherit;cursor:pointer;transition:background 150ms,border-color 150ms,box-shadow 150ms;color:inherit}.hist-panel .close{border:0;background:none;padding:0 1px;color:#52655a;font-size:22px;line-height:20px}.hist-panel .close:hover{color:#183e2e}
.hist-distribution{display:flex;align-items:center;gap:8px;margin-bottom:14px}.hist-distribution>span{font-size:12px;white-space:nowrap}.scope{display:flex;gap:2px;align-items:center}.scope button{border:0;border-radius:6px;background:transparent;padding:5px 7px;font-size:11px;color:#718278}.range-scope{margin-left:auto;background:#f0f3f2;padding:3px;border-radius:20px;box-shadow:inset 0 1px 3px #1e3b2d0c}.range-scope button{border-radius:16px;padding:5px 10px}.scope button.selected{background:#e5efea;color:#245c48}.range-scope button.selected,.hist-modes button.selected{background:linear-gradient(155deg,#2e7b61,#19533f);border-color:#347e64;color:#fff;box-shadow:0 2px 5px #164d3626,inset 0 1px 0 #ffffff24}.hist-log{border:1px solid #dbe3df;border-radius:7px;padding:5px 7px;background:#fafcfc;box-shadow:0 1px 3px #243e3009;font-size:11px!important;white-space:nowrap}.hist-log.selected{background:#e2eeea;border-color:#80a998;color:#265e4b}
.hist-plot{width:100%;height:112px;overflow:visible;display:block;touch-action:none;user-select:none}.hist-handle{cursor:ew-resize;outline:none}.hist-handle:focus path{stroke:#0c8983;stroke-width:2}.hist-axis{display:flex;justify-content:space-between;color:#65786c;font-size:11px;font-variant-numeric:tabular-nums;margin:0 0 12px}.hist-values{display:flex;justify-content:space-between;gap:16px;margin:10px 0 14px}.hist-values label{flex:1;min-width:0;display:flex;gap:7px;align-items:center;white-space:nowrap;color:#3b5145;font-size:12px}.hist-values input{width:100%;min-width:0;box-sizing:border-box;height:31px;border:1px solid #d7e1db;border-radius:7px;background:#fff;color:#263e32;font:12px ui-monospace,monospace;padding:5px 7px;font-variant-numeric:tabular-nums;box-shadow:inset 0 1px 2px #213c2805}.hist-section-label{font-size:11px;color:#708276;margin-bottom:7px}.hist-modes{display:flex;gap:5px;align-items:center}.hist-modes>button{flex:1;white-space:nowrap;border:1px solid #dce5df;background:linear-gradient(#fff,#f4f7f6);border-radius:7px;padding:7px 3px;box-shadow:0 1px 3px #243e300c}.hist-modes>button:hover{border-color:#89ad9b}
.curve-entry{width:100%;display:flex;justify-content:space-between;align-items:center;margin-top:9px;padding:8px 9px;border:1px solid #dce5df;border-radius:8px;background:#f7f9f8}.curve-entry span{font-size:11px;color:#87958c}.curve-entry.selected{border-color:#9dbfb0;background:linear-gradient(100deg,#e5efea,#f5f8f6);color:#205e48}.curve-active{cursor:crosshair}.curve-point{cursor:move;outline:none}.curve-point:focus circle:last-child{stroke-width:3}.curve-caption{display:flex;justify-content:space-between;gap:5px;margin:0 0 9px;font-size:10px;color:#7b9083}.curve-caption button{border:0;background:none;padding:0;color:#3b796c;white-space:nowrap}
.hist-link{display:flex;align-items:center;justify-content:space-between;margin-top:10px;font-size:11px;color:#73867a}.hist-panel button:disabled{cursor:not-allowed;opacity:.4}.hist-panel footer{display:flex;align-items:center;justify-content:space-between;border-top:1px solid #e4ece6;margin-top:13px;padding-top:10px;color:#708477;font-size:11px}.hist-panel footer button{padding:3px 0;border:0;background:none;color:#315c46;font-size:12px}.hist-panel footer button span{font-size:18px;vertical-align:-1px;margin-right:3px}.hist-note{font-size:11px;line-height:1.6;color:#7e9184;margin:8px 0 0}.source-note{border-top:1px solid #e8eeea;margin:10px -15px -12px;padding:10px 15px 12px;background:#f5f8f650}.source-note>span:first-child{margin-right:4px}.hist-empty{color:#87958c;line-height:1.7;padding:24px 0}.hist-panel input:focus-visible,.hist-panel button:focus-visible,.hist-entry:focus-visible,.hist-plot:focus-visible{outline:2px solid #438e82;outline-offset:2px}@media(prefers-reduced-motion:reduce){.hist-panel button,.hist-entry{transition:none}}
</style>
