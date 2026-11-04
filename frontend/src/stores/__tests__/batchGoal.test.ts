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
    payload: { from: 'DETECTING', to: 'MASK_READY' }, created_at: seq,
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
