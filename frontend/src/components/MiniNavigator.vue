<script setup lang="ts">
import {ref,watch,nextTick,onMounted,onBeforeUnmount} from 'vue';
import {useViewerStore} from '../stores/viewer';
import {useAutoTourStore} from '../stores/autoTour';
const tour=useAutoTourStore();
const store=useViewerStore(); const roots=ref<HTMLCanvasElement[]>([]);
const windowRef=ref<HTMLDivElement|null>(null);
const position=ref<{left:number;top:number}|null>(null);
const VISIBILITY_KEY='sr.viewer.minimap.visible';
function restoredVisibility(){try{return localStorage.getItem(VISIBILITY_KEY)!=='0';}catch{return true;}}
const visible=ref(restoredVisibility());
watch(visible,(value)=>{try{localStorage.setItem(VISIBILITY_KEY,value?'1':'0');}catch{/* retain session state */}if(!value)settingsOpen.value=false;});
const settingsOpen=ref(false);
const opacity=ref(100);
const size=ref(100);
let drag:{x:number;y:number;left:number;top:number}|null=null;
let observer:ResizeObserver|null=null;
function clamp(left:number,top:number){
 const el=windowRef.value,parent=el?.parentElement;if(!el||!parent)return;
 position.value={left:Math.max(14,Math.min(Math.max(14,parent.clientWidth-el.offsetWidth-14),left)),top:Math.max(14,Math.min(Math.max(14,parent.clientHeight-el.offsetHeight-14),top))};
}
function snap(){
 const el=windowRef.value,parent=el?.parentElement,p=position.value;if(!el||!parent||!p)return;
 clamp(p.left,p.top);const pos=position.value!;
 const right=Math.max(14,parent.clientWidth-el.offsetWidth-14),bottom=Math.max(14,parent.clientHeight-el.offsetHeight-14);
 const distances=[pos.left-14,right-pos.left,pos.top-14,bottom-pos.top];
 switch(distances.indexOf(Math.min(...distances))){case 0:pos.left=14;break;case 1:pos.left=right;break;case 2:pos.top=14;break;case 3:pos.top=bottom;}
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
  const route=tour.plan;
  if(route && p.rec?.id===store.activeRec?.id){
   ctx.strokeStyle='rgba(22,108,103,.4)';ctx.lineWidth=.75;ctx.beginPath();
   route.points.forEach((point,j)=>{const x=point.x/route.width*c.width,y=point.y/route.height*c.height;if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
   if(tour.current){ctx.fillStyle='#166c67';ctx.beginPath();ctx.arc(tour.current.x/route.width*c.width,tour.current.y/route.height*c.height,2.5,0,Math.PI*2);ctx.fill();}
  }
  ctx.strokeStyle='#3da9a4';ctx.lineWidth=1.75;
  ctx.shadowColor='rgba(10,35,28,.8)';ctx.shadowBlur=2;
  const sx=c.width/r.thumb.width,sy=c.height/r.thumb.height;
  ctx.strokeRect(-p.view.ox/p.view.scale*sx,-p.view.oy/p.view.scale*sy,p.rect.w/p.view.scale*sx,p.rect.h/p.view.scale*sy);
 });
}
watch(()=>[store.renderTick,size.value,visible.value,tour.plan],paint,{immediate:true});
function navigate(e:PointerEvent,i:number){if(e.type==='pointermove'&&!e.buttons)return;const p=store.panes[i],r=p.rec;if(!r?.thumb||store.drawMode)return;const c=e.currentTarget as HTMLCanvasElement,b=c.getBoundingClientRect();if(e.type==='pointerdown')c.setPointerCapture(e.pointerId);const x=Math.max(0,Math.min(1,(e.clientX-b.left)/b.width))*r.thumb.width,y=Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))*r.thumb.height;store.noteUserIntent();store.setPaneView(p.side,{...p.view,ox:p.rect.w/2-x*p.view.scale,oy:p.rect.h/2-y*p.view.scale});}
</script>
<template>
 <Transition name="minimap">
 <button v-if="!visible" type="button" class="reopen" aria-label="展开鹰眼导航" aria-expanded="false" @pointerdown.stop @mousedown.stop @dblclick.stop @click.stop="visible=true" title="展开鹰眼导航">
  <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2" width="12" height="12" rx="2"/><rect x="5" y="5" width="6" height="5" rx=".5"/><path d="M8 7v4M6 9h4"/></svg>
 </button>
 </Transition>
 <Transition name="minimap">
 <div v-show="visible" ref="windowRef" class="navigator" aria-label="鹰眼导航" :style="position?{left:position.left+'px',top:position.top+'px',right:'auto',bottom:'auto'}:{}" @mousedown.stop @dblclick.stop @wheel.stop>
  <div class="navigator-tools">
  <button class="move-handle" aria-label="移动鹰眼窗口" title="拖动移动，松开吸附边缘；方向键移动" @pointerdown.stop.prevent="startDrag" @pointermove.stop="moveDrag" @pointerup.stop="endDrag" @pointercancel.stop="endDrag" @lostpointercapture="endDrag" @keydown="moveByKey">
   <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1v14M1 8h14M5 4l3-3 3 3M5 12l3 3 3-3M4 5L1 8l3 3M12 5l3 3-3 3" /></svg>
  </button>
   <button class="settings-toggle" aria-label="鹰眼显示设置" :aria-expanded="settingsOpen" title="调整不透明度和大小" @pointerdown.stop @click.stop="settingsOpen=!settingsOpen">⚙</button>
   <button aria-label="关闭鹰眼导航" title="收起鹰眼导航" aria-expanded="true" @pointerdown.stop @click.stop="visible=false">×</button>
  </div>
  <div v-if="settingsOpen" class="settings" @pointerdown.stop @keydown.esc.stop="settingsOpen=false">
   <label>不透明度 <output>{{ opacity }}%</output><input v-model.number="opacity" type="range" min="10" max="100" step="5" aria-label="鹰眼不透明度" /></label>
   <label>大小 <output>{{ size }}%</output><input v-model.number="size" type="range" min="50" max="200" step="10" aria-label="鹰眼相对大小" /></label>
  </div>
  <div class="maps" :style="{opacity:opacity/100}"><canvas v-for="(p,i) in store.panes" v-show="p.rec?.thumb" :key="p.side" :ref="el=>{if(el)roots[i]=el as HTMLCanvasElement}" :aria-label="p.side+' 影像导航'" @pointerdown.stop="navigate($event,i)" @pointermove.stop="navigate($event,i)" /></div>
 </div>
 </Transition>
</template>
<style scoped>
.navigator{position:absolute;bottom:14px;right:14px;border:1px solid var(--tb-border);border-radius:10px;z-index:4;background:var(--tb-bg);box-shadow:0 2px 5px #183c3014,0 8px 24px #183c3024;transform-origin:bottom right}
.maps{display:flex;align-items:flex-start;gap:1px;border-radius:9px;overflow:hidden}
canvas{display:block;width:auto;height:auto;flex:none;cursor:crosshair;touch-action:none}
.navigator-tools{position:absolute;left:6px;right:6px;top:6px;z-index:2;display:flex;align-items:center;gap:4px;pointer-events:none;opacity:.45;transition:opacity var(--motion-hover) var(--ease-standard)}
.navigator:hover .navigator-tools,.navigator:focus-within .navigator-tools{opacity:1}
.navigator-tools button{pointer-events:auto;display:grid;place-items:center;width:28px;height:28px;flex:none;padding:4px;border:1px solid var(--tb-border);border-radius:8px;background:var(--tb-bg);color:var(--tb-fg);box-shadow:0 1px 3px #183c3014;cursor:pointer;font-size:16px;transition:background var(--motion-hover),color var(--motion-hover)}
.navigator-tools button:hover{background:var(--tb-hover)}
.navigator-tools button:focus-visible,.reopen:focus-visible{outline:2px solid var(--tb-focus);outline-offset:2px}
.navigator-tools .move-handle{cursor:grab;touch-action:none}
.settings-toggle{margin-left:auto}
.settings{position:absolute;bottom:calc(100% + 8px);right:0;z-index:3;width:190px;box-sizing:border-box;padding:12px;background:var(--tb-bg);color:var(--tb-fg);font-size:12px;border:1px solid var(--tb-border);border-radius:10px;box-shadow:0 2px 5px #183c3014,0 8px 24px #183c3024}
.settings label{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:4px}
.settings label+label{margin-top:6px}.settings input{width:100%;margin:0;accent-color:#3da9a4}
.reopen{position:absolute;right:14px;bottom:14px;z-index:4;display:grid;place-items:center;width:42px;height:42px;padding:10px;border:1px solid var(--tb-border);border-radius:10px;background:var(--tb-bg);color:var(--tb-primary);cursor:pointer;box-shadow:0 2px 5px #183c3014,0 8px 24px #183c3024}
.reopen svg{width:20px;height:20px}
.minimap-enter-active,.minimap-leave-active{transition:opacity var(--motion-live) var(--ease-standard),transform var(--motion-live) var(--ease-standard)}
.minimap-enter-from,.minimap-leave-to{opacity:0;transform:scale(.985)}
.minimap-leave-active{pointer-events:none}
@media(prefers-reduced-motion:reduce){.minimap-enter-active,.minimap-leave-active,.navigator-tools,.navigator-tools button{transition:none}.minimap-enter-from,.minimap-leave-to{transform:none}}
.move-handle:active{cursor:grabbing}.move-handle:focus-visible{outline:2px solid #3da9a4;outline-offset:-2px}
svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.2}
</style>
