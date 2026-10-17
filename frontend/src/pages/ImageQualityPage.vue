<script setup lang="ts">
/** 可信源码插件示范页面：通过上下文契约读取影像和 ROI。 */
</script>

<template>
  <section class="extension-page">
    <h1>影像质量</h1>
    <p>这是可信源码插件示范入口。插件通过只读查看器上下文读取影像和 ROI。</p>
    <p class="hint">当前页面不直接访问查看器内部状态或原始文件。</p>
  </section>
</template>

<style scoped>
.extension-page { padding: 24px; color: var(--ink); }
.extension-page h1 { margin: 0 0 12px; font-size: 20px; }
.extension-page p { color: var(--ink-sub); line-height: 1.7; }
.extension-page .hint { color: var(--ink-faint); font-size: 13px; }
</style>
