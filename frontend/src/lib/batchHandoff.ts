import { useBatchGoalStore } from '../stores/batchGoal';
import { useBatchDraftStore } from '../stores/batchDraft';

/** One-click handoff from the Viewer tool panel into the Agent repair flow.
 *
 *  It only prepares work: the Agent must call ``prepare_repair`` and let the
 *  real user confirm the plan card.  ``start_repair`` remains server-gated by
 *  that confirmation, so this prompt cannot auto-approve anything. */
export const AGENT_REPAIR_PROMPT =
  '请对当前 Viewer 工作区发起自动修复：先调用 prepare_repair'
  + '（scope=workspace；当前工作区为空时改用 current）准备修复计划并展示计划卡，'
  + '不要直接提交 SR。请说明目标景数、检测方法与掩码策略，'
  + '我会在计划卡上确认后启动。';

export function requestAgentRepair(): void {
  window.dispatchEvent(new CustomEvent('batch-agent-handoff', { detail: {
    text: AGENT_REPAIR_PROMPT,
    autoSend: true,
  } }));
}

/** Transfer facts into the existing Agent composer. Sending remains explicit. */
export function handoffBatch(kind: 'draft' | 'report'): void {
  const batch = useBatchGoalStore();
  const draft = useBatchDraftStore();
  const evidence = [...new Set(draft.candidates.map(c => c.evidence))];
  const evidenceIndex = new Map(evidence.map((text, index) => [text, index]));
  const context = kind === 'draft'
    ? { source: '当前窗口工作清单（未冻结）', total: draft.candidates.length,
        title: draft.title, mode: draft.mode, strategy: draft.strategy,
        columns: ['name', 'path', 'status', 'evidence_index', 'validationError'], evidence_index_base: 0, evidence,
        // All identities and validation issues remain present. Only repeated prose/keys are factored out.
        candidates: draft.candidates.map(c => [c.name, c.path, c.status, evidenceIndex.get(c.evidence), c.validationError ?? null]) }
    : { source: '权威 batch-snapshot', snapshot: batch.snapshot };
  const request = kind === 'draft' ? '请整理待修复清单，解释身份疑点并规划复核。' : '请总结批次结果，解释异常并建议后续复核。';
  window.dispatchEvent(new CustomEvent('batch-agent-handoff', { detail: {
    text: `${request}\n先用 3–6 点给出范围、主要问题和下一步；按问题归类，避免逐景复述已提供的清单。证据不足时说明未知，不重复查询已有事实。\n仅提供解释与建议，不启动或代替 Batch Goal Worker，不提交 SR。\n批次 ID：${batch.goalId ?? '尚未创建'}\n${JSON.stringify(context)}`,
  } }));
}
