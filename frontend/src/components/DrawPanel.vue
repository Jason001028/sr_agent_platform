<script setup lang="ts">
/**
 * DrawPanel.vue — 掩码绘制浮动面板（tif-viewer.html #drawPanel 直译）
 * ------------------------------------------------------------------
 * 矩形/多边形/魔棒/删除 + 容差 input + 合并重叠/撤销/清空/导出掩码JSON/完成 + ROI 计数。
 * 合并期间禁用按钮（merging 守卫）；面板随 drawMode 显隐。
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();
const roiCount = computed(() => store.getRois().length);
</script>

<template>
  <div v-if="store.drawMode" class="draw-panel">
    <button
      type="button"
      :class="{ on: store.drawTool === 'rect' }"
      @click="store.setDrawTool('rect')"
    >矩形</button>
    <button
      type="button"
      :class="{ on: store.drawTool === 'polygon' }"
      @click="store.setDrawTool('polygon')"
    >多边形</button>
    <button
      type="button"
      :class="{ on: store.drawTool === 'wand' }"
      @click="store.setDrawTool('wand')"
    >魔棒</button>
    <span class="lbl">容差</span>
    <input v-model.number="store.wandTol" type="number" min="0" max="255" />
    <button
      type="button"
      :class="{ on: store.drawTool === 'del' }"
      @click="store.setDrawTool('del')"
    >删除</button>

    <span class="sep"></span>

    <button
      type="button"
      :disabled="store.merging"
      @click="store.mergeRois()"
    >合并重叠</button>
    <button type="button" @click="store.undoRoi()">撤销</button>
    <button type="button" @click="store.clearRois()">清空</button>
    <button type="button" @click="store.exportMaskJson()">导出掩码JSON</button>

    <span class="sep"></span>

    <span v-if="roiCount" class="lbl">ROI {{ roiCount }} 个</span>
    <button type="button" @click="store.exitDraw()">完成</button>
  </div>
</template>

<style scoped>
.draw-panel {
  position: absolute;
  left: 12px;
  top: 12px;
  z-index: 15;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(236, 236, 240, 0.9);
  border-radius: 12px;
  box-shadow: var(--shadow-pop);
}
.draw-panel button {
  height: 28px;
  padding: 0 11px;
  background: var(--surface-2);
  color: var(--ink-body);
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: 7px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease, color 0.15s ease;
}
.draw-panel button:hover { background: #e9eeea; color: var(--ink); }
.draw-panel button.on { background: var(--accent-soft); color: var(--accent-deep); border-color: var(--accent-2); }
.draw-panel button:disabled { opacity: 0.5; cursor: wait; }
.draw-panel .lbl { color: var(--ink-sub); font-size: 11px; margin: 0 2px; }
.draw-panel .sep { width: 1px; height: 18px; background: var(--line); margin: 0 2px; }
.draw-panel input[type=number] {
  width: 54px;
  height: 28px;
  padding: 0 6px;
  background: var(--surface-2);
  color: var(--ink);
  font-size: 12px;
  border: 1px solid var(--line);
  border-radius: 7px;
  transition: border-color 0.15s ease;
}
.draw-panel input[type=number]:focus { outline: none; border-color: var(--accent-3); }
</style>
