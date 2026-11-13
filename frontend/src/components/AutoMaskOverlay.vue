<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue';
import { useDiffStore } from '../stores/diff';
import { useViewerStore } from '../stores/viewer';
import { thumbToScreen } from '../lib/viewMath';
const diff = useDiffStore();
const viewer = useViewerStore();
const panes = computed(() => {
  const batch = diff.autoDrawing;
  if (!batch) return [];
  return viewer.panes.filter(p => p.rec?.id === batch.imageId).map(p => ({
    side: p.side, rect: p.rect, reviewing: batch.reviewing,
    polygons: batch.polygons.map((poly, i) => {
      const screen = poly.map(([x, y]) => thumbToScreen(p.view, x, y));
      return ({
      id: poly.autoDiff!.region_id,
      points: screen.map(p => p.join(',')).join(' '),
      x: Math.max(0, Math.min(p.rect.w - 76, Math.max(...screen.map(p => p[0])) - 76)),
      y: Math.max(0, Math.min(p.rect.h - 32, Math.min(...screen.map(p => p[1])) - 32)),
      decision: batch.decisions[poly.autoDiff!.region_id],
      delay: `${i * batch.duration / batch.polygons.length}ms`,
      duration: `${batch.duration / batch.polygons.length}ms`,
      review: poly.autoDiff!.needs_review,
    }); }),
  }));
});
function escape(e: KeyboardEvent) { if (e.key === 'Escape') diff.cancelAutoDraw(); }
onMounted(() => document.addEventListener('keydown', escape));
onBeforeUnmount(() => { document.removeEventListener('keydown', escape); diff.cancelAutoDraw(); });
</script>

<template>
  <div class="auto-mask-layer" data-e2e="auto-mask-animation">
    <svg v-for="pane in panes" :key="pane.side" class="auto-mask-pane"
      :style="{ left: `${pane.rect.x}px`, top: `${pane.rect.y}px`, width: `${pane.rect.w}px`, height: `${pane.rect.h}px` }">
      <defs><mask :id="`auto-review-shade-${pane.side}`" maskUnits="userSpaceOnUse" x="0" y="0" :width="pane.rect.w" :height="pane.rect.h">
        <rect width="100%" height="100%" fill="white" />
        <polygon v-for="p in pane.polygons.filter(p => p.decision !== 'rejected')" :key="p.id" :points="p.points" class="cutout" />
      </mask></defs>
      <rect v-if="pane.reviewing" width="100%" height="100%" fill="black" opacity="0.45" :mask="`url(#auto-review-shade-${pane.side})`" />
      <g v-for="p in pane.polygons" :key="p.id">
        <polygon :points="p.points" pathLength="1"
          :class="{ review: p.review, ready: pane.reviewing, approved: p.decision === 'approved', rejected: p.decision === 'rejected' }"
          :style="{ '--delay': p.delay, '--duration': p.duration }" />
        <foreignObject v-if="pane.reviewing" :x="p.x" :y="p.y" width="76" height="32">
          <div class="approval-actions" @pointerdown.stop @mousedown.stop @dblclick.stop @wheel.stop>
            <button type="button" :aria-label="`通过区域 ${p.id}`" :aria-pressed="p.decision === 'approved'" :disabled="diff.autoReviewBusy"
              title="通过此区域" @click.stop="diff.reviewAutoRegion(p.id, 'approved')">√</button>
            <button type="button" :aria-label="`驳回区域 ${p.id}`" :aria-pressed="p.decision === 'rejected'" :disabled="diff.autoReviewBusy"
              title="驳回此区域" @click.stop="diff.reviewAutoRegion(p.id, 'rejected')">×</button>
          </div>
        </foreignObject>
      </g>
    </svg>
    <div v-if="diff.autoDrawing?.reviewing" class="review-progress" role="status">
      区域审批 {{ Object.keys(diff.autoDrawing.decisions).length }} / {{ diff.autoDrawing.polygons.length }}
      <span v-if="diff.autoReviewBusy">正在确认…</span>
      <button v-else type="button" @click.stop="diff.cancelAutoDraw()">取消</button>
    </div>
  </div>
</template>

<style scoped>
.auto-mask-layer{position:absolute;inset:0;pointer-events:none;z-index:6;overflow:hidden}
.auto-mask-pane{position:absolute;overflow:hidden;pointer-events:none}
polygon{fill:#3da9a400;stroke:#6ee4cc;stroke-width:2;stroke-linejoin:round;vector-effect:non-scaling-stroke;stroke-dasharray:1;stroke-dashoffset:1;opacity:0;filter:drop-shadow(0 0 3px #113f3680);animation:auto-trace var(--duration) cubic-bezier(.22,1,.36,1) var(--delay) forwards}
polygon.review{stroke:#ffc976}
polygon.cutout{fill:black;stroke:none;animation:none;opacity:1;filter:none}
polygon.ready{animation:none;opacity:1;stroke-dashoffset:0;fill:#3da9a438}
polygon.approved{stroke:#6ee4cc;fill:#3da9a438}
polygon.rejected{stroke:#e89999;fill:transparent;stroke-dasharray:4 4;opacity:.5}
.approval-actions{display:flex;gap:4px;pointer-events:auto}
.approval-actions button{width:32px;height:28px;border:1px solid #7a8c87;border-radius:5px;background:#182923;color:#d9ffed;font-size:19px;cursor:pointer}
.approval-actions button:last-child{color:#ffc6c6}
.approval-actions button[aria-pressed=true]{border:2px solid currentColor;background:#354b43}
.approval-actions button:focus-visible{outline:2px solid white;outline-offset:1px}
.review-progress{position:absolute;bottom:16px;left:50%;transform:translateX(-50%);background:#182923ed;color:#edfff7;padding:7px 12px;border-radius:6px;pointer-events:auto;white-space:nowrap;font-size:12px}
.review-progress button{margin-left:10px;color:inherit;background:transparent;border:1px solid #839b90;border-radius:4px;cursor:pointer}
@keyframes auto-trace{0%{opacity:1;stroke-dashoffset:1;fill:#3da9a400}75%{opacity:1;stroke-dashoffset:0;fill:#3da9a400}100%{opacity:1;stroke-dashoffset:0;fill:#3da9a438}}
@media(prefers-reduced-motion:reduce){polygon{animation:none;opacity:1;stroke-dashoffset:0;fill:#3da9a438}}
</style>
