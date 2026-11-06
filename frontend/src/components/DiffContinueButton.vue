<script setup lang="ts">
/**
 * DiffContinueButton.vue — 「延续到下一景」
 * ------------------------------------------------------------------
 * 只在本景已有热力结果、查看器是单图、左侧列表还有下一景时出现，位置紧挨
 * 「差异定位」。视觉沿用鹰眼导航的工具键：28px、tb 令牌、圆角 8、浅阴影，
 * 默认半透明，悬停 / 键盘聚焦时恢复不透明。
 *
 * 点击不改任何参数：A/B 的角色（PAN / NOSR / SR…）、显示方式、不透明度、
 * 羽化、方法与灵敏度都由 stores/diff.ts 的 continueToNextScene() 原样带过去。
 */
import { useDiffStore } from '../stores/diff';

const diff = useDiffStore();
</script>

<template>
  <button
    v-if="diff.canContinueScene"
    type="button"
    class="diff-continue"
    data-e2e="diff-continue"
    :disabled="diff.busy"
    aria-label="延续到下一景"
    title="沿用当前 A / B 的角色与显示方式，直接分析左侧列表里的下一景热力图"
    @pointerdown.stop
    @click="diff.continueToNextScene()"
  >
    {{ diff.busy ? '续景中…' : '延续到下一景' }}
  </button>
</template>

<style scoped>
.diff-continue {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  padding: 0 9px;
  box-sizing: border-box;
  white-space: nowrap;
  border: 1px solid var(--tb-border);
  border-radius: 8px;
  background: var(--tb-bg);
  color: var(--tb-fg);
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  box-shadow: 0 1px 3px #183c3014;
  opacity: .45;
  cursor: pointer;
  transition: opacity var(--motion-hover) var(--ease-standard),
              background var(--motion-hover),
              color var(--motion-hover);
}
.diff-continue:hover:enabled,
.diff-continue:focus-visible {
  opacity: 1;
  background: var(--tb-hover);
}
.diff-continue:focus-visible { outline: 2px solid var(--tb-focus); outline-offset: 2px; }
.diff-continue:disabled { cursor: default; }
@media (prefers-reduced-motion: reduce) {
  .diff-continue { transition: none; }
}
</style>
