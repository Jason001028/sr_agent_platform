// @vitest-environment jsdom
import { beforeEach, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useBatchDraftStore } from '../batchDraft';
beforeEach(() => setActivePinia(createPinia()));
it('keeps exact identities, deduplicates paths and requires explicit confirmation', () => {
  const draft = useBatchDraftStore();
  draft.add([{ path: '/one/PAN.tif', name: 'PAN' }, { path: '/two/PAN.tif', name: 'PAN' }]);
  draft.candidates[0]!.status = '已排除';
  draft.add([{ path: '/one/PAN.tif', name: 'same image' }]);
  expect(draft.candidates).toHaveLength(2);
  expect(draft.candidates[0]!.status).toBe('已排除');
  expect(draft.candidates[1]!.status).toBe('待确认');
  expect(draft.candidates[1]!.evidence).toContain('待检测');
});
