<script setup lang="ts">
defineProps<{ name: string; busy: boolean }>();
defineEmits<{ analyze: [] }>();
import AppIcon from './AppIcon.vue';
</script>

<template>
  <div class="image-context">
    <button class="image-pill" :disabled="busy" :title="name + ' · 分析当前影像'" @click="$emit('analyze')">
      <span class="image-pill-icon"><AppIcon name="camera" :size="15" /></span>
      <span class="image-pill-copy"><span class="image-pill-label">当前影像已就绪</span><span class="image-pill-name">{{ name }}</span></span>
      <span class="image-pill-action">{{ busy ? '分析中…' : '分析影像' }}<span aria-hidden="true">↗</span></span>
    </button>
  </div>
</template>

<style scoped>
.image-context{padding:10px 12px 4px;min-width:0;flex:none}
.image-pill{display:flex;align-items:center;gap:9px;width:100%;min-width:0;box-sizing:border-box;border:1px solid var(--line,#d8e4df);border-radius:24px;padding:8px 12px;background:#fff;color:var(--text,#203e36);box-shadow:0 1px 2px #00000005,0 4px 12px #00000006;cursor:pointer;text-align:left;transition:border-color 150ms,box-shadow 150ms}
.image-pill:hover:not(:disabled){border-color:#a6c9c3;box-shadow:0 2px 10px #155c6612}
.image-pill:focus-visible{outline:2px solid #287f8b;outline-offset:2px}.image-pill:disabled{opacity:.65;cursor:default}
.image-pill-icon{display:grid;place-items:center;flex:none;width:29px;height:29px;border-radius:50%;background:#f1f7f4}
.image-pill-copy{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}.image-pill-label{font-size:11px;color:var(--text-muted,#758780)}
.image-pill-name{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:500}
.image-pill-action{display:flex;align-items:center;gap:5px;flex:none;font-size:12px;color:#155c66;white-space:nowrap}
</style>
