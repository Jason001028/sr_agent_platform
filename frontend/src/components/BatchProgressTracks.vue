<script setup lang="ts">
import { computed } from 'vue';
import type { BatchSnapshot, BatchCounts } from '../lib/api';
import { missionTrackText } from '../lib/batchMission';
import { batchDisplayProgress, displayPercent } from '../lib/batchDisplayProgress';
const props = defineProps<{ snapshot?: BatchSnapshot; counts?: Partial<BatchCounts> | null; demo?: boolean }>();
const tracks = computed(() => {
  const progress = batchDisplayProgress(props.snapshot?.counts ?? props.counts);
  return [
    { key: 'detection' as const, label: '检测', path: 'M3 7V3h4m6 0h4v4m0 6v4h-4m-6 0H3v-4M13 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0' },
    { key: 'submission' as const, label: props.demo ? '模拟提交' : '提交', path: 'm2 6 8-4 8 4-8 4-8-4m0 4 8 4 8-4m-16 4 8 4 8-4' },
    { key: 'verification' as const, label: props.demo ? '演示验证' : '验收', path: 'M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0M6 10l3 3 5-6' },
  ].map(t => ({ ...t, track: progress[t.key], percent: displayPercent(progress[t.key]) }));
});
</script>
<template>
  <div class="phase-track" data-e2e="batch-tracks" aria-label="独立阶段进度">
    <div v-for="t in tracks" :key="t.key" class="phase" :data-stage="t.key">
      <svg class="phase-node" viewBox="0 0 20 20" aria-hidden="true"><path :d="t.path"/></svg>
      <span class="phase-label" :title="t.label">{{ t.label }}</span>
      <strong :title="missionTrackText(t.track)">{{ missionTrackText(t.track) }}</strong>
      <span class="phase-progress" role="progressbar" :aria-label="t.label"
        :aria-valuenow="t.percent == null ? undefined : Math.min(t.track.done, t.track.total)" :aria-valuemin="0"
        :aria-valuemax="t.percent == null ? undefined : t.track.total"
        :aria-valuetext="t.percent == null ? missionTrackText(t.track) : undefined">
        <i :style="{ width: `${t.percent ?? 0}%` }"/>
      </span>
      <span class="phase-percent">{{ t.percent == null ? '—' : `${Math.round(t.percent)}%` }}</span>
    </div>
  </div>
</template>
<style scoped>
.phase-track{min-width:0;overflow:hidden;border:1px solid var(--line);border-radius:9px;padding:0 9px;background:#f3f7f52b}
.phase{display:grid;grid-template-columns:15px 28px 46px minmax(0,1fr) 29px;align-items:center;gap:6px;min-width:0;padding:10px 0;color:var(--ink-sub);font-size:10px}
.phase+.phase{border-top:1px solid var(--line)}
.phase>*{min-width:0;overflow:hidden}
.phase-node{width:15px;height:15px;stroke:#487d6e;fill:none;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.phase-label,.phase strong{white-space:nowrap;text-overflow:ellipsis}
.phase strong{font-size:10px;font-weight:500;font-variant-numeric:tabular-nums;text-align:right}
.phase-progress{height:5px;border-radius:8px;background:#dce7e1;overflow:hidden;width:100%}
.phase-progress i{display:block;height:100%;max-width:100%;border-radius:inherit;background:#477e6c;transition:width 180ms ease}
.phase-percent{text-align:right;font-variant-numeric:tabular-nums;color:#42695c;font-size:10px}
@media(prefers-reduced-motion:reduce){.phase-progress i{transition:none}}
</style>
