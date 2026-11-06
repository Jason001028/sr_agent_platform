// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
import type { ViewerRec } from '../../stores/viewer';
const state = vi.hoisted(() => ({ viewer: {} as any, batch: {} as any, chat: {} as any }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/chat', () => ({ useChatStore: () => state.chat }));
vi.mock('../api', () => ({ apiBatchImport: vi.fn(), apiCreateBatch: vi.fn() }));
import { apiBatchImport, apiCreateBatch } from '../api';
import BatchRepairDraft from '../../components/BatchRepairDraft.vue';
import { viewerRepairSequences } from '../batchSequences';
import { batchDockOpen, openBatchSelection } from '../batchDock';
const imported = (n = 1) => ({ manifest_ref: 'manifest:test', rejected: false, counts: { items: n, errors: 0, warnings: 0 }, diagnostics: [], items: [] });
const rec = (id: number, dir: string | null, stageKind = 'nosr') => ({ id, sceneDir: dir, stageKind, name: 'display.jpg' } as ViewerRec);
let app: App | undefined, root: HTMLDivElement;
async function flush() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick(); } }
async function mount() { root = document.createElement('div'); document.body.append(root); app = createApp(BatchRepairDraft); app.mount(root); await flush(); }
function buttons() { return [...root.querySelectorAll('button')]; }
const start = () => root.querySelector<HTMLButtonElement>('.primary')!;
beforeEach(() => {
  vi.resetAllMocks();
  state.viewer = reactive({ recsOrdered: [rec(1, '/prod/A'), rec(2, '/prod/A', 'product'), rec(3, '/prod/B')], sceneOrdinalOf: (r: ViewerRec) => r.sceneDir === '/prod/A' ? 7 : 2 });
  state.chat = reactive({ sessionId: 'session-a' });
  state.batch = { selectGoal: vi.fn().mockResolvedValue(undefined), setFollow: vi.fn() };
  vi.mocked(apiBatchImport).mockImplementation(async (_t, _m, sources) => imported(sources?.length));
  vi.mocked(apiCreateBatch).mockResolvedValue({ goal_id: 'goal-a' } as any);
});
afterEach(() => { app?.unmount(); app = undefined; root?.remove(); vi.unstubAllGlobals(); });
describe('compact sequence selection', () => {
  it('uses sticky Viewer ordinals, groups duplicate cards, and includes unopened PAN bodies', () => {
    const groups = viewerRepairSequences([...state.viewer.recsOrdered, rec(4, null)], state.viewer.sceneOrdinalOf);
    expect(groups.map(g => [g.ordinal, g.path])).toEqual([[2, '/prod/B/PAN.tif'], [7, '/prod/A/PAN.tif']]);
  });
  it('existing entry always opens selection without submitting', () => {
    const handler = vi.fn(); window.addEventListener('batch-task-open', handler);
    batchDockOpen.value = false; openBatchSelection(); openBatchSelection();
    expect(batchDockOpen.value).toBe(true);
    expect(handler.mock.calls[0][0].detail.section).toBe('draft');
    expect(apiCreateBatch).not.toHaveBeenCalled();
    window.removeEventListener('batch-task-open', handler);
  });
  it('defaults to all eligible groups; supports none, all, and individual selection', async () => {
    await mount();
    expect(start().textContent).toContain('已选 2 组');
    expect(apiCreateBatch).not.toHaveBeenCalled();
    expect(root.textContent).not.toContain('/prod/');
    expect(root.querySelector('.count')).toBeNull();
    expect([...root.querySelectorAll('.state')].map(e => e.textContent)).toEqual(['可修复', '可修复']);
    expect([...root.querySelectorAll('.ordinal')].map(e => e.textContent)).toEqual(['02', '07']);
    buttons()[1].click(); await flush(); expect(start().disabled).toBe(true);
    buttons()[0].click(); await flush();
    root.querySelector<HTMLInputElement>('input')!.click(); await flush();
    expect(start().textContent).toContain('已选 1 组');
    start().click(); await flush();
    expect(vi.mocked(apiBatchImport).mock.calls.at(-1)).toEqual(['', 'production', [{ path: '/prod/A/PAN.tif', name: 'A', evidence: expect.any(String) }], 'automatic']);
    expect(apiCreateBatch).toHaveBeenCalledTimes(1);
    expect(state.batch.selectGoal).toHaveBeenCalledWith('goal-a');
    expect(state.batch.setFollow).toHaveBeenCalledWith(false);
  });
  it('disables missing NOSR groups and exposes their reason without paths', async () => {
    vi.mocked(apiBatchImport).mockResolvedValue({ ...imported(0), rejected: true, diagnostics: [{ line: 1, severity: 'error', message: '缺 /prod/A/A_NOSR.tif' }] });
    await mount();
    expect(start().disabled).toBe(true);
    expect(root.querySelector('.state')?.textContent).toBe('异常');
    expect(root.querySelector('.state')?.getAttribute('title')).toContain('NOSR');
    expect(root.querySelector('.state')?.getAttribute('aria-label')).toContain('缺');
    expect(root.textContent).not.toContain('/prod/');
    expect([...root.querySelectorAll<HTMLInputElement>('input')].every(e => e.disabled)).toBe(true);
  });
  it('never creates from a rejected final manifest', async () => {
    await mount();
    vi.mocked(apiBatchImport).mockResolvedValue({ ...imported(0), rejected: true, diagnostics: [{ line: 1, severity: 'error', message: 'PAN 备份已存在' }] });
    start().click(); await flush();
    expect(apiCreateBatch).not.toHaveBeenCalled();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('备份');
  });
  it.each(['http', 'no-crypto'])('starts without randomUUID (%s), guards double clicks and keeps retry identity', async environment => {
    const getRandomValues = vi.fn((bytes: Uint32Array) => { bytes.fill(7); return bytes; });
    vi.stubGlobal('crypto', environment === 'http' ? { getRandomValues } : undefined);
    await mount();
    vi.mocked(apiCreateBatch).mockRejectedValueOnce(new Error('连接中断'));
    start().click(); start().click(); await flush();
    expect(apiCreateBatch).toHaveBeenCalledTimes(1);
    const first = vi.mocked(apiCreateBatch).mock.calls[0][0];
    expect(first.request_key).toMatch(/^batch-[a-z0-9]+-[a-z0-9]+-[a-f0-9]{32}$/);
    if (environment === 'http') expect(getRandomValues).toHaveBeenCalledTimes(1);
    const imports = vi.mocked(apiBatchImport).mock.calls.length;
    start().click(); await flush();
    expect(apiCreateBatch).toHaveBeenLastCalledWith(first);
    expect(apiBatchImport).toHaveBeenCalledTimes(imports);
    expect(state.batch.selectGoal).toHaveBeenCalledWith('goal-a');
  });
  it('discards final validation when the session changes', async () => {
    await mount();
    let resolve!: (value: ReturnType<typeof imported>) => void;
    vi.mocked(apiBatchImport).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    start().click(); await flush(); state.chat.sessionId = 'session-b';
    resolve(imported(2)); await flush();
    expect(apiCreateBatch).not.toHaveBeenCalled();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('会话已变化');
  });
});
