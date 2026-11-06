<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, useId, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { feedbackMatches, isolateFeedbackSvg, itemStatusLabel } from '../lib/batchUi';
import type { BatchFeedback } from '../lib/batchUi';
const props = withDefaults(defineProps<{ goalId: string; itemId: string; attempt: number; status: string; cue?: BatchFeedback; scanning?: boolean; enabled?: boolean }>(), { enabled: true });
const batch = useBatchGoalStore();
const root = ref<HTMLElement>();
const visible = ref(false);
const pageVisible = ref(!document.hidden);
const active = ref(false);
const scope = 'batch-' + useId().replace(/[^a-zA-Z0-9_-]/g, '-');
let timer: ReturnType<typeof setTimeout> | undefined;
let observer: IntersectionObserver | undefined;
let remaining = 0;
let startedAt = 0;
const durations = { mask: 780, receipt: 620, verified: 850 };
const eligible = computed(() => feedbackMatches(props.cue, props.goalId, props.itemId, props.attempt, props.status));
const canRun = computed(() => visible.value && pageVisible.value && props.enabled !== false);
function pauseTimer(): void {
  if (timer != null) {
    clearTimeout(timer); timer = undefined;
    remaining = Math.max(0, remaining - (performance.now() - startedAt));
  }
}
watch(() => props.cue?.key, () => { pauseTimer(); active.value = false; remaining = 0; });
watch([eligible, canRun, () => props.cue?.key], () => {
  pauseTimer();
  if (!eligible.value) { active.value = false; return; }
  if (!canRun.value) return;
  const cue = props.cue!;
  if (!active.value) {
    // Brief snapshot lag is allowed; hidden/history feedback is never queued indefinitely.
    if (Date.now() - cue.at > 3000 || !batch.claimFeedback(cue.key)) return;
    active.value = true;
    remaining = durations[cue.kind];
  }
  if (remaining > 0) {
    startedAt = performance.now();
    timer = setTimeout(() => { timer = undefined; remaining = 0; active.value = false; }, remaining);
  }
}, { immediate: true });
const files = import.meta.glob('../../icon/sr-batch-microfeedback/*.svg', { eager: true, query: '?raw', import: 'default' }) as Record<string, string>;
const kind = computed(() => active.value && eligible.value ? props.cue?.kind : props.status === 'MASK_READY' ? 'mask' : props.status === 'SUCCEEDED' ? 'verified' : 'scan');
const showSvg = computed(() => active.value && eligible.value || ['DETECTING', 'MASK_READY', 'SUCCEEDED'].includes(props.status));
const markup = computed(() => {
  const name = kind.value === 'mask' ? 'mask-ready-once' : kind.value === 'receipt' ? 'sr-received-once' : kind.value === 'verified' ? 'item-verified-once' : 'tile-scanning-loop';
  const svg = files['../../icon/sr-batch-microfeedback/' + name + '.svg'] || '';
  // Visibility changes only pause CSS; they never replace these SVG nodes.
  const animated = kind.value === 'scan' ? !!props.scanning && props.status === 'DETECTING' : active.value && eligible.value;
  return isolateFeedbackSvg(svg, scope, animated);
});
const error = computed(() => ['FAILED', 'BLOCKED', 'REVIEW_REJECTED'].includes(props.status));
function visibility() { pageVisible.value = !document.hidden; }
onMounted(() => {
  observer = new IntersectionObserver(entries => { visible.value = entries[0]?.isIntersecting ?? false; });
  if (root.value) observer.observe(root.value);
  document.addEventListener('visibilitychange', visibility);
});
onBeforeUnmount(() => { pauseTimer(); observer?.disconnect(); document.removeEventListener('visibilitychange', visibility); });
</script>
<template>
  <span ref="root" class="batch-feedback-icon" :class="{ 'is-paused': !canRun, 'is-verified-feedback': active && eligible && cue?.kind === 'verified' }" :title="itemStatusLabel(status)" aria-hidden="true">
    <span v-if="error" class="feedback-error">!</span>
    <span v-else-if="showSvg" v-html="markup" />
    <svg v-else class="feedback-quiet" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <circle cx="12" cy="12" r="8" />
      <path v-if="status === 'NO_ISSUE' || status === 'SKIPPED'" d="M8 12h8" />
      <path v-else d="M12 7v5l3 2" />
    </svg>
  </span>
</template>
<style scoped>
.batch-feedback-icon { display:inline-flex; width:28px; height:28px; flex:0 0 28px; align-items:center; justify-content:center; }
.batch-feedback-icon :deep(svg) { width:28px; height:28px; display:block; }
.is-paused :deep(svg *) { animation-play-state:paused!important; }
.feedback-error { display:grid; place-items:center; width:24px; height:24px; border:1px solid var(--err); border-radius:7px; color:var(--err); font-weight:600; }
.feedback-quiet { color:var(--ink-faint); }
</style>
