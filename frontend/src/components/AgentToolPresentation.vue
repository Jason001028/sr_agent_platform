<script setup lang="ts">
import { computed, type ComponentPublicInstance } from 'vue';
import type { AgentActivityProjection } from '../lib/agentActivity';
import { toolPayload } from '../lib/agentActivity';
import AgentInspectionCard from './AgentInspectionCard.vue';
import AgentActivityTimeline from './AgentActivityTimeline.vue';
import AgentRepairPlanCard from './AgentRepairPlanCard.vue';
import SceneSearchResultCard from './SceneSearchResultCard.vue';
import AgentBatchReceipt from './AgentBatchReceipt.vue';
import AgentAnnotationReference from './AgentAnnotationReference.vue';
const props = defineProps<{ planCardRef?: (key: string, index: number, card: InstanceType<typeof AgentRepairPlanCard> | null) => void; projection: AgentActivityProjection; index: number; sessionKey?: string | null; developerMode?: boolean; resolvePlan?: (data: unknown) => unknown; timelineScope?: string }>();
const emit = defineEmits<{ openScene: [item: Record<string, unknown>]; planRevised: [source: unknown, replacement: unknown]; toolCallsUpdated: [] }>();
const tail = computed(() => props.projection.repairTails.get(props.index));
const isTail = computed(() => Array.from(props.projection.repairTails.values()).includes(props.index));
const timelineId = (index: number) => `${props.timelineScope}-repair-${index}`;
function registerPlanCard(key: string, card: Element | ComponentPublicInstance | null) {
  props.planCardRef?.(key, props.index, card as InstanceType<typeof AgentRepairPlanCard> | null);
}
</script>
<template>
  <div v-if="projection.groups.has(index) || projection.business.has(index) || timelineScope && isTail" class="tool-presentation">
    <AgentActivityTimeline v-for="group in projection.groups.get(index)" :key="group.key" :group="group" :session-key="sessionKey" :developer-mode="developerMode" />
    <template v-for="step in projection.business.get(index)" :key="step.key">
      <AgentInspectionCard v-if="step.business === 'inspection'" :data="step.inspection" />
      <AgentRepairPlanCard v-else-if="step.business === 'plan'" :ref="card => registerPlanCard(step.key, card)" :data="resolvePlan ? resolvePlan(step.result?.data) : step.result?.data" :auto-start="step.result?.autoStartRepair === true"
        :timeline-target="timelineScope && tail !== undefined ? '#' + timelineId(tail) : undefined" :developer-mode="developerMode"
        @plan-revised="emit('planRevised', step.result?.data, $event)" @tool-calls-updated="emit('toolCallsUpdated')" />
      <SceneSearchResultCard v-else-if="step.business === 'scenes'" :data="toolPayload(step.result)" :args="(step.args ?? {}) as Record<string, unknown>" status="completed" @open="emit('openScene', $event)" />
      <AgentBatchReceipt v-else-if="step.business === 'receipt'" :data="step.result?.data" />
      <AgentAnnotationReference v-else-if="step.business === 'annotation'" :data="step.result?.data" />
      <!-- request_clarification keeps the existing GoalClarifications action area. -->
      <details v-if="developerMode && step.business !== 'confirmation' && step.business !== 'plan'" class="business-debug"><summary>开发者详情 · {{ step.label }}</summary><pre>{{ JSON.stringify({tool:step.name,call_id:step.callId,arguments:step.args,result:step.result}, null, 2) }}</pre></details>
    </template>
    <div v-if="timelineScope && isTail" :id="timelineId(index)" class="tool-record-slot"></div>
  </div>
</template>
<style scoped>
.tool-presentation { display:grid; gap:8px; min-width:0; width:100%; }
.tool-record-slot { display:contents; }
.business-debug { color:var(--ink-faint); font-size:10px; } summary { cursor:pointer; } pre { overflow:auto; overflow-wrap:anywhere; white-space:pre-wrap; max-height:220px; }
</style>
