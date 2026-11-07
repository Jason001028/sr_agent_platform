<script setup lang="ts">
import { computed } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { verificationNote } from '../lib/batchUi';
import { handoffBatch } from '../lib/batchHandoff';
import BatchControls from './BatchControls.vue';
const batch = useBatchGoalStore();
const snap = computed(() => batch.snapshot);
</script>
<template>
  <section v-if="snap?.report.available" class="result" data-e2e="batch-result">
    <BatchControls :snapshot="snap" variant="report" report-label="查看结果" />
    <p v-if="verificationNote(snap)" class="note">{{ verificationNote(snap) }}</p>
    <details><summary>结果处置</summary><button @click="handoffBatch('report')">交给 Agent 总结</button><p class="note">质检清单同步仍在下方原有清单中操作。</p></details>
  </section>
</template>
<style scoped>.result{display:grid;gap:8px;font-size:12px}.note{font-size:11px;color:var(--ink-sub);margin:0;line-height:1.6}summary{font-size:11px;color:var(--ink-sub);cursor:pointer;padding:5px 0}button{font:inherit;cursor:pointer;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--accent-deep);padding:6px 9px;margin:5px 0}</style>
