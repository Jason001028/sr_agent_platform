<script setup lang="ts">
import { onMounted,onBeforeUnmount } from 'vue';
import { useAutoTourStore } from '../stores/autoTour';
const tour=useAutoTourStore();
import { useViewerStore } from '../stores/viewer';
const viewer=useViewerStore();
function hidden(){if(document.hidden)tour.pause('已暂停 · 页面隐藏');}
let media:MediaQueryList;
function reduced(){if(media.matches)tour.pause('已暂停 · 减少动效');}
onMounted(()=>{document.addEventListener('visibilitychange',hidden);media=matchMedia('(prefers-reduced-motion: reduce)');media.addEventListener('change',reduced);});
onBeforeUnmount(()=>{tour.stop();document.removeEventListener('visibilitychange',hidden);media?.removeEventListener('change',reduced);});
</script>
<template>
 <Transition name="tour-capsule"><div v-if="tour.status!=='idle'" class="tour-capsule" aria-label="巡览控制" @pointerdown.stop @mousedown.stop @click.stop @wheel.stop @dblclick.stop>
  <template v-if="tour.compare"><span>{{ viewer.flickerPairName }} · {{ tour.mode==='stop'?'驻停交替':'移动交替' }}</span><span class="tour-image" :title="viewer.flickerCurrentName">{{ viewer.flickerCurrentName }}</span></template>
  <button @click="tour.status==='playing'?tour.pause():tour.resume()">{{ tour.status==='playing'?'暂停':'继续' }}</button>
  <span role="status">{{ tour.status==='playing'?'播放中':'已暂停' }}</span>
  <span>{{ {slow:'慢速',normal:'标准',fast:'快速',ultra:'极速',teleport:'瞬移'}[tour.speed] }}</span>
  <span :aria-label="'路线进度 '+tour.progress+'%'">{{ tour.progress }}%</span>
  <span v-if="tour.reason" class="tour-reason" role="status">{{ tour.reason }}</span>
  <button @click="tour.stop">结束</button>
 </div></Transition>
</template>
<style scoped>
.tour-capsule{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);z-index:6;display:flex;align-items:center;gap:12px;width:max-content;max-width:calc(100% - 32px);box-sizing:border-box;padding:8px 12px;border:1px solid var(--tb-border);border-radius:12px;background:color-mix(in srgb,var(--tb-bg) 94%,transparent);box-shadow:0 2px 5px #173e3114,0 8px 24px #173e3118;color:var(--ink);font-size:12px;white-space:nowrap;flex-wrap:wrap;justify-content:center}
.tour-image{max-width:160px;overflow:hidden;text-overflow:ellipsis}
button{border:0;border-radius:6px;background:transparent;color:var(--tb-primary);font:inherit;font-weight:600;padding:4px;cursor:pointer}button:hover{background:var(--tb-active-bg)}.tour-reason{font-size:11px;color:var(--ink-sub)}
.tour-capsule-enter-active,.tour-capsule-leave-active{transition:opacity var(--motion-live) var(--ease-standard)}.tour-capsule-enter-from,.tour-capsule-leave-to{opacity:0}.tour-capsule-leave-active{pointer-events:none}
</style>
<style>
.stage:has(.tour-capsule) .toast { bottom: 80px; }
.stage:has(.tour-capsule) .viewer-history { visibility:hidden;pointer-events:none; }
</style>
