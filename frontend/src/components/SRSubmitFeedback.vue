<script setup lang="ts">
import { ref, onBeforeUnmount, nextTick } from 'vue';
import AppIcon from './AppIcon.vue';
import type { QueueSubmitResult } from '../lib/api';
import { receiptKind, submissionUnknown } from '../lib/submissionFeedback';
const props = defineProps<{ submit: () => Promise<QueueSubmitResult | undefined>; disabled?: boolean; title?: string; compact?: boolean; navigateOnly?: boolean }>();
const emit = defineEmits<{ accepted: [id: number, fresh: boolean]; query: []; view: [id: number] }>();
const state = ref('idle');
const message = ref('');
const receipt = ref<QueueSubmitResult>();
const existing = ref(false);
const visible = ref(false);
const anchor = ref<HTMLButtonElement>();
const card = ref<HTMLElement>();
const position = ref({ left: '0px', top: '0px' });
let disposed = false;
let timers: ReturnType<typeof setTimeout>[] = [];
let closeTimer: ReturnType<typeof setTimeout> | undefined;
const hovered = ref(false);
const focused = ref(false);
const later = (fn: () => void, ms: number) => { timers.push(setTimeout(() => { if (!disposed) fn(); }, ms)); };
function close() { visible.value = false; clearTimeout(closeTimer); }
function scheduleClose() {
  clearTimeout(closeTimer);
  if (!hovered.value && !focused.value) closeTimer = setTimeout(close, 6000);
}
function positionCard() {
  const b = anchor.value?.getBoundingClientRect();
  if (!b || !b.width || !b.height) { close(); return; }
  const w = Math.min(300, window.innerWidth - 24);
  const h = card.value?.offsetHeight || 165;
  position.value = { left: `${Math.max(12, Math.min(b.right - w, window.innerWidth - w - 12))}px`,
    top: `${Math.max(12, Math.min(b.bottom + 8 + h <= window.innerHeight - 12 ? b.bottom + 8 : b.top - h - 8, window.innerHeight - h - 12))}px` };
}
async function run() {
  if (props.disabled || ['submitting', 'unknown'].includes(state.value)) return;
  timers.forEach(clearTimeout); timers = []; close();
  state.value = 'submitting'; message.value = '';
  try {
    const r = await props.submit();
    if (disposed) return;
    if (props.navigateOnly) { state.value = 'idle'; return; }
    if (!r) { state.value = 'error'; return; }
    const kind = receiptKind(r);
    if (kind === 'unknown') { state.value = 'unknown'; message.value = '提交结果待确认'; return; }
    receipt.value = r; existing.value = kind === 'existing'; state.value = 'accepted';
    message.value = existing.value ? '已关联现有任务' : '任务已加入队列';
    emit('accepted', r.task_id, kind === 'new');
    later(async () => { positionCard(); visible.value = true; await nextTick(); positionCard(); scheduleClose(); }, 100);
    later(() => { state.value = 'idle'; }, 1400);
  } catch (e) {
    if (disposed) return;
    state.value = submissionUnknown(e) ? 'unknown' : 'error';
    message.value = state.value === 'unknown' ? '提交结果待确认。请查询任务，确认前不要再次提交。' : `提交失败：${e instanceof Error ? e.message : String(e)}`;
    if (state.value === 'unknown') emit('query');
  }
}
onBeforeUnmount(() => { disposed = true; timers.forEach(clearTimeout); clearTimeout(closeTimer); window.removeEventListener('resize', positionCard); window.removeEventListener('scroll', positionCard, true); });
window.addEventListener('resize', positionCard);
window.addEventListener('scroll', positionCard, true);
</script>

<template>
  <div class="sr-feedback" :class="{ 'sr-compact': compact }">
    <button ref="anchor" type="button" class="sr-submit" :class="state" :title="title" :disabled="disabled || state === 'submitting' || state === 'unknown'" :aria-busy="state === 'submitting'" @click="run">
      <span class="sr-content"><span class="sr-icon" aria-hidden="true"><i v-if="state === 'submitting'" class="sr-spinner" /><span v-else-if="state === 'accepted'">✓</span><span v-else-if="state === 'unknown'">!</span><AppIcon v-else name="square-chart-gantt" :size="15" tint /></span><span>{{ state === 'submitting' ? '正在提交…' : state === 'accepted' ? (existing ? '已关联任务' : '已加入队列') : state === 'unknown' ? '结果待确认' : '提交 SR' }}</span></span>
    </button>
    <span class="sr-status" role="status" aria-live="polite">{{ message }}</span>
    <button v-if="state === 'unknown'" class="sr-link" @click="emit('query')">查询已有任务状态 →</button>
    <Teleport to="body">
      <Transition name="sr-receipt">
        <aside v-if="visible && receipt" ref="card" class="sr-receipt" :style="position" aria-label="任务回执" @mouseenter="hovered = true; scheduleClose()" @mouseleave="hovered = false; scheduleClose()" @focusin="focused = true; scheduleClose()" @focusout="focused = !!card?.contains($event.relatedTarget as Node); scheduleClose()">
          <button class="sr-close" aria-label="关闭任务回执" @click="close">×</button>
          <strong><span aria-hidden="true">✓</span> {{ existing ? '已关联现有任务' : '任务已加入队列' }}</strong>
          <p>任务 ID：{{ receipt.task_id }}</p>
          <details v-if="!existing && receipt.notice" class="sr-details"><summary>写入说明</summary><p>{{ receipt.notice }}</p></details>
          <button class="sr-link" @click="emit('view', receipt.task_id); close()">查看任务 →</button>
        </aside>
      </Transition>
    </Teleport>
  </div>
</template>

<style>
:root { --motion-sr-press: 90ms; --motion-sr-content: 150ms; --motion-sr-morph: 260ms; --motion-sr-enter: 280ms; --motion-sr-queue: 240ms; --motion-sr-highlight: 750ms; --motion-sr-leave: 220ms; --ease-sr: cubic-bezier(.22,1,.36,1); }
.sr-feedback { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.sr-compact { flex-wrap: nowrap; position: relative; }
.sr-compact .sr-submit { height: 32px; border-radius: 8px; font-weight: 500; }
.sr-compact .sr-status { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.sr-compact:has(.error) .sr-status, .sr-compact:has(.unknown) .sr-status { position: fixed; right: 18px; bottom: 110px; width: min(320px, calc(100vw - 36px)); height: auto; clip-path: none; padding: 12px; border-radius: 8px; background: var(--surface); border: 1px solid var(--warn-line); z-index: 2000; }
.sr-submit:disabled:not(.submitting):not(.unknown) { opacity: .55; cursor: not-allowed; }
.sr-receipt { max-height: calc(100vh - 24px); overflow-y: auto; }
.sr-submit { width: 142px; height: 36px; border: 1px solid transparent; border-radius: 9px; background: var(--tb-primary); color: white; font-family: inherit; font-size: 13px; font-weight: 600; cursor: pointer; transition: background var(--motion-sr-morph) var(--ease-sr), color var(--motion-sr-morph), transform 110ms ease-out; }
.sr-submit:active:not(:disabled) { transform: scale(.975); transition-duration: var(--motion-sr-press); }
.sr-submit.accepted { background: var(--ok-bg); color: var(--tb-primary); }
.sr-submit.unknown { background: var(--warn-bg); color: var(--warn); }
.sr-submit:disabled { cursor: default; }
.sr-submit > span { display: inline-flex; align-items: center; gap: 6px; }
.sr-icon { display: inline-flex; align-items: center; justify-content: center; width: 15px; height: 15px; flex: none; }
.sr-details { margin: 8px 0; color: var(--ink-sub); }
.sr-details summary { cursor: pointer; font-size: 11px; }
.sr-spinner { width: 12px; height: 12px; border: 1.5px solid #ffffff60; border-top-color: white; border-radius: 50%; animation: sr-spin .7s linear infinite; }
@keyframes sr-spin { to { transform: rotate(360deg); } }
.sr-status { font-size: 12px; color: var(--ink-sub); }
.sr-receipt { position: fixed; z-index: 2000; box-sizing: border-box; width: min(300px, calc(100vw - 24px)); padding: 14px; border-radius: 12px; border: 1px solid var(--line); background: var(--surface, white); color: var(--ink); box-shadow: 0 2px 4px #173e3110, 0 8px 24px #173e3114; font-size: 12px; line-height: 1.5; }
.sr-receipt strong { font-size: 13px; padding-right: 18px; display: block; }
.sr-receipt strong span { color: var(--tb-primary); }
.sr-receipt p { margin: 8px 0; overflow-wrap: anywhere; color: var(--ink-sub); }
.sr-link, .sr-close { background: none; border: 0; cursor: pointer; color: var(--tb-primary); font-size: 12px; font-weight: 600; padding: 0; }
.sr-close { position: absolute; right: 10px; top: 8px; padding: 3px; font-size: 18px; }
.sr-receipt-enter-active { transition: opacity var(--motion-sr-enter), transform var(--motion-sr-enter) var(--ease-sr); }
.sr-receipt-leave-active { transition: opacity var(--motion-sr-leave), transform var(--motion-sr-leave); }
.sr-receipt-enter-from { opacity: 0; transform: translateY(8px) scale(.985); }
.sr-receipt-leave-to { opacity: 0; transform: translateY(4px); pointer-events: none; }
@media (prefers-reduced-motion: reduce) { .sr-submit, .sr-receipt, .sr-submit > span { transform: none !important; transition: none !important; } .sr-spinner { animation: none; } }
</style>
