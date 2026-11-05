<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { useAutoTourStore } from '../stores/autoTour';
import AppIcon from './AppIcon.vue';
import TourRoutePicker from './TourRoutePicker.vue';
const tour=useAutoTourStore(),open=ref(false);
const INTERVALS=[200,300,400,500,600,700,800,900,1000,1100,1200,1300,1400,1500,1600];
import { useViewerStore } from '../stores/viewer';
const viewer=useViewerStore();
const locked=computed(()=>tour.status!=='idle');
const pairNotice=computed(()=>[...new Set([tour.pairMessage,tour.pairReason].filter(Boolean))].join('；'));
watch(()=>tour.compare,enabled=>{if(!enabled){tour.cancelPreparation();if(tour.status==='idle'&&viewer.compareMode==='flicker')viewer.setCompareMode('off');}});
watch(()=>[open.value,tour.compare,viewer.activeRec?.id],()=>{if(open.value&&tour.compare&&tour.status==='idle')void tour.discoverPairs();});
const hidden=ref(document.hidden),reduced=ref(true),loaded=ref(false);
let motion:MediaQueryList|undefined;
const flying=computed(()=>tour.status==='playing'&&!hidden.value&&!reduced.value);
const animatedSrc=`${import.meta.env.BASE_URL}icon/auto-tour-drone-animated.svg`;
watch(flying,()=>{loaded.value=false;},{flush:'sync'});
function visibility(){hidden.value=document.hidden;if(hidden.value)tour.cancelPreparation();}
function preference(){reduced.value=motion?.matches??true;}
function start(){if(tour.start())open.value=false;}
function escape(e:KeyboardEvent){if(e.key==='Escape'){open.value=false;tour.stop();}}
function outside(e:Event){if(!(e.target as Element).closest('.tour-entry'))open.value=false;}
onMounted(()=>{document.addEventListener('keydown',escape);document.addEventListener('pointerdown',outside);document.addEventListener('visibilitychange',visibility);motion=matchMedia('(prefers-reduced-motion: reduce)');preference();motion.addEventListener('change',preference);});
onBeforeUnmount(()=>{tour.cancelPreparation();document.removeEventListener('keydown',escape);document.removeEventListener('pointerdown',outside);document.removeEventListener('visibilitychange',visibility);motion?.removeEventListener('change',preference);});
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
   <header class="tour-heading">
    <span class="heading-icon"><AppIcon name="drone" :size="16" tint /></span>
    <div><strong>自动巡览</strong><span class="heading-caption">设定路线，细看每一处</span></div>
    <button class="tour-close" type="button" aria-label="关闭巡览设置" title="关闭设置" @click="open=false"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5 5 6 6M11 5l-6 6" /></svg></button>
   </header>
   <div class="tour-body">
    <TourRoutePicker v-model="tour.route" :disabled="locked" />
    <div class="tour-grid tour-navigation">
     <label class="tour-field"><span>巡览范围</span><select v-model="tour.range" aria-label="巡览范围" :disabled="locked"><option value="all">整张影像</option><option value="visible">当前可见区域</option></select></label>
     <label class="tour-field"><span>行进速度</span><select v-model="tour.speed" aria-label="巡览速度" :disabled="locked"><option value="slow">{{ tour.compare?'40 px/s · 慢速':'慢速' }}</option><option value="normal">{{ tour.compare?'60 px/s · 标准':'标准' }}</option><option value="fast">{{ tour.compare?'80 px/s · 快速':'快速' }}</option><option value="ultra">{{ tour.compare?'120 px/s · 极速':'极速' }}</option></select></label>
    </div>
    <section class="tour-compare-section" aria-label="交替设置">
     <label class="tour-toggle"><span><span class="toggle-title">交替对比</span><span class="toggle-caption">双图共用同一条路线</span></span><input v-model="tour.compare" class="tour-switch" aria-label="交替对比" type="checkbox" role="switch" :disabled="locked" /></label>
     <div v-if="tour.compare" class="compare-settings">
      <label class="tour-field"><span>影像配对</span><select v-model.number="tour.selected" aria-label="影像配对" :disabled="locked||tour.pairLoading"><option :value="-1">{{ tour.pairLoading?'正在核对配对…':'请选择配对' }}</option><option v-for="(pair,i) in tour.pairs" :key="i" :value="i" :disabled="!!pair.refusal" :title="pair.refusal??pair.label">{{ pair.label }}{{ pair.refusal?'（不可用：'+pair.refusal+'）':'' }}</option></select></label>
      <div class="pair-actions"><button :disabled="tour.pairLoading||locked" @click="tour.discoverPairs">刷新配对</button><button :disabled="!tour.selectedPair||!!tour.selectedPair.refusal||tour.pairLoading||locked" @click="tour.preparePair">{{ tour.pairLoading?'正在载入…':'载入配对' }}</button><button :disabled="!!tour.pairReason||tour.status==='playing'" @click="viewer.setTourLayer(!viewer.flickerShowingAlt)">手动切换</button></div>
      <div class="tour-grid">
       <label class="tour-field"><span>交替模式</span><select v-model="tour.mode" aria-label="交替模式" :disabled="locked"><option value="stop">驻停交替</option><option value="move">移动交替</option></select></label>
       <label class="tour-field"><span>切换间隔</span><select v-model.number="tour.interval" aria-label="切换间隔" :disabled="locked"><option v-for="ms in INTERVALS" :key="ms" :value="ms">{{ (ms/1000).toFixed(1) }} 秒</option></select></label>
      </div>
      <p v-if="pairNotice" class="tour-notice" role="status">{{ pairNotice }}</p>
     </div>
    </section>
    <label class="tour-toggle tour-loop"><span class="toggle-title">循环巡览</span><input v-model="tour.loop" class="tour-switch" aria-label="循环巡览" type="checkbox" role="switch" /></label>
    <p v-if="tour.reason" class="tour-notice" role="status">{{ tour.reason }}</p>
   </div>
   <footer class="tour-footer">
    <button class="tour-start" :disabled="tour.compare&&(tour.pairLoading||!!tour.pairReason)" @click="start"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="m4 2 6 4-6 4Z" /></svg>{{ tour.status==='idle'?'开始巡览':'重新规划并开始' }}</button>
    <p>仅辅助浏览，不代表检测或质量验收</p>
   </footer>
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
.tour-popover{position:absolute;top:calc(100% + 8px);right:0;width:328px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 116px);overflow:auto;box-sizing:border-box;z-index:40;background:var(--surface);border:1px solid var(--tb-border);border-radius:12px;box-shadow:0 1px 2px #173e3110,0 4px 12px #173e3114,0 12px 32px #173e3114;font-size:11px;color:var(--ink);scrollbar-width:thin}
.tour-heading{display:flex;align-items:center;gap:8px;padding:12px 13px 10px;border-bottom:1px solid var(--tb-divider)}.heading-icon{display:grid;place-items:center;width:29px;height:29px;border:1px solid color-mix(in srgb,var(--tb-primary) 15%,var(--tb-border));border-radius:8px;background:var(--tb-active-bg);color:var(--tb-primary)}.tour-heading strong{display:block;font-size:12px;line-height:17px;font-weight:650;letter-spacing:.03em}.heading-caption{display:block;font-size:10px;line-height:15px;color:var(--ink-sub)}
.tour-close{margin-left:auto;align-self:flex-start;border:0;background:transparent;display:grid;place-items:center;width:24px;height:24px;border-radius:5px;color:var(--ink-sub);cursor:pointer}.tour-close svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.2;stroke-linecap:round}.tour-close:hover{background:var(--tb-active-bg);color:var(--ink)}
.tour-body{padding:11px 13px 0}.tour-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.tour-navigation{margin-top:11px}.tour-field{display:flex;flex-direction:column;gap:5px;min-width:0}.tour-field>span{font-size:10px;color:var(--ink-sub);line-height:14px}.tour-field select{width:100%;min-width:0;height:28px;box-sizing:border-box;border:1px solid var(--tb-border);border-radius:6px;background:var(--tb-surface);padding:0 7px;color:var(--ink);font:inherit;font-size:11px;cursor:pointer}
.tour-compare-section{margin-top:11px;border-top:1px solid var(--tb-divider);padding-top:10px}.tour-toggle{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:24px;cursor:pointer}.toggle-title{font-size:11px;line-height:16px;font-weight:500}.toggle-caption{margin-left:7px;font-size:9px;line-height:14px;color:var(--ink-sub)}
.tour-switch{appearance:none;-webkit-appearance:none;flex:none;position:relative;width:28px;height:16px;border:1px solid var(--tb-border);border-radius:20px;margin:0;background:color-mix(in srgb,var(--ink-sub) 18%,var(--surface));cursor:pointer;transition:background var(--motion-hover) var(--ease-standard),border-color var(--motion-hover) var(--ease-standard)}.tour-switch::after{content:'';position:absolute;left:2px;top:2px;width:10px;height:10px;border-radius:50%;background:var(--surface);box-shadow:0 1px 2px #173e3129;transition:transform var(--motion-hover) var(--ease-standard)}.tour-switch:checked{background:var(--tb-primary);border-color:var(--tb-primary)}.tour-switch:checked::after{transform:translateX(12px)}
.compare-settings{display:grid;gap:8px;margin-top:8px}.pair-actions{display:flex;gap:5px}.pair-actions button{flex:1;min-width:0;height:25px;box-sizing:border-box;font:inherit;font-size:10px;border:1px solid var(--tb-border);border-radius:5px;background:var(--tb-surface);color:var(--ink-sub);padding:0 5px;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard)}.pair-actions button:hover:enabled{background:var(--tb-active-bg);color:var(--tb-primary)}
.tour-notice{padding:6px 7px;margin:0;border-radius:5px;background:var(--tb-bg);font-size:10px;line-height:15px;color:var(--ink-sub);white-space:normal;overflow-wrap:anywhere;max-height:60px;overflow:auto}.tour-loop{padding:9px 0;border-top:1px solid var(--tb-divider);margin-top:10px}.tour-body>.tour-notice{margin-bottom:8px}
.tour-footer{padding:9px 13px 10px;border-top:1px solid var(--tb-divider);background:color-mix(in srgb,var(--tb-bg) 45%,var(--surface));border-radius:0 0 12px 12px}.tour-start{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;min-height:31px;padding:6px;border:1px solid transparent;border-radius:7px;background:var(--tb-primary);color:white;font:inherit;font-size:11px;font-weight:600;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard)}.tour-start svg{width:11px;height:11px;fill:currentColor}.tour-start:hover:enabled{background:color-mix(in srgb,var(--tb-primary) 88%,black)}.tour-footer p{margin:6px 0 0;text-align:center;font-size:9px;line-height:13px;color:var(--ink-sub)}
button:disabled,select:disabled,input:disabled{opacity:.45;cursor:default}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--tb-focus);outline-offset:2px}
</style>
