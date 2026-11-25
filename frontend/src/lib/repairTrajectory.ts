import type { BatchEvent } from './api';
import type { ActivityStep, ActivityState } from './agentActivity';
import { toolActionLabel } from './chatActivity';

type Outcome = [ActivityState, string, string?];
export type RepairToolStep = ActivityStep & { itemId: string };

/** The blocked/failed reason lives on the item transition, not the step text.
 *  Local Agent events carry the post-transition Todo; older events may expose
 *  the same message under one of the flat payload keys. */
function itemReason(event: BatchEvent): string {
  const { payload } = event;
  const todo = payload.todo;
  const values = [
    todo && typeof todo === 'object' && !Array.isArray(todo) ? (todo as Record<string, unknown>).error : undefined,
    payload.error, payload.reason, payload.blocked_reason, payload.message,
  ];
  const text = values.find((value): value is string => typeof value === 'string' && value.trim() !== '');
  return text ? text.replace(/\s+/g, ' ').trim() : '';
}

function actionOutcome(action: string, event: BatchEvent): Outcome | undefined {
  const { event_type: type, payload } = event;
  if (type === 'item_state_changed') {
    const reason = itemReason(event) || undefined;
    switch (payload.to) {
      case 'FAILED': return ['failed', '执行失败', reason];
      case 'BLOCKED': return ['failed', '执行受阻', reason];
      case 'RETRY_WAIT': return ['failed', '本次尝试失败，等待重试', reason];
      case 'NO_ISSUE':
        if (action === 'detect_repair_item') return ['success', '未检出候选区域', reason];
        break;
    }
  }
  switch (action) {
    case 'detect_repair_item':
      if (type === 'repair_detection_ready') return ['success', typeof payload.candidate_count === 'number'
        ? `检出 ${payload.candidate_count} 个候选区域` : '候选区域已就绪'];
      if (type === 'quality_review_ready' && payload.kind === 'no-candidate') return ['success', '待人工核对无候选结果'];
      break;
    case 'build_repair_mask':
      if (type === 'mask_ready') return ['success', typeof payload.candidate_count === 'number'
        ? `掩码已生成 · ${payload.candidate_count} 个候选区域` : '掩码已生成'];
      break;
    case 'submit_repair_item':
      if (type === 'sr_receipt_recorded') {
        const status = typeof payload.status === 'string' ? payload.status.toUpperCase() : '';
        const queue = status === 'QUEUED' || status === 'PENDING' ? '排队中' : status === 'RUNNING' ? '执行中' : '';
        const receipt = payload.recovered === true ? '已恢复作业回执' : '已收到作业回执';
        return ['success', [receipt, jobLabel(payload.job_id), queue].filter(Boolean).join(' · ')];
      }
      break;
    case 'verify_repair_output':
      if (type === 'repair_output_verified') return ['success', '产物校验通过'];
      if (type === 'item_verified' && typeof payload.ok === 'boolean') {
        return payload.ok ? ['success', '产物校验通过'] : ['failed', '产物校验未通过'];
      }
      break;
    case 'assess_repair_effect':
      if (type === 'quality_review_ready') return ['success', typeof payload.regions === 'number'
        ? `回检已完成 · ${payload.regions} 个区域待验收` : '回检已完成，待人工验收'];
      break;
  }
}

function jobLabel(job: unknown): string {
  return typeof job === 'number' || typeof job === 'string' && /^[\w.-]+$/.test(job)
    ? `Job ${job}` : '';
}

export function projectRepairToolCalls(events: BatchEvent[], settled: boolean, ordinals: ReadonlyMap<string, number>): RepairToolStep[] {
  const steps: RepairToolStep[] = [];
  const open = new Map<string, RepairToolStep>();
  for (const event of events) {
    const { event_type: type, payload } = event;
    const id = typeof payload.item_id === 'string' ? payload.item_id : event.step_id ?? '';
    const action = typeof payload.action === 'string' ? payload.action : '';
    if (type === 'agent_action_selected' && id && action) {
      const previous = open.get(id);
      if (previous) { previous.state = 'interrupted'; previous.summary = '结果未确认'; }
      const ordinal = event.step_ordinal ?? ordinals.get(id);
      const step: RepairToolStep = {
        key: `${event.goal_id}:${event.seq}`, name: action,
        itemId: id,
        label: [toolActionLabel(action), ordinal && `第 ${ordinal} 景`].filter(Boolean).join(' · '),
        callId: typeof payload.call_id === 'string' ? payload.call_id : undefined,
        args: { item_id: id, ...(payload.reason ? { reason: payload.reason } : {}) },
        callIndex: event.seq, state: 'running', summary: '正在执行', business: null,
      };
      steps.push(step);
      open.set(id, step);
    } else {
      const step = open.get(id);
      const outcome = step && actionOutcome(step.name, event);
      if (step && outcome) {
        const [state, summary, summaryTitle] = outcome;
        step.state = state;
        step.summary = summaryTitle ? `${summary} · ${summaryTitle}` : summary;
        step.summaryTitle = summaryTitle;
        step.result = { seq: null, role: 'tool', content: '', toolName: step.name, toolCallId: step.callId,
          ok: state === 'success', data: payload };
        open.delete(id);
      }
    }
  }
  if (settled) for (const step of open.values()) {
    step.state = 'interrupted';
    step.summary = '结果未确认';
  }
  return steps;
}
