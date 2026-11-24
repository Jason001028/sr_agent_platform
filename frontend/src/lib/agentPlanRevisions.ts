import { ref, watch } from 'vue';
import { repairPlanId } from './repairPlan';

export function useAgentPlanRevisions(session: () => string | null) {
  const revisions = ref(new Map<string, unknown>());
  watch(session, () => revisions.value.clear());

  function resolvePlan(data: unknown) {
    return revisions.value.get(repairPlanId(data) ?? '') ?? data;
  }

  function revisePlan(source: unknown, replacement: unknown) {
    const id = repairPlanId(source);
    if (id) revisions.value.set(id, replacement);
  }

  return { resolvePlan, revisePlan };
}
