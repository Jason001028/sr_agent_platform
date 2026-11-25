<script setup lang="ts">
/**
 * ThinkingIndicator.vue — 等待 Agent 时的循环动画
 * ------------------------------------------------------------------
 * 三点依次起落 + 文案，表示「回合还没结束」。同一组件复用于会话加载
 * 与模型推理两种场景，靠 label 区分。
 * prefers-reduced-motion 下停掉位移动画，只保留静态圆点。
 */
import { ref, watch } from 'vue';

const props = withDefaults(defineProps<{ label?: string; showElapsed?: boolean }>(), { label: '模型推理中' });
const seconds = ref(0);
watch(() => [props.showElapsed, props.label] as const, ([enabled], _, onCleanup) => {
  seconds.value = 0;
  if (!enabled) return;
  const since = Date.now();
  const timer = setInterval(() => { seconds.value = Math.floor((Date.now() - since) / 1000); }, 1000);
  onCleanup(() => clearInterval(timer));
}, { immediate: true });
</script>

<template>
  <span class="thinking" role="status" aria-live="polite">
    <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="thinking-label">{{ label }}</span>
    <span v-if="seconds >= 3" class="thinking-elapsed" aria-hidden="true">· {{ seconds }} 秒</span>
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
  animation: think-bounce var(--motion-think) infinite var(--ease-loop);
}
.dots i:nth-child(2) { animation-delay: var(--motion-think-step); }
.dots i:nth-child(3) { animation-delay: calc(var(--motion-think-step) * 2); }

.thinking-label { letter-spacing: 0.01em; }
.thinking-elapsed { white-space: nowrap; font-variant-numeric: tabular-nums; }

@keyframes think-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-4px); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .dots i { animation: none; opacity: 0.7; }
}
</style>
