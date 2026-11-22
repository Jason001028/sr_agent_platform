<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';

const props = defineProps<{ label: string; disabled?: boolean }>();
const emit = defineEmits<{ confirm: [] }>();
const holding = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let pointer: number | undefined;
let key: string | undefined;

function cancel() {
  clearTimeout(timer);
  holding.value = false;
  pointer = undefined;
  key = undefined;
  window.removeEventListener('blur', cancel);
}

function start() {
  holding.value = true;
  window.addEventListener('blur', cancel);
  timer = setTimeout(() => {
    if (holding.value && !props.disabled) emit('confirm');
  }, 1000);
}

function pointerDown(event: PointerEvent) {
  if (props.disabled || holding.value || event.button !== 0) return;
  pointer = event.pointerId;
  (event.currentTarget as HTMLButtonElement).setPointerCapture?.(event.pointerId);
  start();
}

function pointerEnd(event: PointerEvent) {
  if (pointer === event.pointerId) cancel();
}

function keyDown(event: KeyboardEvent) {
  if (event.key === 'Escape') { cancel(); return; }
  if (event.key !== ' ' && event.key !== 'Enter') return;
  event.preventDefault();
  if (event.repeat || props.disabled || holding.value) return;
  key = event.key;
  start();
}

function keyUp(event: KeyboardEvent) {
  if (key === event.key) cancel();
}

watch(() => props.disabled, disabled => { if (disabled) cancel(); });
onBeforeUnmount(cancel);
</script>

<template>
  <button type="button" class="hold-confirm" :class="{ 'is-holding': holding }"
    :disabled="disabled" :title="`长按 1 秒${label}`" :aria-label="`长按 1 秒${label}`"
    @pointerdown.stop="pointerDown" @pointerup="pointerEnd" @pointercancel="pointerEnd"
    @lostpointercapture="pointerEnd" @keydown="keyDown" @keyup="keyUp" @blur="cancel"
    @click.stop.prevent @contextmenu.prevent>
    <span class="hold-fill" aria-hidden="true" />
    <span class="hold-content"><slot /></span>
  </button>
</template>

<style scoped>
.hold-confirm { position: relative; isolation: isolate; overflow: hidden; touch-action: none; user-select: none; }
.hold-fill {
  position: absolute;
  inset: 0;
  background: var(--hold-confirm-fill, #d2545438);
  transform: scaleX(0);
  transform-origin: left;
  pointer-events: none;
}
.hold-confirm.is-holding .hold-fill { transform: scaleX(1); transition: transform 1000ms linear; }
.hold-content { position: relative; display: inline-flex; align-items: center; justify-content: center; }
</style>
