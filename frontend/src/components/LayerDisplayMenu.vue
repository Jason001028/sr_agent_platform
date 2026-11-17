<script setup lang="ts">
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useDiffStore } from '../stores/diff';
import AppIcon from './AppIcon.vue';
import LayerSwitch from './LayerSwitch.vue';

const viewer = useViewerStore();
const diff = useDiffStore();
const layers = computed(() => [
  { key: 'difference', title: '差异定位', detail: '热力图与候选区域', selected: diff.resultVisible, color: '#c98460' },
  { key: 'repair', title: '修复区域', detail: '掩码轮廓与修复范围', selected: viewer.repairOverlaySelected, color: '#8190c7' },
  { key: 'annotations', title: '空间批注', detail: '编号与批注信息', selected: viewer.agentAnnotationsVisible, color: '#78958b' },
  { key: 'roi', title: 'ROI 选区', detail: '当前查看区域', selected: viewer.roiOverlaySelected, color: '#6c9b92' },
]);
function toggle(key: string) {
  if (key === 'difference') diff.resultVisible = !diff.resultVisible;
  if (key === 'repair') viewer.repairOverlaySelected = !viewer.repairOverlaySelected;
  if (key === 'annotations') viewer.setAgentAnnotationsVisible(!viewer.agentAnnotationsVisible);
  if (key === 'roi') viewer.roiOverlaySelected = !viewer.roiOverlaySelected;
}
</script>

<template>
  <div class="layer-panel" aria-label="图层显示">
    <header><strong><AppIcon name="layers" :size="14" tint />图层显示</strong><span>Viewer 统一管理</span></header>
    <div class="layer-list">
      <button v-for="layer in layers" :key="layer.key" type="button" class="layer-row"
        :data-layer="layer.key" :aria-label="layer.title" role="switch" :aria-checked="layer.selected" @click="toggle(layer.key)">
        <svg class="layer-symbol" :style="{ color: layer.color }" width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true">
          <template v-if="layer.key === 'annotations'"><path d="M15 8c0 4-5 8-5 8S5 12 5 8a5 5 0 0 1 10 0Z"/><circle cx="10" cy="8" r="1.8"/></template>
          <template v-else><rect x="4" y="4" width="12" height="12" rx="2" :stroke-dasharray="layer.key === 'roi' ? '1.5 1.5' : '4 3'"/><circle v-if="layer.key === 'difference'" cx="10" cy="10" r="1.5"/></template>
        </svg>
        <span class="layer-copy"><span>{{ layer.title }}</span><small>{{ layer.detail }}</small></span>
        <LayerSwitch :selected="layer.selected" />
      </button>
    </div>
    <footer><span>临时隐藏全部叠加层</span><button type="button" :aria-pressed="!viewer.overlayMasterVisible" @click="viewer.overlayMasterVisible = !viewer.overlayMasterVisible"><AppIcon :name="viewer.overlayMasterVisible ? 'eye' : 'eye-closed'" :size="12" tint />{{ viewer.overlayMasterVisible ? '全部隐藏' : '恢复显示' }}</button></footer>
  </div>
</template>

<style scoped>
.layer-panel{box-sizing:border-box;width:328px;max-width:calc(100vw - 40px);padding:12px;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:0 5px 20px #00000012;font-size:13px}
header,header strong,footer,footer button{display:flex;align-items:center}
header{justify-content:space-between;gap:16px;margin-bottom:8px}
header strong{gap:8px;font-weight:600}
header>span,footer{font-size:11px;color:var(--muted)}
.layer-list{display:grid;gap:2px}
.layer-row{display:flex;align-items:center;gap:20px;width:100%;min-height:49px;padding:8px;border:0;border-radius:6px;background:transparent;color:inherit;text-align:left;cursor:pointer;font:inherit}
.layer-row:hover{background:var(--chrome)}
.layer-copy{display:grid;gap:3px;flex:1}
.layer-copy small{font-size:10px;color:var(--muted)}
.layer-symbol{flex:none}
footer{justify-content:space-between;gap:12px;margin-top:12px;padding-top:16px;border-top:1px solid var(--line)}
footer button{gap:5px;padding:4px 10px;border:1px solid var(--line);border-radius:20px;background:transparent;color:inherit;font:inherit;cursor:pointer}
button:focus-visible{outline:2px solid var(--accent-3);outline-offset:2px}
</style>
