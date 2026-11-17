<script setup lang="ts">
import { ref, watch, onBeforeUnmount, nextTick } from 'vue';
import { evidenceComparisonRequest } from '../lib/evidenceViewer';
import { useViewerStore } from '../stores/viewer';
import { useBatchQualityStore } from '../stores/batchQuality';
import type { SceneSiblings } from '../lib/api';
const viewer=useViewerStore();
const notice=ref('');
let generation=0;
watch(() => evidenceComparisonRequest.value?.token, async () => {
  const request=evidenceComparisonRequest.value;
  if (!request) return;
  const gen=++generation;
  if (viewer.drawMode) { notice.value='请先完成或取消手绘草稿，归档证据仍可查看'; return; }
  viewer.noteUserIntent();const intent=viewer.userIntentSeq;
  const wanted=() => gen===generation && viewer.userIntentSeq===intent && !viewer.drawMode;
  try {
    useBatchQualityStore().clear();
    const ids:number[]=[];
    for (const image of request.comparison.images) {
      ids.push(await viewer.loadTourImage(image,{lqPath:request.comparison.scene_path} as SceneSiblings,wanted));
      if (!wanted()) return;
    }
    if (!await viewer.openPreviewPair(ids[0],ids[1])) return;
    const openedIntent=viewer.userIntentSeq;
    await nextTick();await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    if (gen!==generation || viewer.drawMode || viewer.userIntentSeq!==openedIntent) return;
    viewer.splitLinked=true;
    const bbox=request.comparison.source_bbox;
    if (bbox) {
      const reference=viewer.recs.find(r=>r.id===ids[0]);
      if(reference?.W && reference.H) {
        const [x0,y0,x1,y1]=bbox,w=reference.W,h=reference.H;
        const norm=Math.min(...viewer.panes.map(p=>Math.min(p.rect.w*.7/Math.max((x1-x0+1)/w,.001),p.rect.h*.7/Math.max((y1-y0+1)/w,.001))));
        viewer.stopViewAnim();
        for(const pane of viewer.panes)if(pane.rec?.thumb)viewer.setPaneView(pane.side,{scale:norm/pane.rec.thumb.width,ox:pane.rect.w/2-(x0+x1)/2/w*norm,oy:pane.rect.h/2-(y0+y1)/2/w*norm});
      }
    }
    notice.value=request.comparison.comparison_role==='output_vs_before'?'只读对照：左侧修复产物，右侧修复前 PAN；此入口不提交人工验收。':'只读对照：左侧产物，右侧 NOSR。';
  } catch { if(gen===generation)notice.value='原始影像无法定位，请返回报告查看归档裁片'; }
  finally { if(evidenceComparisonRequest.value?.token===request.token)evidenceComparisonRequest.value=null; }
}, { immediate: true });
onBeforeUnmount(()=>{ generation++; });
</script>
<template><div v-if="notice" class="evidence-notice" role="status">{{ notice }} <button @click="notice=''">关闭提示</button></div></template>
<style scoped>.evidence-notice{padding:8px 12px;background:var(--surface);color:var(--ink-sub);font-size:12px;overflow-wrap:anywhere}</style>
