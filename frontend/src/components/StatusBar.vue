<script setup lang="ts">
/**
 * StatusBar.vue — 底部当前文件元信息条（rec-bar）
 * ------------------------------------------------------------------
 * 保持阶段2 demo 的 `.rec-bar` 格式（e2e 断言依赖）：
 * name · W×H · bits/spp/sf/ph/comp · 路由 · 预览尺寸 · layout。
 * 数据来自 store.activeRec（解码完成前 probe/route 可能为空，容错显示）。
 */
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();
</script>

<template>
  <div v-if="store.activeRec" class="rec-bar">
    <!-- 分屏时先说清这条讲的是**哪一侧**：整条的字段全部来自 activeRec，也就是
         活动侧那张（掩码/云量/任务状态跟着它走）。不说这一句，分屏下看数字会认错图。 -->
    <span v-if="store.split" class="side" data-e2e="sb-side">
      [{{ store.activeSide === 'A' ? '左' : '右' }}]
    </span>
    <span class="name">{{ store.activeRec.name }}</span>
    <template v-if="store.activeRec.W">
      · {{ store.activeRec.W }}×{{ store.activeRec.H }}
      <template v-if="store.activeRec.probe">
        · bits={{ store.activeRec.probe.bits }} spp={{ store.activeRec.probe.spp }}
        sf={{ store.activeRec.probe.sampleFormat }} ph={{ store.activeRec.probe.photometric }}
        comp={{ store.activeRec.probe.compression }}
      </template>
      · 路由={{ store.activeRec.route || '…' }} · 预览 {{ store.activeRec.srcw }}×{{ store.activeRec.srch }}
    </template>
    <span v-if="store.activeRec.layout" class="layout"> · {{ store.activeRec.layout }}</span>
  </div>
</template>

<style scoped>
.rec-bar {
  flex: none;
  font-size: 12px;
  color: var(--ink-sub);
  background: var(--chrome);
  border-top: 1px solid var(--line);
  padding: 7px 16px;
  word-break: break-all;
  line-height: 1.6;
  font-variant-numeric: tabular-nums;
}
.rec-bar .name {
  font-weight: 600;
  color: var(--ink);
}
.rec-bar .layout {
  color: var(--ink-faint);
}
.rec-bar .side {
  font-weight: 600;
  color: var(--cmp-active);
}
</style>
