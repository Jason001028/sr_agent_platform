<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { overlayPaneRect } from '../lib/batchOverlay';
import { MSS_WATER_VERSION, waterOverlayMatches, type WaterReady } from '../lib/mssWater';
import { joinBase, loadSrConfig } from '../lib/scene';
import { useViewerStore } from '../stores/viewer';

const viewer = useViewerStore();
const active = computed(() => {
  const rec = viewer.activeRec;
  return rec?.thumb && rec.sceneId && rec.sceneDir && !rec.previewOnly
    && (rec.stageKind === 'input' || rec.stageKind === 'nosr') ? rec : null;
});
const ready = ref<WaterReady | null>(null);
const loading = ref(false);
const visible = ref(true);
const failure = ref('');
let request: AbortController | null = null;

function reset() {
  request?.abort();
  request = null;
  ready.value = null;
  loading.value = false;
  visible.value = true;
  failure.value = '';
}

watch(() => [active.value?.id, active.value?.token, active.value?.sceneId, active.value?.srcRev], reset);
onBeforeUnmount(reset);

function endpoint(sceneId: string) {
  return joinBase(loadSrConfig().apiBase, '/api/scenes/' + encodeURIComponent(sceneId) + '/mss-water');
}

const assetBase = computed(() => active.value?.sceneId && ready.value
  ? endpoint(active.value.sceneId) + '/' + encodeURIComponent(ready.value.id) + '/' : '');
const matching = computed(() => active.value?.sceneId && ready.value
  && waterOverlayMatches(ready.value, active.value.sceneId, active.value));

async function load() {
  const id = active.value?.sceneId;
  if (!id) return;
  reset();
  const current = new AbortController();
  request = current;
  loading.value = true;
  try {
    const response = await fetch(endpoint(id), { method: 'POST', signal: current.signal });
    if (!response.ok) throw new Error(response.status === 503 ? '影像工作队列已满，请稍后重试' : '水体请求失败');
    const result = await response.json();
    if (request !== current) return;
    if (result.status === 'ready' && result.version === MSS_WATER_VERSION && result.usage === 'viewer_preview_only') ready.value = result;
    else failure.value = result.detail || '水体产物不可用';
  } catch (error) {
    if (request === current) failure.value = error instanceof Error ? error.message : '水体请求失败';
  } finally {
    if (request === current) loading.value = false;
  }
}

const paneLayers = computed(() => {
  const result = ready.value;
  const sceneId = active.value?.sceneId;
  if (!result || !sceneId || !visible.value || !viewer.overlayMasterVisible || viewer.drawMode || failure.value) return [];
  return viewer.panes.flatMap(({ rec, rect, view, side }) => {
    if (!rec?.thumb || !waterOverlayMatches(result, sceneId, rec)) return [];
    return [{ side, rect, image: overlayPaneRect({ source_width: rec.W, source_height: rec.H },
      rec.thumb.width, rec.thumb.height, view, { ...rect, x: 0, y: 0 }) }];
  });
});
</script>

<template>
  <template v-if="active">
    <div v-for="pane in paneLayers" :key="pane.side" class="water-pane"
      :style="{ left: pane.rect.x + 'px', top: pane.rect.y + 'px', width: pane.rect.w + 'px', height: pane.rect.h + 'px' }">
      <img :src="assetBase + 'overlay.png'" alt="" draggable="false" class="water-image"
        :style="{ left: pane.image.left + 'px', top: pane.image.top + 'px', width: pane.image.width + 'px', height: pane.image.height + 'px' }"
        @error="failure = '水体叠加预览不可用，请重试'">
    </div>
    <div class="water-controls" data-e2e="mss-water">
      <details v-if="ready?.alignment.status === 'unaligned' && !failure" open class="water-native">
        <summary>MSS 原始网格预览（未叠加到 PAN）</summary>
        <img :src="assetBase + 'mss-preview.png'" alt="MSS 水体分类：蓝色水体、深灰非水体、棕灰无效"
          @error="failure = 'MSS 原始网格预览不可用，请重试'">
        <div>蓝色水体 · 深灰非水体 · 棕灰无效</div>
      </details>
      <button v-if="!ready || failure" type="button" :disabled="loading" @click="load">
        {{ loading ? '正在分析 MSS…' : failure ? '重试 MSS 水体' : 'MSS 水体' }}
      </button>
      <span v-if="failure" role="status">{{ failure }}</span>
      <template v-if="ready && !failure">
        <button v-if="matching" type="button" :aria-pressed="visible" @click="visible = !visible">{{ visible ? '隐藏水体' : '显示水体' }}</button>
        <span v-else role="status">{{ ready.alignment.status === 'unaligned' ? ready.alignment.detail : '未完成空间对齐：当前显示影像版本未核对' }}</span>
        <span>NDWI ≥ {{ ready.profile.threshold }}（参考阈值）</span>
        <a :href="assetBase + 'ndwi.tif'">NDWI</a>
        <a :href="assetBase + 'water.tif'">水体</a>
        <a :href="assetBase + 'valid.tif'">有效像素</a>
        <a :href="assetBase + 'metadata.json'">元数据</a>
      </template>
    </div>
  </template>
</template>

<style scoped>
.water-pane { position: absolute; overflow: hidden; pointer-events: none; z-index: 3; }
.water-image { position: absolute; image-rendering: pixelated; }
.water-controls {
  position: absolute; right: 12px; bottom: 12px; z-index: 6; max-width: calc(100% - 24px);
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 4px 8px;
  border-radius: 6px; background: #17232dec; color: #edf6fc; font-size: 12px;
}
.water-controls a { color: #8edfff; }
.water-native { flex-basis: 100%; }
.water-native img { display: block; max-width: min(240px, 100%); max-height: 150px; image-rendering: pixelated; }
</style>
