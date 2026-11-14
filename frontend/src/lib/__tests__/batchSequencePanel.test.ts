// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick, reactive } from 'vue';
import type { App } from 'vue';
import type { ViewerRec } from '../../stores/viewer';
const state = vi.hoisted(() => ({ viewer: {} as any, batch: {} as any, chat: {} as any }));
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../../stores/batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../../stores/chat', () => ({ useChatStore: () => state.chat }));
vi.mock('../api', () => {
  class HttpError extends Error {
    status: number;
    constructor(status: number, message = '') { super(message); this.status = status; }
  }
  return {
    HttpError,
    apiBatchImport: vi.fn(), apiPrepareRepair: vi.fn(),
    apiConfirmRepair: vi.fn(), apiStartRepair: vi.fn(),
  };
});
import { apiBatchImport, apiConfirmRepair, apiPrepareRepair, apiStartRepair, HttpError } from '../api';
import type { RepairPlan } from '../api';
import BatchRepairDraft from '../../components/BatchRepairDraft.vue';
import { viewerRepairSequences } from '../batchSequences';
import { batchDockOpen, openBatchSelection } from '../batchDock';
const imported = (n = 1) => ({ manifest_ref: 'manifest:test', rejected: false, counts: { items: n, errors: 0, warnings: 0 }, diagnostics: [], items: [] });
const plan = (n = 2, over: Partial<RepairPlan> = {}): RepairPlan => ({
  kind: 'repair_plan.v1', contract_version: 'repair_plan.v1', plan_id: 'plan-a',
  version: 1, scope: 'batch', status: 'READY', confirmation_required: true,
  targets: { total: n, excluded: 0, applicable: n, items: [] },
  strategy: { detection: 'existing_diff_pipeline', detection_method: 'absdiff', mask: 'auto_or_existing', execution: 'existing_batch_runtime' },
  mode: 'demo', execution: { allowed: true, mode: 'demo', reason: null },
  manifest_ref: 'manifest:test', title: '批量自动修复',
  ...over,
});
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
  vi.mocked(apiPrepareRepair).mockResolvedValue(plan(2) as never);
  vi.mocked(apiConfirmRepair).mockResolvedValue(plan(2, { status: 'CONFIRMED' }) as never);
  vi.mocked(apiStartRepair).mockResolvedValue({ plan: plan(2, { status: 'STARTED' }), goal: { goal_id: 'goal-a' }, reused: false } as never);
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
    expect(apiPrepareRepair).not.toHaveBeenCalled();
    window.removeEventListener('batch-task-open', handler);
  });
  it('prepares a plan first and only starts after the user confirms', async () => {
    vi.mocked(apiPrepareRepair).mockResolvedValue(plan(1) as never);
    await mount();
    expect(start().textContent).toContain('已选 2 组');
    expect(apiPrepareRepair).not.toHaveBeenCalled();
    expect(root.textContent).not.toContain('/prod/');
    expect([...root.querySelectorAll('.state')].map(e => e.textContent)).toEqual(['可修复', '可修复']);
    expect([...root.querySelectorAll('.ordinal')].map(e => e.textContent)).toEqual(['02', '07']);
    buttons()[1].click(); await flush(); expect(start().disabled).toBe(true);
    buttons()[0].click(); await flush();
    root.querySelector<HTMLInputElement>('input')!.click(); await flush();
    expect(start().textContent).toContain('已选 1 组');
    start().click(); await flush();
    expect(apiPrepareRepair).toHaveBeenCalledTimes(1);
    expect(vi.mocked(apiPrepareRepair).mock.calls[0][0]).toMatchObject({
      session_id: 'session-a', mode: 'demo',
      sources: [{ path: '/prod/A/PAN.tif', name: 'A', evidence: expect.any(String) }],
    });
    expect(start().textContent).toContain('确认并启动 · 1 景');
    start().click(); await flush();
    expect(apiConfirmRepair).toHaveBeenCalledWith('plan-a', { session_id: 'session-a' });
    expect(apiStartRepair).toHaveBeenCalledWith('plan-a', { session_id: 'session-a', plan_version: 1 });
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
  it('never starts a blocked plan and shows the blocking reason', async () => {
    await mount();
    vi.mocked(apiPrepareRepair).mockResolvedValue(plan(2, { status: 'BLOCKED', blocking: [{ message: 'PAN 备份已存在' }] }) as never);
    start().click(); await flush();
    expect(apiConfirmRepair).not.toHaveBeenCalled();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('备份');
  });
  it('keeps a valid plan when demo execution is unavailable and never confirms it', async () => {
    vi.mocked(apiPrepareRepair).mockResolvedValue(plan(2,{execution:{allowed:false,mode:'demo',reason:"BatchGateError: demo batch requires SR_EXECUTOR=slurm, got 'local'"}}) as never);
    await mount();
    start().click();await flush();
    expect(root.querySelector('[data-e2e=batch-plan-summary]')?.textContent).toContain('2 景');
    expect(root.querySelector('[role=status]')?.textContent).toContain('本地执行器');
    expect(root.querySelector('[role=alert]')).toBeNull();
    expect(start().disabled).toBe(true);
    start().click();await flush();
    expect(apiConfirmRepair).not.toHaveBeenCalled();
    expect(apiStartRepair).not.toHaveBeenCalled();
  });
  it('guards double clicks and reuses the prepared plan', async () => {
    await mount();
    start().click(); start().click(); await flush();
    expect(apiPrepareRepair).toHaveBeenCalledTimes(1);
    expect(apiStartRepair).not.toHaveBeenCalled();
    start().click(); await flush();
    expect(apiPrepareRepair).toHaveBeenCalledTimes(1);
    expect(apiConfirmRepair).toHaveBeenCalledTimes(1);
    expect(apiStartRepair).toHaveBeenCalledTimes(1);
  });
  it('re-prepares after a 409 stale-plan response', async () => {
    await mount();
    start().click(); await flush();
    expect(start().textContent).toContain('确认并启动');
    vi.mocked(apiConfirmRepair).mockRejectedValueOnce(new HttpError(409, 'stale'));
    start().click(); await flush();
    expect(apiPrepareRepair).toHaveBeenCalledTimes(2);
    expect(apiStartRepair).not.toHaveBeenCalled();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('计划已失效');
    expect(start().textContent).toContain('确认并启动');
  });
  it('detection method change invalidates a prepared plan before start', async () => {
    await mount();
    start().click(); await flush();
    expect(start().textContent).toContain('确认并启动');
    const detection = root.querySelectorAll<HTMLSelectElement>('select')[1]!;
    detection.value = 'ssim';
    detection.dispatchEvent(new Event('change'));
    await flush();
    expect(apiConfirmRepair).not.toHaveBeenCalled();
    expect(start().textContent).toContain('准备修复计划');
  });
  it('discards the prepared plan when the session changes', async () => {
    await mount();
    let resolve!: (value: ReturnType<typeof plan>) => void;
    vi.mocked(apiPrepareRepair).mockImplementationOnce(() => new Promise<RepairPlan>(r => { resolve = r; }));
    start().click(); await flush(); state.chat.sessionId = 'session-b';
    resolve(plan(2)); await flush();
    expect(apiConfirmRepair).not.toHaveBeenCalled();
    expect(root.querySelector('[role=alert]')?.textContent).toContain('会话已变化');
  });
});
