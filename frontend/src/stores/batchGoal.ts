/**
 * stores/batchGoal.ts — Batch Progress Panel 的权威状态（PR F）
 * ------------------------------------------------------------------
 * 同步顺序固定为：
 *   1. 读 `batch-snapshot`（计数/进度/当前项/队列/水位一次读定）；
 *   2. 按 `before` 取最近事件给时间线；
 *   3. 从**已消费游标**上 SSE，缺口用 `after` 分页追平；
 *   4. 事件只触发「重新读快照」，绝不按事件条数累加业务进度。
 *
 * 自动跟随默认关闭；用户手动选图、缩放、平移、分屏、切换目标都会让
 * `viewer.userIntentSeq` 加一，本 store 据此立即退出跟随。编辑（ROI 绘制）期间
 * 只暂停导航，不覆盖任何 ROI / 掩码 / 批注状态。
 */
import { computed, ref, watch } from 'vue';
import { defineStore } from 'pinia';
import { useViewerStore } from './viewer.js';
import { registerViewerObservation } from '../lib/viewerContext';
import { useScenesStore } from './scenes.js';
import {
  apiBatchControl, apiBatchEvents, apiBatchGoals, apiBatchSnapshot,
  HttpError, loadSrConfig, subscribeGoalEvents,
} from '../lib/api.js';
import type {
  BatchControlAction, BatchEvent, BatchItemSummary, BatchSnapshot, DurableGoal,
} from '../lib/api.js';
import {
  catchUpBatchEvents, eventNeedsSnapshot, mergeBatchEvents,
  pickAssociatedBatchId, DEFAULT_EVENT_LIMIT,
} from '../lib/batchEventSync.js';
import { feedbackEventKind, feedbackKey, isTerminalStatus } from '../lib/batchUi.js';

import type { BatchFeedback } from '../lib/batchUi.js';
import { useImageRefreshStore } from './imageRefresh';
import { useQueueStore } from './queue';

const LAST_GOAL_KEY = 'sr.batch.lastGoal';
const FOLLOW_KEY = 'sr.batch.follow';

function readStored(key: string): string {
  try { return localStorage.getItem(key) || ''; } catch { return ''; }
}
function writeStored(key: string, value: string | null): void {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* optional persistence */ }
}

export type BatchSyncState =
  | 'idle' | 'loading' | 'live' | 'reconnecting' | 'error' | 'done';

export const useBatchGoalStore = defineStore('batchGoal', () => {
  const goalId = ref<string | null>(null);
  const snapshot = ref<BatchSnapshot | null>(null);
  // The backend reads the actual session-bound Goal; client progress is not authority.
  const feedback = ref<Record<string, BatchFeedback>>({});
  const seenFeedback = new Set<string>();
  const presentedFeedback = new Set<string>();
  function claimFeedback(key: string): boolean {
    if (presentedFeedback.has(key)) return false;
    presentedFeedback.add(key);
    return true;
  }
  let resultRefreshTimer: ReturnType<typeof setTimeout> | null = null;
  function scheduleResultRefresh(): void {
    if (resultRefreshTimer != null) return;
    resultRefreshTimer = setTimeout(() => {
      resultRefreshTimer = null;
      void useQueueStore().list().then(() => useImageRefreshStore().scan(true)).catch(() => {});
    }, 250);
  }
  let feedbackFloor = 0;
  const itemAttempts = new Map<string, number>();
  function observeFeedbackEvent(event: BatchEvent, live = true): void {
    if (event.seq <= feedbackFloor || !goalId.value) return;
    const kind = feedbackEventKind(event);
    const id = String(event.payload.item_id ?? event.step_id ?? '');
    const snap = snapshot.value;
    const row = [snap?.producer.current, ...(snap?.active_jobs ?? []), ...(snap?.recent_jobs ?? [])].find(r => r?.item_id === id);
    if (typeof event.payload.attempt === 'number') itemAttempts.set(id, event.payload.attempt);
    const attempt = typeof event.payload.attempt === 'number' ? event.payload.attempt : itemAttempts.get(id) ?? row?.attempt;
    const previous = feedback.value[id];
    if (event.event_type === 'item_state_changed' && previous && event.seq >= previous.seq
        && (attempt !== previous.attempt || ['FAILED', 'BLOCKED', 'SKIPPED', 'NO_ISSUE', 'REVIEW_REJECTED'].includes(String(event.payload.to)))) delete feedback.value[id];
    if (!kind || !id || attempt == null) return;
    const key = feedbackKey(goalId.value, id, attempt, kind);
    if (seenFeedback.has(key)) return;
    seenFeedback.add(key);
    if (kind === 'verified') scheduleResultRefresh();
    // Catch-up repairs business state silently. A delayed event is never a fresh celebration.
    if (!live || !Number.isFinite(event.created_at) || Math.abs(Date.now() - event.created_at * 1000) > 3000) return;
    if (previous && (previous.attempt > attempt || previous.seq > event.seq)) return;
    feedback.value[id] = { key, goalId: goalId.value, itemId: id, attempt, kind, seq: event.seq, at: Date.now(), jobId: typeof event.payload.job_id === 'number' ? event.payload.job_id : undefined };
  }
  const events = ref<BatchEvent[]>([]);
  const historyPage = ref<BatchEvent[]>([]);
  const historyBefore = ref<number | null>(null);
  const availableGoals = ref<DurableGoal[]>([]);
  const syncState = ref<BatchSyncState>('idle');
  const syncError = ref('');
  const loadError = ref('');
  const controlError = ref('');
  const controlBusy = ref(false);
  const loadingEarlier = ref(false);
  const hasEarlier = ref(false);
  const consumedSeq = ref(0);
  const follow = ref(false);
  const followNote = ref('');
  const followError = ref('');

  let streamStop: (() => void) | null = null;
  let manualStop = false;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  let catchUpTask: Promise<void> | null = null;
  let catchUpTaskGen = -1;
  let catchUpTarget = 0;
  let catchUpToken: object | null = null;
  let retryDelay = 1000;
  let lastFollowKey = '';
  /* Every attach/associate takes a new generation.  A response that started
   * under an older generation must not write into the current Panel: switching
   * from batch A to B can otherwise leave B's id next to A's counts and
   * control revision. */
  let loadGen = 0;
  /* Same-goal refreshes may also resolve out of order; only the newest one
   * may commit, so an older response cannot roll the snapshot back. */
  let refreshSeq = 0;
  let discoverySeq = 0;

  async function discover(sessionId: string | null): Promise<void> {
    const seq = ++discoverySeq;
    const gen = loadGen;
    try {
      const goals = await apiBatchGoals(cfg(), 100, sessionId);
      if (seq !== discoverySeq || gen !== loadGen) return;
      availableGoals.value = goals.filter(g => g.session_id === sessionId || g.goal_id === goalId.value);
      // An explicit or remembered selection remains authoritative. New work
      // appears in the existing selector, without stealing the current view.
      if (!goalId.value && sessionId) await associate(sessionId);
    } catch { /* Discovery must not replace the active stream's error state. */ }
  }

  function isCurrent(gen: number, gid: string | null): boolean {
    return gen === loadGen && goalId.value === gid;
  }

  const cfg = () => loadSrConfig();
  const hasGoal = computed(() => !!goalId.value);
  const isVisible = computed(() => !!goalId.value || availableGoals.value.length > 0);
  const isTerminal = computed(() => !!snapshot.value
    && isTerminalStatus(snapshot.value.goal.status));
  const isDone = computed(() => !!snapshot.value
    && isTerminal.value && snapshot.value.goal.monitoring_done);
  const paused = computed(() => snapshot.value?.goal.status === 'PAUSED');

  function clearTimers(): void {
    if (retryTimer != null) { clearTimeout(retryTimer); retryTimer = null; }
    if (refreshTimer != null) { clearTimeout(refreshTimer); refreshTimer = null; }
  }

  function stopStream(): void {
    manualStop = true;
    if (streamStop) streamStop();
    streamStop = null;
    manualStop = false;
  }

  function reset(preserveOverlay = false): void {
    clearTimers();
    stopStream();
    if (resultRefreshTimer != null) clearTimeout(resultRefreshTimer);
    resultRefreshTimer = null;
    snapshot.value = null;
    feedback.value = {};
    feedbackFloor = 0;
    itemAttempts.clear();
    events.value = [];
    historyPage.value = [];
    historyBefore.value = null;
    loadingEarlier.value = false;
    controlBusy.value = false;
    consumedSeq.value = 0;
    hasEarlier.value = false;
    syncError.value = '';
    loadError.value = '';
    controlError.value = '';
    followError.value = '';
    followNote.value = '';
    syncState.value = 'idle';
    lastFollowKey = '';
    if (!preserveOverlay) clearOverlay();
  }

  function detach(): void {
    // Invalidates any attach/refresh still in flight for the old batch.
    loadGen += 1;
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('offline', onOffline);
    window.removeEventListener('online', onOnline);
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('pageshow', onOnline);
    reset();
    goalId.value = null;
  }

  function setSyncError(message: string): void {
    syncError.value = message;
    syncState.value = 'error';
  }

  async function loadSnapshot(): Promise<BatchSnapshot> {
    return await apiBatchSnapshot(cfg(), goalId.value!);
  }

  async function readRecentEvents(
    upTo: number, gen: number, gid: string,
  ): Promise<void> {
    const page = await apiBatchEvents(cfg(), gid, {
      before: upTo + 1, limit: DEFAULT_EVENT_LIMIT,
    });
    if (!isCurrent(gen, gid)) return;
    events.value = mergeBatchEvents([], page.events);
    // Anchor on the first history request, from the ten rows visible then.
    // After browsing starts this cursor is independent of the live buffer.
    historyBefore.value = null;
    hasEarlier.value = page.has_earlier || page.events.length > 10;
  }

  /** Attach to one Goal.  Returns false when the id is not a batch Goal, or
   *  when a newer attach/associate superseded this one while it was loading.
   *  `gen` is supplied by `associate` so the whole association keeps one
   *  generation; a direct attach starts a fresh one. */
  async function attach(id: string, gen?: number): Promise<boolean> {
    if (gen !== undefined && gen !== loadGen) return false;
    const viewer = useViewerStore();
    const query = new URLSearchParams(window.location.search);
    const initialItem = !goalId.value && !viewer.batchOverlayOff
      && query.get('batch_goal') === id ? query.get('batch_item') : null;
    document.removeEventListener('visibilitychange', onVisibility);
    // Attaching the Panel must not disable or reload the explicit deep-link
    // Overlay. Later switches/explicit closes still clear it normally.
    reset(!!initialItem);
    const myGen = gen ?? ++loadGen;
    goalId.value = id;
    writeStored(LAST_GOAL_KEY, id);
    syncState.value = 'loading';
    loadError.value = '';
    try {
      const snap = await loadSnapshot();
      if (!isCurrent(myGen, id)) return false;
      snapshot.value = snap;
      consumedSeq.value = snap.goal.last_seq;
      feedbackFloor = snap.goal.last_seq;
      for (const row of [snap.producer.current, ...snap.active_jobs, ...(snap.recent_jobs ?? [])]) if (row) itemAttempts.set(row.item_id, row.attempt);
      await readRecentEvents(snap.goal.last_seq, myGen, id);
      if (!isCurrent(myGen, id)) return false;
    } catch (e) {
      if (!isCurrent(myGen, id)) return false;
      if (e instanceof HttpError && e.status === 404) {
        if (readStored(LAST_GOAL_KEY) === id) writeStored(LAST_GOAL_KEY, null);
        goalId.value = null;
        loadError.value = '这个批次不存在或已不可访问，请重新选择批次。';
      } else {
        loadError.value = '读取批次快照失败：' + errorText(e);
      }
      syncState.value = 'error';
      return false;
    }
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('offline', onOffline);
    window.addEventListener('online', onOnline);
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onOnline);
    startStream();
    return true;
  }

  /** URL > user selection/local record > active batch in the same session.
   *  A remembered goal that provably belongs to another chat session is not a
   *  user choice for this context; it is ignored so the Panel never auto-binds
   *  work from an unrelated session. */
  async function associate(sessionId: string | null): Promise<boolean> {
    // The list request is part of the association: if a newer associate or a
    // direct attach starts meanwhile, this one must not bind afterwards.
    const gen = ++loadGen;
    loadError.value = '';
    try {
      const goals = await apiBatchGoals(cfg(), 100, sessionId);
      if (gen !== loadGen) return false;
      availableGoals.value = goals.filter(g => g.session_id === sessionId || g.goal_id === goalId.value);
    } catch (e) {
      if (gen !== loadGen) return false;
      availableGoals.value = [];
      loadError.value = '读取批次列表失败：' + errorText(e);
    }
    const urlGoal = typeof window === 'undefined' ? ''
      : new URLSearchParams(window.location.search).get('batch_goal');
    let remembered = readStored(LAST_GOAL_KEY) || null;
    if (remembered && sessionId) {
      const known = availableGoals.value.find((goal) => goal.goal_id === remembered);
      if (!known || known.session_id !== sessionId) remembered = null;
    }
    const picked = pickAssociatedBatchId(availableGoals.value, {
      urlGoal, remembered, sessionId,
    });
    if (!picked) {
      if (snapshot.value && sessionId && !urlGoal
          && snapshot.value.goal.session_id !== sessionId) {
        detach();
      }
      return false;
    }
    if (gen !== loadGen) return false;
    const ok = await attach(picked, gen);
    if (!ok && remembered && picked === remembered && gen === loadGen) {
      // A stale local record must not pin the Panel to a dead Goal.
      const fallback = pickAssociatedBatchId(availableGoals.value, {
        urlGoal, remembered: null, sessionId,
      });
      if (fallback && fallback !== picked) return await attach(fallback, gen);
    }
    return ok;
  }

  async function selectGoal(id: string): Promise<boolean> {
    const attached = await attach(id);
    if (attached && goalId.value === id && typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.searchParams.set('batch_goal', id);
      url.searchParams.delete('batch_item');
      window.history.replaceState(window.history.state, '', url);
    }
    return attached;
  }

  function scheduleRefresh(delay = 350): void {
    // A busy event stream must not postpone the authoritative read forever.
    if (refreshTimer != null) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void refreshSnapshot();
    }, delay);
  }

  /** Re-read the authoritative projection, then replay only the gap. */
  async function refreshSnapshot(): Promise<void> {
    const gid = goalId.value;
    if (!gid) return;
    const gen = loadGen;
    const seq = ++refreshSeq;
    try {
      const snap = await loadSnapshot();
      // Batch switched, or a newer refresh already committed: drop this reply.
      if (!isCurrent(gen, gid) || seq !== refreshSeq) return;
      snapshot.value = snap;
      if (syncError.value.startsWith('事件追平中断')) syncError.value = '';
      if (snap.goal.last_seq > consumedSeq.value) await catchUp();
      if (!isCurrent(gen, gid) || seq !== refreshSeq) return;
      for (const row of [snap.producer.current, ...snap.active_jobs, ...(snap.recent_jobs ?? [])]) {
        if (!row) continue;
        itemAttempts.set(row.item_id, row.attempt);
        const cue = feedback.value[row.item_id];
        if (cue && (cue.attempt !== row.attempt || ['FAILED', 'BLOCKED', 'SKIPPED', 'NO_ISSUE', 'REVIEW_REJECTED'].includes(row.status))) delete feedback.value[row.item_id];
      }
      if (snap.goal.monitoring_done && isTerminalStatus(snap.goal.status)
          && consumedSeq.value >= snap.goal.last_seq) {
        syncState.value = 'done';
      }
    } catch (e) {
      if (!isCurrent(gen, gid) || seq !== refreshSeq) return;
      setSyncError('刷新批次快照失败：' + errorText(e));
    }
  }

  /** Forward pagination from the client's consumed cursor.  Never jumps to
   *  `snapshot.last_seq` without replaying the events in between. */
  async function catchUp(upTo?: number): Promise<void> {
    if (!goalId.value) return;
    const target = Math.max(upTo ?? 0, snapshot.value?.goal.last_seq ?? 0);
    // A task belongs to the batch that started it; after a switch it must not
    // be reused to answer for the new batch.
    if (catchUpTask && catchUpTaskGen === loadGen) {
      const gen = loadGen;
      const previousTarget = catchUpTarget;
      await catchUpTask;
      // An SSE tail/refresh arriving during pagination may extend the target.
      if (gen === loadGen && target > previousTarget && consumedSeq.value < target) {
        await catchUp(target);
      }
      return;
    }
    const gid = goalId.value;
    const gen = loadGen;
    const token = {};
    const task = (async () => {
      try {
        const result = await catchUpBatchEvents(
          (opts) => apiBatchEvents(cfg(), gid, opts),
          consumedSeq.value, target,
        );
        if (!isCurrent(gen, gid)) return;
        if (result.events.length) {
          result.events.forEach(event => observeFeedbackEvent(event, false));
          events.value = mergeBatchEvents(events.value, result.events);
          if (historyBefore.value === null && events.value.length > 10) hasEarlier.value = true;
        }
        consumedSeq.value = Math.max(consumedSeq.value, result.consumed);
        if (result.truncated) {
          syncError.value = '事件追平中断：游标已保留，稍后会继续从断点追平。';
          scheduleRefresh(1200);
        }
      } catch (e) {
        if (!isCurrent(gen, gid)) return;
        setSyncError('事件追平失败：' + errorText(e));
        scheduleReconnect();
      } finally {
        if (catchUpToken === token) {
          catchUpTask = null;
          catchUpToken = null;
        }
      }
    })();
    catchUpTask = task;
    catchUpTaskGen = gen;
    catchUpTarget = target;
    catchUpToken = token;
    return task;
  }

  function onStreamEvent(event: BatchEvent): void {
    if (event.goal_id !== goalId.value) return;
    if (event.seq <= consumedSeq.value) return;
    if (event.seq > consumedSeq.value + 1) {
      void catchUp(event.seq);
    } else {
      observeFeedbackEvent(event);
      events.value = mergeBatchEvents(events.value, [event]);
      if (historyBefore.value === null && events.value.length > 10) hasEarlier.value = true;
      consumedSeq.value = event.seq;
    }
    if (eventNeedsSnapshot(event.event_type)) scheduleRefresh();
  }

  function startStream(): void {
    if (!goalId.value) return;
    stopStream();
    const gen = loadGen;
    const gid = goalId.value;
    streamStop = subscribeGoalEvents(
      cfg(), goalId.value, consumedSeq.value,
      (event) => { if (isCurrent(gen, gid)) onStreamEvent(event); },
      () => { /* onClose carries the reason; onError is not fatal by itself. */ },
      () => {
        if (!isCurrent(gen, gid)) return;
        syncState.value = 'live';
        syncError.value = '';
        retryDelay = 1000;
        void catchUp();
        scheduleRefresh(80);
      },
      (err) => {
        if (!isCurrent(gen, gid)) return;
        if (manualStop) return;
        if (isDone.value) {
          syncState.value = 'done';
          void refreshSnapshot();
          return;
        }
        syncState.value = 'reconnecting';
        syncError.value = err
          ? '事件流已断开：' + errorText(err) + '，正在从已消费位置追平。'
          : '事件流已结束，正在确认最终状态。';
        scheduleReconnect();
      },
    );
  }

  function scheduleReconnect(): void {
    if (retryTimer != null || !goalId.value) return;
    const delay = retryDelay;
    retryDelay = Math.min(retryDelay * 2, 10000);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (!goalId.value) return;
      const gen = loadGen;
      const gid = goalId.value;
      void (async () => {
        await refreshSnapshot();
        if (!isCurrent(gen, gid)) return;
        await catchUp();
        if (!isCurrent(gen, gid)) return;
        startStream();
      })();
    }, delay);
  }

  async function loadEarlier(): Promise<void> {
    if (!goalId.value || loadingEarlier.value) return;
    const gid = goalId.value;
    const gen = loadGen;
    const first = [...events.value].sort((a, b) => b.seq - a.seq).slice(0, 10).at(-1);
    const before = historyBefore.value ?? (first ? first.seq : (snapshot.value?.goal.last_seq ?? 0) + 1);
    historyBefore.value = before;
    loadingEarlier.value = true;
    try {
      const page = await apiBatchEvents(cfg(), gid, {
        before, limit: DEFAULT_EVENT_LIMIT,
      });
      if (!isCurrent(gen, gid)) return;
      historyPage.value = page.events;
      historyBefore.value = page.next_before ?? page.events[0]?.seq ?? before;
      hasEarlier.value = page.has_earlier && historyBefore.value < before;
    } catch (e) {
      if (!isCurrent(gen, gid)) return;
      syncError.value = '加载更早事件失败：' + errorText(e);
    } finally {
      if (isCurrent(gen, gid)) loadingEarlier.value = false;
    }
  }

  function controlNotice(action: BatchControlAction, changed: boolean): string {
    if (!changed) {
      return {
        pause: '批次已经是暂停状态。',
        resume: '批次已经在运行。',
        cancel: '批次已经取消。',
      }[action];
    }
    if (action === 'pause') {
      return '已暂停派发。已经提交给外部调度器的 SR 作业不会被终止，监控仍在更新。';
    }
    if (action === 'resume') return '已继续派发。';
    return '已请求取消。已经提交给外部调度器的 SR 作业不会被终止，监控会继续更新到收尾。';
  }

  async function control(action: BatchControlAction): Promise<string> {
    const gid = goalId.value;
    if (!gid || !snapshot.value || controlBusy.value) return '';
    const gen = loadGen;
    controlBusy.value = true;
    controlError.value = '';
    try {
      const result = await apiBatchControl(
        cfg(), gid, action, snapshot.value.goal.control_revision);
      // Switching batches while the request was in flight: the reply belongs
      // to the old batch, so it must not touch the new Panel.
      if (!isCurrent(gen, gid)) return '';
      await refreshSnapshot();
      await catchUp();
      if (!isCurrent(gen, gid)) return '';
      return controlNotice(action, result.changed);
    } catch (e) {
      if (!isCurrent(gen, gid)) return '';
      if (e instanceof HttpError && e.status === 409) {
        await refreshSnapshot();
        if (!isCurrent(gen, gid)) return '';
        controlError.value = '批次控制版本已变化，已重新读取最新快照。请确认当前状态后再操作。';
      } else if (e instanceof HttpError && e.status === 400) {
        controlError.value = '控制请求缺少有效的版本号：' + errorText(e);
      } else {
        controlError.value = '控制请求失败：' + errorText(e);
      }
      return controlError.value;
    } finally {
      if (isCurrent(gen, gid)) controlBusy.value = false;
    }
  }

  /* ---------------- follow / open ---------------- */

  function setFollow(on: boolean): void {
    follow.value = on;
    writeStored(FOLLOW_KEY, String(on));
    followError.value = '';
    followNote.value = '';
    if (on) {
      lastFollowKey = '';
      void navigateToCurrent();
    }
  }

  async function navigateToCurrent(): Promise<void> {
    const viewer = useViewerStore();
    const current = snapshot.value?.producer.current;
    const gid = goalId.value;
    if (!follow.value || !gid || !current?.image_path) return;
    if (viewer.drawMode) {
      followNote.value = '正在绘制/编辑，导航暂停；退出绘制后会跟上当前场景。';
      return;
    }
    const key = `${current.item_id}`;
    if (key === lastFollowKey) return;
    /* The image fetch can take seconds.  Everything that made this open
     * desirable is captured now and re-checked in the scenes store right
     * before it touches the viewer, so a late arrival cannot steal the view
     * from a user who already selected, zoomed, drew or switched batches. */
    const seq = viewer.userIntentSeq;
    const gen = loadGen;
    const stillWanted = (): boolean => {
      const v = useViewerStore();
      const latest = snapshot.value?.producer.current;
      return follow.value && isCurrent(gen, gid)
        && v.userIntentSeq === seq && !v.drawMode
        && !!latest && `${latest.item_id}` === key;
    };
    const scenes = useScenesStore();
    const err = await scenes.openQuiet(current.image_path, 'follow', { stillWanted });
    if (err) {
      if (follow.value && goalId.value === gid) followError.value = err;
      return;
    }
    if (!stillWanted()) return;
    lastFollowKey = key;
    followError.value = '';
    followNote.value = '';
    viewer.batchOverlayOff = false;
    viewer.batchOverlayTarget = { goalId: gid, itemId: current.item_id, overlayReady: current.overlay_ready };
  }

  async function openItem(item: Pick<BatchItemSummary,
    'item_id' | 'image_path'> & Partial<Pick<BatchItemSummary, 'overlay_ready'>>): Promise<string> {
    const gid = goalId.value;
    if (!gid) return '还没有关联批次。';
    if (!item.image_path) return '这一项还没有可打开的影像路径。';
    if (useViewerStore().drawMode) return '请先退出绘制 / 编辑后再定位影像。';
    const scenes = useScenesStore();
    const gen = loadGen;
    const viewer = useViewerStore();
    // Record the user's action before IO, so a pending follow cannot win.
    viewer.noteUserIntent();
    const seq = viewer.userIntentSeq;
    const stillWanted = () => isCurrent(gen, gid) && viewer.userIntentSeq === seq && !viewer.drawMode;
    const err = await scenes.openQuiet(item.image_path, 'follow', { stillWanted });
    if (err) return err;
    if (!stillWanted()) return '已按新的视图操作取消定位。';
    viewer.batchOverlayOff = false;
    viewer.batchOverlayTarget = { goalId: gid, itemId: item.item_id, overlayReady: item.overlay_ready === true };
    return '';
  }

  function clearOverlay(): void {
    const viewer = useViewerStore();
    viewer.batchOverlayTarget = null;
    viewer.batchOverlayOff = true;
  }

  function onVisibility(): void {
    if (document.visibilityState !== 'visible' || !goalId.value) return;
    void refreshSnapshot();
    void catchUp();
  }

  function onPageHide(): void {
    clearTimers();
    stopStream();
  }

  function onOffline(): void {
    if (!goalId.value) return;
    onPageHide();
    syncState.value = 'reconnecting';
    syncError.value = '事件流已断开，网络恢复后从已消费位置追平。';
  }

  function onOnline(): void {
    if (goalId.value) scheduleReconnect();
  }

  function exitFollow(note: string): void {
    if (!follow.value) return;
    follow.value = false;
    writeStored(FOLLOW_KEY, 'false');
    followNote.value = note;
  }

  /* 修订 3：任何明确的用户视图操作（选图、缩放、平移、分屏、切换目标）
   * 立即退出跟随。跟随自己打开影像时 origin='follow'，不会触发这里。 */
  watch(() => useViewerStore().userIntentSeq, () => {
    exitFollow('已按你的操作退出跟随。');
  });

  /* 绘制期间只暂停导航，不改写 ROI / 掩码 / 批注；退出绘制后补上。 */
  watch(() => useViewerStore().drawMode, (drawing) => {
    if (!drawing && follow.value) void navigateToCurrent();
  });

  watch(() => {
    const cur = snapshot.value?.producer.current;
    return cur ? `${cur.item_id}` : '';
  }, () => {
    if (follow.value) void navigateToCurrent();
  });

  function errorText(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  registerViewerObservation('batch_goal', () => goalId.value ? { goal_id: goalId.value } : null);
  return {
    goalId, snapshot, feedback, claimFeedback, events, historyPage, availableGoals,
    syncState, syncError, loadError, controlError, controlBusy,
    loadingEarlier, hasEarlier, consumedSeq,
    follow, followNote, followError,
    hasGoal, isVisible, isTerminal, isDone, paused,
    associate, discover, selectGoal, attach, detach,
    refreshSnapshot, catchUp, loadEarlier, control,
    setFollow, navigateToCurrent, openItem, clearOverlay,
    scheduleRefresh,
  };
});
