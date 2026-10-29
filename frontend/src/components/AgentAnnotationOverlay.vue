<script setup lang="ts">
import {annotationVisual} from '../lib/annotationVisual';
import {rectanglePath} from '../lib/annotationPath';
import {computed,ref,watch,onMounted,onBeforeUnmount} from 'vue';
import {useViewerStore} from '../stores/viewer';
import {useAnnotationsStore} from '../stores/annotations';
import {imageRef,vertices,project} from '../lib/spatial';
const emit=defineEmits<{wheel:[event:WheelEvent]}>();
const viewer=useViewerStore(), store=useAnnotationsStore();
const panes=computed(()=>viewer.panes.map(p=>({...p,items:store.all.filter(a=>p.rec?.thumb&&imageRef(p.rec)?.image_key===a.image_ref.image_key).map(a=>{
 const pts=vertices(a.geometry).map(v=>project(v,a.image_ref,p.rec!.thumb!.width,p.rec!.thumb!.height,p.view,{...p.rect,x:0,y:0}));
 return {a,pts,path:rectanglePath(pts,pts[0]),anchor:pts[0],points:pts.map(v=>v.join(',')).join(' '),number:String(store.all.indexOf(a)+1).padStart(2,'0')};
})})));
// One global displayed region across panes. Out first, then draw the latest target.
const shown=ref<{id:string;side:string}|null>(null), fading=ref(false);
let switchTimer:ReturnType<typeof setTimeout>|undefined;
let motion:MediaQueryList|undefined;
const reduced=ref(false);
function readMotion(){reduced.value=motion?.matches??false;}
watch(()=>[store.selected,store.selectedPane,store.hidden,viewer.drawMode,reduced.value,panes.value.some(p=>p.side===store.selectedPane&&p.items.some(i=>i.a.annotation_id===store.selected))] as const,
 ([id,side,hidden,drawing,reduce,exists])=>{
  if(switchTimer)clearTimeout(switchTimer);
  const next=id&&!hidden&&!drawing&&exists?{id,side}:null;
  if(!next||reduce){shown.value=next;fading.value=false;return;}
  if(!shown.value){shown.value=next;fading.value=false;return;}
  if(shown.value.id===id&&shown.value.side===side){fading.value=false;return;}
  fading.value=true;
  switchTimer=setTimeout(()=>{shown.value=next;fading.value=false;switchTimer=undefined;},90);
 },{immediate:true});
onMounted(()=>{motion=window.matchMedia('(prefers-reduced-motion: reduce)');readMotion();motion.addEventListener('change',readMotion);});
onBeforeUnmount(()=>{if(switchTimer)clearTimeout(switchTimer);motion?.removeEventListener('change',readMotion);});
function close(e:Event){ if(e.type==='keydown' && ((e as KeyboardEvent).key!=='Escape'||viewer.drawMode))return;
 if(e.type==='pointerdown'&&(e.target as Element).closest('.annotation-interactive, [data-annotation-id]'))return;store.selected=null; }
onMounted(()=>{window.addEventListener('keydown',close);window.addEventListener('pointerdown',close)});
onBeforeUnmount(()=>{window.removeEventListener('keydown',close);window.removeEventListener('pointerdown',close)});
</script>
<template>
 <div v-if="!store.hidden && !viewer.drawMode" class="annotation-overlay" :style="{width:viewer.canvasSize.w+'px',height:viewer.canvasSize.h+'px'}">
  <div v-for="p in panes" :key="p.side" class="annotation-pane" :style="{left:p.rect.x+'px',top:p.rect.y+'px',width:p.rect.w+'px',height:p.rect.h+'px'}">
   <svg class="annotation-geometry" width="100%" height="100%"><template v-for="item in p.items" :key="item.a.annotation_id">
    <g v-if="item.a.geometry.type!=='point' && shown?.id===item.a.annotation_id && shown.side===p.side" :key="item.a.annotation_id+':'+p.side" class="annotation-region" :class="{fading}" :style="annotationVisual(item.a)">
     <template v-if="item.a.geometry.type==='bbox'">
      <path v-if="item.path.connector" class="annotation-connector" :d="item.path.connector" />
      <path class="annotation-bbox" :d="item.path.d" pathLength="1" />
     </template>
     <polygon v-else :points="item.points" class="selected" />
    </g>
   </template></svg>
   <template v-for="item in p.items" :key="item.a.annotation_id">
    <button v-if="item.anchor[0]>=0&&item.anchor[0]<=p.rect.w&&item.anchor[1]>=0&&item.anchor[1]<=p.rect.h" class="annotation-interactive annotation-marker" :class="{selected:store.selected===item.a.annotation_id, hovered:store.hovered===item.a.annotation_id}" @pointerenter="store.hovered=item.a.annotation_id" @pointerleave="store.hovered=null" @focus="store.hovered=item.a.annotation_id" @blur="store.hovered=null" :style="{...annotationVisual(item.a),left:item.anchor[0]+'px',top:item.anchor[1]+'px'}" :title="item.a.title" :aria-pressed="store.selected===item.a.annotation_id && store.selectedPane===p.side" :aria-label="item.number+' '+item.a.title" @wheel.prevent.stop="emit('wheel',$event)" @pointerdown.stop @click.stop="store.selected===item.a.annotation_id && store.selectedPane===p.side?store.selected=null:store.conversation(item.a,p.side)">{{item.number}}</button>
    <section v-if="store.selected===item.a.annotation_id && store.selectedPane===p.side && item.anchor[0]>=0&&item.anchor[0]<=p.rect.w&&item.anchor[1]>=0&&item.anchor[1]<=p.rect.h" class="annotation-interactive annotation-popover" :style="{...annotationVisual(item.a),left:Math.max(4,Math.min(p.rect.w-248,item.anchor[0]+22))+'px',top:Math.max(4,Math.min(p.rect.h-160,item.anchor[1]-130))+'px',maxWidth:Math.max(0,p.rect.w-8)+'px'}" @pointerdown.stop>
     <button class="annotation-close" aria-label="关闭批注" @click="store.selected=null">×</button><strong>{{item.a.title}}</strong><small>{{item.a.source==='mock'?'Mock 演示 · 非真实分析':'Agent 候选 · 待核实'}}</small><p>{{item.a.summary}}</p><button @click="store.conversation(item.a,p.side)">查看关联对话 ↗</button>
    </section>
   </template>
  </div>
 </div>
</template>
<style scoped>
.annotation-overlay{position:absolute;top:0;left:50%;transform:translateX(-50%);pointer-events:none;z-index:4}.annotation-pane{position:absolute;overflow:hidden}.annotation-geometry{position:absolute;pointer-events:none}polygon{stroke:var(--annotation-color,#287f8b);animation:annotation-in 160ms ease-out;stroke-width:1;stroke-dasharray:5 3;fill:var(--annotation-soft)}polygon.selected{stroke-width:2;fill:var(--annotation-soft)}.annotation-interactive{pointer-events:auto}.annotation-marker{position:absolute;transform:translate(-50%,-50%);width:36px;height:36px;border:5px solid transparent;border-radius:50%;background:var(--annotation-color,#287f8b);background-clip:padding-box;color:white;font-size:11px;box-shadow:inset 0 0 0 2px white;cursor:pointer;transition:background-color 160ms,filter 160ms,box-shadow 160ms}.annotation-marker:hover,.annotation-marker.hovered{filter:brightness(.92);box-shadow:inset 0 0 0 2px white,0 1px 4px #0002}.annotation-marker.selected{background-color:var(--annotation-selected,#155c66)}.annotation-marker:focus-visible{outline:2px solid var(--annotation-color,#287f8b)}.annotation-popover{animation:annotation-in 160ms ease-out;position:absolute;box-sizing:border-box;width:240px;max-height:160px;overflow:auto;background:white;border:1px solid #d8e4df;border-radius:11px;padding:12px;box-shadow:0 4px 16px #0002;color:#203e36;font-size:12px}.annotation-popover strong{font-size:13px;display:block;padding-right:18px}.annotation-popover small{display:block;color:#687a74;margin:5px 0}.annotation-popover p{line-height:1.5;margin:6px 0}.annotation-popover button{border:0;background:none;color:var(--annotation-selected,#155c66);cursor:pointer}
.annotation-close{position:absolute;right:7px;top:7px;width:24px;height:24px;border-radius:50%;font-size:17px}.annotation-close:hover{background:var(--annotation-soft)}@keyframes annotation-in{from{opacity:0}to{opacity:1}}@media(prefers-reduced-motion:reduce){.annotation-marker{transition:none}polygon,.annotation-popover{animation:none}}
.annotation-region{opacity:1;transition:opacity 90ms linear}.annotation-region.fading{opacity:0}.annotation-bbox{stroke:var(--annotation-color,#287f8b);stroke-width:1.75;stroke-linejoin:round;fill:var(--annotation-soft);stroke-dasharray:1;stroke-dashoffset:0;animation:annotation-bbox-close 450ms linear both}.annotation-connector{stroke:var(--annotation-color,#287f8b);stroke-width:1;opacity:.5;fill:none}@keyframes annotation-bbox-close{0%{stroke-dashoffset:1;fill-opacity:0}90%{fill-opacity:0}100%{stroke-dashoffset:0;fill-opacity:1}}@media(prefers-reduced-motion:reduce){.annotation-region{transition:none}.annotation-bbox{animation:none;stroke-dashoffset:0}}
</style>
