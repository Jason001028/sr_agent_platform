<script setup lang="ts">
/**
 * ChatAttachTray.vue — 输入框上方的「待发截图」条
 * ------------------------------------------------------------------
 * 只做展示与删除：图存在 chat store 的 `pendingImages` 里，发送时整托盘取走。
 * 缩略图用 data URL 直接预览（与上传体同一份），就不再向后端要一遍。
 * 上限口径来自 `SHOT_MAX_COUNT`，与后端 `chat_images.MAX_IMAGES` 同值。
 */
import type { PendingImage } from '../stores/chat.js';
import { SHOT_MAX_COUNT } from '../lib/chatShot.js';

defineProps<{ items: PendingImage[] }>();
const emit = defineEmits<{ (e: 'remove', id: number): void }>();
</script>

<template>
  <div v-if="items.length" class="tray" data-e2e="chat-tray">
    <div v-for="it in items" :key="it.id" class="tray-item" data-e2e="chat-tray-item"
         :title="it.name">
      <img class="tray-img" :src="it.dataUrl" :alt="it.name" />
      <button type="button" class="tray-x" :aria-label="'移除截图 ' + it.name"
              title="移除这张截图" @click="emit('remove', it.id)">×</button>
    </div>
    <span class="tray-count">{{ items.length }}/{{ SHOT_MAX_COUNT }}</span>
  </div>
</template>

<style scoped>
.tray {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 16px 0;
  animation: tray-in var(--motion-fade) var(--ease-enter);
}
@keyframes tray-in {
  from { opacity: 0; transform: translateY(3px); }
  to { opacity: 1; transform: none; }
}
.tray-item {
  position: relative;
  width: 56px; height: 44px;
  border: 1px solid var(--line); border-radius: 6px;
  overflow: hidden; background: var(--surface-3);
}
/* 缩略图是 contain 而不是 cover：截图要看清是哪一块画面，裁掉边缘就认不出了 */
.tray-img { display: block; width: 100%; height: 100%; object-fit: contain; }
.tray-x {
  position: absolute; top: 1px; right: 1px;
  width: 15px; height: 15px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 50%;
  background: rgba(30, 36, 34, 0.62); color: #fff;
  font-size: 11px; font-family: inherit; cursor: pointer;
  padding: 0;
}
.tray-x:hover { background: var(--err); }
.tray-x:focus-visible { outline: 2px solid var(--accent-3); outline-offset: 1px; }
.tray-count { font-size: 10.5px; color: var(--ink-faint); margin-left: 2px; }

@media (prefers-reduced-motion: reduce) {
  .tray { animation: none; }
}
</style>
