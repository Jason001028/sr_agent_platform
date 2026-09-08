<script setup lang="ts">
/**
 * DecodeOverlay.vue — 解码/合并/生成掩码遮罩 + 进度条 + toast + 错误条
 * ------------------------------------------------------------------
 * tif-viewer.html #decodeMask/#toast/#errBox 直译。绝对定位于 stage 上（TifCanvas slot 注入）。
 * showMask/hideMask/updateProgressUI 由 store 编排。
 */
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();
</script>

<template>
  <!-- 解码/合并/生成遮罩 -->
  <div v-if="store.overlay.visible" class="decode-mask">
    <div class="decode-box">
      <div class="spinner"></div>
      <div class="mask-title">{{ store.overlay.title }}</div>
      <div v-if="store.overlay.sub" class="mask-sub">{{ store.overlay.sub }}</div>
      <div v-if="store.overlay.bar" class="progress-wrap">
        <div class="progress-track">
          <div class="progress-bar" :style="{ width: store.overlay.progress + '%' }"></div>
        </div>
        <div class="progress-pct">{{ store.overlay.progress }}%</div>
      </div>
    </div>
  </div>

  <!-- toast -->
  <div v-if="store.toast" class="toast">{{ store.toast }}</div>

  <!-- 错误条 -->
  <div v-if="store.error" class="err-box">{{ store.error }}</div>
</template>

<style scoped>
/* 解码遮罩：浅色毛玻璃，浮于浅色画布之上；内容为白色卡片 */
.decode-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(233, 237, 235, 0.72);
  backdrop-filter: blur(4px);
  z-index: 20;
  text-align: center;
}
.decode-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 80%;
  background: var(--surface);
  border: 1px solid rgba(236, 236, 240, 0.9);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  padding: 26px 34px;
}
.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--surface-2);
  border-top-color: var(--accent-3);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.mask-title { color: var(--ink); font-size: 15px; font-weight: 600; }
.mask-sub { color: var(--ink-sub); font-size: 12px; white-space: pre-wrap; word-break: break-all; }

.progress-wrap {
  width: 300px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.progress-track {
  flex: 1;
  height: 6px;
  background: var(--surface-2);
  border-radius: 3px;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  background: var(--accent-grad);
  transition: width 0.2s;
}
.progress-pct { color: var(--ink-sub); font-size: 12px; min-width: 40px; text-align: right; }

/* toast：白色浮条 + 青绿语义色，替代原暗绿 */
.toast {
  position: absolute;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  max-width: 70%;
  padding: 10px 16px;
  background: var(--surface);
  border: 1px solid var(--ok-line);
  border-radius: 10px;
  box-shadow: var(--shadow-pop);
  color: var(--accent-deep);
  font-size: 13px;
  z-index: 30;
  text-align: center;
}
.err-box {
  position: absolute;
  left: 50%;
  top: 12px;
  transform: translateX(-50%);
  max-width: 80%;
  padding: 8px 14px;
  background: var(--err-bg);
  border: 1px solid var(--err-line);
  border-radius: 10px;
  color: var(--err);
  font-size: 13px;
  z-index: 30;
  text-align: center;
  word-break: break-all;
}
</style>
