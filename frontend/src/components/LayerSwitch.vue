<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue';
const props = defineProps<{ selected: boolean }>();
const thumb = ref<HTMLElement | null>(null);
let animation: Animation | undefined;
// Unit mass, stiffness 200, damping 14. Travel ends stretched; spring restores width.
const duration = 1100, travel = 180;
const media = typeof window !== 'undefined' ? window.matchMedia?.('(prefers-reduced-motion: reduce)') : undefined;
function stop() { animation?.cancel(); animation = undefined; }
media?.addEventListener('change', stop);
watch(() => props.selected, selected => {
  const element = thumb.value;
  if (!element || !element.animate || media?.matches) { stop(); return; }
  const start = getComputedStyle(element).transform;
  stop();
  const x = selected ? 16 : 0;
  const frames: Keyframe[] = [
    { offset: 0, transform: start },
    { offset: travel/duration/2, transform: 'translateX(8px) scaleX(1.35)' },
    { offset: travel/duration, transform: `translateX(${x}px) scaleX(1.35)` },
  ];
  const frequency = Math.sqrt(200 - 7 * 7);
  for (let i = 1; i <= 56; i++) {
    const t = i/56 * (duration-travel)/1000;
    const scale = i === 56 ? 1 : 1 + .35 * Math.exp(-7*t) * (Math.cos(frequency*t) + 7/frequency*Math.sin(frequency*t));
    frames.push({offset:(travel+i/56*(duration-travel))/duration,transform:`translateX(${x}px) scaleX(${scale})`});
  }
  animation = element.animate(frames, {duration, easing:'linear'});
}, {flush:'post'});
onBeforeUnmount(() => { stop(); media?.removeEventListener('change',stop); });
</script>
<template>
  <span class="layer-switch" :class="{selected}" aria-hidden="true"><span ref="thumb" class="switch-thumb" /></span>
</template>
<style scoped>
.layer-switch{position:relative;display:block;flex:none;width:36px;height:20px;border-radius:999px;background:#cbd3cf;transition:background-color var(--motion-layer-switch) var(--ease-standard)}
.layer-switch.selected{background:var(--accent-deep,#285b50)}
.switch-thumb{position:absolute;left:2px;top:2px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 3px #0002;transform:translateX(0);transform-origin:center}
.selected .switch-thumb{transform:translateX(16px)}
</style>
