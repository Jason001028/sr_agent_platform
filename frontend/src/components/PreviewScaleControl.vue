<script setup lang="ts">
import { computed, ref, watch } from 'vue';

const props = defineProps<{ modelValue: number }>();
const emit = defineEmits<{ 'update:modelValue': [value: number] }>();
const stops = [16, 8, 4, 2, 1];
const names = ['极速预览', '快速预览', '均衡预览', '精细预览', '原始分辨率'];
const label = (i: number) => stops[i] === 1 ? '1:1' : `1/${stops[i]}`;
const index = computed(() => Math.max(0, stops.indexOf(props.modelValue)));
const position = ref(index.value);
const near = computed(() => Math.round(position.value));
const rail = ref<HTMLElement>();
const dragging = ref(false);
const hovered = ref(-1);
let pointer: number | null = null;
let origin = 0;
let startX = 0;
let moved = false;
let wheelAt = 0;
const clamp = (n: number) => Math.min(4, Math.max(0, n));
watch(index, value => { if (!dragging.value) position.value = value; });

function choose(i: number) {
  const target = clamp(i);
  position.value = target;
  if (stops[target] !== props.modelValue) emit('update:modelValue', stops[target]!);
}
function down(e: PointerEvent) {
  if (pointer !== null || e.button !== 0 || !e.isPrimary) return;
  const rect = rail.value!.getBoundingClientRect();
  pointer = e.pointerId;
  startX = e.clientX;
  moved = false;
  // Read the actual interpolated thumb position before interrupting a snap.
  const thumb = rail.value!.querySelector('.psc-thumb')!.getBoundingClientRect();
  origin = clamp(((thumb.left + thumb.width / 2) - rect.left) / rect.width * 4);
  rail.value!.setPointerCapture(e.pointerId);
  rail.value!.focus({ preventScroll: true });
  e.preventDefault();
}
function move(e: PointerEvent) {
  if (e.pointerId !== pointer) return;
  if (Math.abs(e.clientX - startX) > 2) moved = true;
  if (!moved) return;
  if (!dragging.value) position.value = origin;
  dragging.value = true;
  const rect = rail.value!.getBoundingClientRect();
  position.value = clamp((e.clientX - rect.left) / rect.width * 4);
}
function finish(e: PointerEvent, cancel = false) {
  if (e.pointerId !== pointer) return;
  const rect = rail.value!.getBoundingClientRect();
  const target = cancel ? index.value : Math.round(dragging.value ? position.value
    : clamp((e.clientX - rect.left) / rect.width * 4));
  pointer = null;
  // A frame separates direct manipulation from the eased magnetic settle.
  // Force style resolution so pointerup never coalesces with the last move.
  rail.value!.getBoundingClientRect();
  dragging.value = false;
  choose(target);
  if (rail.value!.hasPointerCapture(e.pointerId)) rail.value!.releasePointerCapture(e.pointerId);
}
function key(e: KeyboardEvent) {
  let target = index.value;
  if (e.key === 'ArrowRight' || e.key === 'ArrowUp') target++;
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') target--;
  else if (e.key === 'Home') target = 0;
  else if (e.key === 'End') target = 4;
  else return;
  e.preventDefault();
  choose(target);
}
function wheel(e: WheelEvent) {
  if (e.ctrlKey || Math.abs(e.deltaY) < 2 || dragging.value) return;
  e.preventDefault();
  const now = performance.now();
  if (now - wheelAt < 180) return;
  wheelAt = now;
  choose(index.value + (e.deltaY < 0 ? 1 : -1));
}
</script>

<template>
  <div class="preview-scale" :class="{ dragging }" data-e2e="preview-div" @wheel="wheel">
    <svg class="psc-icon" width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <title>图像预览档位</title>
      <path d="m10 2 8 4.5-8 4.5-8-4.5L10 2Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
      <path d="m2 10 8 4.5 8-4.5M2 13.5 10 18l8-4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
    <div ref="rail" class="psc-rail" role="slider" tabindex="0" aria-label="图像预览档位"
      aria-valuemin="0" aria-valuemax="4" :aria-valuenow="index"
      :aria-valuetext="`${label(index)} · ${names[index]}`"
      :style="{ '--position': position, '--progress': `${position * 25}%` }"
      @pointerdown="down" @pointermove="move" @pointerup="finish($event)"
      @pointercancel="finish($event, true)" @lostpointercapture="finish($event, true)"
      @keydown="key" @pointerleave="hovered = -1">
      <span class="psc-track"></span>
      <span class="psc-fill"></span>
      <span v-for="(stop, i) in stops" :key="stop" class="psc-stop"
        :class="{ passed: i <= near, near: dragging && i === near, hovered: hovered === i }"
        :style="{ left: `${i * 25}%` }" :title="`${label(i)} · ${names[i]}`"
        @pointerenter="hovered = i"><i></i></span>
      <span class="psc-thumb"><i></i></span>
    </div>
    <span class="psc-value" :title="names[index]" aria-hidden="true">
      <Transition name="psc-number">
        <span :key="index" class="psc-number">{{ label(index) }}</span>
      </Transition>
    </span>
  </div>
</template>

<style scoped>
.preview-scale {
  --psc-ink: #24544e;
  --psc-ease: cubic-bezier(.22, 1, .36, 1);
  display: inline-flex;
  align-items: center;
  gap: 11px;
  height: 32px;
  padding: 0 5px 0 10px;
  flex: 0 0 auto;
  border: 1px solid #ccdad5;
  border-radius: 18px;
  background: linear-gradient(180deg, #f7faf8, #eaf1ed);
  color: var(--psc-ink);
  box-shadow: inset 0 1px 0 #ffffffd9, 0 1px 3px #183d3610;
  user-select: none;
  transition: border-color 180ms, box-shadow 180ms;
}
.preview-scale:hover, .preview-scale.dragging {
  border-color: #a7c0b6;
  box-shadow: inset 0 1px 0 #fff, 0 2px 5px #183d3614;
}
.psc-icon { flex: none; opacity: .8; }
.psc-rail {
  position: relative;
  width: 96px;
  height: 28px;
  margin: 0 5px;
  cursor: pointer;
  touch-action: none;
  outline: none;
}
.psc-rail:focus-visible { outline: 2px solid #458e80; outline-offset: 3px; border-radius: 8px; }
.psc-track, .psc-fill { position: absolute; left: 0; top: 13px; height: 3px; border-radius: 3px; pointer-events: none; }
.psc-track { width: 100%; background: #cedcd5; box-shadow: inset 0 1px 1px #24483e0d; }
.psc-fill { width: var(--progress); background: #43857a; transition: width 260ms var(--psc-ease); }
.psc-stop { position: absolute; top: 5px; width: 18px; height: 18px; transform: translateX(-50%); display: grid; place-items: center; }
.psc-stop i { width: 5px; height: 5px; border-radius: 50%; background: #b0c5ba; box-shadow: 0 0 0 2px #eff5f0; transition: background 180ms, box-shadow 180ms, transform 180ms; }
.psc-stop.passed i { background: #549386; }
.psc-stop.hovered i, .psc-stop.near i { background: #205b50; transform: scale(1.25); box-shadow: 0 0 0 5px #4288781a; }
.psc-thumb { position: absolute; left: var(--progress); top: 6px; width: 16px; height: 16px; transform: translateX(-50%); border-radius: 50%; display: grid; place-items: center; background: linear-gradient(145deg, #36796c, #1c5048); box-shadow: 0 1px 3px #183d3640, inset 0 1px 0 #ffffff40; transition: left 260ms var(--psc-ease), box-shadow 180ms; }
.psc-thumb i { width: 4px; height: 4px; border-radius: 50%; background: #f1faf5; opacity: .9; pointer-events: none; }
.dragging .psc-thumb { cursor: grabbing; box-shadow: 0 0 0 4px #4385781c, 0 2px 5px #183d3640; }
.dragging .psc-thumb, .dragging .psc-fill { transition: none; }
.psc-value { position: relative; display: block; flex: none; width: 44px; height: 24px; overflow: hidden; border-radius: 12px; background: #fbfdfb; border: 1px solid #dbe5de; box-shadow: inset 0 1px 1px #fff, 0 1px 2px #234c3d06; font: 600 12px/24px ui-monospace, SFMono-Regular, Consolas, monospace; font-variant-numeric: tabular-nums; }
.psc-number { position: absolute; inset: 0; text-align: center; }
.psc-number-enter-active, .psc-number-leave-active { transition: transform 260ms var(--psc-ease), opacity 260ms var(--psc-ease); }
.psc-number-enter-from { transform: translateY(7px); opacity: 0; }
.psc-number-leave-to { transform: translateY(-7px); opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .preview-scale, .psc-fill, .psc-thumb, .psc-stop i, .psc-number-enter-active, .psc-number-leave-active { transition-duration: 1ms; }
  .psc-number-enter-from, .psc-number-leave-to { transform: none; }
}
</style>
