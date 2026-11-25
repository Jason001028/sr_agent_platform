<script setup lang="ts">
import { computed } from 'vue';
import type { ActivityGroup } from '../lib/agentActivity';
import { activityDisclosure, setActivityDisclosure } from '../lib/activityDisclosure';
import AgentActivityStep from './AgentActivityStep.vue';
const props = defineProps<{ group: ActivityGroup; sessionKey?: string | null; developerMode?: boolean; keepLatest?: boolean }>();
const identity = computed(() => `${props.sessionKey ?? 'unbound'}:${props.group.key}`);
const running = computed(() => props.group.steps.filter(s => s.state === 'running'));
const inspection = computed(() => props.group.steps.filter(step => step.inspectionProgress).at(-1));
const attention = computed(() => props.group.steps.filter(s => !!s.attention));
const completed = computed(() => props.group.steps.filter(s => s.state === 'success' && !s.inspectionProgress && !s.attention).length);
const failed = computed(() => props.group.steps.filter(s => s.state === 'failed').length);
const interrupted = computed(() => props.group.steps.filter(s => s.state === 'interrupted').length);
const single = computed(() => props.group.steps.length === 1);
const preference = computed(() => activityDisclosure.get(identity.value));
const expanded = computed(() => preference.value ?? (!inspection.value && running.value.length > 0));
const heading = computed(() => [
  inspection.value && `质检 · ${inspection.value.inspectionProgress}`,
  completed.value && `已完成 ${completed.value} 项操作`,
  failed.value && `${failed.value} 项失败`,
  attention.value.filter(step => !step.inspectionProgress).length > 0 && '有结果需关注',
  interrupted.value && `${interrupted.value} 项结果未确认`,
  running.value.length && `${running.value.length} 项执行中`,
].filter(Boolean).join(' · '));
function toggle() { setActivityDisclosure(identity.value,!expanded.value); }
</script>
<template>
  <section class="activity-timeline agent-trace" aria-label="工具调用记录" :data-group="group.key">
    <div v-if="!single" class="group-header">
      <button type="button" class="group-toggle" :aria-expanded="expanded" @click="toggle">
        <span class="trace-node" :class="{ active: running.length > 0, issue: failed || interrupted || attention.length }" aria-hidden="true"></span><span>{{ heading }}</span>
        <span class="chevron" :class="{ expanded }" aria-hidden="true">›</span>
      </button>
      <slot v-if="!expanded && inspection" name="action" :step="inspection" />
    </div>
    <ol class="steps">
      <AgentActivityStep v-for="step in group.steps" :key="step.key" :step="step" :developer-mode="developerMode"
        :hidden="!(single || expanded || step.state !== 'success' || step.attention || keepLatest && !step.inspectionProgress && preference !== false && step === group.steps.at(-1))">
        <template #action><slot name="action" :step="step" /></template>
      </AgentActivityStep>
    </ol>
  </section>
</template>
<style scoped>
.activity-timeline { min-width:0; max-width:100%; margin:4px 0; font-size:12px; color:var(--ink-sub); }
.group-header { display:flex; align-items:center; flex-wrap:wrap; column-gap:8px; }
.group-toggle { display:flex; align-items:center; gap:8px; min-height:28px; padding:0; border:0; background:none; color:var(--ink-sub); font:inherit; cursor:pointer; max-width:100%; text-align:left; }
.chevron { width:14px; flex:0 0 14px; font-size:18px; text-align:center; color:var(--ink-faint); transition:transform 180ms ease; } .chevron.expanded { transform:rotate(90deg); }
.steps { position:relative; padding:0; margin:0; min-height:0; list-style:none; }
.group-toggle:focus-visible { outline:2px solid var(--tb-focus); outline-offset:3px; border-radius:3px; }
@media(prefers-reduced-motion:reduce) { * { transition:none!important; } }
</style>
