<script setup lang="ts">
/** Monitor 后台 SR 队列：在途优先，其次是异常，最后是最近结束的尾部。
 *  数字来自快照；错误原文可展开，不做二次加工。 */
import { computed, ref } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal.js';
import { useViewerStore } from '../stores/viewer.js';
import type { BatchJobSummary, BatchSnapshot } from '../lib/api.js';
import { formatClock, itemStatusLabel } from '../lib/batchUi.js';

const props = defineProps<{ snapshot: BatchSnapshot }>();
const batch = useBatchGoalStore();
const viewer = useViewerStore();
const onlyIssues = ref(false);
const openingId = ref('');

const activeIds = computed(() => new Set(
  props.snapshot.active_jobs.map((job) => job.item_id)));

const jobs = computed<BatchJobSummary[]>(() => {
  const merged = new Map<string, BatchJobSummary>();
  for (const job of props.snapshot.recent_jobs ?? []) merged.set(job.item_id, job);
  for (const job of props.snapshot.active_jobs) {
    if (!merged.has(job.item_id)) merged.set(job.item_id, job);
  }
  const list = [...merged.values()];
  return onlyIssues.value ? list.filter(isIssue) : list;
});

const issueCount = computed(() => {
  const merged = new Map<string, BatchJobSummary>();
  for (const job of props.snapshot.recent_jobs ?? []) merged.set(job.item_id, job);
  for (const job of props.snapshot.active_jobs) {
    if (!merged.has(job.item_id)) merged.set(job.item_id, job);
  }
  return [...merged.values()].filter(isIssue).length;
});

function isIssue(job: BatchJobSummary): boolean {
  if (job.unknown) return true;
  if (job.status === 'FAILED' || job.status === 'BLOCKED' || job.status === 'RETRY_WAIT') {
    return true;
  }
  const sr = String(job.sr_status ?? '').toUpperCase();
  return sr.includes('FAIL') || job.verification_ok === false;
}

function tone(job: BatchJobSummary): string {
  if (job.unknown) return 'warn';
  if (job.status === 'FAILED' || job.status === 'BLOCKED' || job.verification_ok === false) {
    return 'err';
  }
  if (job.status === 'RETRY_WAIT') return 'warn';
  if (activeIds.value.has(job.item_id)) return 'live';
  if (job.status === 'SUCCEEDED' || job.status === 'NO_ISSUE') return 'ok';
  return '';
}

function label(job: BatchJobSummary): string {
  if (job.unknown) return '状态未知';
  const sr = String(job.sr_status ?? '').toUpperCase();
  if (job.status === 'WAITING_SR' && sr) return `${itemStatusLabel(job.status)} · ${sr}`;
  return itemStatusLabel(job.status);
}

async function locate(job: BatchJobSummary): Promise<void> {
  if (!job.image_path || openingId.value) return;
  openingId.value = job.item_id;
  const error = await batch.openItem(job);
  if (error) viewer.showErr(error);
  openingId.value = '';
}
</script>

<template>
  <section class="batch-jobs" data-e2e="batch-jobs">
    <div class="bj-head">
      <span class="bj-title">后台 SR 队列</span>
      <span class="bj-summary">
        在途 {{ snapshot.counts.in_flight_sr }}
        <template v-if="issueCount"> · <b class="is-err">{{ issueCount }} 异常</b></template>
      </span>
      <label class="bj-filter">
        <input v-model="onlyIssues" type="checkbox" data-e2e="batch-only-issues">
        仅异常
      </label>
    </div>

    <p v-if="!jobs.length" class="bj-empty">
      {{ onlyIssues ? '当前没有异常作业。' : '还没有提交到外部调度器的作业。' }}
    </p>
    <ul v-else class="bj-list">
      <li v-for="job in jobs" :key="job.item_id" class="bj-row" :class="'is-' + tone(job)">
        <div class="bj-line">
          <span class="bj-ordinal">#{{ job.ordinal }}</span>
          <span class="bj-name" :title="job.name">{{ job.name }}</span>
          <span class="bj-status">{{ label(job) }}</span>
        </div>
        <div class="bj-meta">
          <span v-if="job.job_id != null" class="bj-jobid">Job {{ job.job_id }}</span>
          <span v-else class="bj-jobid muted">无 Job</span>
          <span v-if="job.unknown" class="bj-unknown">需人工确认</span>
          <span v-if="job.submitted_at" class="bj-time">提交 {{ formatClock(job.submitted_at) }}</span>
          <span v-if="job.updated_at" class="bj-time">更新 {{ formatClock(job.updated_at) }}</span>
          <button
            type="button"
            class="bj-locate"
            :disabled="!job.image_path || openingId === job.item_id"
            @click="locate(job)"
          >定位</button>
        </div>
        <details v-if="job.error" class="bj-error">
          <summary>查看失败原因</summary>
          <pre>{{ job.error }}</pre>
        </details>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.batch-jobs {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}
.bj-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.bj-title { color: var(--ink-sub); font-size: 11px; font-weight: 600; }
.bj-summary { color: var(--ink-faint); font-size: 10px; }
.bj-summary b { font-weight: 600; }
.bj-summary .is-err { color: var(--err); }
.bj-filter {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-sub);
  font-size: 10px;
  cursor: pointer;
}
.bj-filter input { margin: 0; }
.bj-list { max-height: 300px; overflow-y: auto; list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.bj-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 7px;
  background: var(--surface-2);
  border-left: 2px solid var(--line);
  border-radius: var(--r-ctrl);
}
.bj-row.is-live { border-left-color: var(--accent-deep); }
.bj-row.is-ok { border-left-color: var(--ok); }
.bj-row.is-warn { border-left-color: var(--warn); }
.bj-row.is-err { border-left-color: var(--err); }
.bj-line { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.bj-ordinal { flex: none; color: var(--ink-faint); font-size: 10px; font-variant-numeric: tabular-nums; }
.bj-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
  font-size: 11px;
}
.bj-status { flex: none; color: var(--ink-sub); font-size: 10px; }
.bj-row.is-err .bj-status { color: var(--err); }
.bj-row.is-warn .bj-status { color: var(--warn); }
.bj-row.is-live .bj-status { color: var(--accent-deep); }
.bj-row.is-ok .bj-status { color: var(--ok); }
.bj-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  color: var(--ink-faint);
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
}
.bj-jobid.muted { opacity: 0.7; }
.bj-unknown { color: var(--warn); font-weight: 600; }
.bj-locate {
  margin-left: auto;
  padding: 0 5px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  color: var(--accent-deep);
  font: inherit;
  font-size: 9.5px;
  cursor: pointer;
}
.bj-locate:hover:not(:disabled) { background: var(--accent-soft); }
.bj-locate:disabled { opacity: 0.45; cursor: default; }
.bj-error { font-size: 10px; color: var(--ink-sub); }
.bj-error summary { cursor: pointer; color: var(--err); }
.bj-error pre {
  margin: 4px 0 0;
  padding: 5px 7px;
  max-height: 110px;
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--err-line);
  border-radius: var(--r-ctrl);
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 9.5px;
}
.bj-empty { margin: 0; color: var(--ink-faint); font-size: 11px; }
</style>
