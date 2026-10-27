<script setup lang="ts">
/**
 * ReasoningBlock.vue — Qwen 推理内容（reasoning_content）折叠块
 * ------------------------------------------------------------------
 * 只服务当前回合：思考内容不落库，刷新 / 切会话后自然消失（见 api-contract §3.2）。
 * 原始推理仅开发者模式可见，默认折叠；当前模型阶段在两种模式下都可见。
 * 没有回答时也能单独出现（工具回合的 assistant 消息常常只有推理 + 工具声明）。
 */
import { computed, nextTick, ref, watch } from 'vue';
import type { ChatPhase } from '../lib/api.js';
import { phaseLabel } from '../lib/executionOverview.js';

const props = defineProps<{
  text: string;
  streaming?: boolean;
  phase?: ChatPhase | null;
  developerMode?: boolean;
}>();

const open = ref(false);
const bodyEl = ref<HTMLElement | null>(null);
const currentPhase = computed(() => phaseLabel(props.phase));
/** 用户手动点过之后，定稿时不再自动收起。 */
let userToggled = false;

function nearBottom(): boolean {
  const el = bodyEl.value;
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 24;
}

watch(() => props.text, async () => {
  if (!open.value || !props.streaming) return;
  const stick = nearBottom();
  await nextTick();
  if (stick && bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight;
});

watch(() => props.streaming, (now, before) => {
  if (before && !now && !userToggled) open.value = false;
});

watch(() => props.developerMode, (enabled) => {
  if (!enabled) open.value = false;
});

function toggle(): void {
  userToggled = true;
  open.value = !open.value;
}
</script>

<template>
  <div v-if="streaming || developerMode" class="reason">
    <div class="reason-head">
      <button v-if="developerMode" type="button" class="reason-toggle" :aria-expanded="open" @click="toggle">
        <span class="chev" :class="{ open }" aria-hidden="true">▸</span>
        <span class="reason-title">深度思考中</span>
      </button>
      <span v-if="streaming" class="reason-live" role="status">
        <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
        {{ currentPhase }}
      </span>
    </div>
    <pre v-if="developerMode && open" ref="bodyEl" class="reason-body">{{ text }}</pre>
  </div>
</template>

<style scoped>
.reason {
  max-width: 94%;
  margin-bottom: 6px;          /* 与同一回合的正文气泡留缝（同属一条 assistant 消息） */
  border: 0;
  border-radius: 0;
  background: transparent;
  overflow: hidden;
}
.reason-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 2px;
  border: 0;
  background: none;
  color: var(--ink-sub);
  font: inherit;
  font-size: 11.5px;
  text-align: left;
}
.reason-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  cursor: pointer;
}
.reason-toggle:hover .reason-title { color: var(--ink-sub); }
.chev {
  display: inline-block;
  color: var(--ink-faint);
  transition: transform var(--motion-hover) var(--ease-standard);
}
.chev.open { transform: rotate(90deg); }
.reason-title { color: var(--ink-faint); font-size: 10px; font-weight: 400; }
.reason-live {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent-deep);
}
.reason-count {
  margin-left: auto;
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}
.reason-body {
  margin: 0;
  padding: 4px 10px 7px 18px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink-sub);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.6;
}
.dots { display: inline-flex; gap: 2px; }
.dots i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: currentColor;
  animation: reason-dot var(--motion-think) infinite var(--ease-loop);
}
.dots i:nth-child(2) { animation-delay: var(--motion-think-step); }
.dots i:nth-child(3) { animation-delay: calc(var(--motion-think-step) * 2); }
@keyframes reason-dot {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .dots i { animation: none; opacity: 0.7; }
  .chev { transition: none; }
}
</style>
