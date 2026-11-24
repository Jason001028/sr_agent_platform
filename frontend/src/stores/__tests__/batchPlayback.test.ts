import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useBatchPlaybackStore } from '../batchPlayback';
import { agentRepairSettings, DEFAULT_AGENT_REPAIR_SETTINGS } from '../../lib/agentRepairSettings';

const state = vi.hoisted(() => ({
  batch: { goalId: 'g', follow: true, isTerminal: true, setFollow: vi.fn() },
  viewer: { userIntentSeq: 0, drawMode: false, batchOverlayOff: false, fit: vi.fn(),
    panes: [{ side: 'A', rec: { W: 1000, H: 1000, thumb: { width: 100, height: 100 } } }],
    focusPaneRect: vi.fn() },
  scenes: { openQuiet: vi.fn(), preloadQuiet: vi.fn(async () => {}) },
  review: { previewing: false, preview: vi.fn<() => Promise<boolean>>(), close: vi.fn() },
  items: vi.fn(), overlay: vi.fn(),
}));
vi.mock('../batchGoal', () => ({ useBatchGoalStore: () => state.batch }));
vi.mock('../batchMaskReview', () => ({ useBatchMaskReviewStore: () => state.review }));
vi.mock('../viewer', () => ({ useViewerStore: () => state.viewer }));
vi.mock('../scenes', () => ({ useScenesStore: () => state.scenes }));
vi.mock('../../lib/api', () => ({ apiBatchItems: (...args: unknown[]) => state.items(...args) }));
vi.mock('../../lib/spatial', () => ({ imageRef: () => ({ image_key: 'image' }) }));
vi.mock('../../lib/batchOverlay', async importOriginal => ({
  ...await importOriginal<typeof import('../../lib/batchOverlay')>(),
  loadBatchOverlay: (...args: unknown[]) => state.overlay(...args),
  overlayMatchesImage: () => true,
}));

function row(ordinal: number, ready = true) {
  return { item_id: `i${ordinal}`, ordinal, name: `scene-${ordinal}`, status: ready ? 'MASK_READY' : 'DETECTING',
    checkpoint: { target_image: { path: `/${ordinal}.tif` },
      ...(ready ? { detection: { candidates: [{}] }, mask: {} } : {}) } };
}
beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); setActivePinia(createPinia());
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS, auto_play_preview: false };
  state.batch.follow = true; state.batch.goalId = 'g'; state.batch.isTerminal = true; state.viewer.userIntentSeq = 0;
  state.batch.setFollow.mockImplementation(on => { state.batch.follow = on; });
  state.scenes.openQuiet.mockResolvedValue('');
  state.viewer.focusPaneRect.mockReturnValue(true);
  state.review.previewing = false;
  state.review.preview.mockImplementation(async () => { state.review.previewing = true; return true; });
  state.review.close.mockImplementation(() => { state.review.previewing = false; });
  state.overlay.mockResolvedValue({ status: 'ready', doc: {
    candidates: [{ candidate_id: 'r', kind: 'polygon', points: [[100,100],[250,100],[250,250],[100,250]] }],
    source: { image_key: 'image' },
  } });
});
afterEach(() => { useBatchPlaybackStore().stop(); vi.useRealTimers(); });

it('presents each scene in order despite the producer having finished, and preloads the next', async () => {
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: [row(1),row(2)].filter(r=>r.ordinal>after) }));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(9000); await run;
  expect(state.scenes.openQuiet.mock.calls.map(c=>c[0])).toEqual(['/1.tif','/2.tif']);
  expect(state.scenes.preloadQuiet).toHaveBeenCalledWith('/2.tif');
  expect(state.viewer.focusPaneRect).not.toHaveBeenCalled();
  expect(state.viewer.fit).toHaveBeenCalled();
  expect(playback.completed).toBe(2);
  expect(playback.phase).toBe('done');
});

it('pauses pending detection without advancing, and resumes the same scene', async () => {
  state.batch.isTerminal = false;
  state.items.mockResolvedValue({ items: [row(1,false)] });
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start(); await vi.advanceTimersByTimeAsync(500);
  expect(playback.phase).toBe('detecting');
  playback.stop(); await run;
  expect(playback.completed).toBe(0);
  state.batch.isTerminal = true;
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: after ? [] : [row(1)] }));
  const resumed = playback.start(); await vi.advanceTimersByTimeAsync(5000); await resumed;
  expect(playback.completed).toBe(1);
  expect(state.overlay).toHaveBeenCalledTimes(1);
});

it('retries a failed open once, skips it, and presents the next scene', async () => {
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: [row(1),row(2)].filter(r=>r.ordinal>after) }));
  state.scenes.openQuiet.mockImplementation(path => Promise.resolve(path==='/1.tif' ? 'missing file' : ''));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start(); await vi.advanceTimersByTimeAsync(10000); await run;
  expect(state.scenes.openQuiet.mock.calls.map(c=>c[0])).toEqual(['/1.tif','/1.tif','/2.tif']);
  expect(playback.phase).toBe('done');
  expect(state.overlay).toHaveBeenCalledTimes(1);
});

it('does not consume a NO_ISSUE scene when paused during its result hold', async () => {
  const empty = { ...row(1), status: 'NO_ISSUE' };
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: after ? [] : [empty] }));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start(); await vi.advanceTimersByTimeAsync(500);
  expect(playback.phase).toBe('holding');
  playback.stop(); await run;
  expect(playback.completed).toBe(0);
  state.items.mockClear();
  const resumed = playback.start(); await vi.advanceTimersByTimeAsync(2000); await resumed;
  expect(state.items.mock.calls[0][1]).toBe(0);
  expect(playback.completed).toBe(1);
});

it('an aborted NO_ISSUE hold cannot advance a newly started goal', async () => {
  state.items.mockImplementation((g, after) => Promise.resolve({ items: after ? [] : [
    g === 'g' ? { ...row(9), status: 'NO_ISSUE' } : row(1,false),
  ] }));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const oldRun = playback.start(); await vi.advanceTimersByTimeAsync(500);
  expect(playback.phase).toBe('holding');
  playback.reset(); state.batch.goalId = 'new'; state.batch.isTerminal = false;
  const newRun = playback.start();
  await vi.advanceTimersByTimeAsync(500); await oldRun;
  expect(playback.completed).toBe(0);
  expect(state.items.mock.calls.filter(c=>c[0]==='new').every(c=>c[1]===0)).toBe(true);
  expect(playback.ordinal).toBe(1);
  playback.stop(); await newRun;
});

it('plays the QC split and heat before drawing when 全程播放预览 is enabled', async () => {
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS, auto_play_preview: true };
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: [row(1)].filter(r=>r.ordinal>after) }));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(12000); await run;
  expect(state.review.preview).toHaveBeenCalledOnce();
  expect(state.review.close).toHaveBeenCalled();
  expect(state.viewer.batchOverlayOff).toBe(true);
  expect(playback.phase).toBe('done');
});

it('reports a stalled image load as paused and rejects a late view update', async () => {
  state.items.mockResolvedValue({ items: [row(1)] });
  state.scenes.openQuiet.mockReturnValue(new Promise(() => {}));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(60_000); await run;
  expect(playback.phase).toBe('paused');
  expect(playback.note).toContain('加载影像超时');
  expect(state.batch.follow).toBe(false);
  expect(state.scenes.openQuiet.mock.calls[0][2].stillWanted()).toBe(false);
  expect(playback.completed).toBe(0);
});

it('advances past completed scenes with incomplete checkpoints', async () => {
  const incomplete = { ...row(1), checkpoint: { mask: {} } };
  const rows = [incomplete, row(2), { ...row(3), checkpoint: { ...row(3).checkpoint, detection: undefined } }];
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: rows.filter(r => r.ordinal > after) }));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(9000); await run;
  expect(state.scenes.openQuiet.mock.calls.map(c => c[0])).toEqual(['/2.tif']);
  expect(playback.completed).toBe(3);
  expect(playback.phase).toBe('done');
});

it('closes a stalled quality preview immediately when the display is cleared', async () => {
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS, auto_play_preview: true };
  state.items.mockResolvedValue({ items: [row(1)] });
  let resolvePreview!: (shown: boolean) => void;
  state.review.preview.mockImplementation(() => {
    state.review.previewing = true;
    return new Promise(resolve => { resolvePreview = resolve; });
  });
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(500);
  expect(playback.phase).toBe('quality');
  playback.hideOverlay(); await run;
  expect(playback.phase).toBe('idle');
  expect(playback.doc).toBeNull();
  expect(state.review.close).toHaveBeenCalledOnce();
  resolvePreview(true);
  await vi.advanceTimersByTimeAsync(1000);
  expect(state.viewer.focusPaneRect).not.toHaveBeenCalled();
});

it('animates verified candidates even when a blocked scene has no published mask', async () => {
  const scene = row(1);
  const checkpoint = {
    target_image: { path: '/1.tif', image_key: 'image', revision: 'r1', width: 1000, height: 1000 },
    detection: { provenance: 'real', candidates: [
      { candidate_id: 'candidate', kind: 'polygon', points: [[10, 10], [90, 10], [90, 90]] },
    ] },
  };
  state.items.mockImplementation((_g, after) => Promise.resolve({
    items: after ? [] : [{ ...scene, status: 'BLOCKED', checkpoint }],
  }));
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(800);
  expect(playback.phase).toBe('drawing');
  expect(playback.detectionOnly).toBe(true);
  expect(state.overlay).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(4000); await run;
});

it('draws the published mask after the optional comparison times out', async () => {
  agentRepairSettings.value = { ...DEFAULT_AGENT_REPAIR_SETTINGS, auto_play_preview: true };
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: after ? [] : [row(1)] }));
  state.review.preview.mockImplementation(() => {
    state.review.previewing = true;
    return new Promise(() => {});
  });
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(9000);
  expect(playback.phase).toBe('drawing');
  expect(state.review.close).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(4000); await run;
});

it('waits for the published mask when detection has finished and the scene is still active', async () => {
  const finished = row(1);
  const pending = { ...finished, status: 'DETECTING', checkpoint: {
    ...finished.checkpoint, phase: 'MASK_PENDING', mask: undefined,
  } };
  state.batch.isTerminal = false;
  state.items.mockResolvedValue({ items: [pending] });
  const playback = useBatchPlaybackStore(); playback.mounted = true;
  const run = playback.start();
  await vi.advanceTimersByTimeAsync(500);
  expect(playback.phase).toBe('detecting');
  expect(playback.note).toContain('等待本景掩码发布');
  expect(state.viewer.focusPaneRect).not.toHaveBeenCalled();
  state.batch.isTerminal = true;
  state.items.mockImplementation((_g, after) => Promise.resolve({ items: after ? [] : [finished] }));
  await vi.advanceTimersByTimeAsync(5000); await run;
  expect(state.overlay).toHaveBeenCalledOnce();
  expect(state.viewer.focusPaneRect).not.toHaveBeenCalled();
});
