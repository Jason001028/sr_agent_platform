<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useDiffStore } from '../stores/diff';
import { useViewerStore } from '../stores/viewer';
import { colorizeHeatBytes, drawDiffSource, HEAT_GRADIENT, SMART_HEAT_GRADIENT } from '../lib/diffRender';
import { gridCoverageToThumb, gridRectToThumb } from '../lib/diffLocate';
import type { DiffMethod } from '../lib/diffLocate';
import { fitView, wheelZoom } from '../lib/viewMath';

const diff=useDiffStore(), viewer=useViewerStore();
const methods:DiffMethod[]=['absdiff','ssim','smart'];
const names={absdiff:'像素差异',ssim:'结构差异 SSIM',smart:'智能对比'};
const host=ref<HTMLElement|null>(null), mode=ref<'heat'|'overlay'>('heat');
const camera=ref({scale:1,ox:0,oy:0}), error=ref(''), loading=ref(true);
const canvases=new Map<DiffMethod,HTMLCanvasElement>();
const layers=new Map<DiffMethod,{raw:NonNullable<Awaited<ReturnType<typeof diff.comparisonRaster>>>;heat:HTMLCanvasElement}>();
const source=computed(()=>viewer.recs.find(r=>r.id===diff.aId));
let observer:ResizeObserver|null=null, frame=0, generation=0, width=1,height=1;
let drag:{id:number;x:number;y:number}|null=null;
function basis(){return {w:source.value?.thumb?.width??source.value?.W??1,h:source.value?.thumb?.height??source.value?.H??1};}
function schedule(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;draw();});}
function fit(){const b=basis();camera.value=fitView(b.w,b.h,width,height);schedule();}
function resize(){if(!host.value)return;width=Math.max(1,Math.floor(host.value.clientWidth/3));height=Math.max(1,host.value.clientHeight);fit();}
function colorize(){
  for(const [method,{raw,heat}] of layers){
    heat.width=raw.w;heat.height=raw.h;
    heat.getContext('2d')!.putImageData(new ImageData(colorizeHeatBytes(raw.diff,raw.w,raw.h,raw.valid,null,
      {smart:method==='smart',threshold:method==='smart'?diff.displayThreshold:0}) as Uint8ClampedArray<ArrayBuffer>,raw.w,raw.h),0,0);
  }
  schedule();
}
function draw(){
  const b=basis();
  for(const method of methods){
    const canvas=canvases.get(method),layer=layers.get(method),entry=diff.comparison?.results[method];
    if(!canvas)continue;
    if(canvas.width!==width)canvas.width=width;if(canvas.height!==height)canvas.height=height;
    const ctx=canvas.getContext('2d');if(!ctx)continue;
    ctx.clearRect(0,0,width,height);if(!layer||!entry)continue;
    ctx.save();ctx.translate(camera.value.ox,camera.value.oy);ctx.scale(camera.value.scale,camera.value.scale);
    drawDiffSource(ctx,(source.value?.thumb??layer.heat) as unknown as CanvasImageSource,b.w,b.h,
      {heat:layer.heat,coverage:gridCoverageToThumb(entry,b.w,b.h)},mode.value,diff.opacity,'off');ctx.restore();
  }
}
function point(event:PointerEvent|WheelEvent){const rect=(event.currentTarget as HTMLElement).getBoundingClientRect();return {x:(event.clientX-rect.left)*width/rect.width,y:(event.clientY-rect.top)*height/rect.height};}
function wheel(event:WheelEvent){const p=point(event);camera.value=wheelZoom(camera.value,p.x,p.y,Math.exp(-Math.sign(event.deltaY)*.15));schedule();}
function down(event:PointerEvent){if(event.button!==0)return;const p=point(event);drag={id:event.pointerId,...p};(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);}
function move(event:PointerEvent){if(!drag||drag.id!==event.pointerId)return;const p=point(event);camera.value={...camera.value,ox:camera.value.ox+p.x-drag.x,oy:camera.value.oy+p.y-drag.y};drag={id:event.pointerId,...p};schedule();}
async function load(){
  const gen=++generation;loading.value=true;error.value='';layers.clear();
  try{for(const method of methods){const raw=await diff.comparisonRaster(method);if(gen!==generation)return;if(raw)layers.set(method,{raw,heat:document.createElement('canvas')});}colorize();}
  catch(e){if(gen===generation)error.value=String(e);}
  finally{if(gen===generation)loading.value=false;}
}
watch(()=>diff.comparison,()=>{void load();});
watch(()=>diff.displayThreshold,colorize);
watch([mode,()=>diff.opacity],schedule);
watch(()=>diff.selectedRegion,region=>{
  if(!region||!diff.result)return;
  const b=basis(),[x,y,w,h]=gridRectToThumb(diff.result,region.bbox,b.w,b.h);
  const scale=Math.min(width*.8/Math.max(1,w),height*.8/Math.max(1,h));
  camera.value={scale,ox:width/2-(x+w/2)*scale,oy:height/2-(y+h/2)*scale};schedule();
});
onMounted(()=>{observer=new ResizeObserver(resize);if(host.value)observer.observe(host.value);resize();void load();});
onBeforeUnmount(()=>{generation++;observer?.disconnect();cancelAnimationFrame(frame);layers.clear();canvases.clear();});
</script>

<template>
  <section class="three-views" aria-label="三算法同步对照" data-e2e="diff-three-views" @pointerdown.stop @dblclick.stop @contextmenu.prevent.stop>
    <header><strong>三算法同步对照</strong><span>共用视野 · 滚轮缩放 / 拖动平移</span>
      <select v-model="mode" aria-label="三图显示方式"><option value="heat">独立热力图</option><option value="overlay">叠加原图</option></select>
      <button @click="fit">适配三图</button><button @click="diff.comparisonView=false">返回查看器</button></header>
    <p class="semantics">三幅颜色分别表示像素差异、结构差异和相对纹理损失，不是相同的绝对严重程度；智能评分不是概率。</p>
    <p v-if="loading||error" role="status">{{ error||'正在解码对照结果…' }}</p>
    <div ref="host" class="three-grid">
      <div v-for="method in methods" :key="method" class="method-pane">
        <canvas :ref="el=>{if(el)canvases.set(method,el as HTMLCanvasElement)}" :aria-label="names[method]+'热力图'"
          :data-view-transform="JSON.stringify(camera)" @wheel.prevent="wheel" @pointerdown="down" @pointermove="move" @pointerup="drag=null" @pointercancel="drag=null" @dblclick="fit" />
        <b>{{ names[method] }}</b>
        <div class="method-legend"><i :style="{background:method==='smart'?SMART_HEAT_GRADIENT:HEAT_GRADIENT}"></i><span>0 · 低 — 1 · 高</span><small v-if="method==='smart'">相对异常强度 · 非概率</small></div>
        <p v-if="!diff.comparison?.results[method]" class="failure">{{ diff.comparison?.summary.find(s=>s.method===method)?.error||'该方法无可用结果' }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.three-views{position:absolute;inset:0;z-index:15;display:flex;flex-direction:column;background:var(--surface);color:var(--ink);font-size:12px}
header{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:9px;border-bottom:1px solid var(--line)}
header span,.semantics{color:var(--ink-sub)}header strong{margin-right:auto}button,select{font:inherit;color:var(--ink);background:var(--surface-2);border:1px solid var(--line);border-radius:5px;padding:5px;cursor:pointer}
.semantics{margin:7px 10px;font-size:11px}.three-grid{display:flex;flex:1;min-height:0;overflow:hidden}.method-pane{position:relative;flex:1;min-width:0;background:#202c39;border-right:1px solid var(--line);overflow:hidden}
canvas{display:block;width:100%;height:100%;cursor:grab;touch-action:none}.method-pane b{position:absolute;left:8px;top:8px;background:var(--surface);padding:4px 7px;border-radius:4px;pointer-events:none}.failure{position:absolute;inset:40% 10px auto;color:#fff;text-align:center}
.method-legend{position:absolute;bottom:8px;left:8px;right:8px;max-width:200px;background:var(--surface);padding:6px;border-radius:5px;pointer-events:none;font-size:10px}.method-legend i{display:block;height:7px}.method-legend span,.method-legend small{display:block;margin-top:3px;color:var(--ink-sub)}
</style>
