import type { RepairPlan } from './api';
import type { ChatMsg } from '../stores/chat';

/** Extract the plan object from a tool result's data (live or model-view). */
export function repairPlanData(data: unknown): RepairPlan | null {
  const raw = data as { data?: unknown } | undefined;
  const value = (raw?.data ?? data) as RepairPlan | undefined;
  return value && value.kind === 'repair_plan.v1' && value.plan_id ? value : null;
}

export function repairPlanId(data: unknown): string | null {
  return repairPlanData(data)?.plan_id ?? null;
}

export const isCompactRepairTool = (name?: string | null) => name === 'prepare_repair' || name === 'read_tool_result';
export function compactRepairTools(message: ChatMsg, messages: ChatMsg[] = [], index = -1) {
  const later = messages.slice(index + 1);
  const nextUser = later.findIndex(row => row.role === 'user');
  const rest = nextUser < 0 ? later : later.slice(0, nextUser);
  return (message.toolCalls ?? []).filter((tool, i, all) => isCompactRepairTool(tool.name)
    && !all.slice(i + 1).some(other => other.name === tool.name)
    && !rest.some(row => row.toolCalls?.some(other => other.name === tool.name)));
}
/** Scope narrative folding to a turn with a real structured plan, never parse prose. */
export function hasRepairPlanInTurn(messages: ChatMsg[], index: number): boolean {
  let start = index, end = index + 1;
  while (start > 0 && messages[start - 1].role !== 'user') start--;
  while (end < messages.length && messages[end].role !== 'user') end++;
  return messages.slice(start, end).some(m => m.role === 'tool' && !!repairPlanId(m.data));
}
