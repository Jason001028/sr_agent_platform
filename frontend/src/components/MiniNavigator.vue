<script setup lang="ts">
import {ref,watch,nextTick,onMounted,onBeforeUnmount} from 'vue';
import {useViewerStore} from '../stores/viewer';
const store=useViewerStore(); const roots=ref<HTMLCanvasElement[]>([]);
const windowRef=ref<HTMLDivElement|null>(null);
const position=ref<{left:number;top:number}|null>(null);
const visible=ref(true);
const settingsOpen=ref(false);
const opacity=ref(100);
const size=ref(100);
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
async function paint(){
 await nextTick();
 if(!visible.value)return;
 store.panes.forEach((p,i)=>{
  const c=roots.value[i],r=p.rec;if(!c||!r?.thumb)return;
  const ctx=c.getContext('2d');if(!ctx||!r.thumb.width||!r.thumb.height)return;
  // Fit inside the navigator bounds without stretching the source image.
  const scale=Math.min(320/r.thumb.width,200/r.thumb.height)*size.value/100;
  c.width=Math.max(1,Math.round(r.thumb.width*scale));
  c.height=Math.max(1,Math.round(r.thumb.height*scale));
  ctx.drawImage(r.thumb as unknown as CanvasImageSource,0,0,c.width,c.height);
  ctx.strokeStyle='#3da9a4';ctx.lineWidth=2;
  const sx=c.width/r.thumb.width,sy=c.height/r.thumb.height;
  ctx.strokeRect(-p.view.ox/p.view.scale*sx,-p.view.oy/p.view.scale*sy,p.rect.w/p.view.scale*sx,p.rect.h/p.view.scale*sy);
 });
}
watch(()=>[store.renderTick,size.value,visible.value],paint,{immediate:true});
function navigate(e:PointerEvent,i:number){if(e.type==='pointermove'&&!e.buttons)return;const p=store.panes[i],r=p.rec;if(!r?.thumb||store.drawMode)return;const c=e.currentTarget as HTMLCanvasElement,b=c.getBoundingClientRect();if(e.type==='pointerdown')c.setPointerCapture(e.pointerId);const x=Math.max(0,Math.min(1,(e.clientX-b.left)/b.width))*r.thumb.width,y=Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))*r.thumb.height;p.view.ox=p.rect.w/2-x*p.view.scale;p.view.oy=p.rect.h/2-y*p.view.scale;store.renderTick++;}
</script>
<template>
 <button v-if="!visible" class="reopen" @mousedown.stop @dblclick.stop @click.stop="visible=true" title="显示鹰眼导航">鹰眼</button>
 <div v-show="visible" ref="windowRef" class="navigator" :style="position?{left:position.left+'px',top:position.top+'px',right:'auto',bottom:'auto'}:{}" @mousedown.stop @dblclick.stop @wheel.stop>
  <div class="navigator-tools">
  <button class="move-handle" aria-label="移动鹰眼窗口" title="拖动移动，松开吸附边缘；方向键移动" @pointerdown.stop.prevent="startDrag" @pointermove.stop="moveDrag" @pointerup.stop="endDrag" @pointercancel.stop="endDrag" @lostpointercapture="endDrag" @keydown="moveByKey">
   <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1v14M1 8h14M5 4l3-3 3 3M5 12l3 3 3-3M4 5L1 8l3 3M12 5l3 3-3 3" /></svg>
  </button>
   <button class="settings-toggle" aria-label="鹰眼显示设置" :aria-expanded="settingsOpen" title="调整不透明度和大小" @click="settingsOpen=!settingsOpen">⚙</button>
   <button aria-label="关闭鹰眼导航" title="关闭鹰眼导航" @click="visible=false">×</button>
  </div>
  <div v-if="settingsOpen" class="settings">
   <label>不透明度 <output>{{ opacity }}%</output><input v-model.number="opacity" type="range" min="10" max="100" step="5" aria-label="鹰眼不透明度" /></label>
   <label>大小 <output>{{ size }}%</output><input v-model.number="size" type="range" min="50" max="200" step="10" aria-label="鹰眼相对大小" /></label>
  </div>
  <div class="maps" :style="{opacity:opacity/100}"><canvas v-for="(p,i) in store.panes" v-show="p.rec?.thumb" :key="p.side" :ref="el=>{if(el)roots[i]=el as HTMLCanvasElement}" :aria-label="p.side+' 影像导航'" @pointerdown.stop="navigate($event,i)" @pointermove.stop="navigate($event,i)" /></div>
 </div>
</template>
<style scoped>
.navigator{position:absolute;bottom:4px;right:4px;border:1px solid #708f81;border-radius:3px;overflow:hidden;z-index:4;box-shadow:0 1px 5px #183c3026}
.maps{display:flex;align-items:flex-start;gap:1px}
canvas{display:block;width:auto;height:auto;flex:none;cursor:crosshair;touch-action:none}
.navigator-tools{display:flex;align-items:center;gap:2px;padding:2px;background:#edf3f0}
.navigator-tools button{display:grid;place-items:center;width:22px;height:22px;padding:3px;border:0;border-radius:3px;background:transparent;color:#244c3b;cursor:pointer;font-size:16px}
.navigator-tools button:hover{background:#d4e3dc}
.navigator-tools .move-handle{cursor:grab;touch-action:none}
.settings-toggle{margin-left:auto}
.settings{width:180px;box-sizing:border-box;padding:6px 8px;background:#edf3f0;color:#244c3b;font-size:12px}
.settings label{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:4px}
.settings label+label{margin-top:6px}.settings input{width:100%;margin:0;accent-color:#3da9a4}
.reopen{position:absolute;right:4px;bottom:4px;z-index:4;padding:4px 9px;border:1px solid #708f81;border-radius:3px;background:#edf3f0;color:#244c3b;cursor:pointer}
.move-handle:active{cursor:grabbing}.move-handle:focus-visible{outline:2px solid #3da9a4;outline-offset:-2px}
svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.2}
</style>
