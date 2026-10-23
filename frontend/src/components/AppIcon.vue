<script setup lang="ts">
/** Small wrapper for the provided monochrome SVG set. */
import { computed } from 'vue';

const props = withDefaults(defineProps<{
  name: string;
  label?: string;
  size?: number;
  /** 按 currentColor 着色（默认 false，仍走 <img>）。
      外部 <img> 引进来的 SVG 取不到父级 currentColor，只能把图标自身当遮罩、
      用 background-color 画出那个形状 —— 素材文件不动，颜色随按钮状态走
      （普通深绿 / 激活青绿 / 主按钮白 / 禁用灰绿），hover 也不会黑白反转闪一下。 */
  tint?: boolean;
}>(), { size: 16, tint: false });

const iconSrc = computed(() => `${import.meta.env.BASE_URL}icon/${props.name}.svg`);
/** 遮罩靠 inline style 带每个图标自己的 URL（CSS 读不到 <img>/<span> 的 src）。 */
const tintStyle = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  WebkitMaskImage: `url(${iconSrc.value})`,
  maskImage: `url(${iconSrc.value})`,
}));
</script>

<template>
  <span
    v-if="props.tint"
    class="app-icon app-icon-tint"
    :style="tintStyle"
    :role="props.label ? 'img' : undefined"
    :aria-label="props.label"
    :aria-hidden="props.label ? undefined : 'true'"
  ></span>
  <img
    v-else
    class="app-icon"
    :src="iconSrc"
    :alt="props.label ?? ''"
    :width="props.size"
    :height="props.size"
    :aria-hidden="props.label ? undefined : 'true'"
    draggable="false"
  />
</template>

<style scoped>
.app-icon {
  display: block;
  flex: none;
  width: auto;
  height: auto;
}
/* 只保留遮罩所需的形状属性；颜色取 background-color，由祖先的 color 决定。
   尺寸写在 inline style（tintStyle）里，覆盖上面的 auto。 */
.app-icon-tint {
  background-color: currentColor;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-position: center;
  -webkit-mask-size: contain;
  mask-size: contain;
}
</style>
