<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue';

type Profile = 'fast' | 'low' | 'balanced' | 'deep';
const props = defineProps<{ modelValue: Profile; status?: string; disabled?: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: Profile] }>();
const profiles: Profile[] = ['fast', 'low', 'balanced', 'deep'];
const labels = ['极速', '快速', '均衡', '深度'];
const descriptions = ['轻量思考，适合简单问答', '简短推敲，适合明确指令', '兼顾效率与思考，适合日常任务', '充分推敲，适合复杂分析'];
const clamp = (value: number) => Math.min(3, Math.max(0, value));
const index = computed(() => profiles.indexOf(props.modelValue));
const position = ref(index.value);
const previewIndex = computed(() => Math.round(clamp(position.value)));
const open = ref(false);
const root = ref<HTMLElement>();
const trigger = ref<HTMLButtonElement>();
const slider = ref<HTMLElement>();
const dragging = ref(false);
const drawerId = useId();
let pointer: number | null = null;
let startX = 0;
let startPosition = 0;
let moved = false;
watch(index, value => { if (!dragging.value) position.value = value; });
watch(() => props.disabled, value => { if (value) close(); });

function choose(target: number) {
  position.value = clamp(target);
  emit('update:modelValue', profiles[clamp(target)]!);
}
async function toggle() {
  if (open.value) { close(true); return; }
  if (props.disabled) return;
  open.value = true;
  await nextTick();
  if (open.value) slider.value?.focus({ preventScroll: true });
}
function close(restoreFocus = false) {
  cancelGesture();
  open.value = false;
  if (restoreFocus) trigger.value?.focus({ preventScroll: true });
}
function outside(event: PointerEvent) {
  if (open.value && event.target instanceof Node && !root.value?.contains(event.target)) close();
}
function focusout(event: FocusEvent) {
  if (event.relatedTarget instanceof Node && !root.value?.contains(event.relatedTarget)) close();
}
function cancelGesture() {
  const activePointer = pointer;
  pointer = null;
  dragging.value = false;
  position.value = index.value;
  if (activePointer !== null && slider.value?.hasPointerCapture(activePointer)) slider.value.releasePointerCapture(activePointer);
}
function beforeLeave(element: Element) {
  element.setAttribute('inert', '');
  element.setAttribute('aria-hidden', 'true');
}
function down(event: PointerEvent) {
  if (props.disabled || pointer !== null || !event.isPrimary || event.button !== 0) return;
  // Continue from the visible position when a gesture interrupts the snap.
  const current = Number.parseFloat(getComputedStyle(slider.value!).getPropertyValue('--thinking-position'));
  startPosition = Number.isFinite(current) ? current : position.value;
  position.value = startPosition;
  startX = event.clientX;
  moved = false;
  dragging.value = true;
  pointer = event.pointerId;
  slider.value!.focus({ preventScroll: true });
  slider.value!.setPointerCapture(pointer);
  event.preventDefault();
}
function move(event: PointerEvent) {
  if (event.pointerId !== pointer) return;
  const distance = event.clientX - startX;
  moved ||= Math.abs(distance) > 4;
  position.value = clamp(startPosition + distance / (slider.value!.getBoundingClientRect().width / 4));
}
function end(event: PointerEvent) {
  if (event.pointerId !== pointer) return;
  const box = slider.value!.getBoundingClientRect();
  const target = moved ? Math.round(position.value) : Math.round((event.clientX - box.left) / (box.width / 4) - .5);
  pointer = null;
  dragging.value = false;
  choose(clamp(target));
  if (slider.value?.hasPointerCapture(event.pointerId)) slider.value.releasePointerCapture(event.pointerId);
}
function key(event: KeyboardEvent) {
  if (props.disabled || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  cancelGesture();
  choose(event.key === 'Home' ? 0 : event.key === 'End' ? 3 : index.value + (['ArrowRight', 'ArrowUp'].includes(event.key) ? 1 : -1));
}
onMounted(() => document.addEventListener('pointerdown', outside));
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside);
  cancelGesture();
});
</script>

<template>
  <div ref="root" class="thinking-control" @focusout="focusout" @keydown.esc.stop.prevent="close(true)">
    <button ref="trigger" type="button" class="thinking-trigger" :class="{ active: open }" :disabled="disabled"
            :aria-label="`思考强度：${labels[index]}`" :aria-expanded="open" :aria-controls="drawerId"
            :title="`思考强度：${labels[index]}，点击调整`" @click="toggle">
      <span class="thinking-meter" aria-hidden="true">
        <i v-for="n in 4" :key="n" :class="{ lit: n <= index + 1 }" :style="{ '--bar': n }" />
      </span>
      <span class="thinking-value" aria-hidden="true">
        <span v-for="(label, i) in labels" :key="label" :class="{ current: i === index }">{{ label }}</span>
      </span>
      <span class="thinking-chevron" aria-hidden="true" />
    </button>

    <Transition name="thinking-drawer" @before-leave="beforeLeave">
      <section v-if="open" :id="drawerId" class="thinking-drawer" :aria-labelledby="`${drawerId}-title`">
        <div class="thinking-heading">
          <div class="thinking-title"><span class="thinking-eyebrow" aria-hidden="true">AGENT</span><h3 :id="`${drawerId}-title`">思考强度</h3></div>
          <button type="button" class="thinking-close" aria-label="收起思考强度" title="收起" @click="close(true)">×</button>
        </div>
        <div ref="slider" class="thinking-slider" :class="{ dragging }" role="slider" tabindex="0"
             :style="{ '--thinking-position': position }" aria-label="思考强度"
             aria-valuemin="0" aria-valuemax="3" aria-orientation="horizontal"
             :aria-valuenow="index" :aria-valuetext="labels[index]" :aria-describedby="`${drawerId}-description ${drawerId}-hint`"
             @pointerdown="down" @pointermove="move" @pointerup="end"
             @pointercancel="cancelGesture" @lostpointercapture="cancelGesture" @keydown="key">
          <span class="gear-selection" aria-hidden="true" />
          <span v-for="(label, i) in labels" :key="label" class="gear-label" :class="{ selected: previewIndex === i }" aria-hidden="true">
            {{ label }}
          </span>
          <span class="gear-track" aria-hidden="true"><span class="gear-fill" /></span>
          <span v-for="n in 4" :key="n" class="gear-stop" :class="{ passed: n <= previewIndex + 1 }" :style="{ '--stop': n - 1 }" aria-hidden="true" />
          <span class="gear-thumb" aria-hidden="true" />
        </div>
        <p :id="`${drawerId}-description`" class="thinking-description">{{ descriptions[previewIndex] }}</p>
        <div class="thinking-drawer-foot" :id="`${drawerId}-hint`">
          <span class="thinking-next"><i aria-hidden="true" />下次请求生效</span><span>滑动或点选切换</span>
        </div>
        <p v-if="status" class="thinking-status">{{ status }}</p>
      </section>
    </Transition>
    <span class="sr-status" role="status" aria-live="polite">{{ labels[index] }}{{ status ? `，${status}` : '' }}</span>
  </div>
</template>

<style>
/* One interpolated number keeps the selected tile, rail and thumb together. */
@property --thinking-position { syntax: '<number>'; inherits: true; initial-value: 2; }
</style>
<style scoped>
.thinking-control { position: relative; display: inline-flex; align-self: center; flex: none; }
.thinking-trigger {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  height: 30px; padding: 0 10px; border: 1px solid var(--tb-divider); border-radius: var(--r-pill);
  color: var(--tb-muted); background: var(--tb-bg); font: inherit; cursor: pointer;
  transition: background var(--motion-hover) var(--ease-standard), border-color var(--motion-hover) var(--ease-standard), box-shadow var(--motion-hover) var(--ease-standard);
}
.thinking-trigger:hover:not(:disabled), .thinking-trigger.active { color: var(--tb-active-fg); border-color: var(--tb-active-border); background: var(--tb-active-bg); }
.thinking-trigger.active { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-2) 10%, transparent); }
.thinking-trigger:disabled { opacity: .5; cursor: default; }
.thinking-meter { display: flex; align-items: flex-end; gap: 2px; height: 13px; }
.thinking-meter i { width: 2px; height: calc(3px + var(--bar) * 2.5px); border-radius: 2px; background: var(--tb-divider); transition: background var(--motion-live) var(--ease-standard); }
.thinking-meter i.lit { background: var(--accent-ink); }
.thinking-value { display: grid; font-size: 11px; font-weight: 600; line-height: 1; }
.thinking-value span { grid-area: 1 / 1; opacity: 0; transform: translateY(4px); transition: opacity var(--motion-fade) var(--ease-standard), transform var(--motion-live) var(--ease-gear-snap); }
.thinking-value span.current { opacity: 1; transform: translateY(0); }
.thinking-chevron { width: 5px; height: 5px; margin: 2px 1px 0 2px; border-top: 1px solid currentColor; border-left: 1px solid currentColor; transform: rotate(45deg); transition: transform var(--motion-live) var(--ease-gear-snap); }
.active .thinking-chevron { transform: rotate(225deg); margin-top: -2px; }
.thinking-drawer {
  position: absolute; z-index: 45; bottom: calc(100% + 8px); left: 0;
  width: 280px; max-width: calc(100vw - 40px); padding: 12px 14px; border-radius: 16px;
  border: 1px solid var(--tb-divider); color: var(--tb-fg);
  background: linear-gradient(145deg, var(--tb-surface), var(--tb-bg));
  box-shadow: 0 2px 4px color-mix(in srgb, var(--nav-bg) 4%, transparent), 0 8px 24px color-mix(in srgb, var(--nav-bg) 9%, transparent), 0 24px 56px color-mix(in srgb, var(--nav-bg) 10%, transparent);
  transform-origin: 30px bottom;
}
.thinking-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.thinking-title { display: flex; align-items: center; gap: 8px; }
.thinking-eyebrow { font: 9px var(--font-mono); letter-spacing: 1.2px; color: var(--accent-ink); border: 1px solid var(--tb-active-border); border-radius: 4px; padding: 3px 4px; }
.thinking-title h3 { font-size: 13px; font-weight: 600; margin: 0; letter-spacing: .5px; }
.thinking-close { display: grid; place-items: center; flex: none; width: 24px; height: 24px; margin: -4px -4px -4px 0; padding: 0; border: 0; border-radius: 50%; background: transparent; color: var(--tb-muted); font: 20px/1 var(--font-sans); cursor: pointer; transition: background var(--motion-hover) var(--ease-standard); }
.thinking-close:hover { background: var(--tb-hover); }
.thinking-slider {
  position: relative; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); height: 56px;
  padding: 0; border-radius: 12px; background: var(--tb-hover); cursor: ew-resize;
  user-select: none; touch-action: pan-y;
  transition: --thinking-position var(--motion-gear-snap) var(--ease-gear-snap);
}
.thinking-slider.dragging { transition: none; }
.gear-selection { position: absolute; top: 4px; left: 0; width: 25%; height: 32px; transform: translateX(calc(var(--thinking-position) * 100%)); pointer-events: none; }
.gear-selection::before { content: ''; position: absolute; inset: 0 4px; background: var(--tb-surface); border: 1px solid var(--tb-active-border); border-radius: 9px; box-shadow: 0 2px 5px color-mix(in srgb, var(--nav-bg) 7%, transparent); }
.gear-label { position: relative; display: flex; align-items: center; justify-content: center; height: 40px; color: var(--tb-muted); font-size: 12px; line-height: 18px; pointer-events: none; transition: color var(--motion-hover) var(--ease-standard); }
.gear-label.selected { color: var(--tb-active-fg); font-weight: 600; }
.gear-track { position: absolute; height: 2px; left: 12.5%; right: 12.5%; bottom: 11px; background: var(--tb-divider); border-radius: 2px; pointer-events: none; }
.gear-fill { display: block; height: 100%; width: calc(var(--thinking-position) / 3 * 100%); border-radius: inherit; background: var(--accent-2); }
.gear-stop { position: absolute; width: 4px; height: 4px; left: calc(12.5% + var(--stop) * 25%); bottom: 10px; border-radius: 50%; background: var(--tb-border); transform: translateX(-50%); pointer-events: none; }
.gear-stop.passed { background: var(--accent-ink); }
.gear-thumb { position: absolute; left: calc(12.5% + var(--thinking-position) * 25%); bottom: 6px; width: 12px; height: 12px; border: 3px solid var(--tb-surface); border-radius: 50%; background: var(--accent-ink); box-shadow: 0 0 0 1px var(--tb-active-border), 0 2px 4px color-mix(in srgb, var(--nav-bg) 12%, transparent); transform: translateX(-50%); pointer-events: none; }
.thinking-description { min-height: 18px; margin: 8px 0; font-size: 11px; line-height: 18px; color: var(--tb-muted); }
.thinking-drawer-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; border-top: 1px solid var(--tb-divider); padding-top: 8px; color: var(--tb-muted); font-size: 10px; }
.thinking-next { display: flex; align-items: center; gap: 5px; }
.thinking-next i { width: 4px; height: 4px; border-radius: 50%; background: var(--accent-ink); }
.thinking-status { margin: 8px 0 0; padding: 5px 8px; border-radius: 6px; background: var(--tb-hover); color: var(--tb-muted); font-size: 10px; line-height: 1.6; overflow-wrap: anywhere; }
.thinking-drawer-enter-active { transition: opacity var(--motion-live) var(--ease-standard), transform var(--motion-live) var(--ease-gear-snap); }
.thinking-drawer-leave-active { transition: opacity var(--motion-fade) var(--ease-standard), transform var(--motion-fade) var(--ease-standard); pointer-events: none; }
.thinking-drawer-enter-from, .thinking-drawer-leave-to { opacity: 0; transform: translateY(8px) scale(.98); }
.sr-status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
</style>
