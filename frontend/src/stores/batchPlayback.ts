/** Ordered presentation of real batch results. Detection/SR never waits on UI. */
import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import { apiBatchItems } from '../lib/api';
import { loadBatchOverlay, overlayMatchesImage } from '../lib/batchOverlay';
import type { BatchOverlayDoc } from '../lib/batchOverlay';
import { imageRef } from '../lib/spatial';
import { useBatchGoalStore } from './batchGoal';
import { useScenesStore } from './scenes';
import { useViewerStore } from './viewer';

export type PlaybackPhase = 'idle' | 'loading' | 'detecting' | 'drawing' | 'holding' | 'waiting' | 'paused' | 'done';

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}

export const useBatchPlaybackStore = defineStore('batchPlayback', () => {
  const phase = ref<PlaybackPhase>('idle');
  const name = ref('');
  const ordinal = ref(0);
  const completed = ref(0);
  const note = ref('');
  const doc = shallowRef<BatchOverlayDoc | null>(null);
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
    idle: '逐景展示', loading: '加载影像', detecting: '等待识别结果', drawing: '绘制掩码',
    holding: '本景结果', waiting: '等待下一景', paused: '展示已暂停', done: '展示完成',
  };
  const label = computed(() => labels[phase.value]);

  function stop() {
    controller?.abort(); controller = null;
    running.value = false;
    if (phase.value !== 'done' && phase.value !== 'idle') phase.value = 'paused';
  }
  function skip() { skipped = true; }
  function hideOverlay() {
    stop(); doc.value = null; regionIndex.value = -1;
  }
  function reset() {
    stop(); goal = ''; cursor = 0; completed.value = 0;
    doc.value = null; name.value = ''; ordinal.value = 0; note.value = ''; phase.value = 'idle';
  }

  async function start() {
    const batch = useBatchGoalStore(), viewer = useViewerStore(), scenes = useScenesStore();
    const gid = batch.goalId;
    if (!gid || running.value || !mounted.value) return;
    if (gid !== goal) { reset(); goal = gid; }
    const abort = new AbortController(); controller = abort;
    const signal = abort.signal;
    const intent = viewer.userIntentSeq;
    const wanted = () => controller === abort && !signal.aborted && batch.goalId === gid && batch.follow
      && !viewer.drawMode && viewer.userIntentSeq === intent;
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
        try { page = await apiBatchItems(gid, cursor, '', signal); }
        catch (e) {
          if (!wanted()) break;
          note.value = `连接暂不可用，正在重试：${e instanceof Error ? e.message : String(e)}`;
          phase.value = 'waiting'; await delay(2000, signal); continue;
        }
        if (!wanted()) break;
        const rows = page.items.filter(row => row.ordinal > cursor).sort((a,b) => a.ordinal-b.ordinal);
        const row = rows[0];
        if (!row) {
          phase.value = batch.isTerminal ? 'done' : 'waiting';
          if (batch.isTerminal) break;
          await delay(1500, signal); continue;
        }
        if (presenting !== row.item_id) {
          presenting = row.item_id;
          doc.value = null; regionIndex.value = -1; skipped = false;
          name.value = row.name; ordinal.value = row.ordinal; note.value = '';
        }
        const advance = () => {
          if (!wanted()) return;
          cursor = row.ordinal; completed.value++; opened = '';
          loadFailures = 0; overlayFailures = 0;
        };
        const unavailable = !row.checkpoint.mask && row.status !== 'NO_ISSUE'
          && (batch.isTerminal || ['FAILED','BLOCKED','SKIPPED','REVIEW_REJECTED','CANCELLED'].includes(row.status));
        if (skipped || unavailable) {
          note.value = skipped ? '已跳过本景展示，后台任务继续运行。' : row.error || '本景未生成可展示的掩码。';
          advance(); await delay(800, signal); continue;
        }
        const path = row.checkpoint.target_image?.path;
        if (!path) { phase.value = 'waiting'; await delay(1500, signal); continue; }
        const nextPath = rows[1]?.checkpoint.target_image?.path;
        if (nextPath) void scenes.preloadQuiet(nextPath);
        if (opened !== row.item_id) {
          phase.value = 'loading';
          const error = await scenes.openQuiet(path, 'follow', { stillWanted: wanted });
          if (!wanted()) break;
          if (error) {
            note.value = error;
            if (++loadFailures >= 2) { advance(); await delay(1500, signal); }
            else await delay(1500, signal);
            continue;
          }
          opened = row.item_id;
          viewer.batchOverlayOff = true;
          // Show the full scene first, then visit each repair region.
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
        if (!row.checkpoint.detection || !row.checkpoint.mask) {
          phase.value = 'detecting'; await delay(1200, signal); continue;
        }
        phase.value = 'detecting';
        const overlay = await loadBatchOverlay({ goalId: gid, itemId: row.item_id }, { signal });
        if (!wanted()) break;
        if (overlay.status !== 'ready') {
          note.value = '区域结果暂不可用，正在重新加载。';
          if (++overlayFailures >= 3) { note.value = '区域结果加载失败，已跳过本景展示。'; advance(); }
          await delay(1200, signal); continue;
        }
        const pane = viewer.panes.find(p => p.rec && overlayMatchesImage(overlay.doc, imageRef(p.rec)));
        if (!pane?.rec?.thumb) {
          note.value = '影像版本与检测结果不一致，已跳过展示。';
          advance(); await delay(1200, signal); continue;
        }
        doc.value = overlay.doc;
        const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        duration.value = reduced ? 0 : 900;
        for (let i=0; i<overlay.doc.candidates.length && wanted() && !skipped; i++) {
          const candidate = overlay.doc.candidates[i];
          const points = candidate.points?.length ? candidate.points : candidate.bbox ? [
            [candidate.bbox[0],candidate.bbox[1]],
            [candidate.bbox[2],candidate.bbox[3]],
          ] : [];
          if (!points.length) continue;
          const xs = points.map(p=>p[0]), ys = points.map(p=>p[1]);
          const x=Math.min(...xs), y=Math.min(...ys), w=Math.max(...xs)-x, h=Math.max(...ys)-y;
          const r = pane.rec;
          phase.value = 'loading';
          viewer.focusPaneRect(pane.side, [x/r.W*r.thumb!.width, y/r.H*r.thumb!.height,
            Math.max(1,w)/r.W*r.thumb!.width, Math.max(1,h)/r.H*r.thumb!.height], false);
          await delay(reduced ? 0 : 350, signal);
          if (!wanted() || skipped) break;
          regionIndex.value = i; phase.value = 'drawing';
          const area = w*h/(r.W*r.H)*100;
          note.value = `区域 ${i+1} / ${overlay.doc.candidates.length} · 包围范围 ${area.toFixed(1)}%`;
          await delay(duration.value + 350, signal);
        }
        if (!wanted()) break;
        phase.value = 'holding';
        if (!skipped) note.value = `${overlay.doc.candidates.length} 个掩码区域，扩大范围请人工复核。`;
        await delay(1000, signal);
        if (wanted()) advance();
      }
    } catch (e) {
      if (!signal.aborted) {
        note.value = `展示已暂停：${e instanceof Error ? e.message : String(e)}。可继续重试本景。`;
        batch.setFollow(false);
      }
    } finally {
      if (controller === abort) {
        controller = null; running.value = false;
        if (phase.value !== 'done') phase.value = 'paused';
        else batch.setFollow(false);
      }
    }
  }
  return { phase, name, ordinal, completed, note, doc, regionIndex, duration, running, mounted,
    active, label, start, stop, skip, reset, hideOverlay };
});
