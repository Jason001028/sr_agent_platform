<script setup lang="ts">
/**
 * ViewerPage.vue — 查看器页（阶段3 组件化：Toolbar + FileList + TifCanvas + StatusBar）
 * ------------------------------------------------------------------
 * 组合阶段3 各组件，onMounted 挂输出目录恢复（initFsIO）与 window.__viewer E2E 钩子。
 * 交互/掩码/导出状态全部在 stores/viewer.ts；TifCanvas 内部是双画布，其 slot 承载
 * 浮动面板（DrawPanel）与遮罩/toast/error（DecodeOverlay）。
 */
import { onMounted } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { mountE2EHooks } from '../viewer/e2eHooks';
import Toolbar from '../components/Toolbar.vue';
import FileList from '../components/FileList.vue';
import TifCanvas from '../components/TifCanvas.vue';
import DrawPanel from '../components/DrawPanel.vue';
import DecodeOverlay from '../components/DecodeOverlay.vue';
import StatusBar from '../components/StatusBar.vue';

const store = useViewerStore();

onMounted(() => {
  store.initFsIO();     // 恢复上次授权输出目录（FS Access/IndexedDB）
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
    </div>
    <StatusBar />
  </div>
</template>

<style scoped>
.viewer-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0f1113;
  border: 1px solid #2c313a;
  border-radius: 8px;
  overflow: hidden;
}

.viewer-body {
  display: flex;
  flex: 1;
  min-height: 0;
}
</style>
