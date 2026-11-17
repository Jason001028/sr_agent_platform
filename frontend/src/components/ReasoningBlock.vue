<script setup lang="ts">
/**
 * ReasoningBlock.vue — 推理内容的轻量执行轨迹
 * ------------------------------------------------------------------
 * 只服务当前回合：思考内容不落库，刷新 / 切会话后自然消失（见 api-contract §3.2）。
 *
 * 运行中显示「深度思考中…」与三点呼吸；`phase` 离开 `thinking` → streaming
 * 结束即为一个思考阶段，上报真实耗时。完成后自动收起为低对比度灰绿色标题，
 * 用户可手动展开查看已有推理；连续短阶段由 `reasoningTrace.ts` 合并成
 * 「已完成 N 段思考 · 累计 X 秒」。原始推理不再只限开发者模式。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import type { ChatPhase } from '../lib/api.js';
import type { ReasoningTiming } from '../lib/reasoningTrace.js';

const props = defineProps<{
  text: string;
  activity?: string;
  streaming?: boolean;
  phase?: ChatPhase | null;
  /** Parent-owned completed duration (survives a merged re-render). */
  seconds?: number | null;
  /** Segments already completed inside this message. */
  segmentCount?: number;
  /** Set only when this block represents several short segments. */
  mergedCount?: number;
  mergedSeconds?: number | null;
}>();
const emit = defineEmits<{ timing: [value: ReasoningTiming] }>();

const open = ref(false);
const bodyEl = ref<HTMLElement | null>(null);
/** 用户手动点过之后，定稿时不再自动收起。 */
let userToggled = false;

const thinkingActive = computed(() =>
  Boolean(props.streaming && (props.phase === 'thinking' || props.phase == null)));

let segmentStart: number | null = null;
let totalSeconds = 0;
let segmentCount = 0;
const emitted = ref<ReasoningTiming | null>(null);

function finishSegment(): void {
  if (segmentStart == null) return;
  totalSeconds += Math.max(0, (performance.now() - segmentStart) / 1000);
  segmentStart = null;
  segmentCount += 1;
  emitted.value = { seconds: totalSeconds, segments: segmentCount };
  emit('timing', emitted.value);
}

watch(thinkingActive, (active) => {
  if (active) {
    if (segmentStart == null) segmentStart = performance.now();
  } else {
    finishSegment();
  }
}, { immediate: true });

onBeforeUnmount(finishSegment);

const effectiveSeconds = computed<number | null>(() => {
  if (props.mergedCount && props.mergedCount > 1) {
    return props.mergedSeconds ?? null;
  }
  return props.seconds ?? emitted.value?.seconds ?? null;
});
const effectiveCount = computed(() => {
  if (props.mergedCount && props.mergedCount > 0) return props.mergedCount;
  return Math.max(props.segmentCount ?? 1, emitted.value?.segments ?? 1);
});

function formatSeconds(value: number): string {
  if (value < 10) {
    const rounded = Math.round(value * 10) / 10;
    return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  }
  return String(Math.round(value));
}

const title = computed(() => {
  if (thinkingActive.value) return '深度思考中…';
  const seconds = effectiveSeconds.value;
  if (effectiveCount.value > 1) {
    return seconds == null
      ? `已完成 ${effectiveCount.value} 段思考`
      : `已完成 ${effectiveCount.value} 段思考 · 累计 ${formatSeconds(seconds)} 秒`;
  }
  return seconds == null ? '思考已结束' : `已思考 ${formatSeconds(seconds)} 秒`;
});

function nearBottom(): boolean {
  const el = bodyEl.value;
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 24;
}

watch(() => props.text, async () => {
  if (!open.value || !thinkingActive.value) return;
  const stick = nearBottom();
  await nextTick();
  if (stick && bodyEl.value) bodyEl.value.scrollTop = bodyEl.value.scrollHeight;
});

watch(thinkingActive, (now, before) => {
  if (before && !now && !userToggled) open.value = false;
});

function toggle(): void {
  userToggled = true;
  open.value = !open.value;
}
</script>

<template>
  <div class="reason agent-trace" :class="thinkingActive ? 'running' : 'done'">
    <button type="button" class="reason-toggle" :aria-expanded="open"
            :title="thinkingActive && activity ? activity : undefined" @click="toggle">
      <span class="trace-node" :class="{active:thinkingActive}" aria-hidden="true"></span>
      <span class="reason-title">{{ title }}</span>
      <Transition name="reason-dots">
        <span v-if="thinkingActive" class="dots" role="status" aria-label="深度思考中">
          <i></i><i></i><i></i>
        </span>
      </Transition>
    </button>
    <pre v-if="open" ref="bodyEl" class="reason-body">{{ text }}</pre>
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
.reason-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  padding: 0;
  min-height: 28px;
  border: 0;
  background: none;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.reason-toggle:hover .reason-title,
.reason-toggle:focus-visible .reason-title { color: var(--accent-deep); }
.reason-toggle:focus-visible { outline: 2px solid var(--accent-deep); outline-offset: 2px; border-radius: 5px; }
.chev {
  display: inline-block;
  color: var(--ink-faint);
  transition: transform var(--motion-hover) var(--ease-standard);
}
.chev.open { transform: rotate(90deg); }
.reason-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-sub);
  font-size: inherit;
  font-weight: 400;
  transition: color var(--motion-hover) var(--ease-standard);
}
.reason.running .reason-title { color: var(--accent-deep); }
.reason-body {
  margin: 0;
  padding: 4px 10px 7px 18px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: #62786e;
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
.reason-dots-leave-active { transition: opacity var(--motion-fade) var(--ease-standard); }
.reason-dots-leave-to { opacity: 0; }
@keyframes reason-dot {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .dots i { animation: none; opacity: 0.7; }
  .chev { transition: none; }
  .reason-dots-leave-active { transition: none; }
}
</style>
