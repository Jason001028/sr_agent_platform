/** Preparation only. Never represents an executing or server-persisted Goal. */
import { defineStore } from 'pinia';
import { ref } from 'vue';
export interface RepairCandidate {
  path: string;
  name: string;
  evidence: string;
  status: '待确认' | '已确认' | '已排除' | '需复核';
}
export const useBatchDraftStore = defineStore('batchDraft', () => {
  const candidates = ref<RepairCandidate[]>([]);
  function add(rows: { path: string; name: string; evidence?: string }[]) {
    for (const row of rows) {
      if (!row.path || candidates.value.some(c => c.path === row.path)) continue;
      candidates.value.push({ ...row, evidence: row.evidence || '用户从 Viewer 手动加入；问题与区域待检测', status: '待确认' });
    }
  }
  return { candidates, add };
});
