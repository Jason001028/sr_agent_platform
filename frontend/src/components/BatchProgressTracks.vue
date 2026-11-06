<script setup lang="ts">
/** 三维进度轨：检测 D/A、提交 S/R、验证 V/R。
 *  比例只画条；数字是快照事实。R=0 且检测未完成不写成「不适用」。 */
import { computed, ref, watch } from 'vue';
import type { BatchProgressTrack, BatchSnapshot } from '../lib/api.js';
import { repairTrackText, trackPercent, trackText } from '../lib/batchUi.js';

const props = defineProps<{ snapshot: BatchSnapshot }>();

const tracks = computed(() => {
  const progress = props.snapshot.progress;
  return [
    {
      key: 'detection', label: '检测', short: 'D/A',
      done: progress.detection.done, total: progress.detection.total,
      track: progress.detection as BatchProgressTrack,
      hint: progress.detection.known ? '' : '检测尚未完成',
    },
    {
      key: 'submission', label: '提交 SR', short: 'S/R',
      done: progress.submission.done, total: progress.submission.total,
      track: progress.submission as BatchProgressTrack,
      hint: !progress.submission.known && progress.submission.applicable ? '当前已知需修复 · 检测尚未结束' : repairTrackText(progress.submission),
    },
    {
      key: 'verification', label: '结果验收', short: 'V/R',
      done: progress.verification.done, total: progress.verification.total,
      track: progress.verification as BatchProgressTrack,
      hint: !progress.verification.known && progress.verification.applicable ? '当前已知需修复 · 检测尚未结束' : repairTrackText(progress.verification),
    },
  ];
});

const increments = ref<Record<string, number>>({});
watch(() => props.snapshot, (next, previous) => {
  if (!previous || next.goal.goal_id !== previous.goal.goal_id) { increments.value = {}; return; }
  for (const key of ['detection', 'submission', 'verification'] as const) {
    for (const field of ['done', 'total'] as const) {
      if (next.progress[key][field] > previous.progress[key][field]) increments.value[key + field] = (increments.value[key + field] ?? 0) + 1;
    }
  }
});
function percent(track: BatchProgressTrack): number {
  return trackPercent(track) ?? 0;
}
</script>

<template>
  <section class="batch-tracks" data-e2e="batch-tracks">
    <div v-for="track in tracks" :key="track.key" class="bt-row">
      <div class="bt-head">
        <span class="bt-label">{{ track.label }} <em>{{ track.short }}</em></span>
        <span class="bt-value" :class="{ muted: !track.track.applicable }">
          <template v-if="track.track.applicable"><span :key="track.key + 'done' + increments[track.key + 'done']" :class="{ 'sr-flow-count just-updated': increments[track.key + 'done'] }">{{ track.done }}</span> / <span :key="track.key + 'total' + increments[track.key + 'total']" :class="{ 'sr-flow-count just-updated': increments[track.key + 'total'] }">{{ track.total }}</span></template><template v-else>{{ trackText(track.track) }}</template>
        </span>
      </div>
      <div
        class="bt-bar"
        :class="{ empty: !track.track.applicable }"
        role="progressbar"
        :aria-label="track.label"
        :aria-valuenow="track.track.applicable ? track.done : 0"
        :aria-valuemax="track.track.applicable ? track.total : 100"
      >
        <span class="bt-fill" :style="{ width: percent(track.track) + '%' }" />
      </div>
      <p v-if="track.hint" class="bt-hint">{{ track.hint }}</p>
    </div>
  </section>
</template>

<style scoped>
.batch-tracks {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}
.bt-row { display: flex; flex-direction: column; gap: 4px; }
.bt-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.bt-label { color: var(--ink-sub); font-size: 11px; font-weight: 600; }
.bt-label em {
  margin-left: 3px;
  font-style: normal;
  font-weight: 400;
  color: var(--ink-faint);
  font-size: 9.5px;
}
.bt-value { color: var(--ink); font-size: 12px; font-variant-numeric: tabular-nums; }
.bt-value.muted { color: var(--ink-faint); font-size: 11px; }
.bt-bar {
  height: 5px;
  overflow: hidden;
  border-radius: var(--r-pill);
  background: var(--surface-3);
}
.bt-bar.empty { background: var(--surface-2); border: 1px dashed var(--line); }
.bt-fill {
  display: block;
  height: 100%;
  min-width: 0;
  border-radius: inherit;
  background: var(--accent-deep);
  transition: width 350ms ease;
}
.bt-hint { margin: 0; color: var(--ink-faint); font-size: 10px; line-height: 1.3; }
.batch-tracks { padding: 8px 0; border: 0; border-radius: 0; gap: 12px; }
.bt-row:nth-child(2) .bt-fill { background: #C99358; } .bt-fill { background: #6484BA; } .bt-row:last-child .bt-fill { background: #438C7B; }
</style>
