<script setup lang="ts">
import {ref,watch,nextTick,onMounted,onBeforeUnmount} from 'vue';
import {useViewerStore} from '../stores/viewer';
const store=useViewerStore(); const roots=ref<HTMLCanvasElement[]>([]);
const windowRef=ref<HTMLDivElement|null>(null);
const position=ref<{left:number;top:number}|null>(null);
let drag:{x:number;y:number;left:number;top:number}|null=null;
let observer:ResizeObserver|null=null;
function clamp(left:number,top:number){
 const el=windowRef.value,parent=el?.parentElement;if(!el||!parent)return;
 position.value={left:Math.max(4,Math.min(Math.max(4,parent.clientWidth-el.offsetWidth-4),left)),top:Math.max(4,Math.min(Math.max(4,parent.clientHeight-el.offsetHeight-4),top))};
}
function snap(){
 const el=windowRef.value,parent=el?.parentElement,p=position.value;if(!el||!parent||!p)return;
 clamp(p.left,p.top);const pos=position.value!;
 const right=Math.max(4,parent.clientWidth-el.offsetWidth-4),bottom=Math.max(4,parent.clientHeight-el.offsetHeight-4);
 const distances=[pos.left-4,right-pos.left,pos.top-4,bottom-pos.top];
 switch(distances.indexOf(Math.min(...distances))){case 0:pos.left=4;break;case 1:pos.left=right;break;case 2:pos.top=4;break;case 3:pos.top=bottom;}
}
function startDrag(e:PointerEvent){
 if(e.button!==0||!windowRef.value)return;
 const el=windowRef.value;drag={x:e.clientX,y:e.clientY,left:el.offsetLeft,top:el.offsetTop};
 (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
}
function moveDrag(e:PointerEvent){if(drag)clamp(drag.left+e.clientX-drag.x,drag.top+e.clientY-drag.y);}
function endDrag(){if(drag){drag=null;snap();}}
function moveByKey(e:KeyboardEvent){
 const moves:Record<string,[number,number]>={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]};
 const d=moves[e.key],el=windowRef.value;if(!d||!el)return;
 e.preventDefault();e.stopPropagation();clamp(el.offsetLeft+d[0],el.offsetTop+d[1]);snap();
}
onMounted(()=>{observer=new ResizeObserver(()=>{if(position.value)snap();});if(windowRef.value?.parentElement)observer.observe(windowRef.value.parentElement);if(windowRef.value)observer.observe(windowRef.value);});
onBeforeUnmount(()=>observer?.disconnect());
async function paint(){await nextTick();store.panes.forEach((p,i)=>{const c=roots.value[i],r=p.rec;if(!c||!r?.thumb)return;const ctx=c.getContext('2d');if(!ctx)return;c.width=160;c.height=100;ctx.clearRect(0,0,160,100);ctx.drawImage(r.thumb as unknown as CanvasImageSource,0,0,160,100);ctx.strokeStyle='#3da9a4';ctx.lineWidth=2;const sx=160/r.thumb.width,sy=100/r.thumb.height;ctx.strokeRect(-p.view.ox/p.view.scale*sx,-p.view.oy/p.view.scale*sy,p.rect.w/p.view.scale*sx,p.rect.h/p.view.scale*sy);});}
watch(()=>store.renderTick,paint,{immediate:true});
function navigate(e:PointerEvent,i:number){if(e.type==='pointermove'&&!e.buttons)return;const p=store.panes[i],r=p.rec;if(!r?.thumb||store.drawMode)return;const c=e.currentTarget as HTMLCanvasElement,b=c.getBoundingClientRect();if(e.type==='pointerdown')c.setPointerCapture(e.pointerId);const x=Math.max(0,Math.min(1,(e.clientX-b.left)/b.width))*r.thumb.width,y=Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))*r.thumb.height;p.view.ox=p.rect.w/2-x*p.view.scale;p.view.oy=p.rect.h/2-y*p.view.scale;store.renderTick++;}
</script>
<template>
 <div ref="windowRef" class="navigator" :style="position?{left:position.left+'px',top:position.top+'px',right:'auto',bottom:'auto'}:{}" @mousedown.stop @dblclick.stop @wheel.stop>
  <div class="maps"><canvas v-for="(p,i) in store.panes" :key="p.side" :ref="el=>{if(el)roots[i]=el as HTMLCanvasElement}" :aria-label="p.side+' 影像导航'" @pointerdown.stop="navigate($event,i)" @pointermove.stop="navigate($event,i)" /></div>
  <button class="move-handle" aria-label="移动鹰眼窗口" title="拖动移动，松开吸附边缘；方向键移动" @pointerdown.stop.prevent="startDrag" @pointermove.stop="moveDrag" @pointerup.stop="endDrag" @pointercancel.stop="endDrag" @lostpointercapture="endDrag" @keydown="moveByKey">
   <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1v14M1 8h14M5 4l3-3 3 3M5 12l3 3 3-3M4 5L1 8l3 3M12 5l3 3-3 3" /></svg>
  </button>
 </div>
</template>
<style scoped>
.navigator{position:absolute;bottom:4px;right:4px;border:1px solid #708f81;border-radius:3px;overflow:hidden;z-index:4;box-shadow:0 1px 5px #183c3026}
.maps{display:flex;gap:1px;background:#708f81}
canvas{display:block;width:160px;height:100px;cursor:crosshair;touch-action:none}
.move-handle{position:absolute;top:2px;right:2px;display:grid;place-items:center;width:20px;height:20px;padding:3px;border:0;border-radius:3px;background:#183c3099;color:#f5f8f6;cursor:grab;touch-action:none}
.move-handle:active{cursor:grabbing}.move-handle:focus-visible{outline:2px solid #3da9a4;outline-offset:-2px}
svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.2}
</style>
