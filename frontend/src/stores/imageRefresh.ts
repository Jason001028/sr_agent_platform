import { computed, ref, watch } from 'vue';
import { defineStore } from 'pinia';
import { apiImageRefresh, fetchImageRefreshPreview } from '../lib/api';
import type { ImageRefreshResult } from '../lib/api';
import type { SceneOpenMeta } from '../lib/scene';
import { useQueueStore, normDir } from './queue';
import { useViewerStore } from './viewer';

export interface RefreshEntry { result: ImageRefreshResult; unread: boolean; recId: number; }
const READ_KEY = 'sr.imageRefresh.read.v1';
const MAX_READ = 512;
export function resultVersion(result: ImageRefreshResult): string | null {
  return result.state === 'ready' && result.row?.hasPreview && result.row.previewDiv
    && result.row.id && result.row.srcRev && result.version === `${result.row.id}|${result.row.srcRev}` ? result.version : null;
}
export const useImageRefreshStore = defineStore('imageRefresh', () => {
  const entries = ref<Record<number, RefreshEntry>>({});
  const preparing = ref<Record<string, boolean>>({});
  const errors = ref<Record<number, string>>({});
  const opening = ref<number | null>(null);
  const visibleIds = ref<number[]>([]);
  const nearIds = ref<number[]>([]);
  let read: string[] = [];
  try { const saved = JSON.parse(localStorage.getItem(READ_KEY) || '[]'); if (Array.isArray(saved)) read = saved.filter(x => typeof x === 'string').slice(-MAX_READ); } catch { /* optional local preference */ }
  let active = false, scanning = false, again = false, poll: ReturnType<typeof setInterval> | undefined;
  let stopWatch: (() => void) | undefined;
  const signatures = new Map<number, string>();
  const pendingTasks = new Map<number, string>();
  const dismissed = new Set<string>();
  const warmDone = new Set<string>();
  let warming = false;
  let warmController: AbortController | undefined;
  let warmingId: number | undefined;
  const unreadCount = computed(() => Object.values(entries.value).filter(e => e.unread).length);

  function meta(result: ImageRefreshResult): SceneOpenMeta {
    const row = result.row!;
    return { name: row.name, W: row.W!, H: row.H!, sceneId: row.id,
      sceneDir: result.group, lqPath: result.kind === 'input' ? result.group! : null,
      stageKind: result.kind, srcRev: row.srcRev };
  }
  function accept(result: ImageRefreshResult) {
    const version = resultVersion(result);
    if (!version || !result.row?.W || !result.row.H || !result.group || dismissed.has(version)) return;
    const viewer = useViewerStore();
    const anchor = viewer.recs.find(r => normDir(r.sceneDir || r.lqPath || '') === normDir(result.group!));
    if (!anchor) return; // Never import the whole scene library / old queue into the workspace.
    const existing = viewer.recs.find(r => !!r.sceneId && (r.sceneId === result.row!.id || result.aliases?.includes(r.sceneId)));
    const recId = existing?.id ?? viewer.insertSceneCard(meta(result), result.row, anchor.id, true);
    const current = entries.value[recId];
    if (current && (current.result.finished_at ?? 0) > (result.finished_at ?? 0)) return;
    if (current?.result.version === version) { current.result = result; return; }
    entries.value[recId] = { recId, result, unread: !read.includes(version) };
    delete errors.value[recId];
  }
  function prune() {
    const ids = new Set(useViewerStore().recs.map(r => r.id));
    for (const [id, entry] of Object.entries(entries.value)) if (!ids.has(Number(id))) {
      if (entry.result.version) dismissed.add(entry.result.version);
      delete entries.value[Number(id)];
    }
  }
  async function scan(retry = false): Promise<void> {
    if (!active || document.hidden) return;
    if (scanning) { again = true; return; }
    scanning = true;
    try {
      prune();
      const groups = new Set(useViewerStore().recs.map(r => normDir(r.sceneDir || r.lqPath || '')).filter(Boolean));
      for (const [id, entry] of Object.entries(entries.value)) {
        const task = useQueueStore().tasks.find(t => t.task_id === entry.result.task_id);
        if (task && task.state !== 'COMPLETED') delete entries.value[Number(id)];
      }
      const tasks = useQueueStore().tasks.filter(t => t.state === 'COMPLETED' && groups.has(normDir(t.params.lq_path)))
        .sort((a, b) => (a.finished_at || a.updated_at) - (b.finished_at || b.updated_at));
      const taskIds = new Set(tasks.map(t => t.task_id));
      for (const id of pendingTasks.keys()) if (!taskIds.has(id)) pendingTasks.delete(id);
      const jobs = tasks.filter(t => {
        const key = `${t.updated_at}|${t.finished_at}|${t.preview_state}`;
        // Recheck real source versions even when the task row itself did not change.
        if (signatures.get(t.task_id) === key && !retry) return false;
        signatures.set(t.task_id, key); return true;
      });
      // Two metadata requests; JPEG warmup has a separate single compressed-byte slot.
      let cursor = 0;
      await Promise.all([0, 1].map(async () => {
        while (active && cursor < jobs.length) {
          const task = jobs[cursor++]; if (!task) break;
          try {
            const result = await apiImageRefresh(task.task_id);
            if (!active) return;
            const latest = useQueueStore().tasks.find(t => t.task_id === task.task_id);
            if (!latest || latest.state !== 'COMPLETED' || latest.updated_at !== task.updated_at) continue;
            const group = normDir(task.params.lq_path);
            if (result.state !== 'ready') for (const [id, entry] of Object.entries(entries.value)) {
              if (entry.result.task_id === task.task_id) delete entries.value[Number(id)];
            }
            pendingTasks.delete(task.task_id);
            if (result.state === 'preparing') pendingTasks.set(task.task_id, group);
            else if (result.state === 'ready') accept(result);
          } catch { signatures.delete(task.task_id); }
        }
      }));
      // A ready older result must not hide a newer job still preparing in the same group.
      preparing.value = Object.fromEntries([...pendingTasks.values()].map(group => [group, true]));
      void warm();
    } finally { scanning = false; if (again) { again = false; void scan(); } }
  }
  function setVisible(visible: number[], near = visible) {
    visibleIds.value = visible; nearIds.value = near;
    if (document.hidden || warmingId != null && !near.includes(warmingId)) warmController?.abort();
    void warm();
  }
  async function warm() {
    if (warming || !active || document.hidden) return;
    warming = true;
    try {
      // Re-evaluate after each request: scrolling must not warm an obsolete viewport.
      while (active && !document.hidden) {
        const id = nearIds.value.find(id => {
          const e = entries.value[id], version = e && resultVersion(e.result);
          return e?.unread && !!version && !warmDone.has(version);
        });
        if (id == null) break;
        const entry = entries.value[id], version = entry && resultVersion(entry.result);
        if (!active || document.hidden) break;
        if (!entry?.unread || !version || warmDone.has(version)) continue;
        warmDone.add(version);
        warmingId = id;
        warmController = new AbortController();
        try { await fetchImageRefreshPreview(entry.result, warmController.signal, 32 * 1024 * 1024); }
        catch { if (warmController.signal.aborted) warmDone.delete(version); }
      }
    } finally { warming = false; warmController = undefined; warmingId = undefined; }
  }
  async function open(id: number): Promise<boolean> {
    const entry = entries.value[id], version = entry && resultVersion(entry.result);
    if (!entry || !version || opening.value !== null) return false;
    const viewer = useViewerStore();
    if (viewer.drawMode || viewer.busy) { errors.value[id] = '请先完成当前加载或退出绘制，再查看新图'; return false; }
    viewer.noteUserIntent();
    const intent = viewer.userIntentSeq;
    const wanted = () => viewer.userIntentSeq === intent && !viewer.drawMode && entries.value[id]?.result.version === version;
    opening.value = id; delete errors.value[id];
    try {
      // Reconfirm the actual source revision; no stale badge can open an overwritten file.
      const fresh = await apiImageRefresh(entry.result.task_id);
      if (resultVersion(fresh) !== version) { if (fresh.state === 'ready') accept(fresh); throw new Error('成果版本已更新，请核对新版本后再打开'); }
      const blob = await fetchImageRefreshPreview(fresh);
      if (!wanted()) return false;
      const shown = await viewer.displayImageRefresh(id, meta(fresh), fresh.row!, blob, wanted);
      if (!shown || !wanted()) return false;
      entry.unread = false;
      read = [...read.filter(x => x !== version), version].slice(-MAX_READ);
      try { localStorage.setItem(READ_KEY, JSON.stringify(read)); } catch { /* session read state still works */ }
      return true;
    } catch (e) { errors.value[id] = e instanceof Error ? e.message : String(e); return false; }
    finally { opening.value = null; }
  }
  function groupCount(group: string) { return Object.values(entries.value).filter(e => e.unread && normDir(e.result.group || '') === normDir(group)).length; }
  function onVisibility() { if (document.hidden) warmController?.abort(); else { void scan(true); void warm(); } }
  function start() {
    if (active) return; active = true;
    const queue = useQueueStore(), viewer = useViewerStore();
    stopWatch = watch(() => [queue.tasks.map(t => `${t.task_id}:${t.state}:${t.updated_at}:${t.preview_state}`).join(','), viewer.recs.map(r => `${r.id}:${r.sceneDir}`).join(',')], () => { void scan(); }, { immediate: true });
    void queue.list();
    poll = setInterval(() => { if (!document.hidden && viewer.recs.some(r => r.sceneDir)) { void queue.list(); void scan(true); } }, 15000);
    document.addEventListener('visibilitychange', onVisibility);
  }
  function stop() { active = false; stopWatch?.(); if (poll) clearInterval(poll); warmController?.abort(); document.removeEventListener('visibilitychange', onVisibility); }
  return { entries, preparing, errors, opening, unreadCount, visibleIds, nearIds, accept, scan, setVisible, open, groupCount, start, stop };
});
