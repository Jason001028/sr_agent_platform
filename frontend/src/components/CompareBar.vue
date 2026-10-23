<script setup lang="ts">
/**
 * CompareBar.vue — 图像对比条（2026-09-20）
 * ------------------------------------------------------------------
 * 由 ViewerPage 渲染在 `.toolbar` **之外**的第二行，展开/收起由 store.cmpStripOpen 管。
 *
 * 为什么不在工具栏里：`test-manual-scene.js` 有一条「1366 下工具栏不横向溢出、无子元素
 * 右边界越界」的守卫。放进工具栏就得把三个模式 + 图例 + 回正 + 三枚场景芯片一起塞进
 * 那一行 —— 那是必然溢出。**代价要如实记下**：多一行会改变舞台高度，而 `resize()` →
 * `setCanvasSize()` 的既有语义就是重新适配，所以展开/收起对比条会重置当前缩放
 * （分屏下两侧都重置）。这是既有行为（切路径栏同理），本轮不改它。
 *
 * 三选一用 `<button>` + `.on`（全仓没有 `type="radio"`），抄 ContextPanel 的
 * `.ctx-tab` 形态；补 `role="radio"` + `aria-checked` 让读屏知道这是个单选组。
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';
import type { CompareMode } from '../lib/compare';

const store = useViewerStore();

const MODES: { value: CompareMode; label: string; title: string }[] = [
  { value: 'off', label: '关闭', title: '一次看一张（拖入的新图顶掉当前这张，与原行为一致）' },
  { value: 'click', label: '点选对比', title: '拖入的新图直接覆盖当前这张；「点选清单」里可来回切' },
  { value: 'split', label: '分屏对比', title: '左右各一张，滚轮与拖动同步作用于两侧；拖放落在哪半就进哪半' },
];

/** 只有分屏需要图例与「回正」。 */
const isSplit = computed(() => store.compareMode === 'split');

/** 场景芯片：绑**活动侧**的 rec。活动侧是本地文件（没有 sceneId）→ 整排禁用。 */
const sceneId = computed(() => store.activeSceneId());

const CHIPS: { kind: 'input' | 'product' | 'nosr'; label: string }[] = [
  { kind: 'input', label: '输入影像' },
  { kind: 'product', label: '本轮超分产物' },
  { kind: 'nosr', label: 'NOSR' },
];

function pick(kind: 'input' | 'product' | 'nosr') {
  void store.openSceneSibling(kind);
}
</script>

<template>
  <Transition name="cmp-bar-fade">
    <div v-if="store.cmpStripOpen" class="cmp-bar" data-e2e="cmp-bar">
      <div class="cmp-modes" role="radiogroup" aria-label="图像对比模式">
        <button
          v-for="m in MODES"
          :key="m.value"
          type="button"
          class="cmp-mode"
          role="radio"
          :aria-checked="store.compareMode === m.value"
          :class="{ on: store.compareMode === m.value }"
          :title="m.title"
          :data-e2e="'cmp-mode-' + m.value"
          @click="store.setCompareMode(m.value)"
        >{{ m.label }}</button>
      </div>

      <template v-if="isSplit">
        <span class="cmp-sep" aria-hidden="true"></span>
        <span class="cmp-legend">
          <span class="cmp-chip-a">左</span>
          <span class="cmp-chip-b">右</span>
          <span class="cmp-legend-text">
            拖放至左右侧；左右同步预览
          </span>
        </span>
        <button
          type="button"
          class="cmp-reset"
          data-e2e="cmp-reset"
          title="分隔线回到中间，两侧重新适配"
          @click="store.resetSplit()"
        >回正</button>
      </template>

      <span class="cmp-sep" aria-hidden="true"></span>
      <span class="cmp-scenes">
        <button
          v-for="c in CHIPS"
          :key="c.kind"
          type="button"
          class="cmp-scene"
          :disabled="!sceneId || store.busy"
          :title="sceneId
            ? '打开这个场景的' + c.label + '，显示在活动侧'
              + '（服务端生成的下采样 JPG，不必本地解码）'
            : '先打开一张带盘阵关联的图（场景目录打开的场景图，或反推关联上的本地图）'"
          :data-e2e="'cmp-sib-' + c.kind"
          @click="pick(c.kind)"
        >{{ c.label }}</button>
        <span v-if="!sceneId" class="cmp-note" data-e2e="cmp-sib-note">
          先打开一张带盘阵关联的图
        </span>
      </span>
    </div>
  </Transition>
</template>

<style scoped>
/* 浅色条：深色工具栏与画布之间（白面浮层卡，铺满整宽）。
   高度由内容决定，不设固定值 —— 分屏时才出现的图例与「回正」不该把这条撑得忽高忽低。 */
.cmp-bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 8px 12px 0;
  padding: 6px 10px;
  background: var(--surface);
  border: 1px solid var(--tb-divider);
  border-radius: var(--r-ctrl);
  font-size: 12px;
  color: var(--tb-fg);
  overflow: hidden;
}
.cmp-bar-fade-enter-active,
.cmp-bar-fade-leave-active { transition: opacity var(--motion-fade) var(--ease-standard), transform var(--motion-fade) var(--ease-standard); }
.cmp-bar-fade-enter-from,
.cmp-bar-fade-leave-to { opacity: 0; transform: translateY(-3px); }

.cmp-modes {
  display: flex;
  gap: 2px;
  flex: none;
}
/* 三选一的选中态与工具栏的模式激活同一套 token（--tb-active-*），
   两者说的是同一件事：对比模式还开着。 */
.cmp-mode {
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-pill);
  background: var(--tb-bg);
  color: var(--tb-muted);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--motion-hover) var(--ease-standard), color var(--motion-hover) var(--ease-standard), border-color var(--motion-hover) var(--ease-standard);
}
.cmp-mode:hover { color: var(--tb-active-fg); }
.cmp-mode.on {
  background: var(--tb-active-bg);
  border-color: var(--tb-active-border);
  color: var(--tb-active-fg);
}

.cmp-sep {
  flex: none;
  width: 1px;
  height: 18px;
  background: var(--tb-divider);
}

.cmp-legend {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}
.cmp-chip-a,
.cmp-chip-b {
  padding: 1px 7px;
  border-radius: var(--r-pill);
  font-size: 11px;
  font-weight: 600;
}
.cmp-chip-a { background: var(--cmp-a-bg); color: var(--cmp-a); border: 1px solid var(--cmp-a-line); }
.cmp-chip-b { background: var(--cmp-b-bg); color: var(--cmp-b); border: 1px solid var(--cmp-b-line); }
.cmp-legend-text { color: var(--ink-sub); }

.cmp-reset {
  flex: none;
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-ctrl);
  background: var(--tb-bg);
  color: var(--tb-fg);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.cmp-reset:hover { color: var(--tb-active-fg); border-color: var(--tb-active-border); }

.cmp-scenes {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.cmp-scene {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-ctrl);
  background: var(--tb-bg);
  color: var(--tb-fg);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.cmp-scene:hover:not(:disabled) { color: var(--tb-active-fg); border-color: var(--tb-active-border); }
.cmp-scene:disabled { opacity: 0.5; cursor: not-allowed; }
.cmp-note { color: var(--tb-muted); white-space: nowrap; }
</style>
