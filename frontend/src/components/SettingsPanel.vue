<script setup lang="ts">
/**
 * SettingsPanel.vue — 右上角设置浮层
 * ------------------------------------------------------------------
 * 目前两行：对比模式的后台预取开关（默认关），与本地预览缓存的行 + 清空按钮。
 *
 * 挂在**页面根**（ViewerPage.vue 里 `<NoticeModal />` 旁边），不要塞进工具栏 ——
 * `.toolbar` 是 `position: static`，fixed 浮层挂它里面会以初始包含块为参照，
 * 位置看着对、滚动起来就飘。同理也不放进 `.stage`（里面有 transform 祖先）。
 *
 * 关法三种：再点一次工具栏那颗按钮、点浮层外的任意处（透明捕手）、Esc。
 */
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();

/** 缓存的展示值。**跟着缓存的内容走**（打开浮层时读一次，之后每逢内容变化再读一次，
    见 store 的 previewCacheRev）—— 改这份缓存的按钮（预取开关）就在这一行上面，
    只读打开时那一次快照，用户点完开关盯着这行数字看，数字永远停在之前的值。 */
const cache = ref({ count: 0, bytes: 0, maxBytes: 0 });
const cacheText = ref('0 项 / 0.0 MB');

function refreshCache() {
  cache.value = store.previewCacheStats();
  cacheText.value = cache.value.count + ' 项 / '
    + (cache.value.bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

watch([() => store.settingsOpen, () => store.previewCacheRev],
  ([open]) => { if (open) refreshCache(); });

function clearCache() {
  store.clearPreviewCache();
  refreshCache();
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && store.settingsOpen) store.setSettingsOpen(false);
}
onMounted(() => window.addEventListener('keydown', onKey));
onUnmounted(() => window.removeEventListener('keydown', onKey));
</script>

<template>
  <template v-if="store.settingsOpen">
    <!-- 透明捕手：点浮层之外的任何地方即关（比只认「关」按钮省事，也不遮住画面） -->
    <div class="set-catch" @click="store.setSettingsOpen(false)"></div>
    <div class="set-panel" data-e2e="set-panel" role="dialog" aria-label="设置">
      <div class="set-title">设置</div>

      <div class="set-row">
        <button
          type="button"
          class="set-sw"
          :class="{ on: store.cmpPrefetchOn }"
          role="switch"
          :aria-checked="store.cmpPrefetchOn"
          data-e2e="set-prefetch"
          @click="store.setCmpPrefetch(!store.cmpPrefetchOn)"
        >对比模式后台预取{{ store.cmpPrefetchOn ? ' · 开' : ' · 关' }}</button>
        <div class="set-note">
          进对比模式时提前把同场景另两类图的预览取到本地，切图不必现取。
          只取服务端已有现成的那份，不会触发烘焙。
        </div>
        <!-- 上一次预取干了什么。**为「缓存行停在 0 项」提供解释**：
             第一次打开某个场景时合格项本来就是空的（另两类还没人烤过），
             不写出来，用户分不清「没东西可预取」与「预取坏了」。 -->
        <div v-if="store.prefetchNote" class="set-out" data-e2e="set-prefetch-note">
          {{ store.prefetchNote }}
        </div>
      </div>

      <div class="set-row set-row-cache">
        <span class="set-line">
          本地预览缓存 <span class="set-num" data-e2e="set-cache-line">{{ cacheText }}</span>
        </span>
        <button
          type="button"
          class="set-clear"
          data-e2e="set-cache-clear"
          :disabled="cache.count === 0"
          title="丢掉本地留着的预览，下次打开重新取一遍"
          @click="clearCache"
        >清空</button>
      </div>
    </div>
  </template>
</template>

<style scoped>
/* 与 NoticeModal 同一套 token（--surface / --line / --r-card / --shadow-pop），
   但它不是模态：没有压暗背景，右上角一张卡片，点外面就关。 */
.set-catch {
  position: fixed;
  inset: 0;
  z-index: 99;
}
.set-panel {
  position: fixed;
  top: 60px;          /* 工具栏 52px + 8px 缝 */
  right: 16px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 320px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-pop);
  font-size: 12px;
  color: var(--ink-body);
}
.set-title { color: var(--ink); font-size: 14px; font-weight: 600; }

.set-row { display: flex; flex-direction: column; gap: 6px; }
.set-row-cache {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

/* 开关：与 CompareBar 的 .cmp-mode 同一族（按钮 + .on，全仓没有 type=checkbox） */
.set-sw {
  align-self: flex-start;
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-pill);
  background: var(--surface-2);
  color: var(--ink-sub);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.set-sw:hover { color: var(--accent-deep); }
.set-sw.on {
  background: var(--accent-soft);
  border-color: var(--accent-2);
  color: var(--accent-deep);
}
.set-note { color: var(--ink-sub); font-size: 11px; line-height: 1.6; opacity: 0.9; }
/* 预取结果行：与说明同字号，但**不透明度更低** —— 它是过程回执，不是说明。 */
.set-out {
  color: var(--ink-sub);
  font-size: 11px;
  line-height: 1.6;
  opacity: 0.75;
}

.set-line { color: var(--ink-sub); }
.set-num { color: var(--ink); font-variant-numeric: tabular-nums; }
.set-clear {
  flex: none;
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-pill);
  background: var(--surface-2);
  color: var(--ink-sub);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.set-clear:hover:not(:disabled) { color: var(--accent-deep); background: var(--chrome); }
.set-clear:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
