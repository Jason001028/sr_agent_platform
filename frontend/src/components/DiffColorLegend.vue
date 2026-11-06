<script setup lang="ts">
import { computed } from 'vue';
import { useDiffStore } from '../stores/diff';
import { HEAT_GRADIENT } from '../lib/diffRender';
const diff = useDiffStore();
const noData = computed(() => {
  const r = diff.result;
  if (!r) return '';
  const count = r.coverage.gridPixels - r.coverage.validPixels;
  return count ? `NoData ${count.toLocaleString()} px${r.validityMap ? '' : '（旧服务未提供位置）'}` : '无 NoData';
});
</script>

<template>
  <div v-if="diff.result" class="diff-color-legend" aria-label="差异强度色标" data-e2e="diff-legend">
    <div class="legend-title"><strong>{{ diff.result.method === 'ssim' ? '1−SSIM' : 'AbsDiff' }}</strong><span>差异强度</span></div>
    <div class="gradient" :style="{ background: HEAT_GRADIENT }"></div>
    <div class="range"><span>0 · 低</span><span>{{ diff.toneWeight ? '1 · 高（已加权）' : '1 · 高' }}</span></div>
    <div v-if="diff.toneWeight" class="tone-note" data-e2e="diff-legend-tone">
      逐像素亮度归因 ×0.3–1.8；差异乘权重后超过 1 截断
    </div>
    <div class="status"><i class="nodata"></i>{{ noData }}<i class="outside"></i>未分析</div>
  </div>
</template>

<style scoped>
.diff-color-legend{width:100%;box-sizing:border-box;color:var(--ink);font-size:11px;line-height:16px}
.legend-title,.range{display:flex;align-items:center;justify-content:space-between;gap:8px}
.legend-title span,.range,.status{color:var(--ink-sub)}
.legend-title strong{font-size:11px;font-weight:600}
.gradient{height:9px;border-radius:3px;margin:5px 0 2px;border:1px solid #ffffff25}
.status{display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-top:3px;font-size:10px}
.status i{width:8px;height:8px;display:inline-block;border:1px solid #ffffff35;border-radius:2px;flex:none}
.nodata{background:#637180}.outside{background:#202c39;margin-left:6px}
.tone-note{margin-top:2px;font-size:10px;line-height:14px;color:var(--ink-sub)}
</style>
