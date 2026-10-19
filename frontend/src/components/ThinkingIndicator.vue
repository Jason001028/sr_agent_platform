<script setup lang="ts">
/**
 * ThinkingIndicator.vue — 等待 Agent 时的循环动画
 * ------------------------------------------------------------------
 * 三点依次起落 + 文案，表示「回合还没结束」。同一组件复用于会话加载
 * 与模型推理两种场景，靠 label 区分。
 * prefers-reduced-motion 下停掉位移动画，只保留静态圆点。
 */
withDefaults(defineProps<{ label?: string }>(), { label: '模型推理中' });
</script>

<template>
  <span class="thinking" role="status" aria-live="polite">
    <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="thinking-label">{{ label }}</span>
  </span>
</template>

<style scoped>
.thinking {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-sub);
  font-size: 12.5px;
  line-height: 1;
}
.dots { display: inline-flex; align-items: flex-end; gap: 3px; height: 10px; }
.dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-2);
  animation: think-bounce 1.2s infinite ease-in-out;
}
.dots i:nth-child(2) { animation-delay: 0.15s; }
.dots i:nth-child(3) { animation-delay: 0.3s; }

.thinking-label { letter-spacing: 0.01em; }

@keyframes think-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-4px); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .dots i { animation: none; opacity: 0.7; }
}
</style>
