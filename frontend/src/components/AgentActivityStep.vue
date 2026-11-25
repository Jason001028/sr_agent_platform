<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { toolDetails, type ActivityStep } from '../lib/agentActivity';
const props = defineProps<{ step: ActivityStep; developerMode?: boolean }>();
const details = computed(() => toolDetails(props.step));
const stateLabels = {running:'执行中',success:'已完成',failed:'失败',interrupted:'结果未确认'};
const now = ref(Date.now());
watch(() => props.step.state === 'running' ? props.step.startedAt : undefined, (startedAt, _, onCleanup) => {
  if (startedAt === undefined) return;
  now.value = Date.now();
  const timer = setInterval(() => { now.value = Date.now(); }, 1000);
  onCleanup(() => clearInterval(timer));
}, { immediate: true });
const duration = computed(() => {
  const { state, startedAt, result } = props.step;
  if (state === 'running' && startedAt !== undefined) {
    const seconds = Math.floor((now.value - startedAt) / 1000);
    return seconds >= 3 ? `已等待 ${seconds} 秒` : '';
  }
  const ms = result?.durationMs;
  return typeof ms === 'number' ? ms < 1000 ? '不到 1 秒' : `${Math.floor(ms / 1000)} 秒` : '';
});
</script>
<template>
  <li class="activity-step" :data-state="step.state" :data-attention="!!step.attention">
    <details class="step-disclosure">
      <summary class="step-line">
        <span class="trace-node" :class="{active:step.state === 'running',issue:step.state === 'failed' || step.state === 'interrupted' || step.attention}" :aria-label="step.attention || stateLabels[step.state]" role="img"></span>
        <span class="step-label">{{ step.label }}<span v-if="step.target"> · {{ step.target }}</span><span class="step-summary" :title="step.summaryTitle"> · {{ step.summary }}</span><span v-if="duration" class="step-duration"> · {{ duration }}</span></span>
        <span class="step-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="step-details">
        <dl v-if="details.length">
          <template v-for="(detail, index) in details" :key="index">
            <dt>{{ detail.label }}</dt><dd>{{ detail.value }}</dd>
          </template>
        </dl>
        <p v-else>{{ stateLabels[step.state] }}</p>
        <details v-if="developerMode"><summary>开发者详情</summary>
          <pre>{{ JSON.stringify({tool:step.name,call_id:step.callId,arguments:step.args,result:step.result}, null, 2) }}</pre>
        </details>
      </div>
    </details>
    <div v-if="$slots.action" class="step-action"><slot name="action" /></div>
  </li>
</template>
<style scoped>
.activity-step { position:relative; min-width:0; list-style:none; font-size:12px; color:var(--ink-sub); }
.step-line { display:flex; align-items:center; gap:8px; min-height:28px; min-width:0; list-style:none; }
.step-line::-webkit-details-marker { display:none; }
.step-chevron { color:var(--ink-faint); font-size:16px; line-height:1; }
.step-disclosure[open] .step-chevron { transform:rotate(90deg); }
.step-label { min-width:0; overflow-wrap:anywhere; }
.step-summary { color:var(--ink-faint); } [data-state=failed] .step-summary { color:#ad756c; }
[data-attention=true] .step-summary { color:var(--ink-sub); }
.step-duration { color:var(--ink-faint); font-variant-numeric:tabular-nums; white-space:nowrap; }
.step-details,.step-action:not(:empty) { margin:0 0 6px 22px; overflow-wrap:anywhere; }
.step-action:empty { display:none; }
.step-details { font-size:11px; } summary { cursor:pointer; }
.step-details dl { display:grid; grid-template-columns:minmax(0, 2fr) minmax(0, 3fr); gap:4px 8px; margin:0 0 6px; }
.step-details dt { color:var(--ink-faint); }
.step-details dd,.step-details p { margin:0; }
pre { max-height:220px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--ink-sub); font-size:10px; }
</style>
