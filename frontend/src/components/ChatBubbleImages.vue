<script setup lang="ts">
/**
 * ChatBubbleImages.vue — 用户气泡里的截图缩略图 + 点开放大
 * ------------------------------------------------------------------
 * `images[].url` 由 chat store 统一换算好（流式回合是 data URL，历史是
 * `/api/chat/sessions/<sid>/images/<uuid>.jpg`），这里不再判断来源。
 *
 * 加载失败只出中性占位：会话图片被清理、或旧记录的目录已不存在时，一个碎图标
 * 比一句「截图不可用」更像是页面坏了。
 */
import { onBeforeUnmount, ref, watch } from 'vue';
import type { ChatImage } from '../lib/api.js';

defineProps<{ images: ChatImage[] }>();

const failed = ref<Record<string, true>>({});
const zoom = ref<ChatImage | null>(null);

function markFail(url: string): void {
  failed.value = { ...failed.value, [url]: true };
}

function close(): void { zoom.value = null; }

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') close();
}

// 放大层开着才监听 Esc，别在页面别处抢键
watch(zoom, (v) => {
  if (v) window.addEventListener('keydown', onKey);
  else window.removeEventListener('keydown', onKey);
});
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));
</script>

<template>
  <div v-if="images.length" class="shots">
    <div v-for="(im, i) in images" :key="im.url + '#' + i" class="shot">
      <img v-if="!failed[im.url]" class="shot-img" :src="im.url"
           :alt="im.name || '截图'" :title="im.name || '点击放大'"
           loading="lazy" @error="markFail(im.url)" @click.stop="zoom = im" />
      <span v-else class="shot-ph" :title="im.name || ''">截图不可用</span>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="zoom" class="lb" @click.self="close">
      <img class="lb-img" :src="zoom.url" :alt="zoom.name || '截图'" />
      <button type="button" class="lb-x" aria-label="关闭" @click="close">×</button>
    </div>
  </Teleport>
</template>

<style scoped>
.shots { display: flex; flex-wrap: wrap; gap: 5px; }
.shot { display: flex; }
.shot-img {
  display: block; max-width: 100%; max-height: 120px;
  border-radius: 6px; cursor: zoom-in;
  background: var(--surface-3);
  border: 1px solid rgba(255, 255, 255, 0.35);
}
.shot-ph {
  display: flex; align-items: center; justify-content: center;
  width: 96px; height: 64px;
  border-radius: 6px; font-size: 10.5px;
  color: rgba(255, 255, 255, 0.82);
  background: rgba(255, 255, 255, 0.14);
  border: 1px dashed rgba(255, 255, 255, 0.38);
}

.lb {
  position: fixed; inset: 0; z-index: 80;
  display: flex; align-items: center; justify-content: center;
  padding: 28px;
  background: rgba(20, 26, 24, 0.72);
}
.lb-img {
  max-width: 100%; max-height: 100%;
  border-radius: 8px; background: #fff;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.35);
}
.lb-x {
  position: absolute; top: 14px; right: 16px;
  width: 30px; height: 30px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 50%;
  background: rgba(255, 255, 255, 0.16); color: #fff;
  font-size: 19px; font-family: inherit; cursor: pointer;
}
.lb-x:hover { background: rgba(255, 255, 255, 0.3); }
</style>
