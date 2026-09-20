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
import { onMounted, onUnmounted } from 'vue';
import { mountE2EHooks } from '../viewer/e2eHooks';
import Toolbar from '../components/Toolbar.vue';
import CompareBar from '../components/CompareBar.vue';
import FileList from '../components/FileList.vue';
import TifCanvas from '../components/TifCanvas.vue';
import DrawPanel from '../components/DrawPanel.vue';
import CompareOverlay from '../components/CompareOverlay.vue';
import DecodeOverlay from '../components/DecodeOverlay.vue';
import ContextPanel from '../components/ContextPanel.vue';
import NoticeModal from '../components/NoticeModal.vue';
import StatusBar from '../components/StatusBar.vue';
import ScenePathBar from '../components/ScenePathBar.vue';
import { useViewerStore } from '../stores/viewer.js';

const viewer = useViewerStore();

/** 手工打开盘阵场景目录（不扫盘：只把用户填的这个目录交给后端 stat 一次）。
 *  错误写进查看器的错误条（DecodeOverlay 那条）—— ScenesPage 写的是
 *  scenes.error，两处入口各自的错误面不同。 */
function openPastedPath(path: string): void {
  void viewer.openScenePath(path);
}

onMounted(() => {
  mountE2EHooks();      // window.__viewer（浏览器回归 + 真机验收）
});

// 模态提示不自动消失，而 store 是全局单例：不关就换页，它会跟着到别的页面上。
onUnmounted(() => viewer.hideModal());
</script>

<template>
  <div class="viewer-page">
    <Toolbar />
    <!-- 对比条在工具栏与路径栏之间：**刻意不放进 .toolbar** —— 那条有「1366 下不横向
         溢出」的 e2e 守卫，三个模式 + 图例 + 场景芯片塞进去必然溢出。
         代价记在 CompareBar 的文件头：多一行会改变舞台高度，而既有语义是尺寸一变就
         重新适配，所以展开/收起它会重置当前缩放。 -->
    <CompareBar />
    <ScenePathBar class="vp-pathbar" :busy="viewer.busy" @open="openPastedPath" />
    <div class="viewer-body">
      <FileList />
      <TifCanvas>
        <DrawPanel />
        <CompareOverlay />
        <DecodeOverlay />
      </TifCanvas>
      <ContextPanel />
    </div>
    <StatusBar />
    <!-- 挂在页面根（不在 TifCanvas 的 slot 里）：.stage 的 transform 祖先会困住 fixed -->
    <NoticeModal />
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

/* 盘阵场景栏：深色工具栏与画布之间的一条白色工作带（不参与 .viewer-body 的
   flex 计算，画布高度按剩余空间自适应） */
.vp-pathbar {
  flex: none;
  margin: 8px 12px 0;
}

.viewer-body {
  display: flex;
  flex: 1;
  min-height: 0;
}
</style>
