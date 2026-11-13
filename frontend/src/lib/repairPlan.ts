import type { RepairPlan } from './api';

/** Extract the plan object from a tool result's data (live or model-view). */
export function repairPlanData(data: unknown): RepairPlan | null {
  const raw = data as { data?: unknown } | undefined;
  const value = (raw?.data ?? data) as RepairPlan | undefined;
  return value && value.kind === 'repair_plan.v1' && value.plan_id ? value : null;
}

export function repairPlanId(data: unknown): string | null {
  return repairPlanData(data)?.plan_id ?? null;
}
