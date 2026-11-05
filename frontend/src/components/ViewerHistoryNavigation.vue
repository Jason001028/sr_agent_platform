<script setup lang="ts">
import {onMounted,onBeforeUnmount,ref} from 'vue';
import {useViewHistoryStore} from '../stores/viewHistory';
import {useAutoTourStore} from '../stores/autoTour';
const history=useViewHistoryStore(),tour=useAutoTourStore(),root=ref<HTMLElement|null>(null),near=ref(false);
let stage:HTMLElement|null=null;
function move(e:PointerEvent){const b=stage?.getBoundingClientRect();near.value=!!b&&e.clientY>=b.bottom-48&&e.clientY<=b.bottom;}
function leave(){near.value=false;}
function down(e:PointerEvent){if(e.button===0&&(e.target as HTMLElement).closest('.view-canvas,.navigator canvas,.cmp-divider'))history.beginGesture();}
function up(){history.endGesture();}
onMounted(()=>{
 stage=root.value?.parentElement??null;history.connect();
 stage?.addEventListener('pointermove',move);stage?.addEventListener('pointerleave',leave);stage?.addEventListener('pointerdown',down,true);
 window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);window.addEventListener('blur',up);
});
onBeforeUnmount(()=>{
 history.disconnect();stage?.removeEventListener('pointermove',move);stage?.removeEventListener('pointerleave',leave);stage?.removeEventListener('pointerdown',down,true);
 window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);window.removeEventListener('blur',up);
});
</script>
<template>
 <nav ref="root" class="viewer-history" :class="{near}" :hidden="tour.status!=='idle'" aria-label="视野历史导航" @pointerdown.stop @mousedown.stop @dblclick.stop @wheel.stop>
  <button type="button" :disabled="!history.canBack" title="恢复上一视野" @click="history.go(-1)"><span aria-hidden="true">‹</span>上一景</button>
  <button type="button" :disabled="!history.canForward" title="恢复下一视野" @click="history.go(1)">下一景<span aria-hidden="true">›</span></button>
 </nav>
</template>
<style scoped>
:global(.stage:has(.viewer-history:not([hidden])) .toast){bottom:64px}
.viewer-history{position:absolute;bottom:6px;left:50%;transform:translateX(-50%);z-index:6;display:flex;align-items:center;gap:40px;height:42px;max-width:calc(100% - 24px);pointer-events:none;opacity:.22;transition:opacity var(--motion-history-reveal,170ms) var(--ease-standard)}
.viewer-history[hidden]{display:none}.viewer-history.near,.viewer-history:hover,.viewer-history:focus-within{opacity:1}
button{display:flex;align-items:center;justify-content:center;gap:8px;box-sizing:border-box;min-width:90px;height:36px;padding:0 15px;pointer-events:auto;border:1px solid #68877b42;border-radius:10px;background:#f6faf6b8;backdrop-filter:blur(7px);color:#244c3f;box-shadow:0 2px 8px #173e3110;font:inherit;font-size:12px;cursor:pointer;touch-action:manipulation}
button>span{font-size:19px;line-height:1;color:#567c6e}button:hover:enabled{background:#f5faf1e8;border-color:#638b7a73}button:disabled{color:#7c8b83;cursor:default;box-shadow:none}button:disabled>span{color:inherit}
button:focus-visible{outline:2px solid var(--tb-focus);outline-offset:3px}
@media(pointer:coarse){.viewer-history{opacity:.78}button{height:42px;min-width:96px}}
@media(max-width:340px){.viewer-history{gap:32px}button{min-width:78px;padding:0 10px}}
@media(prefers-reduced-motion:reduce){.viewer-history{transition:none}}
</style>
