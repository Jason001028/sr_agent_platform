<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { goalStatusLabel, phaseLabel, trackText } from '../lib/batchUi';
import RoiToolsTab from './RoiToolsTab.vue';
import BatchProgressPanel from './BatchProgressPanel.vue';
import BatchRepairDraft from './BatchRepairDraft.vue';
import BatchResultReport from './BatchResultReport.vue';
import AppIcon from './AppIcon.vue';

const batch = useBatchGoalStore();
const chat = useChatStore();
const expanded = ref(true);
const section = ref<'draft' | 'monitor' | 'report'>(batch.goalId ? 'monitor' : 'draft');
const sections = [{ id: 'draft', label: '待修复' }, { id: 'monitor', label: '执行监控' }, { id: 'report', label: '结果报告' }] as const;
const completedAnimation = ref(false);
const seenCompletions = new Set<string>();
watch(() => batch.snapshot?.goal, (goal, previous) => {
  if (goal?.goal_id === previous?.goal_id && goal?.status === previous?.status) return;
  completedAnimation.value = false;
  if (!goal || goal.status !== 'COMPLETED') return;
  if (!seenCompletions.has(goal.goal_id) && previous?.goal_id === goal.goal_id && previous.status !== 'COMPLETED') completedAnimation.value = true;
  seenCompletions.add(goal.goal_id);
});
const snap = computed(() => batch.snapshot);
const disconnected = computed(() => ['error', 'reconnecting'].includes(batch.syncState));
function open(event?: Event) { expanded.value = true; section.value = (event as CustomEvent)?.detail?.section === 'draft' ? 'draft' : batch.goalId ? 'monitor' : 'draft'; }
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
  <section class="task-summary" data-e2e="batch-task-dock">
    <div class="summary-heading">
      <strong><AppIcon name="workflow" :size="16" /> 批量智能修复</strong>
      <button data-e2e="batch-task-expand" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起 ⌃' : '展开 ⌄' }}</button>
    </div>
    <div v-if="snap" class="persistent-status" data-e2e="batch-status" role="status">
      <div class="summary-track"><span class="state"><span v-if="snap.goal.status === 'COMPLETED'" class="completion" :class="{ animate: completedAnimation }">✓ </span>{{ goalStatusLabel(snap.goal.status) }}<template v-if="snap.goal.phase && snap.goal.status !== 'PAUSED'"> · {{ phaseLabel(snap.goal.phase) }}</template></span><small v-if="snap.goal.mode === 'demo'">DEMO / MOCK</small></div>
      <div class="summary-title" :title="snap.goal.title">{{ snap.goal.title }} · {{ snap.counts.total }} 景</div>
      <div class="batch-id" :title="snap.goal.goal_id">{{ snap.goal.goal_id }}</div>
      <div class="summary-track progress-summary"><span>检测 {{ trackText(snap.progress.detection) }}</span><span>提交 {{ trackText(snap.progress.submission) }}</span><span>验收 {{ trackText(snap.progress.verification) }}</span></div>
      <p v-if="snap.goal.status === 'PAUSED'">Goal 已暂停派发 · 外部 SR 不会因此停止（在途 {{ snap.counts.in_flight_sr }}）</p>
      <p v-if="snap.counts.failed || snap.counts.blocked" class="issue">失败 {{ snap.counts.failed }} · 受阻 {{ snap.counts.blocked }}</p>
    </div>
    <p v-else-if="batch.syncState === 'loading'">正在读取批次…</p>
    <p v-if="disconnected" role="status">连接中断，数据可能滞后</p>
    <p v-if="batch.loadError" role="alert">{{ batch.loadError }}</p>
  </section>
  <div class="tools-scroll">
    <section v-show="expanded" class="task-detail">
      <nav aria-label="批量修复详情" role="tablist" :style="{ '--tab-index': sections.findIndex(s => s.id === section) }">
        <button v-for="s in sections" :id="`batch-tab-${s.id}`" :key="s.id" role="tab" :aria-selected="section === s.id" :aria-controls="`batch-view-${s.id}`" :class="{ on: section === s.id }" @click="section = s.id">{{ s.label }}</button>
      </nav>
      <div v-show="section === 'draft'" id="batch-view-draft" role="tabpanel" aria-labelledby="batch-tab-draft"><KeepAlive><BatchRepairDraft v-if="section === 'draft'" @created="section = 'monitor'" /></KeepAlive></div>
      <div v-if="section === 'monitor'" id="batch-view-monitor" role="tabpanel" aria-labelledby="batch-tab-monitor"><BatchProgressPanel /></div>
      <div v-if="section === 'report'" id="batch-view-report" role="tabpanel" aria-labelledby="batch-tab-report"><BatchResultReport /></div>
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
.state { color: #6484BA; }
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
