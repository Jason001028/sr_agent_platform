<script setup lang="ts">
import { TOUR_ROUTES, tourGridOrder } from '../lib/autoTour';
import type { TourRoute } from '../lib/autoTour';
defineProps<{modelValue:TourRoute;disabled?:boolean}>();
const emit=defineEmits<{ 'update:modelValue':[value:TourRoute] }>();
const routes=TOUR_ROUTES.map(route=>{
 const points=tourGridOrder(5,4,route.value).map(p=>({x:10+p.x*15,y:8+p.y*10}));
 const last=points.at(-1)!,previous=points.at(-2)!;
 const angle=Math.atan2(last.y-previous.y,last.x-previous.x)*180/Math.PI;
 return {...route,path:points.map(p=>`${p.x},${p.y}`).join(' '),last,angle};
});
function navigate(e:KeyboardEvent,index:number){
 const delta=({ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1} as Record<string,number>)[e.key];
 const next=e.key==='Home'?0:e.key==='End'?routes.length-1:delta===undefined?null:(index+delta+routes.length)%routes.length;
 if(next===null)return;
 e.preventDefault();emit('update:modelValue',routes[next].value);
 (e.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLButtonElement>('.route-card')[next].focus();
}
</script>
<template>
 <fieldset class="route-picker" :disabled="disabled">
  <legend>巡览路线<span>选择行进方式</span></legend>
  <div class="route-options" role="radiogroup" aria-label="巡览路线">
   <button v-for="(route,i) in routes" :key="route.value" class="route-card" :class="{selected:modelValue===route.value}" type="button" role="radio" :aria-checked="modelValue===route.value" :aria-label="route.label+(route.value==='rows'?'（默认）':'')" :tabindex="modelValue===route.value?0:-1" :title="route.description" :data-route="route.value" @click="emit('update:modelValue',route.value)" @keydown="navigate($event,i)">
    <svg viewBox="0 0 80 46" fill="none" aria-hidden="true">
     <path class="route-guide" d="M10 8H70M10 18H70M10 28H70M10 38H70M10 8V38M25 8V38M40 8V38M55 8V38M70 8V38" />
     <polyline class="route-line" :points="route.path" />
     <circle cx="10" cy="8" r="3" class="route-start" />
     <path class="route-arrow" d="M-3-2.5 0 0-3 2.5" :transform="`translate(${route.last.x},${route.last.y}) rotate(${route.angle})`" />
    </svg>
    <span class="route-label">{{ route.label }}<span v-if="route.value==='rows'" class="default-mark">默认</span></span>
    <span v-if="modelValue===route.value" class="route-check" aria-hidden="true"><svg viewBox="0 0 10 10"><path d="m2 5 2 2 4-4" /></svg></span>
   </button>
  </div>
 </fieldset>
</template>
<style scoped>
.route-picker{border:0;padding:0;margin:0;min-width:0}.route-picker legend{display:flex;align-items:center;justify-content:space-between;width:100%;padding:0;margin-bottom:7px;color:var(--ink);font-size:11px;font-weight:600;letter-spacing:.02em}.route-picker legend>span{font-size:10px;font-weight:400;color:var(--ink-sub);letter-spacing:0}
.route-options{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.route-card{position:relative;display:flex;flex-direction:column;align-items:center;gap:4px;min-width:0;padding:9px 5px 7px;border:1px solid var(--tb-border);border-radius:8px;background:var(--tb-surface);color:var(--ink-sub);font:inherit;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard),border-color var(--motion-hover) var(--ease-standard)}
.route-card>svg{width:76px;max-width:100%;height:40px;overflow:visible}.route-guide{stroke:currentColor;stroke-opacity:.12;stroke-width:.6;stroke-dasharray:1 3}.route-line{stroke:currentColor;stroke-opacity:.7;stroke-width:1.25;stroke-linecap:round;stroke-linejoin:round}.route-start{fill:var(--surface);stroke:currentColor;stroke-width:1.5}.route-arrow{stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.route-label{display:flex;align-items:center;gap:3px;font-size:10px;line-height:14px;white-space:nowrap}.default-mark{font-size:8px;line-height:12px;padding:0 3px;border-radius:3px;background:color-mix(in srgb,currentColor 8%,transparent);opacity:.8}
.route-card:hover{background:var(--tb-active-bg)}.route-card.selected{color:var(--tb-primary);border-color:color-mix(in srgb,var(--tb-primary) 55%,var(--tb-border));background:color-mix(in srgb,var(--tb-active-bg) 65%,var(--surface));box-shadow:inset 0 0 0 .5px color-mix(in srgb,var(--tb-primary) 18%,transparent)}.selected .route-line{stroke-opacity:1}
.route-check{position:absolute;right:5px;top:5px;width:11px;height:11px;border-radius:50%;display:grid;place-items:center;background:var(--tb-primary);color:white}.route-check svg{width:9px;height:9px;fill:none;stroke:currentColor;stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round}
.route-card:focus-visible{outline:2px solid var(--tb-focus);outline-offset:2px}.route-picker:disabled .route-card{opacity:.5;cursor:default}
</style>
