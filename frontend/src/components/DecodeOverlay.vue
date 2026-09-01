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
        <div class="progress-bar" :style="{ width: store.overlay.progress + '%' }"></div>
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
.decode-mask {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(10, 12, 14, 0.92);
  z-index: 20;
  text-align: center;
}
.decode-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 80%;
}
.spinner {
  width: 34px;
  height: 34px;
  border: 3px solid #2c313a;
  border-top-color: #2b7bdd;
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.mask-title { color: #e6e9ed; font-size: 15px; font-weight: 600; }
.mask-sub { color: #9aa5b1; font-size: 12px; white-space: pre-wrap; word-break: break-all; }

.progress-wrap {
  width: 320px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.progress-bar {
  flex: 1;
  height: 8px;
  background: #2c313a;
  border-radius: 4px;
  overflow: hidden;
  position: relative;
}
.progress-bar::after {
  content: '';
  position: absolute;
  inset: 0;
  background: #2b7bdd;
  border-radius: 4px;
}
.progress-bar[style] { background: transparent; }
.progress-pct { color: #9aa5b1; font-size: 12px; min-width: 40px; text-align: right; }

.toast {
  position: absolute;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  max-width: 70%;
  padding: 10px 16px;
  background: #1d2a1d;
  border: 1px solid #3a6b4d;
  border-radius: 8px;
  color: #8fd6a8;
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
  background: #2d1c1c;
  border: 1px solid #6b3a3a;
  border-radius: 8px;
  color: #e8a0a0;
  font-size: 13px;
  z-index: 30;
  text-align: center;
  word-break: break-all;
}
</style>
