import { computed, ref, watch } from 'vue';
import type { AgentActivityProjection } from './agentActivity';
import { repairPlanId } from './repairPlan';

export type AgentTaskView = 'standalone' | 'card';
const key = 'sr.agent.task-view.v1';

function loadTaskView(): AgentTaskView {
  try { return localStorage.getItem(key) === 'standalone' ? 'standalone' : 'card'; }
  catch { return 'card'; }
}

export const agentTaskView = ref<AgentTaskView>(loadTaskView());
watch(agentTaskView, mode => {
  try { localStorage.setItem(key, mode); } catch { /* The current view remains available. */ }
}, { flush: 'sync' });

export function useAgentTaskView(
  projection: () => AgentActivityProjection,
  session: () => string | null,
  sending: () => boolean,
) {
  const plans = computed(() => {
    const result = new Map<string, unknown>();
    for (const steps of projection().business.values()) {
      for (const step of steps) {
        if (step.business !== 'plan') continue;
        const data = step.result?.data;
        const id = repairPlanId(data);
        if (id) result.set(id, data);
      }
    }
    return result;
  });
  const selectedId = ref<string>();
  const revisions = ref(new Map<string, unknown>());
  const latestId = computed(() => Array.from(plans.value.keys()).at(-1));
  watch(session, () => {
    selectedId.value = undefined;
    revisions.value.clear();
  });
  watch(latestId, id => {
    if (id && sending()) selectedId.value = id;
  });
  watch(agentTaskView, mode => {
    if (mode === 'standalone' && !selectedId.value) selectedId.value = latestId.value;
  });
  const task = computed(() => {
    const id = selectedId.value;
    if (agentTaskView.value !== 'standalone' || !id || !plans.value.has(id)) return null;
    return { id, data: resolvePlan(plans.value.get(id)) };
  });
  function resolvePlan(data: unknown) {
    return revisions.value.get(repairPlanId(data) ?? '') ?? data;
  }
  function revisePlan(source: unknown, replacement: unknown) {
    const id = repairPlanId(source);
    if (id) revisions.value.set(id, replacement);
  }
  function reviseTask(replacement: unknown) {
    if (selectedId.value) revisions.value.set(selectedId.value, replacement);
  }
  function closeTask() {
    selectedId.value = undefined;
  }
  return { task, closeTask, resolvePlan, revisePlan, reviseTask };
}
