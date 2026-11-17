<script setup lang="ts">
import { computed } from 'vue';
import type { ChatMsg } from '../stores/chat';
import { repairPlanData } from '../lib/repairPlan';
const props = defineProps<{ name: string; args?: unknown; result?: ChatMsg | null; sending?: boolean; developerMode?: boolean }>();
const label = computed(() => props.name === 'prepare_repair' ? '准备修复计划' : '读取工具结果');
const payload = computed(() => props.result?.data as { ok?: boolean; error?: string; data?: unknown } | undefined);
const failed = computed(() => props.result?.ok === false || payload.value?.ok === false);
const plan = computed(() => repairPlanData(props.result?.data));
const state = computed(() => failed.value ? '读取失败' : plan.value?.status === 'BLOCKED' ? '需补齐输入'
  : props.result?.ok === true ? '已完成' : props.sending ? '处理中' : '结果未确认');
const summary = computed(() => {
  if (failed.value) return props.name === 'read_tool_result' ? '结果读取未完成，任务状态以计划卡为准' : props.result?.detail || payload.value?.error || '计划准备失败';
  if (plan.value) return `${plan.value.targets.total} 景目标 · 计划详情见下方卡片`;
  return props.result?.ok === true ? '已读取保存结果' : '等待工具返回';
});
</script>
<template>
  <section class="repair-tool" :class="{ failed }" :data-state="failed ? 'failed' : 'normal'">
    <header><strong>{{ label }}</strong><span>{{ failed && name === 'prepare_repair' ? '准备失败' : state }}</span></header>
    <p :title="summary">{{ summary }}</p>
    <details v-if="developerMode"><summary>开发者详情</summary><pre>{{ JSON.stringify({ tool: name, args, result }, null, 2) }}</pre></details>
  </section>
</template>
<style scoped>
.repair-tool { min-width:0; max-width:100%; padding:9px 12px; border:1px solid var(--tb-divider); border-radius:10px; background:var(--tb-bg); font-size:11px; color:var(--tb-muted); }
header { display:flex; gap:8px; justify-content:space-between; } strong { font-weight:500; color:var(--tb-fg); } header span { flex-shrink:0; }
p { margin:4px 0 0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; } .failed header span { color:var(--danger, #9b5145); }
details { margin-top:6px; } pre { max-height:200px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; }
</style>
