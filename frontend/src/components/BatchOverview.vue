<script setup lang="ts">
/** Batch 概览：真实状态 + 紧凑统计。统计数字全部来自 snapshot。 */
import { computed } from 'vue';
import type { BatchSnapshot } from '../lib/api.js';
import {
  formatDateTime, goalStatusLabel, modeBadge, phaseLabel, verificationNote,
} from '../lib/batchUi.js';

const props = defineProps<{ snapshot: BatchSnapshot }>();

const badge = computed(() => modeBadge(props.snapshot));
const counts = computed(() => props.snapshot.counts);
const excludedSkipped = computed(() =>
  counts.value.excluded + counts.value.skipped_runtime);
const note = computed(() => verificationNote(props.snapshot));
const statusTone = computed(() => {
  const status = props.snapshot.goal.status;
  if (status === 'FAILED' || status === 'BLOCKED') return 'err';
  if (status === 'PAUSED' || status === 'CANCELLED') return 'warn';
  if (status === 'COMPLETED') return 'ok';
  return 'live';
});

const metrics = computed(() => [
  { key: 'total', label: '总景数', value: counts.value.total },
  { key: 'detected', label: '已检测', value: counts.value.detected },
  { key: 'submitted', label: '已提交', value: counts.value.submitted },
  { key: 'verified', label: '已验证', value: counts.value.verified },
  { key: 'failed', label: '失败', value: counts.value.failed, tone: counts.value.failed ? 'err' : '' },
  { key: 'blocked', label: '受阻', value: counts.value.blocked, tone: counts.value.blocked ? 'warn' : '' },
  { key: 'excluded', label: '排除/跳过', value: excludedSkipped.value },
  { key: 'inflight', label: '在途 SR', value: counts.value.in_flight_sr },
  { key: 'unknown', label: '状态未知', value: counts.value.unknown_jobs,
    tone: counts.value.unknown_jobs ? 'warn' : '' },
]);
</script>

<template>
  <section class="batch-overview" data-e2e="batch-overview">
    <header class="bo-head">
      <div class="bo-title-row">
        <h3 class="bo-title" :title="snapshot.goal.title">{{ snapshot.goal.title }}</h3>
        <span class="bo-mode" :class="badge.tone">{{ badge.text }}</span>
      </div>
      <div class="bo-status-row">
        <span class="bo-status" :class="statusTone">
          {{ goalStatusLabel(snapshot.goal.status) }}
        </span>
        <span v-if="snapshot.goal.phase" class="bo-phase">
          {{ phaseLabel(snapshot.goal.phase) }}
        </span>
        <span class="bo-time">更新 {{ formatDateTime(snapshot.goal.updated_at) }}</span>
      </div>
      <p v-if="note" class="bo-note">{{ note }}</p>
    </header>
    <div class="bo-grid">
      <div v-for="metric in metrics" :key="metric.key" class="bo-metric"
           :class="metric.tone ? 'is-' + metric.tone : ''">
        <span class="bo-metric-label">{{ metric.label }}</span>
        <span class="bo-metric-value">{{ metric.value }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.batch-overview {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}
.bo-head { display: flex; flex-direction: column; gap: 5px; }
.bo-title-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
.bo-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.bo-mode {
  flex: none;
  padding: 1px 7px;
  border-radius: var(--r-pill);
  border: 1px solid var(--line);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--ink-sub);
  background: var(--surface-2);
}
.bo-mode.demo { color: var(--warn); background: var(--warn-bg); border-color: var(--warn-line); }
.bo-mode.production { color: var(--ok); background: var(--ok-bg); border-color: var(--ok-line); }
.bo-status-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; font-size: 11px; }
.bo-status { font-weight: 600; color: var(--ink-sub); }
.bo-status.live { color: var(--accent-deep); }
.bo-status.ok { color: var(--ok); }
.bo-status.warn { color: var(--warn); }
.bo-status.err { color: var(--err); }
.bo-phase { color: var(--ink-sub); }
.bo-time { margin-left: auto; color: var(--ink-faint); font-size: 10px; }
.bo-note {
  margin: 0;
  color: var(--warn);
  font-size: 10.5px;
  line-height: 1.4;
}
.bo-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
}
.bo-metric {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 5px 7px;
  background: var(--surface-2);
  border-radius: var(--r-ctrl);
  min-width: 0;
}
.bo-metric-label { color: var(--ink-faint); font-size: 10px; }
.bo-metric-value { color: var(--ink); font-size: 15px; font-weight: 600; line-height: 1.15; }
.bo-metric.is-warn .bo-metric-value { color: var(--warn); }
.bo-metric.is-err .bo-metric-value { color: var(--err); }
</style>
