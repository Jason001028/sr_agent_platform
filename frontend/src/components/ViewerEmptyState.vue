<script setup lang="ts">
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { isViewerEmpty } from '../lib/viewerEmptyState';
import AsciiLogoIntro from './AsciiLogoIntro.vue';
const viewer = useViewerStore();
const visible = computed(() => isViewerEmpty(viewer));
</script>

<template>
  <section v-if="visible" class="viewer-empty" aria-label="影像分析欢迎页">
    <div class="welcome-content">
      <AsciiLogoIntro />
      <h1>开启你的图像工作流</h1>
      <p class="subtitle">拖入 TIF / JPG，开启影像分析</p>
    </div>
  </section>
</template>

<style scoped>
.viewer-empty { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; user-select: none; container-type: size; }
.welcome-content { width: min(400px, calc(100% - 32px), max(80px, calc(100cqh - 220px))); text-align: center; transform: translateY(-24px); color: #244b40; }
h1 { margin: 20px 0 10px; font-size: 20px; font-weight: 600; line-height: 1.4; }
p { max-width: 340px; margin: 0 auto; }
.subtitle { font-size: 13px; color: #657d73; line-height: 1.7; }
@media (max-height: 580px) { .welcome-content { transform: none; } h1 { margin-top: 10px; } }
</style>
