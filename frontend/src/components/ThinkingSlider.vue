<script setup lang="ts">
import { computed, ref, watch } from 'vue';

type Profile = 'fast' | 'low' | 'balanced' | 'deep';
const props = defineProps<{ modelValue: Profile; status?: string; disabled?: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: Profile] }>();
const profiles: Profile[] = ['fast', 'low', 'balanced', 'deep'];
const labels = ['极速', '快速', '均衡', '深度'];
const index = computed(() => profiles.indexOf(props.modelValue));
const position = ref(index.value);
const capsule = ref<HTMLElement>();
const dragging = ref(false);
const fadeOnly = ref(false);
let pointer: number | null = null;
let startX = 0;
let origin = 0;
let startPosition = 0;
let moved = false;
let suppressClick = false;
const clamp = (value: number, min = 0, max = 3) => Math.min(max, Math.max(min, value));
watch(index, value => { if (!dragging.value) position.value = value; });

function choose(target: number, wrap = false) {
  fadeOnly.value = wrap;
  position.value = clamp(target);
  emit('update:modelValue', profiles[clamp(target)]!);
}
function down(event: PointerEvent) {
  if (props.disabled || pointer !== null || !event.isPrimary || event.button !== 0) return;
  // Read the interpolated position before stopping an in-flight snap.
  const current = Number.parseFloat(getComputedStyle(capsule.value!).getPropertyValue('--thinking-position'));
  startPosition = Number.isFinite(current) ? current : position.value;
  position.value = startPosition;
  origin = index.value;
  startX = event.clientX;
  moved = false;
  suppressClick = false;
  fadeOnly.value = false;
  dragging.value = true;
  pointer = event.pointerId;
  capsule.value!.setPointerCapture(pointer);
}
function move(event: PointerEvent) {
  if (event.pointerId !== pointer) return;
  const distance = event.clientX - startX;
  moved ||= Math.abs(distance) > 4;
  // One gesture can preview at most one adjacent gear; edges have slight resistance.
  const raw = startPosition + distance / 32;
  const bounded = clamp(raw, Math.max(0, origin - 1), Math.min(3, origin + 1));
  position.value = bounded + clamp((raw - bounded) * .06, -.08, .08);
}
function end(event: PointerEvent, cancelled = false) {
  if (event.pointerId !== pointer) return;
  const distance = event.clientX - startX;
  pointer = null;
  dragging.value = false;
  suppressClick = moved || cancelled;
  if (cancelled || !moved) position.value = origin;
  else choose(origin + (Math.abs(distance) >= 12 ? Math.sign(distance) : 0));
  if (capsule.value?.hasPointerCapture(event.pointerId)) capsule.value.releasePointerCapture(event.pointerId);
}
function click() {
  if (suppressClick) { suppressClick = false; return; }
  if (!props.disabled) choose((index.value + 1) % 4, index.value === 3);
}
function key(event: KeyboardEvent) {
  if (props.disabled || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  choose(event.key === 'Home' ? 0 : event.key === 'End' ? 3 : index.value + (event.key === 'ArrowRight' ? 1 : -1));
}
</script>

<template>
  <button ref="capsule" type="button" class="thinking-slider" role="slider"
          :class="{ dragging, 'fade-only': fadeOnly }" :disabled="disabled"
          :style="{ '--thinking-position': position }"
          aria-label="Agent 推理强度" aria-valuemin="0" aria-valuemax="3"
          :aria-valuenow="index" :aria-valuetext="labels[index]" :aria-description="status || '左右滑动或方向键切档，点击循环；下一次请求生效。'"
          :title="`${labels[index]} · 左右滑动切档，点击循环；下一次请求生效`"
          @pointerdown="down" @pointermove="move" @pointerup="end($event)"
          @pointercancel="end($event, true)" @lostpointercapture="end($event, true)"
          @click="click" @keydown="key">
    <span v-for="(label, i) in labels" :key="label" class="gear-label" aria-hidden="true"
          :style="{ '--gear-index': i, '--gear-visible': index === i ? 1 : 0 }">{{ label }}</span>
    <span class="gear-track" aria-hidden="true"><span class="gear-thumb" /></span>
    <span class="sr-status" role="status" aria-live="polite">{{ status }}</span>
  </button>
</template>

<style>
/* A single interpolated number keeps the thumb and both labels in sync, even
   when a new gesture interrupts the settling animation. */
@property --thinking-position { syntax: '<number>'; inherits: true; initial-value: 2; }
</style>
<style scoped>
.thinking-slider {
  position: relative; flex: 0 0 68px; width: 68px; height: 30px; overflow: hidden;
  padding: 0; border: 1px solid var(--tb-divider); border-radius: 999px;
  color: var(--nav-bg); background: var(--tb-hover); cursor: ew-resize;
  user-select: none; touch-action: pan-y; font: inherit;
  transition: --thinking-position var(--motion-gear-snap) var(--ease-gear-snap);
}
.thinking-slider:focus-visible { outline: 2px solid var(--tb-focus); outline-offset: 2px; }
.thinking-slider:disabled { opacity: .5; cursor: default; }
.thinking-slider.dragging { transition: none; }
.gear-label {
  position: absolute; inset: 0; display: grid; place-items: center; padding-bottom: 3px;
  font-size: 12px; font-weight: 600; letter-spacing: 1px; pointer-events: none;
  --distance: calc(var(--thinking-position) - var(--gear-index));
  transform: translateX(calc(var(--distance) * 16px));
  opacity: max(0, calc(1 - max(var(--distance), calc(-1 * var(--distance)))));
}
.gear-track { position: absolute; width: 24px; height: 2px; left: 21px; bottom: 4px; background: var(--tb-divider); border-radius: 2px; }
.gear-thumb { position: absolute; left: 0; width: 6px; height: 2px; border-radius: 2px; background: var(--tb-muted); transform: translateX(calc(var(--thinking-position) * 6px)); }
.fade-only { transition: none; }
.fade-only .gear-label { transform: none; opacity: var(--gear-visible); transition: opacity var(--motion-fade) var(--ease-standard); }
.fade-only .gear-thumb { transition: none; }
.sr-status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
