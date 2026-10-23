<script setup lang="ts">
/**
 * SettingsPanel.vue — 右上角设置浮层
 * ------------------------------------------------------------------
 * 目前几块：聊天流式输出（本机偏好，默认开）、对比模式后台预取（默认关）、
 * 本地预览缓存行 + 清空按钮，以及开发者模式验证。
 *
 * 挂在**页面根**（ViewerPage.vue 里 `<NoticeModal />` 旁边），不要塞进工具栏 ——
 * `.toolbar` 是 `position: static`，fixed 浮层挂它里面会以初始包含块为参照，
 * 位置看着对、滚动起来就飘。同理也不放进 `.stage`（里面有 transform 祖先）。
 *
 * 关法三种：再点一次工具栏那颗按钮、点浮层外的任意处（透明捕手）、Esc。
 */
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import {
  clearDeveloperMode, isDeveloperMode, verifyDeveloperPassword,
} from '../lib/developerMode';
import { isStreamingEnabled, setStreamingEnabled } from '../lib/streaming';

const store = useViewerStore();

/** 缓存的展示值。**跟着缓存的内容走**（打开浮层时读一次，之后每逢内容变化再读一次，
    见 store 的 previewCacheRev）—— 改这份缓存的按钮（预取开关）就在这一行上面，
    只读打开时那一次快照，用户点完开关盯着这行数字看，数字永远停在之前的值。 */
const cache = ref({ count: 0, bytes: 0, maxBytes: 0 });
const cacheText = ref('0 项 / 0.0 MB');
const developerPassword = ref('');
const developerError = ref('');
const developerVerified = ref(isDeveloperMode());
/** 聊天流式输出（本机浏览器偏好，下一回合生效）。 */
const streamingOn = ref(isStreamingEnabled());

function toggleStreaming() {
  streamingOn.value = !streamingOn.value;
  setStreamingEnabled(streamingOn.value);
}

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

function verifyDeveloperMode() {
  developerError.value = '';
  if (!verifyDeveloperPassword(developerPassword.value)) {
    developerError.value = '密码错误';
    return;
  }
  developerPassword.value = '';
  developerVerified.value = true;
}

function revokeDeveloperMode() {
  clearDeveloperMode();
  developerVerified.value = false;
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
          :class="{ on: streamingOn }"
          role="switch"
          :aria-checked="streamingOn"
          data-e2e="set-stream"
          @click="toggleStreaming"
        >聊天流式输出{{ streamingOn ? ' · 开' : ' · 关' }}</button>
        <div class="set-note">
          Agent 回答逐字上屏，思考过程单独折叠。关闭后等服务端整段生成完再显示；
          对已经开始的回合不生效。服务端 <code>SR_LLM_STREAM=0</code> 是总开关，
          置 0 后本项不生效。
        </div>
      </div>

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
          只取服务端已有现成的那份，不会触发生成预览。
        </div>
        <!-- 上一次预取干了什么。**为「缓存行停在 0 项」提供解释**：
             第一次打开某个场景时合格项本来就是空的（另两类还没人生成过），
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

      <div class="set-row set-row-developer" data-e2e="set-developer">
        <span class="set-line">开发者模式</span>
        <template v-if="developerVerified">
          <div class="set-dev-status">已验证，本机可查看其他机器插件</div>
          <button type="button" class="set-clear" @click="revokeDeveloperMode">关闭</button>
        </template>
        <template v-else>
          <div class="set-dev-form">
            <input
              v-model="developerPassword"
              class="set-dev-input"
              type="password"
              autocomplete="off"
              placeholder="输入开发者密码"
              @keyup.enter="verifyDeveloperMode"
            />
            <button type="button" class="set-clear"
                    :disabled="!developerPassword" @click="verifyDeveloperMode">
              验证并开启
            </button>
          </div>
          <div v-if="developerError" class="set-dev-error">{{ developerError }}</div>
        </template>
        <div class="set-note">每台机器只需验证一次，验证状态保存在当前机器浏览器中。</div>
      </div>
    </div>
  </template>
</template>

<style scoped>
/* 与工具栏同一套语义 token（--tb-*）：白底、灰绿细描边、10px 圆角、轻量投影。
   它不是模态：没有压暗背景，右上角一张卡片，点外面就关。 */
.set-catch {
  position: fixed;
  inset: 0;
  z-index: 99;
}
.set-panel {
  position: fixed;
  /* 品牌导航（--topbar-h 52px）+ 工具栏 48px + 8px 缝：浮层落在工具栏**下方**，
     不压住右端的「提交 SR / 设置」两键。 */
  top: calc(var(--topbar-h) + 56px);
  right: 16px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 320px;
  padding: 14px 16px;
  background: var(--tb-surface);
  border: 1px solid var(--tb-border);
  border-radius: 10px;
  box-shadow: var(--shadow-pop);
  font-size: 12px;
  color: var(--tb-fg);
}
.set-title { color: var(--tb-fg); font-size: 14px; font-weight: 600; }

.set-row { display: flex; flex-direction: column; gap: 6px; }
.set-row-cache {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

/* 开关：与 CompareBar 的 .cmp-mode 同一族（按钮 + .on，全仓没有 type=checkbox）。
   选中面取工具栏的 --tb-active-*，浮层与工具栏是同一套视觉语言。 */
.set-sw {
  align-self: flex-start;
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
.set-sw:hover { color: var(--tb-active-fg); }
.set-sw.on {
  background: var(--tb-active-bg);
  border-color: var(--tb-active-border);
  color: var(--tb-active-fg);
}
.set-note { color: var(--tb-muted); font-size: 11px; line-height: 1.6; opacity: 0.9; }
.set-note code {
  font-family: var(--font-mono);
  font-size: 0.95em;
  background: var(--surface-2);
  border-radius: 3px;
  padding: 0 3px;
}
/* 预取结果行：与说明同字号，但**不透明度更低** —— 它是过程回执，不是说明。 */
.set-out {
  color: var(--tb-muted);
  font-size: 11px;
  line-height: 1.6;
  opacity: 0.75;
}

.set-line { color: var(--tb-muted); }
.set-num { color: var(--tb-fg); font-variant-numeric: tabular-nums; }
.set-clear {
  flex: none;
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-pill);
  background: var(--tb-bg);
  color: var(--tb-muted);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background var(--motion-hover) var(--ease-standard), color var(--motion-hover) var(--ease-standard);
}
.set-clear:hover:not(:disabled) { color: var(--tb-active-fg); background: var(--tb-hover); }
.set-clear:disabled { opacity: 0.5; cursor: not-allowed; }
.set-row-developer { border-top: 1px solid var(--tb-divider); padding-top: 10px; }
.set-dev-form { display: flex; gap: 7px; align-items: center; }
.set-dev-input { flex: 1; min-width: 0; height: 28px; padding: 0 9px; border: 1px solid var(--tb-border); border-radius: 8px; background: var(--tb-bg); color: var(--tb-fg); font: inherit; font-size: 12px; }
.set-dev-input:focus { border-color: var(--tb-focus); outline: none; }
.set-dev-status { color: var(--ok); font-size: 11px; line-height: 1.5; }
.set-dev-error { color: var(--err); font-size: 11px; }
</style>
