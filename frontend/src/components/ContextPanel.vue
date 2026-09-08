<script setup lang="ts">
/**
 * ContextPanel.vue — viewer 右侧上下文侧舱（阶段6 v1.0 需求）
 * ------------------------------------------------------------------
 * 可折叠固定右栏（宽 ≤ 左 FileList 的 268，默认收起、展开状态 localStorage 持久化；
 * 掩码绘制模式不自动收起）。顶部 [ROI / 工具]（默认）| [Agent] 两个 tab，子内容分别由
 * RoiToolsTab / AgentChatTab 承载（v-show 常驻，切换 tab 不打断队列 SSE / Agent 会话）。
 * DOM = 两个兄弟根节点（ctx-toggle + ctx-rail），与左侧 [sidebar, side-toggle] 镜像。
 */
import { onMounted, ref, watch } from 'vue';
import RoiToolsTab from './RoiToolsTab.vue';
import AgentChatTab from './AgentChatTab.vue';

const OPEN_KEY = 'sr.viewer.ctxRailOpen';   // '1'=展开（默认收起）

function readOpen(): boolean {
  try {
    if (typeof localStorage === 'undefined') return false;
    return localStorage.getItem(OPEN_KEY) === '1';
  } catch {
    return false;
  }
}

const collapsed = ref(!readOpen());
const tab = ref<'roi' | 'agent'>('roi');

onMounted(() => {
  // 路由离开 /viewer 时组件卸载即自然收起位；展开状态跨页持久化由 watch 负责
});

watch(collapsed, (v) => {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(OPEN_KEY, v ? '0' : '1');
  } catch { /* 隐私模式等写入失败忽略 */ }
});

const toggleGlyph = () => (collapsed.value ? '«' : '»');
const toggleTitle = () => (collapsed.value ? '展开上下文侧舱' : '收起上下文侧舱');
</script>

<template>
  <div
    class="ctx-toggle"
    :title="toggleTitle()"
    @click="collapsed = !collapsed"
  >{{ toggleGlyph() }}</div>

  <aside class="ctx-rail" :class="{ collapsed }">
    <div class="ctx-tabs">
      <button
        type="button"
        class="ctx-tab"
        :class="{ on: tab === 'roi' }"
        @click="tab = 'roi'"
      >ROI / 工具</button>
      <button
        type="button"
        class="ctx-tab"
        :class="{ on: tab === 'agent' }"
        @click="tab = 'agent'"
      >Agent</button>
    </div>
    <div class="ctx-body">
      <RoiToolsTab v-show="tab === 'roi'" />
      <AgentChatTab v-show="tab === 'agent'" />
    </div>
  </aside>
</template>

<style scoped>
/* 右栏 = 左 [sidebar(268) + toggle(26)] 的镜像：同宽、同 chrome 底、同 toggle 样式 */
.ctx-toggle {
  flex: none;
  width: 26px;
  background: var(--surface-2);
  border-right: none;
  border-left: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--ink-sub);
  font-size: 13px;
  user-select: none;
  transition: color 0.15s ease, background 0.15s ease;
}
.ctx-toggle:hover {
  color: var(--accent-deep);
  background: var(--accent-soft);
}

.ctx-rail {
  width: 268px;
  flex: none;
  background: var(--chrome);
  border-left: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.12s ease;
}
.ctx-rail.collapsed {
  width: 0;
  border-left: none;
}

.ctx-tabs {
  flex: none;
  display: flex;
  gap: 2px;
  padding: 6px 6px 0;
  background: var(--chrome);
}
.ctx-tab {
  flex: 1;
  height: 30px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: var(--r-ctrl) var(--r-ctrl) 0 0;
  background: transparent;
  color: var(--ink-sub);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.ctx-tab:hover { color: var(--accent-deep); }
.ctx-tab.on {
  background: var(--surface);
  color: var(--accent-deep);
  border-color: var(--line);
  box-shadow: 0 -2px 6px rgba(46, 90, 78, 0.05);
}

.ctx-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-bottom: none;
}
</style>
