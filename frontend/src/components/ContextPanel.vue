<script setup lang="ts">
/**
 * ContextPanel.vue — viewer 右侧上下文侧舱（阶段6 v1.0 需求）
 * ------------------------------------------------------------------
 * 可折叠固定右栏（宽 400 = 左 FileList 的 400，默认收起、展开状态 localStorage 持久化；
 * 掩码绘制模式不自动收起）。顶部 [ROI / 工具]（默认）| [Agent] 两个 tab，子内容分别由
 * RoiToolsTab / AgentChatTab 承载（v-show 常驻，切换 tab 不打断队列 SSE / Agent 会话）。
 * DOM = 两个兄弟根节点（ctx-toggle + ctx-rail），与左侧 [sidebar, side-toggle] 镜像。
 *
 * **展开状态 2026-09-20 提升到 store**（原先在组件里）：分屏对比要能自动收起它、退出时
 * 再恢复用户此前的手动状态。localStorage 的 key 与默认值一字未改（见
 * stores/viewer.ts 的 readCtxRailOpen），老用户体验不变。
 */
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import RoiToolsTab from './RoiToolsTab.vue';
import AgentChatTab from './AgentChatTab.vue';
import AppIcon from './AppIcon.vue';
import AgentBotIcon from './AgentBotIcon.vue';
import { useChatStore } from '../stores/chat.js';

const store = useViewerStore();
const chat = useChatStore();
const tab = ref<'roi' | 'agent'>('roi');

/** store 里存的是「展开」，组件里用「收起」更自然（CSS 也是 .collapsed）。 */
const collapsed = computed({
  get: () => !store.ctxRailOpen,
  set: (v: boolean) => store.setCtxRailOpen(!v),
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
        ><AppIcon name="logs" :size="16" />ROI / 工具</button>
      <button
        type="button"
        class="ctx-tab"
        :class="{ on: tab === 'agent' }"
        @click="tab = 'agent'"
        ><AgentBotIcon :status="chat.agentStatus" :size="16" />Agent</button>
    </div>
    <div class="ctx-body">
      <!-- 两块都常驻（v-show），切换不打断队列 SSE / Agent 会话；淡入只做新进来那块，
           旧的立即让位。两个 pane 绝对定位叠在同一块区域 —— 否则 v-show 离场的那 100ms
           里两块各占 100% 高度，正在进入的那块会被挤出可视区。 -->
      <Transition name="ctx-content-fade">
        <div v-show="tab === 'roi'" class="ctx-pane">
          <RoiToolsTab />
        </div>
      </Transition>
      <Transition name="ctx-content-fade">
        <div v-show="tab === 'agent'" class="ctx-pane">
          <AgentChatTab />
        </div>
      </Transition>
    </div>
  </aside>
</template>

<style scoped>
/* 右栏 = 左 [sidebar(400) + toggle(26)] 的同族（同 chrome 底、同 toggle 样式），宽度也与
   左栏对齐：置顶的「待修复清单」里要排得下三段式进度条 + 驳回/非模糊通过两个按钮一行，
   再窄就得换行。两栏同时展开时 chrome 占 852px，1366 宽屏上画布只剩约 510px ——
   两边都能收起（各自栏边的 « / »），窄屏上收掉一边即可。 */
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
  transition: color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}
.ctx-toggle:hover {
  color: var(--accent-deep);
  background: var(--accent-soft);
}

.ctx-rail {
  width: 400px;
  flex: none;
  background: var(--chrome);
  border-left: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width var(--motion-fold) var(--ease-linear);
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: var(--r-ctrl) var(--r-ctrl) 0 0;
  background: transparent;
  color: var(--ink-sub);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: background var(--motion-hover) var(--ease-standard), color var(--motion-hover) var(--ease-standard);
}
.ctx-tab:hover { color: var(--accent-deep); }
.ctx-tab.on {
  background: var(--surface);
  color: var(--accent-deep);
  border-color: var(--line);
  box-shadow: 0 -2px 6px rgba(46, 90, 78, 0.05);
}

.ctx-body {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-bottom: none;
}
.ctx-pane { position: absolute; inset: 0; }
/* 只给**进入**的那块做淡入：离场留空反而干净（交叉淡化会让两段内容叠字）。
   没有 leave 规则 = Vue 认为无过渡，切 tab 时旧内容立即隐藏。 */
.ctx-content-fade-enter-active { transition: opacity var(--motion-fold) var(--ease-standard); }
.ctx-content-fade-enter-from { opacity: 0; }
</style>
