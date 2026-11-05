// @vitest-environment jsdom
import { createApp, nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import { expect, it, vi } from 'vitest';
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => ({
  selectedIds: [], recs: [], activeRec: { name: 'PAN', lqPath: '/trusted/scene' },
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
import { apiResolveScene } from '../../lib/api';
it('adds a Viewer reference freely and defers resolution to unified freeze', async () => {
  localStorage.clear(); vi.clearAllMocks();
  const pinia = createPinia(); setActivePinia(pinia);
  const root = document.createElement('div'); document.body.append(root);
  const app = createApp(BatchRepairDraft); app.use(pinia); app.mount(root);
  try {
    const button = [...root.querySelectorAll('button')].find(b => b.textContent?.includes('加入待修复'))!;
    button.click();
    await vi.waitFor(() => expect(useBatchDraftStore().candidates).toHaveLength(1));
    await nextTick();
    expect(apiResolveScene).not.toHaveBeenCalled();
    expect(useBatchDraftStore().candidates[0]!.path).toBe('/trusted/scene');
    expect(useBatchDraftStore().candidates[0]!.status).toBe('待确认');
  } finally { app.unmount(); root.remove(); }
});
