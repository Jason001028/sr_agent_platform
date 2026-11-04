<script setup lang="ts">
/**
 * FlickerControls.vue — 交替对比悬浮胶囊（2026-11-03）
 * ------------------------------------------------------------------
 * 只在 `compareMode === 'flicker'` 时出现，浮在画布上方居中：**不占工具栏那一行**
 * （那里有「1366 不横向溢出」的 e2e 守卫）。
 *
 * 胶囊内容按用户定的顺序：`交替对比 | PAN/NOSR | 1.0秒 | 播放/暂停`。
 * 两个名字里**当前可见的那张**高亮 —— 画面每翻一次面，高亮就跟着换，
 * 不需要另起一行写「当前：xxx」。
 *
 * 播放是**用户动作**：本组件只调 store，不自己排计时器。循环、页面隐藏、
 * prefers-reduced-motion、配对失效这些收口全在 stores/viewer.ts 里，
 * 组件卸载也不会留下在跑的 setTimeout（store.stopFlicker 由页面卸载时调）。
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();

/** 0.6 / 0.8 / 1.0 / 1.2 / 1.5 秒。 */
const intervals = computed(() => store.flickerIntervals.map((ms) => ({
  ms,
  label: (ms / 1000).toFixed(1) + '秒',
})));

const playing = computed(() => store.flickerPlaying);
const blocked = computed(() => !!store.flickerReason);
const showAlt = computed(() => store.flickerIndex === 1);

function onInterval(e: Event) {
  const ms = Number((e.target as HTMLSelectElement).value);
  store.setFlickerInterval(ms);
}

function toggle() {
  store.flickerToggle();
}
</script>

<template>
  <Transition name="fx-capsule-fade">
    <div
      v-if="store.compareMode === 'flicker'"
      class="fx-wrap"
      data-e2e="flicker-capsule"
      @mousedown.stop
      @dblclick.stop
      @wheel.stop
    >
      <div class="fx-capsule" role="group" aria-label="交替对比控制">
        <span class="fx-title">交替对比</span>

        <span class="fx-sep" aria-hidden="true"></span>

        <span class="fx-pair" data-e2e="flicker-pair" :title="store.flickerCurrentName">
          <span
            class="fx-name"
            :class="{ on: !showAlt }"
            data-e2e="flicker-name-a"
          >{{ store.flickerNames.a || '—' }}</span>
          <span class="fx-slash" aria-hidden="true">/</span>
          <span
            class="fx-name"
            :class="{ on: showAlt }"
            data-e2e="flicker-name-b"
          >{{ store.flickerNames.b || '—' }}</span>
        </span>

        <span class="fx-sep" aria-hidden="true"></span>

        <label class="fx-interval" title="两张图交替的节拍">
          <select
            :value="store.flickerIntervalMs"
            data-e2e="flicker-interval"
            aria-label="交替间隔"
            @change="onInterval"
          >
            <option v-for="it in intervals" :key="it.ms" :value="it.ms">{{ it.label }}</option>
          </select>
        </label>

        <button
          type="button"
          class="fx-toggle"
          :class="{ playing }"
          :disabled="blocked && !playing"
          :title="blocked
            ? store.flickerReason
            : (playing ? '停在当前这一张' : '按选择的节拍来回交替；平移、缩放、切图会暂停')"
          :data-e2e="'flicker-' + (playing ? 'pause' : 'play')"
          @click="toggle"
        >{{ playing ? '暂停' : '播放' }}</button>
      </div>

      <div v-if="store.flickerReason" class="fx-reason" data-e2e="flicker-reason">
        {{ store.flickerReason }}
      </div>
    </div>
  </Transition>
</template>

<style scoped>
/* 浮在画布顶部居中；宽度随内容，最长一行是「交替对比 | PAN/NOSR | 1.0秒 | 播放」。
   深色胶囊与深色工具栏同一族（--tb-*），在浅画布井位与亮影像上都够对比。 */
.fx-wrap {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 5;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  max-width: calc(100% - 32px);
  pointer-events: none;
}
.fx-capsule {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-pill);
  background: var(--tb-bg);
  color: var(--tb-fg);
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  box-shadow: 0 2px 8px rgba(18, 14, 38, 0.18);
}
.fx-capsule-fade-enter-active,
.fx-capsule-fade-leave-active { transition: opacity var(--motion-fade) var(--ease-standard), transform var(--motion-fade) var(--ease-standard); }
.fx-capsule-fade-enter-from,
.fx-capsule-fade-leave-to { opacity: 0; transform: translateX(-50%) translateY(-3px); }

.fx-title {
  font-weight: 600;
  color: var(--tb-active-fg);
  flex: none;
}
.fx-sep {
  flex: none;
  width: 1px;
  height: 14px;
  background: var(--tb-divider);
}
.fx-pair {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
  min-width: 0;
  max-width: 220px;
  overflow: hidden;
}
.fx-name {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--tb-muted);
  font-variant-numeric: tabular-nums;
  /* 只是文字强调的过渡，不是画面切换的淡入淡出 —— 影像翻面是硬切。 */
  transition: color var(--motion-hover) var(--ease-standard), font-weight var(--motion-hover) var(--ease-standard);
}
.fx-name.on {
  color: var(--tb-active-fg);
  font-weight: 700;
}
.fx-slash { color: var(--tb-muted); flex: none; }

.fx-interval { flex: none; display: flex; align-items: center; }
.fx-interval select {
  height: 22px;
  padding: 0 4px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-ctrl);
  background: var(--tb-bg);
  color: var(--tb-fg);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.fx-interval select:focus-visible,
.fx-toggle:focus-visible { outline: 2px solid var(--tb-active-border); outline-offset: 1px; }

.fx-toggle {
  flex: none;
  height: 24px;
  min-width: 52px;
  padding: 0 10px;
  border: 1px solid var(--tb-border);
  border-radius: var(--r-pill);
  background: var(--tb-bg);
  color: var(--tb-fg);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--motion-hover) var(--ease-standard), color var(--motion-hover) var(--ease-standard), border-color var(--motion-hover) var(--ease-standard);
}
.fx-toggle:hover:not(:disabled) {
  color: var(--tb-active-fg);
  border-color: var(--tb-active-border);
}
.fx-toggle.playing {
  background: var(--tb-active-bg);
  border-color: var(--tb-active-border);
  color: var(--tb-active-fg);
}
.fx-toggle:disabled { opacity: 0.5; cursor: not-allowed; }

/* 配准/缺图原因：紧贴胶囊下方一条浅色提示，说明为什么不能自动交替。 */
.fx-reason {
  pointer-events: auto;
  max-width: min(520px, 100%);
  padding: 5px 10px;
  border: 1px solid var(--warn-line);
  border-radius: var(--r-ctrl);
  background: var(--warn-bg);
  color: var(--warn);
  font-size: 12px;
  line-height: 1.45;
  text-align: center;
  box-shadow: 0 2px 8px rgba(18, 14, 38, 0.12);
}
</style>
