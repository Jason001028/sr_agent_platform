// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { expect, it, vi } from 'vitest';
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => ({
  selectedIds: [], recs: [], recsOrdered: [], sceneOrdinalOf: () => 0,
  activeRec: { name: 'PAN', lqPath: '/trusted/scene' },
}) }));
vi.mock('../../stores/scenes', () => ({ useScenesStore: () => ({}) }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => ({}) }));
vi.mock('../../stores/chat', () => ({ useChatStore: () => ({ sessionId: null }) }));
vi.mock('../../lib/api', () => ({
  loadSrConfig: () => ({}),
  apiResolveScene: vi.fn().mockResolvedValue({ row: { name: 'resolved scene' }, resolved: { sr_capable: true, input: '/trusted/scene/PAN.tif' } }),
  apiBatchImport: vi.fn(), apiCreateBatch: vi.fn(),
}));
import BatchRepairDraft from '../../components/BatchRepairDraft.vue';
import { useBatchDraftStore } from '../../stores/batchDraft';
import { apiResolveScene, apiBatchImport, apiCreateBatch } from '../../lib/api';
it('preserves previous drafts and requires a numbered production sequence before starting', async () => {
  localStorage.clear(); vi.clearAllMocks();
  const pinia = createPinia(); setActivePinia(pinia);
  const draft = useBatchDraftStore();
  draft.add([{ name: 'previous draft', path: '/trusted/scene' }]);
  const root = document.createElement('div'); document.body.append(root);
  const app = createApp(BatchRepairDraft); app.use(pinia); app.mount(root);
  try {
    await nextTick();
    expect(root.querySelector<HTMLButtonElement>('.primary')?.disabled).toBe(true);
    expect(root.textContent).toContain('暂无已关联生产序列');
    expect(root.textContent).not.toContain('加入待修复');
    expect(apiResolveScene).not.toHaveBeenCalled();
    expect(apiBatchImport).not.toHaveBeenCalled();
    expect(apiCreateBatch).not.toHaveBeenCalled();
    expect(draft.candidates[0]!.path).toBe('/trusted/scene');
    expect(draft.candidates[0]!.status).toBe('待确认');
  } finally { app.unmount(); root.remove(); }
});
