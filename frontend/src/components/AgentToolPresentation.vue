<script setup lang="ts">
import type { AgentActivityProjection } from '../lib/agentActivity';
import { toolPayload } from '../lib/agentActivity';
import AgentActivityTimeline from './AgentActivityTimeline.vue';
import AgentRepairPlanCard from './AgentRepairPlanCard.vue';
import SceneSearchResultCard from './SceneSearchResultCard.vue';
import AgentBatchReceipt from './AgentBatchReceipt.vue';
import AgentAnnotationReference from './AgentAnnotationReference.vue';
defineProps<{ projection: AgentActivityProjection; index: number; sessionKey?: string | null; developerMode?: boolean }>();
const emit = defineEmits<{ openScene: [item: Record<string, unknown>] }>();
</script>
<template>
  <div v-if="projection.groups.has(index) || projection.business.has(index)" class="tool-presentation">
    <AgentActivityTimeline v-for="group in projection.groups.get(index)" :key="group.key" :group="group" :session-key="sessionKey" :developer-mode="developerMode" />
    <template v-for="step in projection.business.get(index)" :key="step.key">
      <AgentRepairPlanCard v-if="step.business === 'plan'" :data="step.result?.data" />
      <SceneSearchResultCard v-else-if="step.business === 'scenes'" :data="toolPayload(step.result)" :args="(step.args ?? {}) as Record<string, unknown>" status="completed" @open="emit('openScene', $event)" />
      <AgentBatchReceipt v-else-if="step.business === 'receipt'" :data="step.result?.data" />
      <AgentAnnotationReference v-else-if="step.business === 'annotation'" :data="step.result?.data" />
      <!-- request_clarification keeps the existing GoalClarifications action area. -->
      <details v-if="developerMode && step.business !== 'confirmation'" class="business-debug"><summary>开发者详情 · {{ step.label }}</summary><pre>{{ JSON.stringify({tool:step.name,call_id:step.callId,arguments:step.args,result:step.result}, null, 2) }}</pre></details>
    </template>
  </div>
</template>
<style scoped>
.tool-presentation { display:grid; gap:8px; min-width:0; width:100%; }
.business-debug { color:var(--ink-faint); font-size:10px; } summary { cursor:pointer; } pre { overflow:auto; overflow-wrap:anywhere; white-space:pre-wrap; max-height:220px; }
</style>
