<script setup lang="ts">
import type { ActivityStep } from '../lib/agentActivity';
defineProps<{ step: ActivityStep; developerMode?: boolean }>();
const stateLabels = {running:'执行中',success:'已完成',failed:'失败',interrupted:'结果未确认'};
</script>
<template>
  <li class="activity-step" :data-state="step.state">
    <div class="step-line">
      <span class="state-icon" :class="step.state" :aria-label="stateLabels[step.state]" role="img">
        <svg v-if="step.state === 'success'" viewBox="0 0 14 14" aria-hidden="true"><path d="m3 7 2.5 2.5L11 4" /></svg>
        <span v-else-if="step.state === 'failed'" aria-hidden="true">×</span>
        <span v-else-if="step.state === 'interrupted'" aria-hidden="true">−</span>
        <span v-else class="running-dot" aria-hidden="true" />
      </span>
      <span class="step-label" :title="`${step.label} · ${step.summary}`">{{ step.label }}<span class="step-summary"> · {{ step.summary }}</span></span>
    </div>
    <details v-if="developerMode" class="step-details"><summary>开发者详情</summary>
      <pre>{{ JSON.stringify({tool:step.name,call_id:step.callId,arguments:step.args,result:step.result}, null, 2) }}</pre>
    </details>
  </li>
</template>
<style scoped>
.activity-step { position:relative; min-width:0; list-style:none; font-size:12px; color:var(--ink-sub); }
.step-line { display:flex; align-items:center; gap:8px; min-height:28px; min-width:0; }
.state-icon { width:14px; height:14px; flex:0 0 14px; display:grid; place-items:center; color:var(--ink-faint); background:var(--surface); border-radius:50%; font-size:14px; line-height:1; }
.state-icon.success { color:#709d87; } .state-icon.failed { color:#bb786d; }
svg { width:14px; height:14px; fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
.step-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
.step-summary { color:var(--ink-faint); } [data-state=failed] .step-summary { color:#ad756c; }
.running-dot { width:6px; height:6px; background:var(--ink-sub); border-radius:50%; animation:activity-breathe 1.8s ease-in-out infinite; }
.step-details { margin:0 0 6px 22px; font-size:10px; color:var(--ink-faint); } summary { cursor:pointer; }
pre { max-height:220px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--ink-sub); font-size:10px; }
@keyframes activity-breathe { 50% { opacity:.35; } }
@media(prefers-reduced-motion:reduce) { .running-dot { animation:none; } }
</style>
