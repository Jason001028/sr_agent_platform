<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { apiBatchImport, apiConfirmRepair, apiPrepareRepair, apiStartRepair, HttpError } from '../lib/api';
import type { RepairPlan } from '../lib/api';
import { registerViewerObservation } from '../lib/viewerContext';
import { compactBatchError, importProblem, viewerRepairSequences } from '../lib/batchSequences';
import type { RepairSequence } from '../lib/batchSequences';
const emit = defineEmits<{ created: [] }>();
const viewer = useViewerStore(), batch = useBatchGoalStore(), chat = useChatStore();
const groups = ref<RepairSequence[]>([]), busy = ref(false), loading = ref(false), error = ref('');
const detectionMethod = ref<'absdiff' | 'ssim' | 'smart'>('absdiff');
const mode = ref<'demo' | 'production'>('demo');
const strategy = computed<'automatic' | 'review'>(() => detectionMethod.value === 'smart' ? 'review' : 'automatic');
const chosen = computed(() => groups.value.filter(g => g.checked && g.count && !g.reason && !g.loading));
// A prepared plan is shown first; the second click is the real user
// confirmation, recorded server-side. The Agent cannot produce that record.
const pendingPlan = ref<RepairPlan | null>(null);
const selectionVersion = ref(0);
watch(groups, () => { selectionVersion.value++; }, {deep:true, flush:'sync'});
const unregisterSelection = registerViewerObservation('repair_selection', () => ({
  draft_id:'viewer-sequences', version:selectionVersion.value, total:groups.value.length,
  selected:chosen.value.length, excluded:groups.value.filter(g=>!g.checked).length,
  selected_names:chosen.value.slice(0,3).map(g=>g.name), loading:loading.value,
}));
onBeforeUnmount(unregisterSelection);
let generation = 0;
onBeforeUnmount(() => { generation++; });
const signature = () => JSON.stringify([chat.sessionId, chosen.value.map(g => g.path), mode.value, detectionMethod.value, strategy.value]);
watch(() => JSON.stringify([viewer.recsOrdered.map(r => [r.id, r.sceneDir, r.previewOnly]), mode.value, detectionMethod.value]), async () => {
  const token = ++generation;
  pendingPlan.value = null;
  groups.value = viewerRepairSequences(viewer.recsOrdered, viewer.sceneOrdinalOf);
  loading.value = true;
  // Freeze metadata only. No Goal, detector, mask or SR starts during selection.
  for (const group of groups.value) {
    try {
      const result = await apiBatchImport('', mode.value, [{ path: group.path, name: group.name, evidence: `Viewer 序列 ${group.ordinal}` }], 'automatic');
      if (token !== generation) return;
      group.reason = compactBatchError(importProblem(result));
      group.count = group.reason ? 0 : result.counts.items;
      group.checked = group.count > 0;
    } catch (e) { if (token !== generation) return; group.reason = compactBatchError(String(e)); }
    finally { group.loading = false; }
  }
  if (token === generation) loading.value = false;
}, { immediate: true });
function selectAll(checked: boolean) { for (const g of groups.value) g.checked = checked && g.count > 0 && !g.reason; }
function planProblem(plan: RepairPlan): string {
  const blocking = (plan.blocking ?? []).map(row => row.message).filter(Boolean).join('；');
  if (plan.status !== 'READY') return blocking || '计划存在阻断项，请重新核对清单';
  if (!plan.targets.applicable) return '计划里没有可执行的目标项';
  return '';
}
async function prepareOnly(sessionId: string | null, token: number, key: string): Promise<RepairPlan> {
  const sources = chosen.value.map(g => ({ path: g.path, name: g.name, evidence: `Viewer 序列 ${g.ordinal}；自动批量修复` }));
  const plan = await apiPrepareRepair({
    session_id: sessionId, sources, mode: mode.value,
    strategy: strategy.value, detection_method: detectionMethod.value,
    title: `批量自动修复 · ${sources.length} 组`, created_by: 'tool_panel',
  });
  if (key !== signature() || token !== generation) throw new Error('序列、模式或会话已变化，请重新开始');
  const problem = planProblem(plan);
  if (problem) throw new Error(problem);
  if (plan.targets.applicable !== sources.length) throw new Error('核验数量与所选 PAN 本体不一致，请重新核对');
  return plan;
}
async function start() {
  if (busy.value || loading.value || !chosen.value.length) return;
  error.value = '';
  if (!chat.sessionId) await chat.newSession();
  const sessionId = chat.sessionId;
  if (!sessionId) { error.value = chat.error || '无法创建修复会话'; return; }
  const token = generation, key = signature();
  if (pendingPlan.value && key === signature()) {
    const pending = pendingPlan.value;
    if (!pending.execution.allowed) return;
    busy.value = true;
    try {
      await apiConfirmRepair(pending.plan_id, { session_id: sessionId });
      const started = await apiStartRepair(pending.plan_id, {
        session_id: sessionId, plan_version: pending.version,
      });
      if (chat.sessionId !== sessionId) throw new Error(`批次 ${started.goal.goal_id} 已在原会话创建，请返回原会话查看`);
      pendingPlan.value = null;
      batch.setFollow(false);
      await batch.selectGoal(started.goal.goal_id);
      emit('created');
    } catch (e) {
      if (e instanceof HttpError && e.status === 409) {
        // Expired/stale plan: discard the confirmation and prepare a fresh
        // plan so the user can confirm again without guessing.
        pendingPlan.value = null;
        error.value = '计划已失效，已重新准备；请再次确认后启动。' + compactBatchError(String(e));
        try { pendingPlan.value = await prepareOnly(sessionId, token, key); }
        catch (again) { error.value = compactBatchError(String(again)); }
      } else {
        error.value = compactBatchError(String(e));
      }
    } finally { busy.value = false; }
    return;
  }
  busy.value = true;
  try {
    // A stale pending plan is never reused; prepare against the current
    // selection/detection method first.
    pendingPlan.value = null;
    pendingPlan.value = await prepareOnly(sessionId, token, key);
  } catch (e) { error.value = compactBatchError(String(e)); }
  finally { busy.value = false; }
}
</script>
<template>
  <div class="draft" data-e2e="batch-draft" :aria-busy="busy || loading">
    <p>选择左侧生产序列，自动处理组内全部符合条件的 PAN 本体。</p>
    <label>执行模式 <select v-model="mode" :disabled="busy || loading" aria-label="批量执行模式"><option value="demo">演示（Fake SR）</option><option value="production">生产（受准入限制）</option></select></label>
    <label>检测方法 <select v-model="detectionMethod" :disabled="busy || loading" aria-label="批量检测方法"><option value="absdiff">像素差异</option><option value="ssim">结构差异</option><option value="smart">LTD（实验性，需人工复核）</option></select></label>
    <p v-if="detectionMethod === 'smart'">LTD · 局部纹理退化检测 · 内网传统算法。候选不会自动提交 SR；先复核候选掩码，再沿用人工质量验收。未检出候选不等于质量通过。</p>
    <div class="selection"><span>{{ groups.length }} 组序列</span><button :disabled="busy || loading" @click="selectAll(true)">全选</button><button :disabled="busy || loading" @click="selectAll(false)">取消全选</button></div>
    <div class="sequence-list">
      <label v-for="g in groups" :key="g.key" class="sequence-row" :class="{ selected: g.checked }">
        <input v-model="g.checked" type="checkbox" :disabled="busy || g.loading || !g.count || !!g.reason" :aria-label="`选择序列 ${String(g.ordinal).padStart(2, '0')}`">
        <span class="ordinal">{{ String(g.ordinal).padStart(2, '0') }}</span>
        <span class="name" :title="g.name">{{ g.name }}</span>
        <span class="state" :class="{ issue: g.reason, pending: g.loading }" :title="g.reason || undefined" :aria-label="g.reason ? `异常：${g.reason}` : undefined" :tabindex="g.reason ? 0 : undefined">{{ g.loading ? '核验中' : g.reason ? '异常' : '可修复' }}</span>
      </label>
    </div>
    <p v-if="!groups.length">左侧暂无已关联生产序列。</p>
    <p v-if="pendingPlan" class="plan-summary" data-e2e="batch-plan-summary">
      已准备计划：{{ pendingPlan.targets.applicable }} 景，{{ pendingPlan.mode === 'demo' ? '演示 / Fake SR' : '生产模式' }}，
      {{ pendingPlan.strategy.detection }}。{{ pendingPlan.execution.allowed ? '请在下方确认后启动。' : '计划已保留，执行条件满足后请重新准备并确认。' }}
    </p>
    <p v-if="pendingPlan && !pendingPlan.execution.allowed" class="error" role="status">
      计划已准备，当前环境暂不能启动：{{ compactBatchError(pendingPlan.execution.reason || '执行条件未满足') }}
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p class="note">自动配对、检测、绘制掩码并提交验收；沿用生产提交限制和成果保护。</p>
    <button class="primary" :disabled="busy || loading || !chosen.length || !!(pendingPlan && !pendingPlan.execution.allowed)" @click="start">{{ busy ? '正在核验并创建任务…' : pendingPlan && !pendingPlan.execution.allowed ? '计划已准备 · 当前环境不可启动' : pendingPlan ? `确认并启动 · ${pendingPlan.targets.applicable} 景` : `准备修复计划 · 已选 ${chosen.length} 组` }}</button>
  </div>
</template>
<style scoped>
.draft { padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 8px; color: var(--ink-sub); font-size: 12px; min-width: 0;  box-sizing: border-box; }
.sequence-list { display: flex; flex-direction: column; gap: 3px; min-height: 0; }
.selection, .draft > p, .primary, label { flex-shrink: 0; }
p { margin: 0; line-height: 1.6; overflow-wrap: anywhere; }
label { display: flex; align-items: center; gap: 8px; }
select { font: inherit; font-size: 11px; padding: 3px 6px; color: var(--ink); border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }
.selection { display: flex; gap: 12px; align-items: center; } .selection span { margin-right: auto; }
button { font: inherit; cursor: pointer; color: var(--accent-deep); background: transparent; border: 0; padding: 4px 0; }
button:disabled { opacity: .5; cursor: default; }
.sequence-row { display: grid; grid-template-columns: 14px 22px minmax(0, 1fr); gap: 2px 6px; align-items: center; padding: 4px 7px; border: 1px solid var(--line); border-radius: var(--r-ctrl); background: var(--surface); cursor: pointer; flex-shrink: 0; }
.sequence-row:hover { border-color: var(--accent-2); }
.sequence-row.selected { border-color: #cdded4; background: #f6f9f7; }
input { accent-color: #245b50; margin: 0; } .ordinal { color: #245b50; font-size: 11px; line-height: 16px; font-variant-numeric: tabular-nums; }
.name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); font-size: 11.5px; line-height: 16px; }
.state { grid-column: 3; justify-self: end; border-radius: var(--r-pill, 999px); padding: 0 6px; font-size: 10px; line-height: 14px; white-space: nowrap; color: #245b50; background: #e8f2ed; }
.state.issue { color: #9b5145; background: #f8eeeb; cursor: help; } .state.pending { color: var(--ink-sub); background: var(--surface-2); }
.error { color: #9b5145; }
.plan-summary { color: #245b50; background: #f0f6f2; border-radius: 6px; padding: 6px 8px; }
.note { display: none; } .primary { position: sticky; bottom: 8px; z-index: 1; background: #245b50; color: white; padding: 10px; border-radius: 6px; width: 100%; }
.primary:disabled { background: #eef1f0; color: #8a9692; opacity: 1; }
</style>
