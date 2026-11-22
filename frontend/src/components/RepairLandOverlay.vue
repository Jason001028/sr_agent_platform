<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { overlayPaneRect } from '../lib/batchOverlay';
import { joinBase, loadSrConfig } from '../lib/scene';
import { useViewerStore } from '../stores/viewer';

const viewer = useViewerStore();
const failed = ref(false);
const shown = ref(true);
const attempt = ref(0);
const target = computed(() => viewer.repairLandTarget);
watch(target, () => {
  failed.value = false;
  shown.value = true;
  attempt.value = 0;
});
const source = computed(() => {
  const url = target.value?.preview_url;
  if (!url || !/^\/api\/repairs\/[\w-]+\/land-preview\/[\w-]+(?:\?[^#]*)?$/.test(url)) return '';
  return joinBase(loadSrConfig().apiBase, url);
});
const layers = computed(() => {
  const item = target.value;
  if (!item?.preview_revision || !item.scene_id || !source.value) return [];
  return viewer.panes.flatMap(({ rec, rect, view, side }) => {
    if (!rec?.thumb || rec.route !== 'jpg' || rec.sceneId !== item.scene_id || rec.srcRev !== item.preview_revision
      || rec.W !== item.width || rec.H !== item.height) return [];
    return [{ side, rect, image: overlayPaneRect({ source_width: rec.W, source_height: rec.H },
      rec.thumb.width, rec.thumb.height, view, { ...rect, x: 0, y: 0 }) }];
  });
});
</script>

<template>
  <template v-if="target && !viewer.drawMode">
    <template v-if="shown && viewer.overlayMasterVisible && !failed">
      <div v-for="pane in layers" :key="pane.side" class="land-pane"
        :style="{ left: pane.rect.x + 'px', top: pane.rect.y + 'px', width: pane.rect.w + 'px', height: pane.rect.h + 'px' }">
        <img :key="source + attempt" :src="source" draggable="false" alt="" class="land-image"
          :style="{ left: pane.image.left + 'px', top: pane.image.top + 'px', width: pane.image.width + 'px', height: pane.image.height + 'px' }"
          @error="failed = true">
      </div>
    </template>
    <div class="land-controls" data-e2e="repair-land-overlay">
      <span v-if="!layers.length" role="status">当前影像与计划的 PAN 版本不匹配</span>
      <template v-else-if="failed">
        <span role="status">陆地约束预览不可用</span>
        <button type="button" @click="attempt++; failed = false">重试</button>
      </template>
      <template v-else>
        <span><i class="water" />水体</span>
        <span><i class="uncertain" />岸线保护及其他不确定区</span>
        <span><i class="excluded" />候选中被排除的范围</span>
        <span><i class="land" />最终可修复范围</span>
        <button type="button" :aria-pressed="shown" @click="shown = !shown">{{ shown ? '隐藏约束' : '显示约束' }}</button>
      </template>
      <button type="button" @click="viewer.repairLandTarget = null">关闭核对</button>
    </div>
  </template>
</template>

<style scoped>
.land-pane { position: absolute; overflow: hidden; pointer-events: none; z-index: 3; }
.land-image { position: absolute; image-rendering: pixelated; }
.land-controls {
  position: absolute; right: 12px; bottom: 12px; z-index: 6; max-width: calc(100% - 24px);
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 4px 8px;
  border-radius: 6px; background: #17232dec; color: #edf6fc; font-size: 12px;
}
.land-controls i { display: inline-block; width: .7em; height: .7em; margin-right: .3em; }
.water { background: #00bdff; }
.uncertain { background: #f59e0b; }
.excluded { background: #bb60f7; }
.land { background: #2ad16e; }
</style>
