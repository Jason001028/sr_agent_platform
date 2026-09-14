<script setup lang="ts">
/**
 * ViewerPage.vue — 查看器页（阶段3 组件化 + 阶段6 右侧上下文侧舱 ContextPanel）
 * ------------------------------------------------------------------
 * 组合各组件，onMounted 挂 window.__viewer E2E 钩子。
 * 交互/掩码/导出状态全部在 stores/viewer.ts；TifCanvas 内部是双画布，其 slot 承载
 * 浮动面板（DrawPanel）与遮罩/toast/error（DecodeOverlay）。
 * 布局行 = [FileList(sidebar+side-toggle)] [TifCanvas stage] [ContextPanel(ctx-toggle+rail)]，
 * 右侧 rail 镜像左栏收起布局，默认收起且宽度持久化在 ContextPanel 内（见其组件注释）。
 */
import { onMounted } from 'vue';
import { mountE2EHooks } from '../viewer/e2eHooks';
import Toolbar from '../components/Toolbar.vue';
import FileList from '../components/FileList.vue';
import TifCanvas from '../components/TifCanvas.vue';
import DrawPanel from '../components/DrawPanel.vue';
import DecodeOverlay from '../components/DecodeOverlay.vue';
import ContextPanel from '../components/ContextPanel.vue';
import StatusBar from '../components/StatusBar.vue';

onMounted(() => {
  mountE2EHooks();      // window.__viewer（浏览器回归 + 真机验收）
});
</script>

<template>
  <div class="viewer-page">
    <Toolbar />
    <div class="viewer-body">
      <FileList />
      <TifCanvas>
        <DrawPanel />
        <DecodeOverlay />
      </TifCanvas>
      <ContextPanel />
    </div>
    <StatusBar />
  </div>
</template>

<style scoped>
/* flush 路由贴边填满：页面底与顶层 chrome 同为浅莫兰迪；画布井位由 .stage 保持中性（不铺绿，利于调色判读） */
.viewer-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--chrome);
  overflow: hidden;
}

.viewer-body {
  display: flex;
  flex: 1;
  min-height: 0;
}
</style>
