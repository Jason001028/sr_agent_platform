<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';

const props = withDefaults(defineProps<{
  size?: number;
  filled?: boolean;
  pulseKey?: number;
}>(), { size: 16, filled: false, pulseKey: 0 });

const pulsing = ref(false);
let pulseTimer: ReturnType<typeof setTimeout> | null = null;
onBeforeUnmount(() => { if (pulseTimer !== null) clearTimeout(pulseTimer); });
watch(() => props.pulseKey, (next, previous) => {
  if (next === previous) return;
  pulsing.value = false;
  requestAnimationFrame(() => {
    pulsing.value = true;
    if (pulseTimer !== null) clearTimeout(pulseTimer);
    pulseTimer = setTimeout(() => { pulsing.value = false; pulseTimer = null; }, 380);
  });
});
const classes = computed(() => ({ 'is-filled': props.filled, 'is-pulsing': pulsing.value }));
</script>

<template>
  <svg class="favorite-star" :class="classes" xmlns="http://www.w3.org/2000/svg"
       :width="props.size" :height="props.size" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
       aria-hidden="true">
    <path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z" />
  </svg>
</template>

<style scoped>
.favorite-star { display: block; flex: none; transform-origin: center; transition: fill 180ms ease-out, stroke 180ms ease-out; }
.favorite-star.is-filled, .favorite-star.is-pulsing { fill: #eab308; stroke: #c58b08; }
.favorite-star.is-pulsing { animation: favorite-star-flash 360ms cubic-bezier(.16,1,.3,1); }
@keyframes favorite-star-flash {
  0% { transform: scale(.92); filter: brightness(.98); }
  32% { transform: scale(1.08); filter: brightness(1.16); }
  62% { transform: scale(1.025); filter: brightness(1.08); }
  100% { transform: scale(1); filter: brightness(1); }
}
@media (prefers-reduced-motion: reduce) {
  .favorite-star.is-pulsing { animation: none; }
}
</style>
