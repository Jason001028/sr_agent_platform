<script setup lang="ts">
import { computed } from 'vue';
import { useBatchQualityStore } from '../stores/batchQuality';
import { useViewerStore } from '../stores/viewer';
import { thumbToScreen } from '../lib/viewMath';
const quality=useBatchQualityStore(),viewer=useViewerStore();
const panes=computed(()=>{
  const d=quality.doc;if(!d||!d.binding.mask||d.binding.kind==='no-candidate'||!quality.comparisonReady||quality.overlay==='off')return [];
  const {width,height}=d.binding.mask;
  return viewer.panes.map(p=>({side:p.side,rect:p.rect,polygons:d.binding.regions.map(c=>{
    const [x0,y0,x1,y1]=c.bbox;
    const points=c.kind==='polygon'&&c.points?c.points:[[x0-.5,y0-.5],[x1+.5,y0-.5],[x1+.5,y1+.5],[x0-.5,y1+.5]];
    return{id:c.candidate_id,selected:c.candidate_id===quality.regionId,
      points:points.map(([x,y])=>thumbToScreen(p.view,x/width*p.rec!.thumb!.width,y/height*p.rec!.thumb!.height).join(',')).join(' ')};
  })}));
});
</script>
<template>
  <div class="quality-mask" aria-hidden="true" data-e2e="batch-quality-overlay">
    <svg v-for="p in panes" :key="p.side" :style="{left:p.rect.x+'px',top:p.rect.y+'px',width:p.rect.w+'px',height:p.rect.h+'px'}">
      <polygon v-for="c in p.polygons" :key="c.id" :points="c.points" :class="{selected:c.selected,filled:quality.overlay==='fill'}" />
    </svg>
  </div>
</template>
<style scoped>
.quality-mask{position:absolute;inset:0;pointer-events:none;overflow:hidden;z-index:5}svg{position:absolute;overflow:hidden}polygon{fill:none;stroke:#ffc976;stroke-width:1;vector-effect:non-scaling-stroke;opacity:.45}polygon.selected{stroke:#ffe49a;stroke-width:2;opacity:1}polygon.filled{fill:#ffba4330}polygon.selected.filled{fill:#ffba4345}
</style>
