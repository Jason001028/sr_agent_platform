<script setup lang="ts">
import { computed } from 'vue';
import type { ChatUsage } from '../lib/api.js';

const props = defineProps<{ usage: ChatUsage | null }>();

const hasUsage = computed(() => Boolean(
  props.usage && props.usage.calls > 0 && props.usage.total_tokens > 0,
));
const inputPct = computed(() => {
  const total = props.usage?.total_tokens ?? 0;
  return total ? Math.round((props.usage!.prompt_tokens / total) * 100) : 0;
});
const outputPct = computed(() => {
  const total = props.usage?.total_tokens ?? 0;
  return total ? Math.max(0, 100 - inputPct.value) : 0;
});
const maxTurn = computed(() => Math.max(
  1, ...(props.usage?.events ?? []).map((e) => e.total_tokens),
));

function fmt(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K';
  return String(n);
}

function eventLabel(source: string): string {
  return source === 'provider' ? '模型返回' : source === 'estimate' ? '估算' : source;
}
</script>

<template>
  <section class="token-panel" data-e2e="token-usage">
    <div class="token-head">
      <span class="token-title">Token 用量</span>
      <span class="token-source">{{ usage ? eventLabel(usage.source) : '暂无数据' }}</span>
    </div>
    <template v-if="hasUsage && usage">
      <div class="token-summary">
        <div><strong>{{ fmt(usage.total_tokens) }}</strong><span>总计</span></div>
        <div><strong>{{ fmt(usage.prompt_tokens) }}</strong><span>输入</span></div>
        <div><strong>{{ fmt(usage.completion_tokens) }}</strong><span>输出</span></div>
      </div>
      <div class="token-bar" :title="`输入 ${inputPct}% · 输出 ${outputPct}%`">
        <span class="token-input" :style="{ width: inputPct + '%' }"></span>
        <span class="token-output" :style="{ width: outputPct + '%' }"></span>
      </div>
      <div class="token-legend">
        <span><i class="dot input"></i>输入上下文 {{ inputPct }}%</span>
        <span><i class="dot output"></i>输出回复 {{ outputPct }}%</span>
        <span>{{ usage.calls }} 次模型调用</span>
      </div>
      <div v-if="usage.events.length > 1" class="token-trend">
        <div class="token-trend-label">回合趋势</div>
        <div class="token-bars">
          <div v-for="event in usage.events" :key="`${event.turn}-${event.call_index}`"
               class="token-bar-column" :title="`第 ${event.turn} 步：${event.total_tokens} tokens`">
            <span :style="{ height: Math.max(8, (event.total_tokens / maxTurn) * 100) + '%' }"></span>
          </div>
        </div>
      </div>
    </template>
    <p v-else class="token-empty">
      当前模型未返回 token 用量；接入支持 OpenAI usage 的 Qwen 服务后会自动显示。
    </p>
  </section>
</template>

<style scoped>
.token-panel { flex: none; width: 330px; margin-left: auto; padding: 8px 10px; border: 1px solid var(--line); border-radius: var(--r-ctrl); background: var(--surface); }
.token-head, .token-legend, .token-summary { display: flex; align-items: center; }
.token-head { justify-content: space-between; gap: 8px; }
.token-title { color: var(--ink); font-size: 12px; font-weight: 700; }
.token-source { color: var(--ink-sub); font-size: 10px; }
.token-summary { gap: 14px; margin-top: 5px; }
.token-summary div { display: flex; align-items: baseline; gap: 4px; }
.token-summary strong { color: var(--ink); font-size: 16px; font-variant-numeric: tabular-nums; }
.token-summary span, .token-legend { color: var(--ink-sub); font-size: 10px; }
.token-bar { display: flex; overflow: hidden; height: 7px; margin-top: 7px; border-radius: var(--r-pill); background: var(--surface-2); }
.token-bar span { display: block; height: 100%; transition: width var(--motion-live) var(--ease-standard); }
.token-input { background: var(--accent-2); }
.token-output { background: #8b5cf6; }
.token-legend { gap: 9px; margin-top: 5px; flex-wrap: wrap; }
.dot { display: inline-block; width: 6px; height: 6px; margin-right: 3px; border-radius: 50%; }
.dot.input { background: var(--accent-2); }
.dot.output { background: #8b5cf6; }
.token-trend { margin-top: 6px; }
.token-trend-label { color: var(--ink-sub); font-size: 10px; }
.token-bars { display: flex; align-items: end; gap: 4px; height: 24px; margin-top: 3px; }
.token-bar-column { display: flex; align-items: end; width: 9px; height: 100%; }
.token-bar-column span { width: 100%; min-height: 2px; border-radius: 3px 3px 0 0; background: var(--accent-2); }
.token-empty { margin: 5px 0 0; color: var(--ink-sub); font-size: 10px; line-height: 1.5; }
@media (max-width: 1000px) { .token-panel { width: 270px; } }
@media (max-width: 760px) { .token-panel { width: 100%; margin-left: 0; } }
</style>
