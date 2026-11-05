<script setup lang="ts">
import { computed, ref } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { itemStatusLabel, verificationNote } from '../lib/batchUi';
import { handoffBatch } from '../lib/batchHandoff';
import BatchControls from './BatchControls.vue';
import BatchItemsList from './BatchItemsList.vue';
import QcListPanel from './QcListPanel.vue';
const batch = useBatchGoalStore();
const snap = computed(() => batch.snapshot);
const detail = ref(false);
const txt = ref(false);
const attention = computed(() => {
  const rows = new Map((snap.value?.recent_jobs ?? []).map(j => [j.item_id, j]));
  for (const j of snap.value?.active_jobs ?? []) rows.set(j.item_id, j);
  return [...rows.values()].filter(j => j.unknown || ['FAILED', 'BLOCKED', 'RETRY_WAIT'].includes(j.status) || j.verification_ok === false).slice(0, 3);
});
</script>
<template>
  <section class="result" data-e2e="batch-result">
    <p v-if="!snap?.report.available" class="empty">报告尚未生成。</p>
    <template v-else>
      <div class="metrics"><div><strong>{{ snap.counts.verified }}</strong><span>已核验{{ snap.goal.mode === 'demo' ? ' · 演示' : '' }}</span></div><div><strong>{{ snap.counts.unknown_jobs }}</strong><span>需复核 · 未知作业</span></div><div><strong>{{ snap.counts.failed + snap.counts.blocked }}</strong><span>异常 · 失败 / 受阻</span></div></div>
      <p v-if="verificationNote(snap)" class="note">{{ verificationNote(snap) }}</p>
      <p v-if="snap.goal.phase === 'waiting_review'" class="note">尚有掩码等待人工复核，请打开完整明细。</p>
      <div v-if="attention.length" class="attention"><h4>需要关注 · 最近结果</h4><article v-for="j in attention" :key="j.item_id"><strong :title="j.name">#{{ j.ordinal }} {{ j.name }}</strong><span>{{ j.unknown ? '状态未知，需复核' : itemStatusLabel(j.status) }}</span><p v-if="j.error">{{ j.error }}</p></article></div>
      <BatchControls :snapshot="snap" variant="report" />
      <button @click="handoffBatch('report')">交给 Agent 总结</button>
      <details @toggle="detail = ($event.target as HTMLDetailsElement).open"><summary>报告详情与处置</summary><template v-if="detail"><BatchItemsList /><details @toggle="txt = ($event.target as HTMLDetailsElement).open"><summary>质检 TXT 同步与批量处置</summary><p class="note">TXT 为独立数据来源；同步与处置不会改变 Batch 验收状态。</p><QcListPanel v-if="txt" /></details></template></details>
    </template>
  </section>
</template>
<style scoped>
.result { padding: 16px 12px; display: grid; gap: 12px; font-size: 12px; }
p, h4 { margin: 0; } h4 { font-size: 12px; font-weight: 500; }
.empty, .note { color: var(--ink-sub); line-height: 1.6; font-size: 11px; }
.metrics { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; }
.metrics div { display: grid; gap: 4px; } .metrics strong { font-size: 23px; font-weight: 500; color: #438C7B; } .metrics div:nth-child(2) strong { color: #B78C4D; } .metrics div:nth-child(3) strong { color: #9b5145; } .metrics span { font-size: 10px; color: var(--ink-sub); }
article { padding: 10px 0; border-bottom: 1px solid var(--line); display: grid; gap: 4px; } article strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; } article span, article p { font-size: 11px; color: #B78C4D; } article p { overflow-wrap: anywhere; }
button { min-height: 34px; font: inherit; color: var(--accent-deep); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; cursor: pointer; }
summary { cursor: pointer; min-height: 34px; align-content: center; color: var(--ink-sub); }
</style>
