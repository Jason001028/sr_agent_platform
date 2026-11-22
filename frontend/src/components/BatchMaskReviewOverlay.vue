<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue';
import { useBatchMaskReviewStore } from '../stores/batchMaskReview';
import { useViewerStore } from '../stores/viewer';
import { batchMaskRequest } from '../lib/batchDock';
import { useBatchGoalStore } from '../stores/batchGoal';
import { thumbToScreen } from '../lib/viewMath';
const review = useBatchMaskReviewStore(), viewer = useViewerStore(), batch = useBatchGoalStore();
watch([() => batchMaskRequest.value?.token, () => batch.goalId], () => {
  if (batchMaskRequest.value?.goalId === batch.goalId) void review.open(batchMaskRequest.value?.itemId);
}, {immediate:true});
onBeforeUnmount(() => review.close());
const panes = computed(() => {
  const d = review.document;
  if (!d || !review.ready || !viewer.overlayMasterVisible || !viewer.repairOverlaySelected) return [];
  return viewer.panes.map(p => ({side:p.side,rect:p.rect,polygons:review.regions.map(c => {
    const [x0,y0,x1,y1] = c.bbox!;
    const points = c.points ?? [[x0,y0],[x1,y0],[x1,y1],[x0,y1]];
    return {id:c.candidate_id,selected:c.candidate_id === review.regionId,decision:review.item?.checkpoint.region_reviews?.[c.candidate_id],
      points:points.map(([x,y]) => thumbToScreen(p.view,x/d.source.width*p.rec!.thumb!.width,y/d.source.height*p.rec!.thumb!.height).join(',')).join(' ')};
  })}));
});
</script>
<template>
  <div v-if="review.active" class="mask-review" data-e2e="mask-review-mode">
    <svg v-for="p in panes" :key="p.side" class="region-pane" :style="{left:p.rect.x+'px',top:p.rect.y+'px',width:p.rect.w+'px',height:p.rect.h+'px'}">
      <polygon v-for="c in p.polygons" :key="c.id" :points="c.points" :class="{selected:c.selected,approved:c.decision==='approve',rejected:c.decision==='exclude'}" />
    </svg>
    <div class="review-toolbar" @pointerdown.stop @mousedown.stop @dblclick.stop @wheel.stop>
      <span class="review-position">候选复核 · {{ review.regions.findIndex(r=>r.candidate_id===review.regionId)+1 }} / {{ review.regions.length }}</span>
      <button :disabled="review.busy || !review.ready" @click="review.navigate(-1)">上一处</button>
      <button class="approve" :disabled="review.busy || !review.ready || !review.selected" @click="review.decide('approve')">通过</button>
      <button :disabled="review.busy || !review.ready || !review.selected" @click="review.decide('exclude')">驳回</button>
      <button :disabled="review.busy || !review.ready" @click="review.navigate(1)">下一处</button>
      <button :disabled="review.busy || !review.ready" @click="review.flicker">{{ viewer.compareMode === 'flicker' ? '返回分屏' : '交替对比' }}</button>
      <button aria-label="退出候选复核" @click="review.close">×</button>
      <button v-if="review.error" :disabled="review.busy" @click="review.open()">重新读取</button>
      <p v-if="review.busy || review.error || review.notice" :class="{error:review.error}" role="status">{{ review.error || (review.busy ? '正在处理…' : review.notice) }}</p>
    </div>
  </div>
</template>
<style scoped>
.mask-review{position:absolute;inset:0;pointer-events:none;z-index:7;overflow:hidden}.region-pane{position:absolute;overflow:hidden}polygon{fill:#8190c712;stroke:#8190c7;stroke-width:1;vector-effect:non-scaling-stroke}polygon.selected{stroke:#b4bff7;stroke-width:2;fill:#8190c725}polygon.approved{stroke:#67bba4}polygon.rejected{stroke:#ad806a;stroke-dasharray:4 3;fill:none;opacity:.5}
.review-toolbar{position:absolute;left:50%;bottom:18px;transform:translateX(-50%);pointer-events:auto;display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:5px;box-sizing:border-box;width:max-content;max-width:calc(100% - 28px);padding:7px 9px;background:var(--tb-bg);border:1px solid var(--tb-border);border-radius:10px;color:var(--tb-fg);box-shadow:0 2px 8px #173f3b14;font-size:11px}.review-position{color:var(--tb-muted);margin-right:4px}button{font:inherit;min-height:28px;padding:0 8px;border:1px solid var(--tb-border);border-radius:6px;background:transparent;color:inherit;cursor:pointer}button.approve{background:var(--nav-bg);border-color:var(--nav-bg);color:white}button:disabled{opacity:.4;cursor:default}p{flex-basis:100%;text-align:center;margin:2px 0;color:var(--tb-muted)}p.error{color:#a36b50}
</style>
