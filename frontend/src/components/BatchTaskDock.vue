<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useImageRefreshStore } from '../stores/imageRefresh';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { goalStatusLabel, phaseLabel, trackText } from '../lib/batchUi';
import { batchDockOpen, openBatchDock } from '../lib/batchDock';
import RoiToolsTab from './RoiToolsTab.vue';
import BatchProgressPanel from './BatchProgressPanel.vue';
import BatchRepairDraft from './BatchRepairDraft.vue';
import BatchResultReport from './BatchResultReport.vue';
import AppIcon from './AppIcon.vue';

const batch = useBatchGoalStore();
const chat = useChatStore();
const expanded = ref(true);
const section = ref<'draft' | 'monitor'>(batch.goalId ? 'monitor' : 'draft');
const detailsOpen = ref(false);
const dock = ref<HTMLElement | null>(null);
const refresh = useImageRefreshStore();
const completedAnimation = ref(false);
const seenCompletions = new Set<string>();
watch(() => batch.snapshot?.goal, (goal, previous) => {
  if (goal?.goal_id === previous?.goal_id && goal?.status === previous?.status) return;
  completedAnimation.value = false;
  if (!goal || goal.status !== 'COMPLETED') return;
  if (!seenCompletions.has(goal.goal_id) && previous?.goal_id === goal.goal_id && previous.status !== 'COMPLETED') completedAnimation.value = true;
  seenCompletions.add(goal.goal_id);
  void refresh.scan(true);
});
const snap = computed(() => batch.snapshot);
const disconnected = computed(() => ['error', 'reconnecting'].includes(batch.syncState));
async function open(event?: Event) {
  openBatchDock(); expanded.value = true; detailsOpen.value = false;
  section.value = (event as CustomEvent)?.detail?.section === 'draft' ? 'draft' : batch.goalId ? 'monitor' : 'draft';
  await nextTick(); dock.value?.scrollIntoView({ block: 'nearest' }); dock.value?.focus({ preventScroll: true });
}
onMounted(() => window.addEventListener('batch-task-open', open));
onBeforeUnmount(() => window.removeEventListener('batch-task-open', open));
// Association lives above the collapsible detail, so both tabs keep synchronization alive.
onMounted(() => { void batch.associate(chat.sessionId); });
let discoveryTimer: ReturnType<typeof setInterval> | undefined;
onMounted(() => { discoveryTimer = setInterval(() => { void batch.discover(chat.sessionId); }, 5000); });
onBeforeUnmount(() => { clearInterval(discoveryTimer); batch.detach(); });
watch(() => chat.sessionId, sid => { void batch.associate(sid); });
</script>

<template>
  <section ref="dock" tabindex="-1" v-show="batchDockOpen" class="task-summary" data-e2e="batch-task-dock">
    <div class="summary-heading">
      <strong><AppIcon name="workflow" :size="16" /> 批量智能修复</strong>
      <button data-e2e="batch-task-expand" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起 ⌃' : '展开 ⌄' }}</button>
    </div>
    <div v-if="snap && section !== 'draft'" class="persistent-status" data-e2e="batch-status" role="status">
      <div class="summary-track"><span class="state"><span v-if="snap.goal.status === 'COMPLETED'" class="completion" :class="{ animate: completedAnimation }">✓ </span>{{ goalStatusLabel(snap.goal.status) }}<template v-if="snap.goal.phase && snap.goal.status !== 'PAUSED'"> · {{ phaseLabel(snap.goal.phase) }}</template></span><small v-if="snap.goal.mode === 'demo'">DEMO / MOCK</small></div>
      <div class="summary-title" :title="snap.goal.title">{{ snap.goal.title }} · {{ snap.counts.total }} 景</div>
      <p>已完成 {{ snap.counts.verified }} · 失败 {{ snap.counts.failed }} · 跳过 {{ snap.counts.excluded + snap.counts.skipped_runtime + snap.counts.capacity_skipped }} · 无需修复 {{ snap.counts.no_issue }}</p>
      <div class="summary-track progress-summary"><span>检测 {{ trackText(snap.progress.detection) }}</span><span>提交 {{ trackText(snap.progress.submission) }}</span><span>验收 {{ trackText(snap.progress.verification) }}</span></div>
      <p>检测中 {{ snap.counts.detecting }} · SR 执行中 {{ snap.counts.in_flight_sr }} · 验收中 {{ snap.counts.verifying }}</p>
      <p v-if="snap.goal.status === 'PAUSED'">Goal 已暂停派发 · 外部 SR 不会因此停止（在途 {{ snap.counts.in_flight_sr }}）</p>
      <p v-if="snap.goal.status === 'COMPLETED'">批次已完成，左侧符合条件的影像将显示更新提醒。</p>
      <p v-if="snap.counts.blocked" class="issue">受阻 {{ snap.counts.blocked }} · 异常信息见详情</p>
    </div>
    <p v-else-if="batch.syncState === 'loading'">正在读取批次…</p>
    <p v-if="disconnected" role="status">连接中断，数据可能滞后</p>
    <p v-if="batch.loadError" role="alert">{{ batch.loadError }}</p>
  </section>
  <div class="tools-scroll">
    <section v-show="batchDockOpen && expanded" class="task-detail">
      <div class="task-body">
        <BatchRepairDraft v-if="batchDockOpen && section === 'draft'" @created="section = 'monitor'" />
        <details v-else-if="section === 'monitor'" @toggle="detailsOpen = ($event.target as HTMLDetailsElement).open">
          <summary>查看详情</summary>
          <template v-if="detailsOpen"><BatchProgressPanel /><BatchResultReport v-if="snap?.report.available" /></template>
        </details>
      </div>
    </section>
    <RoiToolsTab />
  </div>
</template>

<style scoped>
.task-summary { flex: none; padding: 9px 10px; border-bottom: 1px solid var(--line); background: var(--surface); font-size: 12px; font-variant-numeric: tabular-nums; }
.summary-heading, .summary-track { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.summary-heading strong { font-size: 13px; font-weight: 600; margin-right: auto; display: inline-flex; align-items: center; gap: 4px; }
button { font: inherit; cursor: pointer; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--accent-deep); min-height: 28px; padding: 3px 8px; }
.state { font-size: 11px; color: var(--accent-deep); }
.summary-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 4px 0; color: var(--ink-sub); }
small { font-size: 10px; color: var(--warn); }
.summary-track { font-size: 11px; color: var(--ink-sub); }
.track { height: 3px; background: var(--surface-3); margin-top: 5px; border-radius: 3px; overflow: hidden; }
.track i { display: block; height: 100%; background: var(--accent-deep); transition: width var(--motion-batch-progress) var(--ease-standard); }
.task-summary p { margin: 4px 0 0; font-size: 11px; color: var(--ink-sub); }
.task-summary .issue { color: #9b5145; }
.tools-scroll { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; }
.task-detail { border-bottom: 1px solid var(--line); }
/* 选择列表内部滚动，主操作常驻底部；逐景监控和报告只在详情内展开。 */
.task-body { max-height: 440px; overflow-y: auto; overflow-x: hidden; }
.task-body > details > summary { cursor: pointer; padding: 12px; font-size: 12px; color: var(--accent-deep); }
nav { display: flex; gap: 4px; padding: 8px 10px 0; }
nav button { flex: 1; }
nav .on { background: var(--accent-soft); }
.report { display: grid; gap: 8px; padding: 9px; }
.tools-scroll :deep(.rt), .tools-scroll :deep(.batch-panel) { height: auto; overflow: visible; }
.task-summary, .tools-scroll { --surface: #fff; --surface-2: #FAFBFB; --surface-3: #edf1f0; --line: #E4E9E8; --ink: #23312F; --ink-sub: #677772; --ink-faint: #7e8d89; --accent-deep: #245B50; --ok: #438C7B; --warn: #B78C4D; background: #FAFBFB; color: #23312F; min-width: 0; }
.task-summary { padding: 10px 12px; }
.persistent-status { padding-top: 8px; }
.summary-title { margin: 6px 0 2px; color: var(--ink); }
.batch-id { color: var(--ink-faint); font: 10px var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 8px; }
.progress-summary { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 4px; font-size: 10px; }
.summary-heading button { min-height: 32px; background: white; border-radius: 6px; }
.state { color: #245B50; }
.task-detail { background: white; margin-bottom: 14px; }
nav { position: relative; padding: 0; gap: 0; margin: 0 12px; border-bottom: 1px solid var(--line); }
nav button { min-width: 0; height: 36px; border: 0; border-radius: 0; background: transparent; color: var(--ink-sub); padding: 0 2px; }
nav .on { background: transparent; color: var(--accent-deep); }
nav::after { content: ''; position: absolute; bottom: -1px; left: 0; width: 33.3333%; height: 2px; background: var(--accent-deep); transform: translateX(calc(var(--tab-index) * 100%)); transition: transform var(--motion-live) var(--ease-standard); }
.tools-scroll :deep(.batch-panel) { padding: 12px; gap: 10px; }
.tools-scroll :deep(.batch-current), .tools-scroll :deep(.batch-jobs), .tools-scroll :deep(.batch-events), .tools-scroll :deep(.batch-controls) { border: 0; border-radius: 0; border-top: 1px solid var(--line); padding: 10px 0; background: white; box-shadow: none; }
.tools-scroll :deep(.rt) { background: transparent; gap: 2px; padding-top: 0; }
.tools-scroll :deep(button), .tools-scroll :deep(select), .tools-scroll :deep(input:not([type=checkbox])) { min-height: 32px; }
.tools-scroll :deep(.bj-filter) { display: none; }
.tools-scroll :deep(.bp-head) { flex-wrap: wrap; }
.tools-scroll :deep(.bp-select) { max-width: 100%; }
.tools-scroll :deep(.bc-actions button.report) { margin-left: 0; background: #245B50; color: white; border-color: #245B50; min-height: 34px; }
.completion { color: #438C7B; display: inline-block; }
.completion.animate { animation: batch-complete var(--motion-emphasis) var(--ease-standard) 1; }
@keyframes batch-complete { from { opacity: 0; transform: scale(.75); } to { opacity: 1; transform: scale(1); } }
@media (prefers-reduced-motion: reduce) { nav::after { transition: none; } .completion.animate { animation: none; } }
</style>
