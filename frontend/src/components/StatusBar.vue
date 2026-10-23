<script setup lang="ts">
/**
 * StatusBar.vue — 底部极简状态栏（rec-bar，单行 26px）
 * ------------------------------------------------------------------
 * 三段式：左＝操作状态，中＝缩放比例，右＝光标像素坐标。
 *
 * 元数据（文件名 / W×H / probe / 路由 / 预览尺寸 / layout）**不在这里重复**：
 * 左侧卡片与右侧信息栏各有出处，这条只回答「图现在怎么样、我看到的是哪一像素」。
 *
 * 口径：
 *   - 缩放 100% = 原图 1 像素对屏幕 1 像素（`view.scale` 是「屏幕 / 预览图像素」，
 *     乘 `预览宽 / 原图宽` 换算回原图基准）；
 *   - 像素坐标是**原图全分辨率**坐标，与工具栏定位、掩码文件同一套；
 *   - 指针不在活动格影像上（画布留白 / 分屏另一侧 / 移出窗口）→ 两段都显示「—」；
 *   - 没有活动 rec（列表空）时整条不渲染；有卡但还没像素（排队 / 待打开）仍显示，
 *     缩放与像素为「—」。
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';
import type { DrawTool } from '../stores/viewer';

const store = useViewerStore();

const TOOL_LABEL: Record<DrawTool, string> = {
  rect: '矩形选区',
  polygon: '多边形选区',
  wand: '魔棒选区',
  del: '删除 ROI',
};

/** 左段：遮罩（取图/解码/合并/写掩码）> 绘制模式 > 这张图自己的状态。 */
const status = computed(() => {
  const ov = store.overlay;
  if (ov.visible && ov.title) {
    return { text: ov.title + (ov.bar ? ' ' + ov.progress + '%' : ''), cls: '' };
  }
  if (store.drawMode) {
    const tool = TOOL_LABEL[store.drawTool] ?? store.drawTool;
    const pts = store.pendingPts;
    if (store.drawTool === 'polygon' && pts && pts.length) {
      return { text: tool + ' · 已点 ' + pts.length + ' 点（回车闭合）', cls: '' };
    }
    return { text: tool, cls: '' };
  }
  const rec = store.activeRec;
  if (!rec) return { text: '', cls: '' };
  // 解码/生成预览完成的具体耗时留在 title 里，这一行只给结论
  if (rec.statusCls === 'ok') return { text: '已就绪', cls: 'ok' };
  return { text: rec.status, cls: rec.statusCls };
});

/** 中段：原图基准的缩放百分比；不足 10% 保留一位小数（fit 到全景时不会变成「0%」）。 */
const zoom = computed(() => {
  const rec = store.activeRec;
  if (!rec || !rec.thumb || !rec.W) return '—';
  const pct = store.view.scale * rec.thumb.width / rec.W * 100;
  if (!(pct > 0) || !Number.isFinite(pct)) return '—';
  return (pct >= 10 ? String(Math.round(pct)) : pct.toFixed(1)) + '%';
});

const pixel = computed(() => {
  const c = store.cursor;
  return c ? '(' + c[0] + ', ' + c[1] + ')' : '—';
});
</script>

<template>
  <div v-if="store.activeRec" class="rec-bar">
    <span class="sb-status" :class="status.cls" :title="store.activeRec.status">
      <!-- 分屏时先说清这条讲的是**哪一侧**（缩放/像素都来自活动侧那套 view 与 rec）。 -->
      <span v-if="store.split" class="side" data-e2e="sb-side">
        [{{ store.activeSide === 'A' ? '左' : '右' }}]
      </span>
      {{ status.text }}
    </span>
    <span class="sb-zoom" data-e2e="sb-zoom">缩放 {{ zoom }}</span>
    <span class="sb-px" data-e2e="sb-px">像素 {{ pixel }}</span>
  </div>
</template>

<style scoped>
.rec-bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 16px;
  height: 26px;
  padding: 0 16px;
  font-size: 12px;
  line-height: 1;
  color: var(--ink-sub);
  background: var(--surface);
  border-top: 1px solid var(--line);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.sb-status {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sb-status.ok { color: var(--ok); }
.sb-status.err { color: var(--err); }
.sb-status.muted { color: var(--ink-faint); }
.sb-status .side {
  font-weight: 600;
  color: var(--cmp-active);
}
.sb-zoom,
.sb-px {
  flex: none;
}
.sb-px {
  color: var(--ink-faint);
}
</style>
