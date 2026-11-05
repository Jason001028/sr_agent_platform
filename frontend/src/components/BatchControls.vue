<script setup lang="ts">
/** 批次控制：暂停/继续/取消 + 只读报告。
 *  控制带 control_revision，冲突由服务端 409 判定；前端不自行推断版本。 */
import { computed, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal.js';
import { useViewerStore } from '../stores/viewer.js';
import { apiBatchReport, loadSrConfig } from '../lib/api.js';
import type { BatchSnapshot } from '../lib/api.js';
import {
  goalStatusLabel, modeBadge, phaseLabel, trackText,
} from '../lib/batchUi.js';

const props = withDefaults(defineProps<{ snapshot: BatchSnapshot; variant?: 'all' | 'monitor' | 'report' }>(), { variant: 'all' });
const batch = useBatchGoalStore();
const viewer = useViewerStore();

const notice = ref('');
const confirmingCancel = ref(false);
const reportBusy = ref(false);
const reportError = ref('');
let requestGen = 0;
watch(() => props.snapshot.goal.goal_id, () => {
  requestGen += 1;
  notice.value = ''; confirmingCancel.value = false;
  reportBusy.value = false; reportError.value = '';
}, { flush: 'sync' });

const status = computed(() => props.snapshot.goal.status);
const terminal = computed(() => ['COMPLETED', 'FAILED', 'CANCELLED', 'BLOCKED']
  .includes(status.value));
const canPause = computed(() =>
  !terminal.value && status.value !== 'PAUSED' && !batch.controlBusy);
const canResume = computed(() =>
  status.value === 'PAUSED' && !batch.controlBusy);
const canCancel = computed(() => !terminal.value && !batch.controlBusy);
const reportReady = computed(() => props.snapshot.report.available);

async function run(action: 'pause' | 'resume' | 'cancel'): Promise<void> {
  confirmingCancel.value = false;
  const gen = requestGen;
  const message = await batch.control(action);
  if (gen !== requestGen) return;
  // A 409 already renders through controlError; do not repeat it as a notice.
  notice.value = batch.controlError ? '' : message;
}

async function openReport(): Promise<void> {
  if (reportBusy.value || !batch.goalId) return;
  reportBusy.value = true;
  const gen = requestGen;
  const gid = batch.goalId;
  reportError.value = '';
  try {
    const report = await apiBatchReport(loadSrConfig(), gid);
    if (gen !== requestGen || gid !== batch.goalId) return;
    viewer.showModal(
      '批次报告',
      reportSummary(report),
      '报告为只读快照；原始 JSON 可按需展开。',
      prettyJson(report),
    );
  } catch (e) {
    if (gen !== requestGen || gid !== batch.goalId) return;
    reportError.value = '读取批次报告失败：' + (e instanceof Error ? e.message : String(e));
  } finally {
    if (gen === requestGen && gid === batch.goalId) reportBusy.value = false;
  }
}

function reportSummary(report: unknown): string {
  const snap = props.snapshot;
  const badge = modeBadge(snap);
  const counts = snap.counts;
  const lines = [
    `批次：${snap.goal.title}（${badge.text}）`,
    `状态：${goalStatusLabel(snap.goal.status)}`
      + (snap.goal.phase ? ` · ${phaseLabel(snap.goal.phase)}` : ''),
    `进度：检测 ${trackText(snap.progress.detection)}`
      + ` · 提交 ${trackText(snap.progress.submission)}`
      + ` · 验证 ${trackText(snap.progress.verification)}`,
    `统计：失败 ${counts.failed} · 受阻 ${counts.blocked}`
      + ` · 状态未知 ${counts.unknown_jobs} · 在途 SR ${counts.in_flight_sr}`,
  ];
  if (report && typeof report === 'object') {
    const keys = Object.keys(report as Record<string, unknown>);
    lines.push(`报告字段：${keys.join('、')}`);
  }
  return lines.join('\n');
}

function prettyJson(value: unknown): string {
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}
</script>

<template>
  <section class="batch-controls" data-e2e="batch-controls">
    <div class="bc-actions">
      <button
        v-if="variant !== 'report' && status === 'PAUSED'"
        type="button"
        class="primary"
        data-e2e="batch-resume"
        :disabled="!canResume"
        @click="run('resume')"
      >继续</button>
      <button
        v-else-if="variant !== 'report' && !terminal"
        type="button"
        data-e2e="batch-pause"
        :disabled="!canPause"
        @click="run('pause')"
      >暂停</button>
      <details v-if="variant !== 'report'"><summary>更多控制</summary>
      <template v-if="!confirmingCancel">
        <button type="button" class="danger" data-e2e="batch-cancel"
                :disabled="!canCancel" @click="confirmingCancel = true">
          取消批次
        </button>
      </template>
      <template v-else>
        <span class="bc-confirm-text">取消后未开始的场景不再派发。</span>
        <button type="button" class="danger solid" data-e2e="batch-cancel-confirm"
                :disabled="batch.controlBusy"
                @click="run('cancel')">确认取消</button>
        <button type="button" @click="confirmingCancel = false">返回</button>
      </template>
      </details>
      <button
        v-if="variant !== 'monitor'"
        type="button"
        class="report"
        data-e2e="batch-report"
        :disabled="!reportReady || reportBusy"
        :title="reportReady ? '打开只读批次报告' : '报告尚未生成'"
        @click="openReport"
      >{{ reportBusy ? '读取中…' : '查看完整报告' }}</button>
    </div>

    <p v-if="notice" class="bc-notice">{{ notice }}</p>
    <p v-if="batch.controlError" class="bc-error">{{ batch.controlError }}</p>
    <p v-if="reportError" class="bc-error">{{ reportError }}</p>
    <p v-if="variant !== 'report'" class="bc-hint">
      暂停/取消只影响派发；已经提交给外部调度器的 SR 作业不会被终止。
    </p>
  </section>
</template>

<style scoped>
.batch-controls {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 9px 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}
.bc-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.bc-actions button {
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface-2);
  color: var(--ink-sub);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.bc-actions button:hover:not(:disabled) { background: var(--chrome); }
.bc-actions button:disabled { opacity: 0.45; cursor: default; }
.bc-actions button.primary { color: var(--accent-deep); border-color: var(--accent-soft); }
.bc-actions button.danger { color: var(--err); }
.bc-actions button.danger.solid {
  color: var(--err);
  background: var(--err-bg);
  border-color: var(--err-line);
}
.bc-actions button.report { margin-left: auto; color: var(--accent-deep); }
.bc-confirm-text { color: var(--ink-faint); font-size: 10px; }
.bc-notice { margin: 0; color: var(--ink-sub); font-size: 10.5px; line-height: 1.4; }
.bc-error { margin: 0; color: var(--err); font-size: 10.5px; line-height: 1.4; }
.bc-hint { margin: 0; color: var(--ink-faint); font-size: 9.5px; line-height: 1.35; }
</style>
