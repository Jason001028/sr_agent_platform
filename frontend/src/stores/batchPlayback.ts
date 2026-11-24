/** Ordered presentation of real batch results. Detection/SR never waits on UI. */
import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import { apiBatchItems } from '../lib/api';
import { detectionCandidatePreview, loadBatchOverlay, overlayMatchesImage } from '../lib/batchOverlay';
import type { BatchCandidatePreview } from '../lib/batchOverlay';
import { imageRef } from '../lib/spatial';
import { useBatchGoalStore } from './batchGoal';
import { useBatchMaskReviewStore } from './batchMaskReview';
import { useScenesStore } from './scenes';
import { useViewerStore } from './viewer';
import { agentRepairSettings } from '../lib/agentRepairSettings';

export type PlaybackPhase = 'idle' | 'loading' | 'quality' | 'detecting' | 'drawing' | 'holding' | 'waiting' | 'paused' | 'done';

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}

function presentationStep<T>(
  signal: AbortSignal, label: string, timeoutMs: number,
  run: (stepSignal: AbortSignal) => Promise<T>,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const step = new AbortController();
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
    };
    const fail = (error: unknown) => { finish(); reject(error); };
    const cancel = () => { step.abort(); fail(new Error('展示已停止')); };
    const timer = setTimeout(() => {
      step.abort();
      fail(new Error(`${label}超时`));
    }, timeoutMs);
    if (signal.aborted) { cancel(); return; }
    signal.addEventListener('abort', cancel, { once: true });
    Promise.resolve().then(() => {
      if (step.signal.aborted) throw new Error('展示已停止');
      return run(step.signal);
    }).then(value => { finish(); resolve(value); }, fail);
  });
}

export const useBatchPlaybackStore = defineStore('batchPlayback', () => {
  const phase = ref<PlaybackPhase>('idle');
  const name = ref('');
  const ordinal = ref(0);
  const completed = ref(0);
  const note = ref('');
  const doc = shallowRef<BatchCandidatePreview | null>(null);
  const detectionOnly = ref(false);
  const regionIndex = ref(-1);
  const duration = ref(900);
  const running = ref(false);
  const mounted = ref(false);
  let controller: AbortController | null = null;
  let goal = '';
  let cursor = 0;
  let skipped = false;
  const active = computed(() => mounted.value && running.value);
  const labels: Record<PlaybackPhase, string> = {
    idle: '逐景展示', loading: '加载影像', quality: '质检预览', detecting: '等待识别结果', drawing: '绘制掩码',
    holding: '本景结果', waiting: '等待下一景', paused: '展示已暂停', done: '展示完成',
  };
  const label = computed(() => labels[phase.value]);

  function stop(reason = '') {
    controller?.abort(); controller = null;
    running.value = false;
    if (phase.value !== 'done' && phase.value !== 'idle') {
      if (phase.value !== 'paused' || reason) note.value = reason;
      phase.value = 'paused';
    }
  }
  function skip() { skipped = true; }
  function hideOverlay() {
    reset();
  }
  function reset() {
    stop(); goal = ''; cursor = 0; completed.value = 0;
    doc.value = null; detectionOnly.value = false; regionIndex.value = -1; name.value = ''; ordinal.value = 0; note.value = ''; phase.value = 'idle';
  }

  async function start() {
    const batch = useBatchGoalStore(), viewer = useViewerStore(), scenes = useScenesStore();
    const gid = batch.goalId;
    if (!gid || batch.paused || batch.snapshot?.goal.status === 'CANCELLED' || running.value || !mounted.value) return;
    if (gid !== goal) { reset(); goal = gid; }
    const abort = new AbortController(); controller = abort;
    const signal = abort.signal;
    const intent = viewer.userIntentSeq;
    const wanted = () => controller === abort && !signal.aborted && batch.goalId === gid && batch.follow
      && !batch.paused && batch.snapshot?.goal.status !== 'CANCELLED'
      && !viewer.drawMode && viewer.userIntentSeq === intent;
    note.value = '';
    phase.value = 'waiting';
    running.value = true;
    // Resume the interrupted scene; only a fully presented/skipped scene
    // advances cursor. Pagination cannot lose fast backend transitions.
    let opened = '';
    let presenting = '';
    let loadFailures = 0;
    let overlayFailures = 0;
    try {
      while (wanted()) {
        let page;
        page = await presentationStep(signal, '读取展示队列', 30_000, step => apiBatchItems(gid, cursor, '', step, 2));
        if (!wanted()) break;
        const rows = page.items.filter(row => row.ordinal > cursor).sort((a,b) => a.ordinal-b.ordinal);
        const row = rows[0];
        if (!row) {
          phase.value = batch.isTerminal ? 'done' : 'waiting';
          note.value = '';
          if (batch.isTerminal) break;
          await delay(1500, signal); continue;
        }
        if (presenting !== row.item_id) {
          presenting = row.item_id;
          doc.value = null; detectionOnly.value = false; regionIndex.value = -1; skipped = false;
          name.value = row.name; ordinal.value = row.ordinal; note.value = '';
        }
        const advance = () => {
          if (!wanted()) return;
          cursor = row.ordinal; completed.value++; opened = '';
          loadFailures = 0; overlayFailures = 0;
        };
        const path = row.checkpoint.target_image?.path;
        const rowDone = batch.isTerminal
          || ['SUCCEEDED','NO_ISSUE','FAILED','BLOCKED','SKIPPED','REVIEW_REJECTED','CANCELLED'].includes(row.status);
        let candidatePreview = rowDone && !row.checkpoint.mask
          ? detectionCandidatePreview(row.checkpoint.target_image, row.checkpoint.detection) : null;
        const hasResult = row.status === 'NO_ISSUE' || !!(row.checkpoint.detection && row.checkpoint.mask) || !!candidatePreview;
        const unavailable = rowDone && (!path || !hasResult);
        if (skipped || unavailable) {
          phase.value = 'holding';
          note.value = skipped ? '已跳过本景展示。' : row.error || '本景缺少可展示的影像或检测结果，已跳过。';
          advance(); await delay(800, signal); continue;
        }
        if (!path) { phase.value = 'waiting'; note.value = '等待本景影像就绪。'; await delay(1500, signal); continue; }
        const nextPath = rows[1]?.checkpoint.target_image?.path;
        if (nextPath) void scenes.preloadQuiet(nextPath).catch(() => {});
        if (opened !== row.item_id) {
          phase.value = 'loading';
          const error = await presentationStep(signal, '加载影像', 60_000, step =>
            scenes.openQuiet(path, 'follow', { stillWanted: () => !step.aborted && wanted() }));
          if (!wanted()) break;
          if (error) {
            note.value = error;
            if (++loadFailures >= 2) { advance(); await delay(1500, signal); }
            else await delay(1500, signal);
            continue;
          }
          opened = row.item_id;
          note.value = '';
          viewer.batchOverlayOff = true;
          // Show the full scene; each scene is presented without per-region zoom.
          viewer.fit();
          await delay(350, signal);
        }
        if (!wanted()) break;
        if (row.status === 'NO_ISSUE') {
          phase.value = 'holding'; note.value = '未发现满足条件的差异区域。';
          await delay(1000, signal);
          if (!wanted()) break;
          advance(); continue;
        }
        if (!hasResult) {
          phase.value = 'detecting'; note.value = row.checkpoint.phase === 'MASK_PENDING'
            ? '检测完成，等待本景掩码发布。' : '等待后台生成本景检测结果与掩码。';
          await delay(1200, signal); continue;
        }
        phase.value = 'detecting';
        const overlay = candidatePreview ? null
          : await presentationStep(signal, '读取掩码区域', 30_000, step =>
            loadBatchOverlay({ goalId: gid, itemId: row.item_id }, { signal: step }));
        if (!wanted()) break;
        if (overlay?.status === 'error' && ++overlayFailures >= 3) {
          candidatePreview = detectionCandidatePreview(row.checkpoint.target_image, row.checkpoint.detection);
        }
        const displayed = overlay?.status === 'ready' ? overlay.doc : candidatePreview;
        if (!displayed) {
          note.value = '区域结果暂不可用，正在重新加载。';
          if (overlayFailures >= 3) { note.value = '区域结果加载失败，已跳过本景展示。'; advance(); }
          await delay(1200, signal); continue;
        }
        doc.value = displayed;
        detectionOnly.value = !!candidatePreview;
        const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        duration.value = reduced ? 0 : 900;
        if (!candidatePreview && agentRepairSettings.value.auto_play_preview) {
          const review = useBatchMaskReviewStore();
          const close = () => {
            if (!review.previewing) return;
            review.close();
            if (viewer.batchOverlayTarget?.goalId === gid && viewer.batchOverlayTarget.itemId === row.item_id) {
              viewer.batchOverlayOff = true;
              viewer.batchOverlayTarget = null;
            }
          };
          phase.value = 'quality';
          note.value = '质检预览：双图分屏对比与热力图';
          signal.addEventListener('abort', close, { once: true });
          try {
            const shown = await presentationStep(signal, '加载质检预览', 8_000, step => {
              step.addEventListener('abort', close, { once: true });
              return review.preview(row).finally(() => step.removeEventListener('abort', close));
            });
            if (shown && wanted()) await delay(reduced ? 300 : 1800, signal);
          } catch {
            // The optional comparison can fail while the published mask remains drawable.
          } finally {
            signal.removeEventListener('abort', close);
            if (controller === abort) close();
          }
          if (!wanted()) break;
          viewer.batchOverlayOff = true;
          viewer.batchOverlayTarget = null;
          if (!wanted()) break;
          viewer.fit();
          await delay(reduced ? 0 : 250, signal);
          if (!wanted()) break;
        }
        const pane = viewer.panes.find(p => p.rec && overlayMatchesImage(displayed, imageRef(p.rec)));
        if (!pane?.rec?.thumb) {
          note.value = '影像版本与检测结果不一致，已跳过展示。';
          advance(); await delay(1200, signal); continue;
        }
        const total = displayed.candidates.length;
        // The drawing stage stays at the whole-scene view: the relative
        // position of every candidate is the useful information, not a
        // zoomed-in crop of one region. Only the polygons are traced.
        viewer.fit();
        await delay(reduced ? 0 : 250, signal);
        if (!wanted()) break;
        const r = pane.rec;
        const drawnCount = total > 24 ? 8 : total;
        for (let i=0; i<drawnCount && wanted() && !skipped; i++) {
          const candidate = displayed.candidates[i];
          const xs = candidate.points?.length ? candidate.points.map(p=>p[0])
            : candidate.bbox ? [candidate.bbox[0],candidate.bbox[2]] : [];
          const ys = candidate.points?.length ? candidate.points.map(p=>p[1])
            : candidate.bbox ? [candidate.bbox[1],candidate.bbox[3]] : [];
          regionIndex.value = i; phase.value = 'drawing';
          if (!xs.length || !ys.length) { note.value = `区域 ${i+1} / ${total}`; continue; }
          const area = (Math.max(...xs)-Math.min(...xs))*(Math.max(...ys)-Math.min(...ys))/(r.W*r.H)*100;
          note.value = `区域 ${i+1} / ${total} · 包围范围 ${area.toFixed(1)}%`;
          await delay(duration.value + 350, signal);
        }
        if (!wanted()) break;
        if (!skipped && total > drawnCount) {
          regionIndex.value = total - 1;
          phase.value = 'drawing';
          note.value = `整景展示全部 ${total} 个区域`;
          await delay(duration.value + 350, signal);
          if (!wanted()) break;
        }
        phase.value = 'holding';
        if (!skipped) note.value = candidatePreview
          ? row.error || `${total} 个检测候选，本次展示未读取到已发布的掩码。`
          : `${total} 个掩码区域，扩大范围请人工复核。`;
        await delay(1000, signal);
        if (wanted()) advance();
      }
    } catch (e) {
      if (wanted()) {
        phase.value = 'paused';
        note.value = `${e instanceof Error ? e.message : String(e)}。可继续重试。`;
        batch.setFollow(false);
      }
    } finally {
      if (controller === abort) {
        controller = null; running.value = false;
        if (phase.value !== 'done') {
          if (phase.value !== 'paused') {
            phase.value = 'paused';
            note.value = '展示已停止，可继续重试。';
          }
        }
        if (batch.follow) batch.setFollow(false);
      }
    }
  }
  return { phase, name, ordinal, completed, note, doc, detectionOnly, regionIndex, duration, running, mounted,
    active, label, start, stop, skip, reset, hideOverlay };
});
