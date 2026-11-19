/** Pure presentation projection. No execution, timers, persistence or reasoning inference. */
import type { ChatMsg } from '../stores/chat';
import { toolActionLabel } from './chatActivity';
import { repairPlanId } from './repairPlan';

export type ActivityState = 'running' | 'success' | 'failed' | 'interrupted';
export type BusinessKind = 'inspection' | 'plan' | 'scenes' | 'receipt' | 'annotation' | 'confirmation';
export interface ActivityStep {
  key: string; name: string; label: string; args: unknown; callId?: string;
  callIndex: number; resultIndex?: number; result?: ChatMsg;
  state: ActivityState; summary: string; business: BusinessKind | null;
  inspection?: Record<string, unknown>;
}
export interface ActivityGroup { key: string; steps: ActivityStep[] }
export interface AgentActivityProjection {
  steps: ActivityStep[];
  groups: Map<number, ActivityGroup[]>;
  business: Map<number, ActivityStep[]>;
}
export function toolPayload(result?: ChatMsg): Record<string, unknown> | null {
  const raw = result?.data;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const object = raw as Record<string, unknown>;
  const nested = object.data;
  return typeof object.ok === 'boolean' && nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown> : object;
}
export function toolOutcome(result?: ChatMsg): boolean | undefined {
  const envelope = result?.data as { ok?: boolean } | null;
  if (result?.ok === false || envelope?.ok === false) return false;
  if (result?.ok === true || envelope?.ok === true) return true;
  return undefined;
}
function businessKind(step: ActivityStep): BusinessKind | null {
  if (step.state !== 'success') return null;
  const data = toolPayload(step.result);
  if (data?.kind === 'repair_inspection.v1') return 'inspection';
  if (repairPlanId(step.result?.data)) return 'plan';
  if (step.name === 'search_scenes' && data && (Array.isArray(data.results) || typeof data.count === 'number')) return 'scenes';
  if (data?.kind === 'batch_repair_draft.v1' && Array.isArray(data.candidates) && data.candidates.length) return 'receipt';
  if (step.name === 'present_spatial_annotations') return 'annotation';
  if (step.name === 'request_clarification') return 'confirmation';
  return null;
}
function stepSummary(step: ActivityStep): string {
  const data = toolPayload(step.result);
  if (step.state === 'running') return '正在执行';
  if (step.state === 'interrupted') return '结果未确认';
  if (step.state === 'failed') {
    const detail = step.result?.detail;
    const readable = detail && !/^[\s]*[{[]/.test(detail)
      ? detail.replace(/\s+/g,' ').trim() : '';
    if (step.name === 'read_tool_result') {
      return readable
        ? `结果读取失败：${readable}；任务状态以业务卡片为准`
        : '结果读取失败，任务状态以业务卡片为准';
    }
    return readable || '工具调用失败';
  }
  if (step.name === 'read_tool_result') return '已读取保存结果';
  if (step.name === 'get_current_image_info') return '已读取当前影像信息';
  if (step.name === 'prepare_repair') return '已准备修复计划';
  // 质检分页：total 是整批景数，不是本页条数。每页只报“已到第几景”，
  // 末页不带省略号；27 景按 5 景一页即显示 5/27 景…
  if (data?.kind === 'repair_inspection.v1' && Array.isArray(data.items)
      && typeof data.total === 'number') {
    const shown = data.items.length;
    const end = typeof data.cursor === 'number' ? data.cursor + shown
      : typeof data.next_cursor === 'number' ? data.next_cursor : data.total;
    return `${end}/${data.total} 景${data.has_more ? '…' : ''}`;
  }
  if (typeof data?.total === 'number') return `已读取 ${data.total} 项`;
  return '执行完成';
}

function collectInspectionReports(segment: ActivityStep[]) {
  const batches = new Map<string, { rows: Map<number, unknown>; final: boolean; report?: ActivityStep }>();
  for (const step of segment) {
    if (step.business !== 'inspection') continue;
    step.business = null;
    const page = toolPayload(step.result)!;
    const args = step.args as { scope?: string; cursor?: number } | null;
    const { total, items } = page;
    if (typeof total !== 'number' || !Number.isSafeInteger(total) || total < 0 || !Array.isArray(items)) continue;
    const cursor = page.cursor ?? args?.cursor ?? (typeof page.next_cursor === 'number'
      ? page.next_cursor - items.length : total - items.length);
    if (typeof cursor !== 'number' || !Number.isSafeInteger(cursor) || cursor < 0 || cursor + items.length > total) continue;
    const key = JSON.stringify([page.scope ?? args?.scope ?? 'workspace', page.detection_method, page.tone_weight, total]);
    const batch = batches.get(key) ?? { rows: new Map<number, unknown>(), final: false };
    batches.set(key, batch);
    items.forEach((row, index) => batch.rows.set(cursor + index, row));
    batch.final ||= page.has_more === false && cursor + items.length === total;
    if (!batch.final || batch.rows.size !== total || batch.report) continue;
    step.business = 'inspection';
    step.inspection = { ...page, items: Array.from({ length: total }, (_, index) => batch.rows.get(index)) };
    batch.report = step;
  }
}

export function projectAgentActivity(messages: ChatMsg[], sending: boolean): AgentActivityProjection {
  const steps: ActivityStep[] = [];
  const segments: ActivityStep[][] = [];
  let turn = -1, current: ActivityStep[] = [];
  let legacyOrdinal = 0;
  let byId = new Map<string, ActivityStep>();
  let seenResults = new Set<string>();
  let seenDeclarations = new Set<string>();
  const finishTurn = () => { if (current.length) segments.push(current); current = []; };
  messages.forEach((message, index) => {
    if (message.role === 'user') {
      finishTurn(); turn++; legacyOrdinal = 0; byId = new Map(); seenResults = new Set(); seenDeclarations = new Set(); return;
    }
    if (message.role === 'assistant') {
      (message.toolCalls ?? []).forEach((call, ordinal) => {
        const identity = call.id ? `id:${call.id}` : `legacy:${message.seq ?? index}:${ordinal}`;
        if (seenDeclarations.has(identity)) return;
        seenDeclarations.add(identity);
        const existing = call.id ? byId.get(call.id) : undefined;
        if (existing) { existing.args = call.args; existing.name = call.name; existing.label = toolActionLabel(call.name); return; }
        const stableIdentity = call.id ? identity : `legacy:${legacyOrdinal++}`;
        const step: ActivityStep = {key:`turn:${turn}:${stableIdentity}`,name:call.name,label:toolActionLabel(call.name),
          args:call.args,callId:call.id,callIndex:index,state:'interrupted',summary:'',business:null};
        current.push(step); steps.push(step);
        if (call.id) byId.set(call.id,step);
      });
    }
    if (message.role !== 'tool') return;
    const resultIdentity = message.toolCallId ? `id:${message.toolCallId}` : message.seq != null ? `seq:${message.seq}` : '';
    if (resultIdentity && seenResults.has(resultIdentity)) return;
    if (resultIdentity) seenResults.add(resultIdentity);
    let step = message.toolCallId ? byId.get(message.toolCallId) : undefined;
    // A result with a different explicit ID never attaches to another ID.
    if (!step) step = current.find(s => !s.result && s.name === message.toolName && (!message.toolCallId || !s.callId));
    if (!step) {
      const name = message.toolName ?? 'unknown';
      step = {key:`turn:${turn}:result:${resultIdentity || index}`,name,label:toolActionLabel(name),args:null,
        callId:message.toolCallId,callIndex:index,state:'interrupted',summary:'',business:null};
      current.push(step); steps.push(step);
    }
    step.result = message; step.resultIndex = index;
    if (message.toolCallId) byId.set(message.toolCallId,step);
  });
  finishTurn();
  const latestUser = messages.map(m => m.role).lastIndexOf('user');
  const groups = new Map<number, ActivityGroup[]>(), business = new Map<number, ActivityStep[]>();
  const plans = new Map<string, { anchor: ActivityStep; latest: ActivityStep }>();
  for (const step of steps) {
    const outcome = toolOutcome(step.result);
    // Only unresolved calls in the active assistant phase are still running.
    const endedAfterCall = messages.slice(step.callIndex + 1).some(m => m.role === 'user' || m.isError
      || m.role === 'assistant' && !!m.content && !m.streaming);
    step.state = outcome === false ? 'failed' : outcome === true ? 'success'
      : !step.result && sending && step.callIndex > latestUser && !endedAfterCall ? 'running' : 'interrupted';
    step.summary = stepSummary(step); step.business = businessKind(step);
    if (step.business === 'plan') {
      const id = repairPlanId(step.result?.data)!;
      plans.set(id, { anchor: plans.get(id)?.anchor ?? step, latest: step });
    }
  }
  for (const segment of segments) {
    collectInspectionReports(segment);
    let pending: ActivityGroup | null = null, anchor = -1;
    const flush = () => { if (pending) groups.set(anchor,[...(groups.get(anchor) ?? []),pending]); pending = null; };
    let previousIndex = -1;
    for (const step of segment) {
      const proseBoundary = messages.slice(previousIndex + 1,step.callIndex).some(m => m.role === 'assistant' && !!m.content)
        || previousIndex >= 0 && previousIndex !== step.callIndex && messages[step.callIndex].role === 'assistant' && !!messages[step.callIndex].content;
      if (proseBoundary) flush();
      previousIndex = step.callIndex;
      if (step.business) {
        flush();
        const plan = step.business === 'plan' ? plans.get(repairPlanId(step.result?.data)!) : undefined;
        if (!plan || plan.anchor === step) {
          const at = step.resultIndex!;
          const presented = plan ? { ...plan.latest, key: step.key } : step;
          business.set(at,[...(business.get(at) ?? []),presented]);
        }
        continue;
      }
      if (!pending) { anchor = step.callIndex; pending = {key:step.key,steps:[]}; }
      pending.steps.push(step);
    }
    flush();
  }
  return {steps,groups,business};
}
