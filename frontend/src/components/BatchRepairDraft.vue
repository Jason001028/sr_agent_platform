<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { apiBatchImport, apiCreateBatch } from '../lib/api';
import { spatialId } from '../lib/spatial';
import { compactBatchError, importProblem, viewerRepairSequences } from '../lib/batchSequences';
import type { RepairSequence } from '../lib/batchSequences';
import type { BatchImportResult } from '../lib/api';
const emit = defineEmits<{ created: [] }>();
const viewer = useViewerStore(), batch = useBatchGoalStore(), chat = useChatStore();
const groups = ref<RepairSequence[]>([]), busy = ref(false), loading = ref(false), error = ref('');
const detectionMethod = ref<'absdiff' | 'ssim' | 'smart'>('absdiff');
const chosen = computed(() => groups.value.filter(g => g.checked && g.count && !g.reason && !g.loading));
let generation = 0;
onBeforeUnmount(() => { generation++; });
let frozen: { scope: string; result: BatchImportResult; requestKey: string } | null = null;
const scope = () => JSON.stringify([chat.sessionId, chosen.value.map(g => g.path), detectionMethod.value]);
watch(() => JSON.stringify(viewer.recsOrdered.map(r => [r.id, r.sceneDir, r.previewOnly])), async () => {
  const token = ++generation;
  frozen = null;
  groups.value = viewerRepairSequences(viewer.recsOrdered, viewer.sceneOrdinalOf);
  loading.value = true;
  // Freeze metadata only. No Goal, detector, mask or SR starts during selection.
  for (const group of groups.value) {
    try {
      const result = await apiBatchImport('', 'production', [{ path: group.path, name: group.name, evidence: `Viewer 序列 ${group.ordinal}` }], 'automatic');
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
async function start() {
  if (busy.value || loading.value || !chosen.value.length) return;
  busy.value = true; error.value = '';
  const key = scope(), sessionId = chat.sessionId, token = generation;
  const sources = chosen.value.map(g => ({ path: g.path, name: g.name, evidence: `Viewer 序列 ${g.ordinal}；自动批量修复` }));
  try {
    if (!frozen || frozen.scope !== key) {
      const result = detectionMethod.value === 'absdiff'
        ? await apiBatchImport('', 'production', sources, 'automatic')
        : await apiBatchImport('', 'production', sources, 'review', detectionMethod.value);
      if (key !== scope() || token !== generation) throw new Error('序列或会话已变化，请重新开始');
      const problem = importProblem(result);
      if (problem) throw new Error(problem);
      if (result.counts.items !== sources.length) throw new Error('核验数量与所选 PAN 本体不一致，请重新核对');
      frozen = { scope: key, result, requestKey: 'batch-' + spatialId() };
    }
    const goal = await apiCreateBatch({ manifest_ref: frozen.result.manifest_ref, title: `批量智能修复 · ${sources.length} 组`, session_id: sessionId, request_key: frozen.requestKey, exclusions: [] });
    if (chat.sessionId !== sessionId) throw new Error(`批次 ${goal.goal_id} 已在原会话创建，请返回原会话查看`);
    batch.setFollow(false);
    await batch.selectGoal(goal.goal_id);
    emit('created');
  } catch (e) { error.value = compactBatchError(String(e)); }
  finally { busy.value = false; }
}
</script>
<template>
  <div class="draft" data-e2e="batch-draft" :aria-busy="busy || loading">
    <p>选择左侧生产序列，自动处理组内全部符合条件的 PAN 本体。</p>
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
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p class="note">自动配对、检测、绘制掩码并提交验收；沿用生产提交限制和成果保护。</p>
    <button class="primary" :disabled="busy || loading || !chosen.length" @click="start">{{ busy ? '正在核验并创建任务…' : `开始智能修复 · 已选 ${chosen.length} 组` }}</button>
  </div>
</template>
<style scoped>
.draft { padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 8px; color: var(--ink-sub); font-size: 12px; min-width: 0;  box-sizing: border-box; }
.sequence-list { display: flex; flex-direction: column; gap: 3px; min-height: 0; }
.selection, .draft > p, .primary { flex-shrink: 0; }
p { margin: 0; line-height: 1.6; overflow-wrap: anywhere; }
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
.note { display: none; } .primary { position: sticky; bottom: 8px; z-index: 1; background: #245b50; color: white; padding: 10px; border-radius: 6px; width: 100%; }
.primary:disabled { background: #eef1f0; color: #8a9692; opacity: 1; }
</style>
