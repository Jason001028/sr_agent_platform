<script setup lang="ts">
import { computed } from 'vue';
import type { BatchSnapshot } from '../lib/api';
import { missionStage, missionSummary, missionTrackText, terminalMission } from '../lib/batchMission';
import { trackPercent } from '../lib/batchUi';
const props = defineProps<{ snapshot: BatchSnapshot }>();
const tracks = computed(() => [
  { key: 'detection' as const, label: '检测', path: 'M7 12a5 5 0 1 1 7-7 5 5 0 0 1-7 7m7 0 4 4' },
  { key: 'submission' as const, label: 'SR 提交', path: 'm5 15 9-9 2 2-9 9H5v-2M12 6l3 3' },
  { key: 'verification' as const, label: '验收', path: 'm4 10 4 4 8-8' },
].map(t => ({ ...t, track: props.snapshot.progress[t.key],
  active: !terminalMission(props.snapshot.goal.status) && props.snapshot.goal.status !== 'PAUSED' && missionStage(props.snapshot) === t.key,
  attention: t.key === 'detection' && missionSummary(props.snapshot).issueFocus && props.snapshot.counts.submitted === 0 && props.snapshot.counts.detected < props.snapshot.counts.total,
})));
</script>
<template>
  <div class="phase-track" data-e2e="batch-tracks" aria-label="独立阶段进度">
    <div v-for="t in tracks" :key="t.key" class="phase" :class="{ active: t.active, complete: t.track.known && t.track.applicable && t.track.total > 0 && t.track.done >= t.track.total, inactive: t.track.known && !t.track.applicable, attention: t.attention }" :data-stage="t.key" :aria-current="t.active ? 'step' : undefined">
      <span class="phase-node"><svg viewBox="0 0 20 20" aria-hidden="true"><path :d="t.path"/></svg></span>
      <span class="phase-label">{{ t.label }}</span><strong>{{ missionTrackText(t.track) }}</strong>
      <span v-if="t.track.known && t.track.applicable && t.track.total > 0" class="phase-progress" role="progressbar" :aria-label="t.label" :aria-valuenow="t.track.done" :aria-valuemin="0" :aria-valuemax="t.track.total"><i :style="{ width: `${trackPercent(t.track) ?? 0}%` }"/></span>
    </div>
  </div>
</template>
<style scoped>
.phase-track{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;border-top:1px solid var(--line);padding-top:16px}.phase{display:flex;flex-direction:column;gap:6px;position:relative;min-width:0;color:#819188}.phase:not(:last-child)::after{content:'';position:absolute;top:12px;left:30px;right:-7px;height:1px;background:var(--line);transition:background 220ms ease}.phase-node{display:grid;place-items:center;width:24px;height:24px;border:1px solid var(--line);border-radius:7px;background:#f5f8f6;transition:color 220ms,background 220ms,border-color 220ms}.phase-node svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.phase-label{font-size:11px}.phase strong{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:#53665c}.phase.active .phase-node{color:#285b50;background:#edf4ef;border-color:#c8dbd0}.phase.complete:not(:last-child)::after{background:#7ba38d}.phase.complete .phase-node{color:#285b50;background:#edf4ef;border-color:#c8dbd0}.phase.active .phase-label{color:#285b50}.phase.attention .phase-node{color:#ac594e;background:#fbf2f0;border-color:#eedbd5}.phase.inactive{opacity:.55}.phase-progress{height:2px;background:#edf1ee;border-radius:2px;overflow:hidden;max-width:80px}.phase-progress i{display:block;height:100%;background:#658e7b;transition:width 220ms ease}.attention .phase-progress i{background:#ac594e}@media(prefers-reduced-motion:reduce){.phase::after,.phase-node,.phase-progress i{transition:none}}
</style>
