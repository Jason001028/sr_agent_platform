import { useBatchGoalStore } from '../stores/batchGoal';
import { useBatchDraftStore } from '../stores/batchDraft';

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
