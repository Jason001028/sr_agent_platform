<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { useAutoTourStore } from '../stores/autoTour';
import AppIcon from './AppIcon.vue';
const tour=useAutoTourStore(),open=ref(false);
const hidden=ref(document.hidden),reduced=ref(true),loaded=ref(false);
let motion:MediaQueryList|undefined;
const flying=computed(()=>tour.status==='playing'&&!hidden.value&&!reduced.value);
const animatedSrc=`${import.meta.env.BASE_URL}icon/auto-tour-drone-animated.svg`;
watch(flying,()=>{loaded.value=false;},{flush:'sync'});
function visibility(){hidden.value=document.hidden;}
function preference(){reduced.value=motion?.matches??true;}
function start(){if(tour.start())open.value=false;}
function escape(e:KeyboardEvent){if(e.key==='Escape'){open.value=false;tour.stop();}}
function outside(e:Event){if(!(e.target as Element).closest('.tour-entry'))open.value=false;}
onMounted(()=>{document.addEventListener('keydown',escape);document.addEventListener('pointerdown',outside);document.addEventListener('visibilitychange',visibility);motion=matchMedia('(prefers-reduced-motion: reduce)');preference();motion.addEventListener('change',preference);});
onBeforeUnmount(()=>{document.removeEventListener('keydown',escape);document.removeEventListener('pointerdown',outside);document.removeEventListener('visibilitychange',visibility);motion?.removeEventListener('change',preference);});
</script>
<template>
 <span class="tour-entry">
  <button class="tour-button" :class="{on:open||tour.status!=='idle'}" title="自动巡览影像" aria-label="自动巡览影像" :aria-expanded="open" @click="open=!open">
   <span class="tour-icon" :class="{'is-flying':flying&&loaded}">
    <AppIcon class="tour-static" name="drone" :size="16" tint />
    <img v-if="flying" class="tour-animated" :class="{ready:loaded}" :src="animatedSrc" width="16" height="16" alt="" aria-hidden="true" @load="loaded=true" @error="loaded=false" />
   </span>
   <span>自动巡览</span>
  </button>
  <div v-if="open" class="tour-popover" role="dialog" aria-label="自动巡览设置">
   <strong>自动巡览</strong>
   <label>巡览范围<select v-model="tour.range" :disabled="tour.status!=='idle'"><option value="all">整张影像</option><option value="visible">当前可见区域</option></select></label>
   <label>速度<select v-model="tour.speed" :disabled="tour.status!=='idle'"><option value="slow">慢速</option><option value="normal">标准</option><option value="fast">快速</option></select></label>
   <label><span>循环巡览</span><input v-model="tour.loop" type="checkbox" /></label>
   <p>仅辅助浏览，不代表检测或质量验收。</p>
   <p v-if="tour.reason" role="status">{{ tour.reason }}</p>
   <button class="tour-start" @click="start">{{ tour.status==='idle'?'开始巡览':'重新规划并开始' }}</button>
  </div>
 </span>
</template>
<style scoped>
.tour-entry{position:relative;display:inline-flex;flex:none}
.tour-button{display:inline-flex;align-items:center;justify-content:center;gap:6px;flex:none;width:100px;height:32px;padding:0 10px;box-sizing:border-box;white-space:nowrap;font-family:inherit;font-size:13px;font-weight:500;border:1px solid var(--tb-border);border-radius:8px;background:var(--tb-surface);color:var(--tb-fg);cursor:pointer;transition:background var(--motion-hover),color var(--motion-hover)}
.tour-icon{position:relative;display:block;width:16px;height:16px;flex:none}
.tour-static,.tour-animated{position:absolute;inset:0;width:16px;height:16px;pointer-events:none}
.tour-animated{opacity:0;transition:opacity var(--motion-hover) var(--ease-standard)}
.tour-animated.ready{opacity:1}
.tour-icon.is-flying .tour-static{opacity:0;transition:opacity var(--motion-hover) var(--ease-standard)}
@media(prefers-reduced-motion:reduce){.tour-animated{display:none}.tour-icon.is-flying .tour-static{opacity:1;transition:none}}
.tour-button:hover,.tour-button.on{background:var(--tb-active-bg);color:var(--tb-primary)}
.tour-button:focus-visible{outline:2px solid var(--tb-focus)}
.tour-popover{position:absolute;top:calc(100% + 8px);left:0;width:240px;box-sizing:border-box;padding:14px;z-index:40;background:var(--surface);border:1px solid var(--tb-border);border-radius:12px;box-shadow:0 1px 2px #173e3110,0 4px 12px #173e3114,0 12px 32px #173e3114;font-size:12px;color:var(--ink)}
label{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:12px}select{max-width:140px;background:var(--tb-bg);border:1px solid var(--tb-border);border-radius:6px;padding:5px;color:var(--ink)}p{font-size:11px;line-height:1.5;color:var(--ink-sub);white-space:normal}.tour-start{width:100%;padding:8px;border:0;border-radius:8px;background:var(--tb-primary);color:white;cursor:pointer}
</style>
