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
    side: p.side, rect: p.rect,
    polygons: batch.polygons.map((poly, i) => ({
      id: poly.autoDiff!.region_id,
      points: poly.map(([x, y]) => thumbToScreen(p.view, x, y).join(',')).join(' '),
      delay: `${i * batch.duration / batch.polygons.length}ms`,
      duration: `${batch.duration / batch.polygons.length}ms`,
      review: poly.autoDiff!.needs_review,
    })),
  }));
});
function escape(e: KeyboardEvent) { if (e.key === 'Escape') diff.cancelAutoDraw(); }
onMounted(() => document.addEventListener('keydown', escape));
onBeforeUnmount(() => { document.removeEventListener('keydown', escape); diff.cancelAutoDraw(); });
</script>

<template>
  <div class="auto-mask-layer" aria-hidden="true" data-e2e="auto-mask-animation">
    <svg v-for="pane in panes" :key="pane.side" class="auto-mask-pane"
      :style="{ left: `${pane.rect.x}px`, top: `${pane.rect.y}px`, width: `${pane.rect.w}px`, height: `${pane.rect.h}px` }">
      <polygon v-for="p in pane.polygons" :key="p.id" :points="p.points" pathLength="1"
        :class="{ review: p.review }" :style="{ '--delay': p.delay, '--duration': p.duration }" />
    </svg>
  </div>
</template>

<style scoped>
.auto-mask-layer{position:absolute;inset:0;pointer-events:none;z-index:6;overflow:hidden}
.auto-mask-pane{position:absolute;overflow:hidden;pointer-events:none}
polygon{fill:#3da9a400;stroke:#6ee4cc;stroke-width:2;stroke-linejoin:round;vector-effect:non-scaling-stroke;stroke-dasharray:1;stroke-dashoffset:1;opacity:0;filter:drop-shadow(0 0 3px #113f3680);animation:auto-trace var(--duration) cubic-bezier(.22,1,.36,1) var(--delay) forwards}
polygon.review{stroke:#ffc976}
@keyframes auto-trace{0%{opacity:1;stroke-dashoffset:1;fill:#3da9a400}75%{opacity:1;stroke-dashoffset:0;fill:#3da9a400}100%{opacity:1;stroke-dashoffset:0;fill:#3da9a438}}
@media(prefers-reduced-motion:reduce){polygon{animation:none;opacity:1;stroke-dashoffset:0;fill:#3da9a438}}
</style>
