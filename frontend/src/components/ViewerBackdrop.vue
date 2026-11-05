<script setup lang="ts">
import { useViewerStore } from '../stores/viewer';
import { VIEWER_BACKGROUNDS } from '../lib/viewerBackground';
const viewer=useViewerStore();
</script>
<template>
 <div class="viewer-backdrop" :data-background="viewer.canvasBackground" aria-hidden="true">
  <div v-for="pane in viewer.panes" :key="pane.side" class="backdrop-pane"
   :data-side="pane.side"
   :style="viewer.panes.length===1?{inset:'0'}:{left:pane.rect.x+'px',top:pane.rect.y+'px',width:pane.rect.w+'px',height:pane.rect.h+'px'}">
   <div v-for="option in VIEWER_BACKGROUNDS" :key="option.value" class="backdrop-surface"
    :class="['background-'+option.value,{selected:viewer.canvasBackground===option.value}]" />
   <!-- Preserve the original flat display matte under alpha/NoData pixels.
        It follows the image footprint, while the environment stays viewport-fixed. -->
   <div v-if="pane.rec?.thumb" class="image-matte" :style="{left:pane.view.ox+'px',top:pane.view.oy+'px',width:pane.rec.thumb.width*pane.view.scale+'px',height:pane.rec.thumb.height*pane.view.scale+'px'}" />
  </div>
 </div>
</template>
<style scoped>
.viewer-backdrop{position:absolute;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.backdrop-pane{position:absolute;overflow:hidden;background:#e7e9ea}
.backdrop-surface{position:absolute;inset:0;opacity:0;transition:opacity var(--motion-canvas-background,180ms) var(--ease-standard)}
.backdrop-surface.selected{opacity:1}
.background-dots{background-color:#e7e9ea;background-image:radial-gradient(circle,rgba(66,91,84,.16) .5px,transparent .75px);background-size:26px 26px;background-position:13px 13px}
.background-grid{background-color:#e7e9ea;background-image:linear-gradient(to right,rgba(66,91,84,.1),rgba(66,91,84,0) 1px),linear-gradient(to bottom,rgba(66,91,84,.1),rgba(66,91,84,0) 1px);background-size:26px 26px}
.background-studio{background:radial-gradient(ellipse at 50% 44%,#eff0f0 0%,#e5e8e8 58%,#dce0e0 100%)}
.background-graphite{background:#303738}
.image-matte{position:absolute;background:#e9edeb}
@media(prefers-reduced-motion:reduce){.backdrop-surface{transition:none}}
</style>
