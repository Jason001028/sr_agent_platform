import { useBatchGoalStore } from '../stores/batchGoal';
import { useBatchDraftStore } from '../stores/batchDraft';

/** Transfer facts into the existing Agent composer. Sending remains explicit. */
export function handoffBatch(kind: 'draft' | 'report'): void {
  const batch = useBatchGoalStore();
  const context = kind === 'draft'
    ? { source: '当前窗口工作清单（未冻结）', candidates: useBatchDraftStore().candidates }
    : { source: '权威 batch-snapshot', snapshot: batch.snapshot };
  const request = kind === 'draft' ? '请整理待修复清单，解释身份疑点并规划复核。' : '请总结批次结果，解释异常并建议后续复核。';
  window.dispatchEvent(new CustomEvent('batch-agent-handoff', { detail: {
    text: `${request}\n仅提供解释与建议，不启动或代替 Batch Goal Worker，不提交 SR。\n批次 ID：${batch.goalId ?? '尚未创建'}\n${JSON.stringify(context)}`,
  } }));
}
