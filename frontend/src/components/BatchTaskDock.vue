<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { useImageRefreshStore } from '../stores/imageRefresh';
import { batchDockOpen, openBatchDock } from '../lib/batchDock';
import { missionSummary } from '../lib/batchMission';
import { isDeveloperMode } from '../lib/developerMode';
import { handoffBatch } from '../lib/batchHandoff';
import RoiToolsTab from './RoiToolsTab.vue';
import BatchProgressPanel from './BatchProgressPanel.vue';
import BatchProgressTracks from './BatchProgressTracks.vue';
import BatchRepairDraft from './BatchRepairDraft.vue';
import BatchControls from './BatchControls.vue';
import BatchEventTimeline from './BatchEventTimeline.vue';
import AppIcon from './AppIcon.vue';
const batch = useBatchGoalStore(), chat = useChatStore(), refresh = useImageRefreshStore();
const expanded = ref(true), menuOpen = ref(false), dock = ref<HTMLElement | null>(null);
const menuButton = ref<HTMLButtonElement | null>(null);
const compactHeading = ref(false);
const section = ref<'draft' | 'monitor'>(batch.goalId ? 'monitor' : 'draft');
let selectionRequested = false;
const prefKey = 'sr.batch.mission.view.v1';
let stored: Record<string, boolean> = {};
try { stored = JSON.parse(localStorage.getItem(prefKey) || '{}') || {}; } catch { /* optional view preferences */ }
const prefs = reactive({ statistics: stored.statistics === true, fullQueue: stored.fullQueue === true, events: stored.events === true });
watch(prefs, () => { try { localStorage.setItem(prefKey, JSON.stringify(prefs)); } catch { /* optional persistence */ } });
const developer = computed(() => { void menuOpen.value; return isDeveloperMode(); });
const snap = computed(() => batch.snapshot);
const mission = computed(() => snap.value ? missionSummary(snap.value) : null);
const monitoring = computed(() => section.value === 'monitor' && !!snap.value);
const disconnected = computed(() => ['error', 'reconnecting'].includes(batch.syncState));
const goalOptions = computed(() => {
  const list = [...(batch.availableGoals ?? [])];
  if (snap.value && !list.some(g => g.goal_id === snap.value!.goal.goal_id)) list.unshift(snap.value.goal);
  return list;
});
const detailedCounts = computed(() => snap.value ? [
  ['已检测', snap.value.counts.detected], ['已提交', snap.value.counts.submitted], ['已验收', snap.value.counts.verified],
  ['无需修复', snap.value.counts.no_issue], ['失败', snap.value.counts.failed], ['受阻', snap.value.counts.blocked],
  ['已排除', snap.value.counts.excluded], ['运行时跳过', snap.value.counts.skipped_runtime],
  ['在途 SR', snap.value.counts.in_flight_sr], ['未知作业', snap.value.counts.unknown_jobs],
].filter(([, value]) => Number(value) > 0) : []);
watch(() => snap.value?.goal, (goal, previous) => {
  if (!goal) return;
  if (!selectionRequested) section.value = 'monitor';
  if (goal.status === 'COMPLETED' && (previous?.goal_id !== goal.goal_id || previous.status !== 'COMPLETED')) void refresh.scan(true);
});
async function open(event?: Event) {
  openBatchDock(); expanded.value = true; menuOpen.value = false;
  selectionRequested = (event as CustomEvent)?.detail?.section === 'draft';
  section.value = selectionRequested ? 'draft' : batch.goalId ? 'monitor' : 'draft';
  await nextTick(); dock.value?.scrollIntoView({ block: 'nearest' }); dock.value?.focus({ preventScroll: true });
}
function created() { selectionRequested = false; section.value = 'monitor'; menuOpen.value = false; }
async function selectGoal(event: Event) {
  selectionRequested = false;
  await batch.selectGoal((event.target as HTMLSelectElement).value);
  section.value = 'monitor';
}
function outside(e: Event) { if (!(e.target as Element)?.closest('.mission-menu-anchor')) menuOpen.value = false; }
function escape(e: KeyboardEvent) { if (e.key === 'Escape' && menuOpen.value) { menuOpen.value = false; void nextTick(() => menuButton.value?.focus()); } }
let discoveryTimer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  window.addEventListener('batch-task-open', open);
  document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
  void batch.associate(chat.sessionId);
  discoveryTimer = setInterval(() => { void batch.discover(chat.sessionId); }, 5000);
});
onBeforeUnmount(() => {
  window.removeEventListener('batch-task-open', open);
  document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape);
  clearInterval(discoveryTimer); batch.detach();
});
watch(() => chat.sessionId, sid => { void batch.associate(sid); });
</script>
<template>
  <div class="tools-scroll" @scroll="compactHeading = ($event.target as HTMLElement).scrollTop > 100">
    <section ref="dock" v-show="batchDockOpen" tabindex="-1" class="mission" data-e2e="batch-task-dock" aria-label="批量修复任务">
      <header class="mission-heading">
        <span class="mission-brand"><AppIcon name="bot" :size="17" tint /></span><strong>批量修复</strong>
        <span v-if="monitoring && mission && (compactHeading || !expanded)" class="heading-state" :title="`${mission.caption}：${mission.value}`">{{ mission.state }}</span>
        <div class="mission-menu-anchor">
          <button ref="menuButton" class="icon-button" data-e2e="batch-settings" aria-label="批次设置与操作" aria-controls="batch-mission-settings" :aria-expanded="menuOpen" @click="menuOpen = !menuOpen"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="4" cy="10" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="16" cy="10" r="1"/></svg></button>
          <div v-show="menuOpen" id="batch-mission-settings" class="mission-menu" role="dialog" aria-label="批次设置与操作">
            <h4>显示设置</h4>
            <label><span>详细统计</span><input v-model="prefs.statistics" type="checkbox" data-e2e="mission-statistics-toggle"></label>
            <label><span>完整任务列表</span><input v-model="prefs.fullQueue" type="checkbox" data-e2e="mission-queue-toggle"></label>
            <label><span>逐景定位与掩码动画</span><input type="checkbox" data-e2e="batch-follow" :checked="batch.follow" @change="batch.setFollow(($event.target as HTMLInputElement).checked)"></label>
            <label v-if="developer"><span>事件历史（调试）</span><input v-model="prefs.events" type="checkbox" data-e2e="mission-events-toggle"></label>
            <template v-if="goalOptions.length"><h4>关联批次</h4><select class="bp-select" :value="batch.goalId ?? ''" aria-label="选择批次" @change="selectGoal"><option v-for="goal in goalOptions" :key="goal.goal_id" :value="goal.goal_id">{{ goal.title || '未命名批次' }}</option></select></template>
            <template v-if="snap">
              <p class="batch-identity" :title="snap.goal.goal_id">{{ snap.goal.title }}</p>
              <button class="menu-link" :disabled="batch.syncState === 'loading'" @click="batch.refreshSnapshot()">刷新任务状态</button>
              <button v-if="section === 'draft'" class="menu-link" @click="selectionRequested = false; section = 'monitor'; menuOpen = false">返回当前任务</button>
              <h4>任务操作</h4><BatchControls :snapshot="snap" :variant="snap.goal.status === 'COMPLETED' ? 'monitor' : 'all'" report-label="查看批次报告" />
              <button v-if="snap.report.available && snap.goal.status !== 'COMPLETED'" class="menu-link" @click="menuOpen = false; handoffBatch('report')">交给 Agent 总结</button>
              <p v-if="batch.syncError" class="menu-error">{{ batch.syncError }}</p>
              <button class="menu-link" @click="batch.clearOverlay()">关闭掩码叠加</button>
            </template>
          </div>
        </div>
        <button class="icon-button collapse-button" data-e2e="batch-task-expand" :aria-expanded="expanded" :aria-label="expanded ? '收起任务面板' : '展开任务面板'" @click="expanded = !expanded"><svg viewBox="0 0 20 20" aria-hidden="true"><path :d="expanded ? 'm5 12 5-5 5 5' : 'm5 8 5 5 5-5'"/></svg></button>
      </header>
      <div class="mission-collapse" :class="{ collapsed: !expanded }" :aria-hidden="!expanded" :inert="!expanded"><div class="mission-body">
        <div v-if="monitoring && snap && mission" class="mission-status" :class="mission.tone" data-e2e="batch-status" role="status">
          <div class="status-line"><span class="status-dot" aria-hidden="true"/>{{ mission.state }}<small v-if="snap.goal.mode === 'demo'">DEMO / MOCK</small></div>
          <div class="mission-hero">
            <div class="hero-symbol" aria-hidden="true"><svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="13"/><path v-if="mission.tone === 'error'" d="M18 10v10m0 5v1"/><path v-else-if="mission.tone === 'done'" d="m11 18 5 5 10-11"/><path v-else d="M18 10v9l6 3"/></svg></div>
            <div class="hero-copy"><div class="hero-number" :aria-label="`${mission.label} ${mission.value}`"><strong>{{ mission.value }}</strong><span>{{ mission.unit }}</span></div><p>{{ mission.caption }}</p></div>
          </div>
          <BatchProgressTracks v-if="!['COMPLETED', 'CANCELLED'].includes(snap.goal.status)" :snapshot="snap" />
          <p v-if="mission.noJobNotice" class="mission-notice">{{ mission.noJobNotice }}</p>
          <dl v-if="prefs.statistics && detailedCounts.length" class="detailed-statistics" data-e2e="mission-statistics"><template v-for="[label, value] in detailedCounts" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></template></dl>
        </div>
        <p v-if="disconnected" class="connection-note" role="status">连接中断，保留最近快照；正在恢复。</p>
        <p v-if="batch.loadError" class="connection-note error" role="alert">{{ batch.loadError }}</p>
        <p v-if="batch.syncState === 'loading' && !snap" class="connection-note">正在读取任务…</p>
        <BatchRepairDraft v-if="batchDockOpen && section === 'draft'" @created="created" />
        <BatchProgressPanel v-else-if="section === 'monitor' && snap" v-model:full-queue="prefs.fullQueue" />
        <BatchEventTimeline v-if="monitoring && prefs.events && developer" />
      </div></div>
    </section>
    <RoiToolsTab />
  </div>
</template>
<style scoped>
.tools-scroll{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;--surface:#fff;--surface-2:#fafcfb;--line:#e4ebe6;--ink:#25342f;--ink-sub:#829087;--accent-deep:#285b50;background:#fafcfb;color:var(--ink);font-family:var(--font-sans,system-ui,-apple-system,'Microsoft YaHei',sans-serif)}
.mission{min-width:0;background:#fff;border-bottom:1px solid var(--line);margin-bottom:12px;outline:none}.mission-heading{position:sticky;top:0;z-index:8;display:flex;align-items:center;gap:9px;padding:11px 14px;background:#fff;border-bottom:1px solid #edf1ee}.mission-heading strong{font-size:14px;font-weight:600;margin-right:auto}.mission-brand{width:28px;height:28px;display:grid;place-items:center;background:#edf4ef;color:var(--accent-deep);border-radius:8px}.icon-button{display:grid;place-items:center;width:28px;height:28px;padding:4px;border:0;background:transparent;color:#6c8578;border-radius:7px;cursor:pointer}.icon-button:hover{background:#f2f6f3}.icon-button svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.5}.icon-button circle{fill:currentColor;stroke:0}
.mission-status{padding:16px;display:grid;gap:16px}.status-line{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:500;color:#367b6a}.status-dot{width:6px;height:6px;background:currentColor;border-radius:50%;flex:none}.status-line small{margin-left:auto;font-size:10px;color:#a17f48;font-weight:400}.mission-hero{display:flex;align-items:center;gap:13px}.hero-symbol{display:grid;place-items:center;width:55px;height:55px;border-radius:15px;color:#367b6a;background:#f0f6f2;flex:none}.hero-symbol svg{width:33px;height:33px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}.hero-copy{min-width:0}.hero-number{display:flex;align-items:baseline;gap:5px;white-space:nowrap}.hero-number strong{font-size:30px;font-weight:600;line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-1px}.hero-number span{font-size:14px}.hero-copy p{font-size:11px;color:var(--ink-sub);margin:7px 0 0}.error .status-line,.error .hero-symbol{color:#ac594e}.error .hero-symbol{background:#faf0ee}.quiet .status-line{color:#829087}.mission-notice{font-size:11px;line-height:1.6;border-left:2px solid #d5b1aa;padding:5px 0 5px 9px;color:#96665e;margin:0}.collapsed-state{display:flex;justify-content:space-between;gap:8px;margin:0;padding:7px 16px;font-size:11px;color:var(--accent-deep)}.collapsed-state span{color:var(--ink-sub)}
.mission-menu-anchor{position:relative}.mission-menu{position:absolute;right:-34px;top:36px;width:244px;max-width:calc(100vw - 60px);padding:14px;background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 10px 30px #16362614;z-index:20;max-height:calc(100vh - 190px);overflow-y:auto;box-sizing:border-box;color:var(--ink)}.mission-menu h4{font-size:11px;font-weight:500;color:var(--ink-sub);margin:13px 0 8px}.mission-menu h4:first-child{margin-top:0}.mission-menu label{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:11px 0;font-size:12px}.mission-menu input{accent-color:var(--accent-deep)}.mission-menu select{max-width:100%;width:100%;font:inherit;font-size:11px;padding:6px;color:var(--ink);border:1px solid var(--line);border-radius:6px;background:white}.batch-identity{font-size:11px;color:var(--ink-sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:8px 0}.menu-link{display:block;border:0;background:none;color:var(--accent-deep);font:inherit;font-size:11px;padding:6px 0;cursor:pointer}.menu-link:disabled{opacity:.4}.menu-error{font-size:11px;color:#ac594e;overflow-wrap:anywhere}.connection-note{font-size:11px;line-height:1.6;padding:0 16px 12px;color:var(--ink-sub);margin:0;overflow-wrap:anywhere}.connection-note.error{color:#ac594e}
.detailed-statistics{display:grid;grid-template-columns:1fr auto;gap:5px;font-size:11px;margin:0;padding-top:10px;border-top:1px solid var(--line);color:var(--ink-sub)}dt,dd{margin:0}dd{font-variant-numeric:tabular-nums;color:var(--ink)}.tools-scroll :deep(.rt){height:auto;overflow:visible;background:transparent;padding-top:0}.mission :deep(.be-list){max-height:none;overflow:visible}.mission :deep(.recent-events){display:none}.mission :deep(.batch-events){margin:0 16px 14px;border:0;border-top:1px solid var(--line);border-radius:0}.mission :deep(button:focus-visible),.mission :deep(summary:focus-visible){outline:2px solid var(--accent-deep);outline-offset:2px}.mission-body{animation:mission-reveal 180ms ease-out}@keyframes mission-reveal{from{opacity:.5}to{opacity:1}}@media(prefers-reduced-motion:reduce){.mission-body{animation:none}.mission *{transition:none!important}}
</style>

<style scoped>
.mission-collapse{display:grid;grid-template-rows:1fr;opacity:1;transition:grid-template-rows 220ms ease,opacity 180ms ease}.mission-collapse.collapsed{grid-template-rows:0fr;opacity:0;pointer-events:none}.mission-body{min-height:0;overflow:hidden}.heading-state{font-size:10px;color:var(--accent-deep);max-width:78px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}@media(prefers-reduced-motion:reduce){.mission-collapse{transition:none}}
</style>
