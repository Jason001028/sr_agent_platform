<script setup lang="ts">
import { computed } from 'vue';
import { useBatchDraftStore } from '../stores/batchDraft';
const props = defineProps<{ data: unknown }>();
const candidates = computed(() => {
  const raw = props.data as { data?: unknown } | undefined;
  const d = (raw?.data ?? props.data) as { kind?: string; candidates?: { path: string; name: string; evidence: string }[] } | undefined;
  return d?.kind === 'batch_repair_draft.v1' && Array.isArray(d.candidates) ? d.candidates.filter(c => typeof c.path === 'string' && typeof c.name === 'string') : [];
});
function show() {
  useBatchDraftStore().add(candidates.value);
  window.dispatchEvent(new CustomEvent('batch-task-open', { detail: { section: 'draft' } }));
}
</script>
<template><div v-if="candidates.length" class="receipt"><span>批量修复草案 · {{ candidates.length }} 景 · 等待范围与策略确认</span><button @click="show">查看待修复清单</button></div></template>
<style scoped>.receipt { display: grid; gap: 6px; margin: 6px 0; padding: 8px; border: 1px solid var(--line); border-radius: 10px; font-size: 12px; background: var(--surface-2); } button { justify-self: start; min-height: 28px; border: 1px solid var(--line); border-radius: 8px; color: var(--accent-deep); background: var(--surface); cursor: pointer; }</style>
