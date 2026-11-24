import { computed, markRaw, nextTick, ref, shallowRef, watch } from 'vue';
import { defineStore } from 'pinia';
import { useViewerStore } from './viewer';
import { useBatchGoalStore } from './batchGoal';
import { useDiffStore, colorizeHeat } from './diff';
import { apiBatchItems, apiBatchReview, apiSceneSiblings } from '../lib/api';
import type { BatchItemRecord } from '../lib/api';
import { loadSrConfig } from '../lib/scene';
import { loadBatchOverlay, overlayMatchesImage } from '../lib/batchOverlay';
import type { BatchOverlayDoc } from '../lib/batchOverlay';
import { imageRef, resolveSceneIdentity } from '../lib/spatial';

function decodePng(url: string): Promise<HTMLCanvasElement> {
  return new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => {
      const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      canvas.getContext('2d')!.drawImage(image, 0, 0); resolve(canvas);
    }; image.onerror = () => reject(new Error('热力图加载失败')); image.src = url;
  });
}

/** Pre-SR geometry review; decisions stay bound to the server Item/version. */
export const useBatchMaskReviewStore = defineStore('batchMaskReview', () => {
  const viewer = useViewerStore(), batch = useBatchGoalStore(), diff = useDiffStore();
  const active = ref(false), busy = ref(false), previewing = ref(false), error = ref(''), notice = ref('');
  const item = shallowRef<BatchItemRecord | null>(null), document = shallowRef<BatchOverlayDoc | null>(null);
  const pair = shallowRef<{pan: number; nosr: number} | null>(null);
  const heat = shallowRef<HTMLCanvasElement | null>(null), regionId = ref('');
  let generation = 0;
  // Compare mode collapses the context rail; its width transition resizes the
  // canvas and runs Viewer.fit(). Focus only after that existing layout settles.
  async function settledLayout() {
    await nextTick();
    let previous = '', stable = 0;
    for (let frame = 0; frame < 32 && stable < 3; frame++) {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const size = viewer.panes.map(p => `${p.rect.w}:${p.rect.h}`).join('|');
      stable = size === previous ? stable+1 : 0; previous = size;
    }
  }
  const regions = computed(() => document.value?.candidates ?? []);
  const selected = computed(() => regions.value.find(r => r.candidate_id === regionId.value));
  const pending = computed(() => regions.value.filter(r => !item.value?.checkpoint.region_reviews?.[r.candidate_id]));
  const ready = computed(() => {
    const p = pair.value;
    if (!p || viewer.drawMode) return false;
    const pan = viewer.recs.find(r => r.id === p.pan), nosr = viewer.recs.find(r => r.id === p.nosr);
    if (!pan?.thumb || !nosr?.thumb || !document.value || !overlayMatchesImage(document.value,imageRef(pan))) return false;
    const revision = item.value?.checkpoint.reference_image?.revision;
    if (revision && nosr.srcRev !== revision) return false;
    const ids = viewer.compareMode === 'flicker' ? [viewer.flickerRefRec?.id, viewer.flickerAltRec?.id]
      : viewer.split ? viewer.panes.map(p => p.rec?.id) : [];
    return ids.includes(p.pan) && ids.includes(p.nosr);
  });
  function close() {
    const wasPreview = previewing.value;
    generation++; active.value = false; busy.value = false; previewing.value = false;
    pair.value = null; heat.value = null; document.value = null; item.value = null;
    if (wasPreview) {
      viewer.setCompareMode('off', true);
      viewer.repairOverlaySelected = false;
      viewer.overlayMasterVisible = false;
      diff.resultVisible = false;
    }
  }
  function focus(id: string) {
    regionId.value = id;
    const c = selected.value, d = document.value;
    if (!c?.bbox || !d || !ready.value) return;
    const [x0,y0,x1,y1] = c.bbox, w = d.source.width, h = d.source.height;
    const pane = viewer.panes[0], rec = viewer.compareMode === 'flicker' ? viewer.flickerRefRec : pane.rec;
    if (!rec?.thumb) return;
    const scale = Math.min(pane.rect.w * .72 / Math.max(1,(x1-x0+1)/w*rec.thumb.width),
      pane.rect.h * .72 / Math.max(1,(y1-y0+1)/h*rec.thumb.height));
    viewer.stopViewAnim();
    if (viewer.compareMode === 'flicker') viewer.setTourCamera({ scale, ox:pane.rect.w/2-(x0+x1)/2/w*rec.thumb.width*scale, oy:pane.rect.h/2-(y0+y1)/2/h*rec.thumb.height*scale });
    else for (const p of viewer.panes) {
      if (!p.rec?.thumb) continue;
      const s = scale * rec.thumb.width/p.rec.thumb.width;
      viewer.setPaneView(p.side, {scale:s, ox:p.rect.w/2-(x0+x1)/2/w*p.rec.thumb.width*s, oy:p.rect.h/2-(y0+y1)/2/h*p.rec.thumb.height*s});
    }
  }
  function navigate(direction: number) {
    const list = pending.value.length ? pending.value : regions.value;
    const i = list.findIndex(r => r.candidate_id === regionId.value);
    const next = list[(i + direction + list.length) % list.length]; if (next) focus(next.candidate_id);
  }
  /** Open the same split-screen + heat visuals for an already selected item.
   *  `silent` keeps the playback from treating this as a user gesture. */
  async function loadVisuals(current: BatchItemRecord, gen: number, silent: boolean): Promise<void> {
    const gid = batch.goalId;
    if (!gid) throw new Error('没有关联的修复任务');
    const wanted = () => gen === generation && batch.goalId === gid;
    item.value = current;
    const target = {item_id:current.item_id, image_path:current.checkpoint.target_image?.path, overlay_ready:true};
    const open = () => silent ? batch.openItem(target, wanted) : batch.openItem(target);
    const message = await (silent ? viewer.withSilentIntent(open) : open());
    if (message) throw new Error(message);
    if (gen !== generation || batch.goalId !== gid) return;
    const pan = viewer.activeRec;
    if (!pan?.sceneId) throw new Error('PAN 尚未关联服务端影像');
    await resolveSceneIdentity(pan);
    if (!wanted()) return;
    const intent = viewer.userIntentSeq;
    const siblings = await apiSceneSiblings(loadSrConfig(), pan.sceneId);
    if (!wanted()) return;
    const nosr = siblings.items.find(r => r.kind === 'nosr' && r.exists);
    if (!nosr) throw new Error('未找到对应的 NOSR 参考图');
    const id = viewer.ensureSceneImage(nosr, siblings);
    if (id == null || !await viewer.ensureCardPixels(id)) throw new Error('NOSR 参考图加载失败');
    if (gen !== generation || batch.goalId !== gid) return;
    if (intent !== viewer.userIntentSeq || viewer.drawMode) throw new Error('已按新的视图操作取消复核定位');
    const overlay = await loadBatchOverlay({goalId:gid,itemId:current.item_id});
    if (overlay.status !== 'ready') throw new Error('候选修复范围尚未就绪，请重试');
    if (gen !== generation || batch.goalId !== gid) return;
    if (!overlayMatchesImage(overlay.doc, imageRef(pan))) throw new Error('候选掩码与当前 PAN 版本不一致');
    const opened = silent ? await viewer.openPreviewPair(pan.id, id, true, wanted) : await viewer.openPreviewPair(pan.id, id);
    if (!opened) throw new Error('无法打开 PAN / NOSR 分屏');
    if (gen !== generation || batch.goalId !== gid) return;
    viewer.splitLinked = true; viewer.overlayMasterVisible = true; viewer.repairOverlaySelected = true; diff.resultVisible = true;
    pair.value = {pan:pan.id,nosr:id}; document.value = overlay.doc;
    regionId.value = pending.value[0]?.candidate_id ?? regions.value[0]?.candidate_id ?? '';
    await settledLayout();
    if (gen !== generation || batch.goalId !== gid) return;
    focus(regionId.value);
    const analysis = current.checkpoint.detection?.analysis;
    if (analysis?.differenceMap) {
      const [raw, valid] = await Promise.all([decodePng(analysis.weightedDifferenceMap || analysis.differenceMap), analysis.validityMap ? decodePng(analysis.validityMap) : Promise.resolve(null)]);
      if (gen === generation) heat.value = markRaw(colorizeHeat(raw, valid));
    } else if (silent) {
      // 自动播放只展示 Agent 自己的差异图；Viewer 预览重算的范围与候选可能
      // 不同，不能拿来给 Agent 的掩码轮廓配一张来源不一致的热力图。
      notice.value = '该景未提供 Agent 差异图，仅显示候选轮廓';
    } else if (diff.result && !diff.stale && diff.result.imageA.id === pan.id && diff.result.imageB.id === id) {
      heat.value = diff.heatLayer;
    } else {
      // Old checkpoints have no continuous map. Reuse the read-only difference pipeline.
      diff.setPair(pan.id,id); diff.scope = 'common'; diff.display = 'overlay'; diff.opacity = .25;
      if (await diff.analyze() && gen === generation) heat.value = diff.heatLayer;
      else if (gen === generation) notice.value = diff.error || '热力图暂不可用，候选范围仍可复核';
    }
  }

  async function open(itemId?: string) {
    const gid = batch.goalId; if (!gid || viewer.drawMode) { error.value = '请先退出掩码编辑'; return; }
    close(); active.value = true; previewing.value = false; busy.value = true; error.value = ''; notice.value = '';
    const gen = generation;
    try {
      batch.setFollow(false);
      let cursor = 0, current: BatchItemRecord | undefined;
      while (!current) {
        const page = await apiBatchItems(gid, cursor, 'MASK_READY');
        if (gen !== generation || batch.goalId !== gid) return;
        current = page.items.find(i => (!itemId || i.item_id === itemId)
          && i.checkpoint.review_required && !i.checkpoint.review);
        if (current || page.items.length < 20) break;
        const next = page.next_after_ordinal;
        if (next <= cursor) break;
        cursor = next;
      }
      if (gen !== generation || batch.goalId !== gid) return;
      if (!current) { notice.value = '当前批次没有待复核的候选掩码'; return; }
      await loadVisuals(current, gen, false);
    } catch (e) { if (gen === generation) error.value = e instanceof Error ? e.message : String(e); }
    finally { if (gen === generation) busy.value = false; }
  }

  /** Read-only QC stage used by the automatic repair playback. It reuses the
   *  same split/heat layout as「前往复核」without pausing the playback or
   *  accepting review decisions. */
  async function preview(record: BatchItemRecord): Promise<boolean> {
    const gid = batch.goalId;
    if (!gid || !record || viewer.drawMode) return false;
    close(); active.value = true; previewing.value = true; busy.value = true; error.value = ''; notice.value = '';
    const gen = generation;
    try {
      await loadVisuals(record, gen, true);
      return gen === generation && active.value && !error.value;
    } catch (e) {
      if (gen === generation) error.value = e instanceof Error ? e.message : String(e);
      return false;
    } finally { if (gen === generation) busy.value = false; }
  }
  async function decide(decision: 'approve' | 'exclude') {
    const current = item.value, gid = batch.goalId;
    if (!current || !gid || !selected.value || !ready.value || busy.value) return;
    busy.value = true; error.value = ''; const gen = generation;
    try {
      const updated = await apiBatchReview(gid, current, decision, regionId.value);
      if (gen !== generation || batch.goalId !== gid) return;
      item.value = updated; await batch.refreshSnapshot();
      if (pending.value.length) focus(pending.value[0].candidate_id);
      else { notice.value = '本景候选复核完成，正在进入下一景…'; await open(); }
    } catch (e) { if (gen === generation) error.value = String(e); }
    finally { if (gen === generation) busy.value = false; }
  }
  async function flicker() {
    const pairNow = pair.value; if (!pairNow || !ready.value) return;
    const pane = viewer.panes.find(p => p.rec?.id === pairNow.pan) ?? viewer.panes[0], rec = pane.rec;
    if (!rec?.thumb) return;
    const norm = pane.view.scale*rec.thumb.width;
    const cx = (pane.rect.w/2-pane.view.ox)/norm, cy = (pane.rect.h/2-pane.view.oy)/norm;
    const fromFlicker = viewer.compareMode === 'flicker';
    if (fromFlicker) viewer.setCompareMode('split');
    else if (!viewer.openComparePair(pairNow.pan,pairNow.nosr)) { error.value = '当前影像配对无法进入交替对比'; return; }
    await settledLayout();
    viewer.stopViewAnim();
    if (fromFlicker) for (const p of viewer.panes) {
      if (p.rec?.thumb) viewer.setPaneView(p.side,{scale:norm/p.rec.thumb.width,ox:p.rect.w/2-cx*norm,oy:p.rect.h/2-cy*norm});
    } else {
      const p = viewer.panes[0], ref = viewer.flickerRefRec;
      if (ref?.thumb) viewer.setTourCamera({scale:norm/ref.thumb.width,ox:p.rect.w/2-cx*norm,oy:p.rect.h/2-cy*norm});
      viewer.flickerStart();
    }
  }
  watch(() => batch.goalId, close);
  return {active,busy,previewing,error,notice,item,document,pair,heat,regionId,regions,selected,pending,ready,open,preview,close,focus,navigate,decide,flicker};
});
