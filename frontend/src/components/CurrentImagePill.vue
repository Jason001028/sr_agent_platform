<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { installButtonPress } from '../lib/buttonPress';
import AppIcon from './AppIcon.vue';

defineProps<{ name: string; busy: boolean }>();
defineEmits<{ analyze: [] }>();
const context = ref<HTMLElement | null>(null);
let disposePress: (() => void) | undefined;

onMounted(() => {
  if (context.value && typeof window.matchMedia === 'function') {
    disposePress = installButtonPress(context.value, '.image-pill-action');
  }
});
onBeforeUnmount(() => disposePress?.());
</script>

<template>
  <div ref="context" class="image-context">
    <div class="image-pill">
      <span class="image-pill-icon"><AppIcon name="camera" :size="15" /></span>
      <span class="image-pill-copy"><span class="image-pill-label">当前影像已就绪</span><span class="image-pill-name" :title="name">{{ name }}</span></span>
      <button type="button" class="image-pill-action" :disabled="busy" :aria-busy="busy"
        :title="name + ' · 分析当前影像'" @click="$emit('analyze')">
        {{ busy ? '分析中…' : '分析影像' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.image-context{padding:10px 12px 4px;min-width:0;flex:none}
.image-pill{display:flex;align-items:center;gap:9px;width:100%;min-width:0;box-sizing:border-box;border:1px solid var(--line,#d8e4df);border-radius:24px;padding:8px 10px 8px 12px;background:#fff;color:var(--text,#203e36);box-shadow:0 1px 2px #00000005,0 4px 12px #00000006}
.image-pill-icon{display:grid;place-items:center;flex:none;width:29px;height:29px;border-radius:50%;background:#f1f7f4}
.image-pill-copy{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}.image-pill-label{font-size:11px;color:var(--text-muted,#758780)}
.image-pill-name{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:500}
.image-pill-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  min-height: 28px;
  padding: 0 8px;
  border: 1px solid var(--tb-border, #ccdad5);
  border-radius: 7px;
  background: var(--tb-bg, #f5f8f6);
  color: var(--tb-primary, #166c67);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  white-space: nowrap;
}
.image-pill-action:hover:enabled { background: var(--tb-hover, #ebf3f0); }
.image-pill-action:focus-visible { outline: 2px solid var(--tb-focus, #2d968f); outline-offset: 2px; }
.image-pill-action:disabled { opacity: .65; cursor: default; }
</style>
