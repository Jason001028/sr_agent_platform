<script setup lang="ts">
/**
 * StatusBar.vue — 底部当前文件身份条（rec-bar）
 * ------------------------------------------------------------------
 * **只显示图名**（分屏时前面挂 [左]/[右]）—— 2026-09-28 起这里不再复读元信息：
 * 尺寸/布局左栏那张卡上就有（FileList.vue 的 meta 行），路由与预览像素尺寸是
 * 「这张图是怎么来的」的排障项，常驻一整行对看图的人是噪音。
 * 要看时把鼠标停在这条上：title 里仍旧拼全（`barTitle`，字段与旧版逐条相同）。
 * 数据来自 store.activeRec（解码完成前 probe/route 可能为空，容错显示）。
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();

/** 悬停全文：身份 + 元信息，字段与「只显示图名」之前那版逐条相同，只是不再常驻。 */
const barTitle = computed(() => {
  const r = store.activeRec;
  if (!r) return '';
  const parts: string[] = [r.name];
  if (r.W) {
    parts.push(`${r.W}×${r.H}`);
    if (r.probe) {
      parts.push(`bits=${r.probe.bits} spp=${r.probe.spp} sf=${r.probe.sampleFormat}`
        + ` ph=${r.probe.photometric} comp=${r.probe.compression}`);
    }
    parts.push(`路由=${r.route || '…'}`, `预览 ${r.srcw}×${r.srch}`);
  }
  if (r.layout) parts.push(r.layout);
  return parts.join(' · ');
});
</script>

<template>
  <div v-if="store.activeRec" class="rec-bar" :title="barTitle">
    <!-- 分屏时先说清这条讲的是**哪一侧**：名字跟着 activeRec 走，也就是活动侧那张。
         不说这一句，分屏下看名字会认错图。两颗 span 之间不留换行 —— Vue 会把
         它编成空白文本节点，名字的 textContent 就多出空格（e2e 有整串比对）。 -->
    <span v-if="store.split" class="side" data-e2e="sb-side">[{{ store.activeSide === 'A' ? '左' : '右' }}]</span><span class="name">{{ store.activeRec.name }}</span>
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
.rec-bar .side {
  font-weight: 600;
  color: var(--cmp-active);
  margin-right: 4px;
}
</style>
