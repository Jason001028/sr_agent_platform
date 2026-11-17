// @vitest-environment jsdom
import { beforeEach, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useBatchDraftStore } from '../batchDraft';
beforeEach(() => { localStorage.clear(); setActivePinia(createPinia()); });
it('keeps exact identities, deduplicates paths and requires explicit confirmation', () => {
  const draft = useBatchDraftStore();
  expect(draft.mode).toBe('production');
  draft.add([{ path: '/one/PAN.tif', name: 'PAN' }, { path: '/two/PAN.tif', name: 'PAN' }]);
  draft.candidates[0]!.status = '已排除';
  draft.add([{ path: '/one/PAN.tif', name: 'same image' }]);
  expect(draft.candidates).toHaveLength(2);
  expect(draft.candidates[0]!.status).toBe('已排除');
  expect(draft.candidates[1]!.status).toBe('待确认');
  expect(draft.candidates[1]!.evidence).toContain('待检测');
});
it('does not revive a legacy Demo mode when recovering an unsubmitted draft', () => {
  localStorage.setItem('sr.batch.draft.v1',JSON.stringify({version:1,mode:'demo',candidates:[{path:'/s/PAN.tif',name:'PAN',status:'待确认'}]}));
  setActivePinia(createPinia());
  const draft=useBatchDraftStore();
  expect(draft.mode).toBe('production');
  expect(draft.candidates).toHaveLength(1);
});
