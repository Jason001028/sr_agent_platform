<script setup lang="ts">
/**
 * FileThumb.vue — 文件卡里的小图预览横幅
 * ------------------------------------------------------------------
 * 画的是 `rec.thumb` —— 解码后**已经画好的显示层画布**（stretch 之后那份）。不另拉
 * 一次盘阵 JPG：那既多一次网络，又和画布上看到的不是同一份像素。所有 route
 * （utif / sparse / chunked / jpg / img）都走这条，不必按来源分叉。
 *
 * 解码是懒的：**没打开过的图根本没有像素**，这时显占位。这是刻意的，不是缺陷 ——
 * 为了列表里的小图去把每张大图都解码一遍，代价是几十秒 × N。
 *
 * 重绘时机只看这张图自己的像素标识（thumb / paintedMode），**不听全局 renderTick**：
 * 那个值在平移缩放时每个 mousemove 都 +1，会让整列缩略图跟着无谓重绘。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ViewerRec } from '../stores/viewer';

const props = defineProps<{ rec: ViewerRec }>();

const BOX_H = 96;                       // 与 CSS 里的高度一致

const cv = ref<HTMLCanvasElement | null>(null);
let ro: ResizeObserver | null = null;

function draw(): void {
  const el = cv.value;
  if (!el) return;
  // 侧栏收起时 clientWidth 为 0（元素还在、只是被宽度 0 的父级压扁）：这时别改背衬
  // 尺寸，否则展开回来会是一张按错尺寸画出来的糊图。ResizeObserver 会补一次重绘。
  const boxW = el.clientWidth;
  if (!boxW) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(boxW * dpr));
  const h = Math.max(1, Math.round(BOX_H * dpr));
  if (el.width !== w || el.height !== h) {
    el.width = w;
    el.height = h;
  }

  const ctx = el.getContext('2d');
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);

  const src = props.rec.thumb as unknown as HTMLCanvasElement | null;
  if (!src || !src.width || !src.height) return;

  // contain：整幅装进框里，不裁切 —— 预览是给判读用的（比色调、看有没有云），
  // 裁掉边缘会让人对构图产生错误印象。留白由容器底色兜着。
  const s = Math.min(w / src.width, h / src.height);
  const dw = src.width * s;
  const dh = src.height * s;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(src, (w - dw) / 2, (h - dh) / 2, dw, dh);
}

// thumb 出现 = 解码完成；paintedMode 变 = 换了拉伸模式（画布是原地重画的，
// 单看 thumb 的引用不会变）。两者之外这张图的像素不会再动。
watch(
  () => [props.rec.thumb, props.rec.paintedMode],
  () => draw(),
);

onMounted(() => {
  draw();
  if (cv.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => draw());
    ro.observe(cv.value);
  }
});

onBeforeUnmount(() => {
  if (ro) { ro.disconnect(); ro = null; }
});
</script>

<template>
  <div class="ft">
    <canvas ref="cv" class="ft-cv"></canvas>
    <span v-if="!rec.thumb" class="ft-ph">{{ rec.status || '未打开' }}</span>
  </div>
</template>

<style scoped>
.ft {
  position: relative;
  width: 100%;
  height: 96px;
  margin-bottom: 6px;
  border-radius: 8px;
  overflow: hidden;
  /* 中性底：缩略图两侧的留白不该有颜色倾向，否则判读明暗时会被底色带偏 */
  background: var(--surface-3);
  border: 1px solid var(--line);
}
.ft-cv { display: block; width: 100%; height: 100%; }
.ft-ph {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--ink-faint);
  text-align: center;
  padding: 0 8px;
}
</style>
