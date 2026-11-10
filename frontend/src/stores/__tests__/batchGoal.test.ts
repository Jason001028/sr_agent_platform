// @vitest-environment jsdom
/**
 * batchGoal.test.ts — PR F 同步/关联/跟随的 store 级契约
 * ------------------------------------------------------------------
 * 只断言 store 的编排：快照是权威、事件从已消费游标追平、用户操作退出跟随、
 * 409 后重新读快照。网络与 SSE 全部用 mock，不触碰真实后端。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';

vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../lib/imageOpen',async original=>{
  const mod=await original<typeof import('../../lib/imageOpen')>();
  return {...mod,createPreviewAsset:vi.fn(async()=>{throw new mod.ImageOpenError('legacy fixture',404);})};
});
vi.mock('../../lib/api.js', async (original) => ({
  ...await original<typeof import('../../lib/api.js')>(),
  apiBatchGoals: vi.fn(),
  apiBatchSnapshot: vi.fn(),
  apiBatchEvents: vi.fn(),
  apiBatchControl: vi.fn(),
  apiResolveScene: vi.fn(),
  fetchSceneJpg: vi.fn(),
  subscribeGoalEvents: vi.fn(),
}));

import {
  apiBatchControl, apiBatchEvents, apiBatchGoals, apiBatchSnapshot,
  apiResolveScene, fetchSceneJpg, HttpError, subscribeGoalEvents,
} from '../../lib/api';
import { useQueueStore } from '../queue';
import { useImageRefreshStore } from '../imageRefresh';
import { useBatchGoalStore } from '../batchGoal';
import { useViewerStore } from '../viewer';
import type {
  BatchEvent, BatchEventPage, BatchItemSummary, BatchSnapshot, DurableGoal,
  SceneResolveResult,
} from '../../lib/api';
import type { SceneRow } from '../../lib/scene';

function snapshot(overrides: Partial<BatchSnapshot['goal']> = {}): BatchSnapshot {
  return {
    contract_version: 'batch_snapshot.v1',
    goal: {
      goal_id: 'g1', session_id: 's1', title: '批次一', kind: 'batch',
      status: 'RUNNING', phase: 'detecting', mode: 'production',
      version: 4, control_revision: 2, monitoring_done: false,
      monitor_state: null, last_seq: 10, created_at: 1, updated_at: 2,
      finished_at: null, batch_control: {},
      ...overrides,
    },
    counts: {
      total: 3, eligible: 3, detected: 3, detection_pending: 0,
      needs_repair: 1, submitted: 1, verified: 0,
      verified_demo: 0, verified_production: 0, verified_other: 0,
      converged: 0, no_issue: 2, failed: 0, blocked: 0, excluded: 0,
      skipped_runtime: 0, cancelled: 0, capacity_skipped: 0,
      unknown_jobs: 0, detecting: 0, submitting: 0, waiting_sr: 1,
      verifying: 0, in_flight_sr: 1,
    },
    progress: {
      detection: { done: 3, total: 3, applicable: true, known: true },
      submission: { done: 1, total: 1, applicable: true, known: true },
      verification: { done: 0, total: 1, applicable: true, known: true },
    },
    producer: {
      current: null, detecting_count: 0, preparing_count: 0, prepared_count: 3,
    },
    active_jobs: [],
    recent_jobs: [],
    report: { available: false, updated_at: null },
    server_time: 3,
  };
}

function event(seq: number): BatchEvent {
  return {
    goal_id: 'g1', seq, event_type: 'item_state_changed',
    payload: { from: 'DETECTING', to: 'MASK_READY' }, created_at: Date.now() / 1000,
  };
}

function page(rows: BatchEvent[], after: number, hasMore = false): BatchEventPage {
  return {
    goal_id: 'g1', events: rows, last_seq: 10,
    next_after: rows.length ? rows[rows.length - 1].seq : null,
    has_more: hasMore, next_before: null, has_earlier: true,
  };
}

function goal(id: string, sessionId: string, status = 'RUNNING'): DurableGoal {
  return { goal_id: id, session_id: sessionId, status, version: 1 };
}

function item(overrides: Partial<BatchItemSummary> = {}): BatchItemSummary {
  return {
    item_id: 'i1', ordinal: 1, name: 'SCENE', status: 'DETECTING',
    item_version: 1, attempt: 0, updated_at: null, has_mask: false,
    overlay_ready: false, candidate_count: 0, job_id: null,
    ...overrides,
  };
}

function sceneRow(): SceneRow {
  return {
    id: 'r1', name: 'SCENE', satellite: null, sensor: null, date: null,
    size_bytes: 1, fake: false, W: 8192, H: 8192, rel: null,
    jpgUrl: null, hasPreview: true, lq_path: '/Disk/scene',
  };
}

function resolveResult(): SceneResolveResult {
  return {
    source: 'manual',
    row: sceneRow(),
    resolved: {
      dir: '/Disk/scene', input: '/Disk/scene/SCENE.tif',
      input_name: 'SCENE.tif', mask_path: null, mask_exists: false,
      writable: false, sr_capable: true, preview_only: false,
    },
  } as unknown as SceneResolveResult;
}

let streamCbs: {
  onEvent: (event: BatchEvent) => void;
  onOpen?: () => void;
  onClose?: (err: Error | null) => void;
} | null = null;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  streamCbs = null;
  vi.mocked(apiBatchSnapshot).mockResolvedValue(snapshot());
  vi.mocked(apiBatchEvents).mockResolvedValue(page([event(9), event(10)], 8));
  vi.mocked(subscribeGoalEvents).mockImplementation(
    (_cfg, _goal, _after, onEvent, _onError, onOpen, onClose) => {
      streamCbs = { onEvent, onOpen, onClose };
      return () => { /* stop */ };
    });
});

afterEach(() => {
  useBatchGoalStore().detach();
  vi.restoreAllMocks();
});

describe('attach / associate', () => {
  it('首次显式 Overlay 深链接不会被 Panel 清除，主动关闭后不会复活', async () => {
    window.history.replaceState(null, '', '/viewer?batch_goal=g1&batch_item=i1');
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    expect(useViewerStore().batchOverlayOff).toBe(false);
    batch.clearOverlay();
    await batch.attach('g1');
    expect(useViewerStore().batchOverlayOff).toBe(true);
  });

  it('同会话新任务进入选择器且不抢当前选择，其他会话任务不出现', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    vi.mocked(apiBatchGoals).mockResolvedValue([goal('new', 's1'), goal('other', 's2')]);
    await batch.discover('s1');
    expect(apiBatchGoals).toHaveBeenCalledWith(expect.anything(), 100, 's1');
    expect(batch.availableGoals.map(g => g.goal_id)).toEqual(['new']);
    expect(batch.goalId).toBe('g1');
  });
  it('显式选择批次同步 URL，刷新不会回到旧链接任务', async () => {
    window.history.replaceState(null, '', '/viewer?batch_goal=old&batch_item=old-item');
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snapshot({ goal_id: 'g-selected' }));
    const batch = useBatchGoalStore();
    expect(await batch.selectGoal('g-selected')).toBe(true);
    expect(new URLSearchParams(window.location.search).get('batch_goal')).toBe('g-selected');
    expect(new URLSearchParams(window.location.search).has('batch_item')).toBe(false);
  });

  it('同会话有活动批次时自动关联，不盲取列表第一条', async () => {
    vi.mocked(apiBatchGoals).mockResolvedValue([
      goal('g-other', 's2'), goal('g-mine', 's1'), goal('g-done', 's1', 'COMPLETED'),
    ]);
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snapshot({ goal_id: 'g-mine' }));
    const batch = useBatchGoalStore();
    expect(await batch.associate('s1')).toBe(true);
    expect(batch.goalId).toBe('g-mine');
    expect(batch.consumedSeq).toBe(10);
    expect(batch.snapshot?.goal.control_revision).toBe(2);
    expect(vi.mocked(apiBatchEvents)).toHaveBeenCalledWith(
      expect.anything(), 'g-mine', { before: 11, limit: 20 });
    expect(vi.mocked(subscribeGoalEvents)).toHaveBeenCalledWith(
      expect.anything(), 'g-mine', 10,
      expect.any(Function), expect.any(Function),
      expect.any(Function), expect.any(Function));
  });

  it('没有会话时不自动绑定任何批次', async () => {
    vi.mocked(apiBatchGoals).mockResolvedValue([goal('g-mine', 's1')]);
    const batch = useBatchGoalStore();
    expect(await batch.associate(null)).toBe(false);
    expect(batch.goalId).toBeNull();
    expect(vi.mocked(apiBatchSnapshot)).not.toHaveBeenCalled();
  });
});

describe('批次切换竞态', () => {
  it('旧批次的快照迟到时不覆盖新批次', async () => {
    let resolveA!: (value: BatchSnapshot) => void;
    vi.mocked(apiBatchSnapshot)
      .mockImplementationOnce(() => new Promise<BatchSnapshot>((resolve) => {
        resolveA = resolve;
      }))
      .mockResolvedValueOnce(snapshot({ goal_id: 'g-b', last_seq: 7 }));
    const batch = useBatchGoalStore();
    const attachA = batch.attach('g-a');
    expect(await batch.attach('g-b')).toBe(true);

    resolveA(snapshot({ goal_id: 'g-a', last_seq: 99 }));
    expect(await attachA).toBe(false);

    expect(batch.goalId).toBe('g-b');
    expect(batch.snapshot?.goal.goal_id).toBe('g-b');
    expect(batch.consumedSeq).toBe(7);
  });

  it('同批次较早的刷新响应不覆盖较新的水位', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');

    const older = snapshot();
    older.counts.total = 3;
    const newer = snapshot();
    newer.counts.total = 4;
    let resolveOlder!: (value: BatchSnapshot) => void;
    vi.mocked(apiBatchSnapshot)
      .mockImplementationOnce(() => new Promise<BatchSnapshot>((resolve) => {
        resolveOlder = resolve;
      }))
      .mockResolvedValueOnce(newer);

    const first = batch.refreshSnapshot();
    await batch.refreshSnapshot();
    expect(batch.snapshot?.counts.total).toBe(4);

    resolveOlder(older);
    await first;
    expect(batch.snapshot?.counts.total).toBe(4);
  });
});

describe('事件追平', () => {
  it('网络恢复后重读快照并从消费位置追平；pagehide 关闭流', async () => {
    vi.useFakeTimers();
    const stop = vi.fn();
    try {
      vi.mocked(subscribeGoalEvents).mockReturnValue(stop);
      const batch = useBatchGoalStore();
      await batch.attach('g1');
      window.dispatchEvent(new Event('offline'));
      expect(stop).toHaveBeenCalledOnce();
      expect(batch.syncState).toBe('reconnecting');
      vi.mocked(apiBatchSnapshot).mockResolvedValue(snapshot({ last_seq: 12 }));
      vi.mocked(apiBatchEvents).mockResolvedValue(page([event(11), event(12)], 10));
      window.dispatchEvent(new Event('online'));
      await vi.advanceTimersByTimeAsync(1000);
      expect(batch.consumedSeq).toBe(12);
      expect(apiBatchEvents).toHaveBeenLastCalledWith(expect.anything(), 'g1', { after: 10, limit: 200 });
      window.dispatchEvent(new Event('pagehide'));
      expect(stop).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });
  it('分页进行时到达的新尾事件继续追平，重复事件不重复并入', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    let resolvePage!: (p: BatchEventPage) => void;
    vi.mocked(apiBatchEvents)
      .mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve; }))
      .mockResolvedValueOnce(page([event(13), event(14)], 12));
    const first = batch.catchUp(12);
    const tail = batch.catchUp(14);
    resolvePage(page([event(11), event(12)], 10));
    await Promise.all([first, tail]);
    expect(batch.consumedSeq).toBe(14);
    streamCbs!.onEvent(event(14));
    expect(batch.events.filter(e => e.seq === 14)).toHaveLength(1);
    expect(vi.mocked(apiBatchEvents)).toHaveBeenLastCalledWith(expect.anything(), 'g1', { after: 12, limit: 200 });
  });

  it('持续事件不会无限推迟权威快照刷新', async () => {
    vi.useFakeTimers();
    try {
      const batch = useBatchGoalStore();
      await batch.attach('g1');
      for (let seq = 11; seq <= 14; seq++) {
        streamCbs!.onEvent(event(seq));
        await vi.advanceTimersByTimeAsync(100);
      }
      expect(apiBatchSnapshot).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });

  it('切换批次后旧历史与控制响应不写入新批次', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    let history!: (p: BatchEventPage) => void;
    let control!: (p: Awaited<ReturnType<typeof apiBatchControl>>) => void;
    vi.mocked(apiBatchEvents).mockImplementationOnce(() => new Promise(resolve => { history = resolve; }));
    vi.mocked(apiBatchControl).mockImplementationOnce(() => new Promise(resolve => { control = resolve; }));
    const older = batch.loadEarlier();
    const action = batch.control('pause');
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snapshot({ goal_id: 'g2' }));
    await batch.attach('g2');
    history(page([event(1)], 0));
    control({ changed: true } as Awaited<ReturnType<typeof apiBatchControl>>);
    await older;
    expect(await action).toBe('');
    expect(batch.historyPage).toEqual([]);
    expect(batch.controlBusy).toBe(false);
    expect(batch.snapshot?.goal.goal_id).toBe('g2');
  });

  it('SSE 缺口从已消费游标 after 分页追平，而不是跳到快照水位', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    vi.mocked(apiBatchEvents).mockResolvedValueOnce(page([event(11), event(12)], 10));
    streamCbs!.onEvent(event(12));
    await vi.waitFor(() => expect(batch.consumedSeq).toBe(12));
    expect(vi.mocked(apiBatchEvents)).toHaveBeenLastCalledWith(
      expect.anything(), 'g1', { after: 10, limit: 200 });
    expect(batch.events.map((row) => row.seq)).toContain(11);
    expect(batch.events.map((row) => row.seq)).toContain(12);
  });

  it('连续事件直接消费，不触发额外分页', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    const calls = vi.mocked(apiBatchEvents).mock.calls.length;
    streamCbs!.onEvent(event(11));
    expect(batch.consumedSeq).toBe(11);
    expect(vi.mocked(apiBatchEvents).mock.calls.length).toBe(calls);
  });
});

describe('有界事件历史', () => {
  it('首屏无历史时实时增长仍开放历史，首次翻页从当前可见十条之前开始', async () => {
    vi.mocked(apiBatchEvents).mockResolvedValue({ ...page([event(9), event(10)], 8), has_earlier: false });
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    expect(batch.hasEarlier).toBe(false);
    for (let seq = 11; seq <= 40; seq++) streamCbs!.onEvent(event(seq));
    expect(batch.hasEarlier).toBe(true);
    vi.mocked(apiBatchEvents).mockResolvedValue({ ...page([event(29), event(30)], 0), next_before: 29 });
    await batch.loadEarlier();
    expect(vi.mocked(apiBatchEvents)).toHaveBeenLastCalledWith(expect.anything(), 'g1', { before: 31, limit: 20 });
  });

  it('超过 500 条实时事件后历史仍能逐页到达首条，无重复请求', async () => {
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snapshot({ last_seq: 900 }));
    vi.mocked(apiBatchEvents).mockImplementation(async (_cfg, _gid, opts = {}) => {
      const before = opts.before ?? 901;
      const first = Math.max(1, before - (opts.limit ?? 20));
      const rows = Array.from({ length: before - first }, (_, i) => event(first + i));
      return { ...page(rows, 0), next_before: first, has_earlier: first > 1 };
    });
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    for (let seq = 901; seq <= 1500; seq++) streamCbs!.onEvent(event(seq));
    expect(batch.events).toHaveLength(500);
    const cursors: number[] = [];
    while (batch.hasEarlier) {
      await batch.loadEarlier();
      cursors.push(Math.min(...batch.historyPage.map(e => e.seq)));
    }
    expect(cursors.at(-1)).toBe(1);
    expect(new Set(cursors).size).toBe(cursors.length);
    expect(cursors.every((c, i) => !i || c < cursors[i - 1])).toBe(true);
    expect(batch.events).toHaveLength(500);
    expect(batch.historyPage.length).toBeLessThanOrEqual(20);
  });
  it('从最近十条之前读取历史，分页游标独立于实时尾部', async () => {
    vi.mocked(apiBatchEvents).mockResolvedValue(page(Array.from({ length: 20 }, (_, i) => event(i + 1)), 20));
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    vi.mocked(apiBatchEvents).mockResolvedValue({ ...page([event(9), event(10)], 20), next_before: 9 });
    await batch.loadEarlier();
    expect(vi.mocked(apiBatchEvents).mock.calls.at(-1)?.[2]?.before).toBe(11);
    expect(batch.historyPage.map(e => e.seq)).toEqual([9, 10]);
    batch.events = [event(400), event(500)];
    await batch.loadEarlier();
    expect(vi.mocked(apiBatchEvents).mock.calls.at(-1)?.[2]?.before).toBe(9);
  });
});

describe('控制冲突', () => {
  it('409 后重新读快照并提示用户确认', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    vi.mocked(apiBatchControl).mockRejectedValue(
      new HttpError(409, 'stale revision', '', true, {
        reason: 'stale_revision', control_revision: 3,
      }));
    const message = await batch.control('pause');
    expect(message).toContain('控制版本已变化');
    expect(batch.controlError).toContain('控制版本已变化');
    expect(vi.mocked(apiBatchSnapshot).mock.calls.length).toBeGreaterThan(1);
    expect(vi.mocked(apiBatchControl)).toHaveBeenCalledWith(
      expect.anything(), 'g1', 'pause', 2);
  });
});

describe('自动跟随', () => {
  it('手动定位也隔离迟到取图，绘制期间不发导航请求', async () => {
    const batch = useBatchGoalStore();
    const viewer = useViewerStore();
    await batch.attach('g1');
    viewer.drawMode = true;
    expect(await batch.openItem(item({ image_path: '/Disk/scene/SCENE.tif' }))).toContain('退出绘制');
    expect(apiResolveScene).not.toHaveBeenCalled();
    viewer.drawMode = false;
    vi.mocked(apiResolveScene).mockResolvedValue(resolveResult());
    let finish!: (blob: Blob) => void;
    vi.mocked(fetchSceneJpg).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const open = vi.spyOn(viewer, 'openSceneJpg').mockResolvedValue(undefined);
    const pending = batch.openItem(item({ image_path: '/Disk/scene/SCENE.tif' }));
    await vi.waitFor(() => expect(fetchSceneJpg).toHaveBeenCalled());
    viewer.noteUserIntent();
    finish(new Blob(['jpg']));
    expect(await pending).toContain('取消定位');
    expect(open).not.toHaveBeenCalled();
    expect(viewer.batchOverlayTarget).toBeNull();
  });

  it('刷新后不恢复自动跟随；同一 Item 的版本变化不重复导航', async () => {
    localStorage.setItem('sr.batch.follow', 'true');
    const batch = useBatchGoalStore();
    expect(batch.follow).toBe(false);
    const snap = snapshot();
    snap.producer.current = item({ image_path: '/Disk/scene/SCENE.tif' });
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snap);
    vi.mocked(apiResolveScene).mockResolvedValue(resolveResult());
    vi.mocked(fetchSceneJpg).mockResolvedValue(new Blob(['jpg']));
    vi.spyOn(useViewerStore(), 'openSceneJpg').mockResolvedValue(undefined);
    await batch.attach('g1');
    batch.setFollow(true);
    await vi.waitFor(() => expect(useViewerStore().batchOverlayTarget).not.toBeNull());
    const calls = vi.mocked(apiResolveScene).mock.calls.length;
    const updated = { ...snap, producer: { ...snap.producer, current: { ...snap.producer.current!, item_version: 5 } } };
    vi.mocked(apiBatchSnapshot).mockResolvedValue(updated);
    await batch.refreshSnapshot();
    await nextTick();
    expect(vi.mocked(apiResolveScene).mock.calls.length).toBe(calls);
  });

  it('默认关闭；用户视图操作立即退出跟随', async () => {
    const batch = useBatchGoalStore();
    await batch.attach('g1');
    expect(batch.follow).toBe(false);
    batch.setFollow(true);
    expect(batch.follow).toBe(true);
    useViewerStore().noteUserIntent();
    await nextTick();
    expect(batch.follow).toBe(false);
    expect(batch.followNote).toContain('退出跟随');
  });

  it('跟随取图途中用户操作后，迟到结果不再改变视图', async () => {
    const snap = snapshot();
    snap.producer.current = item({ image_path: '/Disk/scene/SCENE.tif' });
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snap);
    vi.mocked(apiResolveScene).mockResolvedValue(resolveResult());
    let resolveBlob!: (blob: Blob) => void;
    vi.mocked(fetchSceneJpg).mockImplementation(
      () => new Promise<Blob>((resolve) => { resolveBlob = resolve; }));

    const viewer = useViewerStore();
    const openJpg = vi.spyOn(viewer, 'openSceneJpg').mockResolvedValue(undefined);
    const batch = useBatchGoalStore();
    await batch.attach('g1');

    batch.setFollow(true);
    await vi.waitFor(() => expect(fetchSceneJpg).toHaveBeenCalled());
    // The user zooms/selects while the follow image is still downloading.
    viewer.noteUserIntent();
    resolveBlob(new Blob(['jpg']));
    await vi.waitFor(() => expect(batch.follow).toBe(false));

    expect(openJpg).not.toHaveBeenCalled();
    expect(viewer.batchOverlayTarget).toBeNull();
  });

  it('显式关闭叠加层后 URL 初始目标不能复活', () => {
    const batch = useBatchGoalStore();
    const viewer = useViewerStore();
    viewer.batchOverlayTarget = { goalId: 'g1', itemId: 'item-1' };
    viewer.batchOverlayOff = false;
    batch.clearOverlay();
    expect(viewer.batchOverlayTarget).toBeNull();
    expect(viewer.batchOverlayOff).toBe(true);
  });
});


describe('batch microfeedback facts', () => {
  it('hydrates quietly, then distinguishes mask, real receipt and successful verification; deduplicates reconnect', async () => {
    const snap = snapshot(); snap.producer.current = item({ attempt: 1 });
    vi.mocked(apiBatchSnapshot).mockResolvedValue(snap);
    const batch = useBatchGoalStore(); await batch.attach('g1');
    expect(batch.feedback).toEqual({});
    const emit = (seq: number, event_type: string, payload: Record<string, unknown>) => streamCbs!.onEvent({ goal_id: 'g1', seq, event_type, payload: { item_id: 'i1', attempt: 1, ...payload }, created_at: Date.now() / 1000 });
    emit(11, 'item_state_changed', { to: 'MASK_READY' });
    const mask = batch.feedback.i1; expect(mask.kind).toBe('mask');
    emit(12, 'mask_ready', {}); expect(batch.feedback.i1).toBe(mask);
    emit(13, 'item_state_changed', { to: 'SUBMITTING' }); expect(batch.feedback.i1).toBe(mask);
    emit(14, 'sr_receipt_recorded', { job_id: 42 });
    const receipt = batch.feedback.i1; expect(receipt.kind).toBe('receipt'); expect(receipt.jobId).toBe(42);
    emit(15, 'sr_receipt_recorded', { job_id: 42 }); expect(batch.feedback.i1).toBe(receipt);
    expect(batch.claimFeedback(receipt.key)).toBe(true); expect(batch.claimFeedback(receipt.key)).toBe(false);
    emit(16, 'item_verified', { ok: false }); expect(batch.feedback.i1).toBe(receipt);
    emit(17, 'item_verified', { ok: true, state: 'NO_ISSUE' }); expect(batch.feedback.i1).toBe(receipt);
    emit(18, 'item_verified', { ok: true, state: 'COMPLETED' }); expect(batch.feedback.i1.kind).toBe('verified');
    const verified = batch.feedback.i1;
    await batch.attach('g1'); expect(batch.feedback).toEqual({});
    emit(19, 'item_verified', { ok: true, state: 'COMPLETED' }); expect(batch.feedback.i1).toBeUndefined();
    expect(verified.key).toContain('verified');
  });
  it('keeps concurrent scene and retry identities separate', async () => {
    await useBatchGoalStore().attach('g1'); const batch = useBatchGoalStore();
    const emit = (seq: number, id: string, attempt: number) => streamCbs!.onEvent({ goal_id: 'g1', seq, event_type: 'sr_receipt_recorded', payload: { item_id: id, attempt, job_id: seq }, created_at: Date.now() / 1000 });
    emit(11, 'i1', 1); emit(12, 'i2', 1);
    expect(Object.keys(batch.feedback)).toEqual(['i1', 'i2']);
    const first = batch.feedback.i1.key; emit(13, 'i1', 2); expect(batch.feedback.i1.key).not.toBe(first);
  });
});


describe('microfeedback recovery regressions', () => {
  it('reconnect catch-up is silent even when the new snapshot is already verified', async () => {
    const first = snapshot(); first.producer.current = item({ attempt: 1, status: 'SUBMITTING' });
    vi.mocked(apiBatchSnapshot).mockResolvedValue(first);
    const batch = useBatchGoalStore(); await batch.attach('g1');
    const next = snapshot({ last_seq: 13 });
    next.recent_jobs = [{ ...item({ attempt: 1, status: 'SUCCEEDED', job_id: 42 }), unknown: false, verification_ok: true }];
    vi.mocked(apiBatchSnapshot).mockResolvedValue(next);
    vi.mocked(apiBatchEvents).mockResolvedValue(page([
      { ...event(11), payload: { item_id: 'i1', attempt: 1, to: 'WAITING_SR' } },
      { ...event(12), event_type: 'sr_receipt_recorded', payload: { item_id: 'i1', job_id: 42 } },
      { ...event(13), event_type: 'item_verified', payload: { item_id: 'i1', ok: true, state: 'COMPLETED' } },
    ], 10));
    await batch.refreshSnapshot();
    expect(batch.snapshot?.recent_jobs?.[0].status).toBe('SUCCEEDED');
    expect(batch.feedback).toEqual({}); expect(batch.consumedSeq).toBe(13);
  });
  it('invalidates the previous attempt receipt immediately on retry or failure', async () => {
    await useBatchGoalStore().attach('g1'); const batch = useBatchGoalStore();
    streamCbs!.onEvent({ ...event(11), event_type: 'sr_receipt_recorded', payload: { item_id: 'i1', attempt: 1, job_id: 42 } });
    expect(batch.feedback.i1.attempt).toBe(1);
    streamCbs!.onEvent({ ...event(12), payload: { item_id: 'i1', attempt: 2, to: 'DETECTING' } });
    expect(batch.feedback.i1).toBeUndefined();
  });
  it('coalesces a burst of verified items into one queue refresh and result scan', async () => {
    vi.useFakeTimers();
    try {
      const list = vi.spyOn(useQueueStore(), 'list').mockResolvedValue();
      const scan = vi.spyOn(useImageRefreshStore(), 'scan').mockResolvedValue();
      await useBatchGoalStore().attach('g1');
      for (let n = 0; n < 50; n++) streamCbs!.onEvent({ ...event(11 + n), event_type: 'item_verified', payload: { item_id: 'i' + n, attempt: 1, ok: true } });
      await vi.advanceTimersByTimeAsync(250);
      expect(list).toHaveBeenCalledTimes(1); expect(scan).toHaveBeenCalledTimes(1);
    } finally { vi.useRealTimers(); }
  });
});
