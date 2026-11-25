<script setup lang="ts">
import type { ComponentPublicInstance } from 'vue';
import type { AgentActivityProjection } from '../lib/agentActivity';
import { toolPayload } from '../lib/agentActivity';
import AgentInspectionCard from './AgentInspectionCard.vue';
import AgentActivityTimeline from './AgentActivityTimeline.vue';
import AgentRepairPlanCard from './AgentRepairPlanCard.vue';
import SceneSearchResultCard from './SceneSearchResultCard.vue';
import AgentBatchReceipt from './AgentBatchReceipt.vue';
import AgentAnnotationReference from './AgentAnnotationReference.vue';
const props = defineProps<{ planCardRef?: (key: string, index: number, card: InstanceType<typeof AgentRepairPlanCard> | null) => void; projection: AgentActivityProjection; index: number; sessionKey?: string | null; developerMode?: boolean; resolvePlan?: (data: unknown) => unknown }>();
const emit = defineEmits<{ openScene: [item: Record<string, unknown>]; planRevised: [source: unknown, replacement: unknown]; toolCallsUpdated: [] }>();
function registerPlanCard(key: string, card: Element | ComponentPublicInstance | null) {
  props.planCardRef?.(key, props.index, card as InstanceType<typeof AgentRepairPlanCard> | null);
}
function focusResult(event: MouseEvent, key: string) {
  const transcript = (event.currentTarget as HTMLElement).closest('[data-agent-transcript]');
  const target = transcript?.querySelector<HTMLElement>(`[data-tool-result="${CSS.escape(key)}"]`);
  target?.focus({ preventScroll: true });
  target?.scrollIntoView({ block: 'nearest' });
}
</script>
<template>
  <div v-if="projection.groups.has(index) || projection.business.has(index)" class="tool-presentation">
    <AgentActivityTimeline v-for="group in projection.groups.get(index)" :key="group.key" :group="group"
      :session-key="sessionKey" :developer-mode="developerMode" :keep-latest="true">
      <template #action="{ step }">
        <button v-if="step.resultLink" type="button" class="tool-result-link"
          @click="focusResult($event, step.resultLink.key)">{{ step.resultLink.label }}</button>
      </template>
    </AgentActivityTimeline>
    <div v-for="step in projection.business.get(index)" :key="step.key" :data-tool-result="step.key" tabindex="-1">
      <AgentInspectionCard v-if="step.business === 'inspection'" :data="step.inspection" />
      <AgentRepairPlanCard v-else-if="step.business === 'plan'" :ref="card => registerPlanCard(step.key, card)" :data="resolvePlan ? resolvePlan(step.result?.data) : step.result?.data" :auto-start="step.result?.autoStartRepair === true"
        :developer-mode="developerMode"
        @plan-revised="emit('planRevised', step.result?.data, $event)" @tool-calls-updated="emit('toolCallsUpdated')" />
      <SceneSearchResultCard v-else-if="step.business === 'scenes'" :data="toolPayload(step.result)" :args="(step.args ?? {}) as Record<string, unknown>" status="completed" @open="emit('openScene', $event)" />
      <AgentBatchReceipt v-else-if="step.business === 'receipt'" :data="step.result?.data" />
      <AgentAnnotationReference v-else-if="step.business === 'annotation'" :data="step.result?.data" />
      <!-- request_clarification keeps the existing GoalClarifications action area. -->
    </div>
  </div>
</template>
<style scoped>
.tool-presentation { display:grid; gap:8px; min-width:0; width:100%; }
[data-tool-result] { min-width:0; }
[data-tool-result]:focus-visible { outline:2px solid var(--tb-focus); outline-offset:2px; border-radius:4px; }
.tool-result-link { border:0; padding:0; background:none; color:var(--accent-deep); font:inherit; text-align:left; cursor:pointer; text-decoration:underline; text-underline-offset:2px; }
</style>
