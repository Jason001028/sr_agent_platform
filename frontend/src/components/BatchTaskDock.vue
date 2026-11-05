<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { goalStatusLabel, trackPercent, trackText } from '../lib/batchUi';
import RoiToolsTab from './RoiToolsTab.vue';
import BatchProgressPanel from './BatchProgressPanel.vue';
import BatchRepairDraft from './BatchRepairDraft.vue';
import BatchOverview from './BatchOverview.vue';
import BatchControls from './BatchControls.vue';
import BatchItemsList from './BatchItemsList.vue';
import AppIcon from './AppIcon.vue';

const batch = useBatchGoalStore();
const chat = useChatStore();
const expanded = ref(false);
const section = ref<'draft' | 'monitor' | 'report'>('monitor');
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
      <span v-if="snap" class="state">{{ snap.goal.phase === 'waiting_review' ? '等待复核' : goalStatusLabel(snap.goal.status) }}</span>
      <button v-if="!batch.goalId" @click="expanded = true; section = 'draft'">＋ 新建</button>
      <button v-else data-e2e="batch-task-expand" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起 ⌃' : '详情 ⌄' }}</button>
    </div>
    <template v-if="snap">
      <div class="summary-title" :title="snap.goal.title">{{ snap.goal.title }} · {{ snap.counts.total }} 景 <small v-if="snap.goal.mode === 'demo'">DEMO / MOCK</small></div>
      <div class="summary-track"><span>检测 {{ trackText(snap.progress.detection) }}</span><span>提交 {{ snap.counts.submitted }} · 验收 {{ snap.counts.verified }}</span></div>
      <div class="track"><i :style="{ width: `${trackPercent(snap.progress.detection) ?? 0}%` }" /></div>
      <p v-if="snap.counts.failed || snap.counts.blocked" class="issue">失败 {{ snap.counts.failed }} · 受阻 {{ snap.counts.blocked }}</p>
    </template>
    <p v-else-if="batch.syncState === 'loading'">正在读取批次…</p>
    <p v-if="disconnected" role="status">连接中断，数据可能滞后</p>
  </section>
  <div class="tools-scroll">
    <section v-show="expanded" class="task-detail">
      <nav aria-label="批量修复详情">
        <button :class="{ on: section === 'draft' }" @click="section = 'draft'">待修复</button>
        <button :class="{ on: section === 'monitor' }" @click="section = 'monitor'">执行监控</button>
        <button :class="{ on: section === 'report' }" @click="section = 'report'">结果报告</button>
      </nav>
      <BatchRepairDraft v-show="section === 'draft'" @created="section = 'monitor'" />
      <BatchItemsList v-if="section === 'draft' && batch.goalId" />
      <BatchProgressPanel v-show="section === 'monitor'" />
      <div v-if="section === 'report'" class="report">
        <template v-if="snap"><BatchOverview :snapshot="snap" /><BatchControls :snapshot="snap" /></template>
        <p v-else>尚无批次报告。</p>
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
nav { display: flex; gap: 4px; padding: 8px 10px 0; }
nav button { flex: 1; }
nav .on { background: var(--accent-soft); }
.report { display: grid; gap: 8px; padding: 9px; }
.tools-scroll :deep(.rt), .tools-scroll :deep(.batch-panel) { height: auto; overflow: visible; }
</style>
