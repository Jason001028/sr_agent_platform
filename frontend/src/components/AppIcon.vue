<script setup lang="ts">
/** Small wrapper for the provided monochrome SVG set. */
import { computed } from 'vue';

const props = withDefaults(defineProps<{
  name: string;
  label?: string;
  size?: number;
}>(), { size: 16 });

const iconSrc = computed(() => `${import.meta.env.BASE_URL}icon/${props.name}.svg`);
</script>

<template>
  <img
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
</style>
