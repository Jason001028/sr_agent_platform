<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { apiSessionGoals, apiRespondClarification, loadSrConfig } from '../lib/api.js';
import type { GoalClarification } from '../lib/api.js';

const props = defineProps<{ sessionId: string | null }>();
const emit = defineEmits<{ updated: [] }>();
const items = ref<(GoalClarification & { goalStatus: string })[]>([]);
const texts = ref<Record<string, string>>({});
const busy = ref<string | null>(null);
const error = ref('');
let epoch = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
let signature = '';
let refreshSerial = 0;
// Retain a key across transport failures so a lost HTTP response is safe to retry.
const submissions = new Map<string, { body: string; key: string }>();
let keySequence = 0;
function answerKey(id: string, body: string): string {
  const old = submissions.get(id);
  if (old?.body === body) return old.key;
  // Works on deployed HTTP pages, where crypto.randomUUID is unavailable.
  const bytes = new Uint32Array(4);
  globalThis.crypto.getRandomValues(bytes);
  const key = `${Date.now()}-${++keySequence}-${Array.from(bytes, n => n.toString(16)).join('')}`;
  submissions.set(id, { body, key });
  return key;
}

async function refresh(expected: number): Promise<void> {
  const sid = props.sessionId;
  if (!sid || expected !== epoch) return;
  const serial = ++refreshSerial;
  controller = new AbortController();
  const signal = controller.signal;
  try {
    const cfg = loadSrConfig();
    const goals = await apiSessionGoals(cfg, sid, signal);
    const rows = goals.map(g => (g.clarifications || []).map(c => ({ ...c, goalStatus: g.status })));
    if (expected !== epoch || serial !== refreshSerial) return;
    items.value = rows.flat();
    if (error.value.startsWith('任务状态加载失败：')) error.value = '';
    const next = goals.map(g => `${g.goal_id}:${g.version}`).join('|');
    if (signature !== next) {
      signature = next;
      if (goals.length) emit('updated');
    }
  } catch (e) {
    if (expected === epoch && !signal.aborted) error.value = '任务状态加载失败：' + String(e);
  }
}

function pending(item: typeof items.value[number]): boolean {
  return item.status === 'PENDING' && item.goalStatus === 'WAITING_USER';
}

async function submit(item: typeof items.value[number], option?: string): Promise<void> {
  if (busy.value || !pending(item)) return;
  const text = option ? undefined : texts.value[item.clarification_id]?.trim();
  if (!option && !text) return;
  const expected = epoch;
  busy.value = item.clarification_id;
  ++refreshSerial;
  error.value = '';
  const body = { selected_option_id: option, answer_text: text };
  try {
    const response = await apiRespondClarification(loadSrConfig(), item,
      { ...body, idempotency_key: answerKey(item.clarification_id, JSON.stringify(body)) });
    if (expected !== epoch) return;
    ++refreshSerial;
    // All status changes come from the committed server response.
    items.value = items.value.map(c => c.clarification_id === item.clarification_id
      ? { ...response.clarification, goalStatus: response.goal.status } : c);
    emit('updated');
  } catch (e) {
    if (expected === epoch) {
      error.value = '回答提交失败：' + String(e);
      await refresh(expected); // A competing client may already have answered.
    }
  } finally {
    if (expected === epoch) busy.value = null;
  }
}

watch(() => props.sessionId, () => {
  const expected = ++epoch;
  clearTimeout(timer);
  controller?.abort();
  items.value = [];
  texts.value = {};
  busy.value = null;
  error.value = '';
  signature = '';
  const poll = async () => {
    await refresh(expected);
    if (expected === epoch && props.sessionId) timer = setTimeout(() => void poll(), 2500);
  };
  void poll();
}, { immediate: true });

onBeforeUnmount(() => { epoch++; clearTimeout(timer); controller?.abort(); });
</script>

<template>
  <section v-if="items.length || error" class="goal-clarifications" aria-label="任务澄清">
    <article v-for="item in items" :key="item.clarification_id" class="clarification-card" :data-status="item.status">
      <div class="clarification-status">{{ busy === item.clarification_id ? '正在提交回答…' : item.status === 'ANSWERED' ? '已回答' : pending(item) ? '等待补充信息' : '已失效' }}</div>
      <p class="clarification-question">{{ item.question }}</p>
      <template v-if="pending(item)">
        <div class="clarification-options">
          <button v-for="option in item.options" :key="option.id" type="button" :disabled="!!busy"
                  @click="submit(item, option.id)">{{ option.label }}{{ option.id === item.recommended_option ? '（推荐）' : '' }}</button>
        </div>
        <form v-if="item.allow_free_text" class="clarification-form" @submit.prevent="submit(item)">
          <textarea v-model="texts[item.clarification_id]" aria-label="补充信息" placeholder="输入补充信息" maxlength="8000" :disabled="!!busy" rows="2" />
          <button type="submit" :disabled="!!busy || !texts[item.clarification_id]?.trim()">{{ busy === item.clarification_id ? '提交中…' : '提交回答' }}</button>
        </form>
      </template>
      <p v-else-if="item.answer" class="clarification-answer">{{ item.options.find(o => o.id === item.answer?.selected_option_id)?.label }} {{ item.answer.answer_text }}</p>
    </article>
    <p v-if="error" class="clarification-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.goal-clarifications { margin: 12px 0; display: grid; gap: 10px; }
.clarification-card { padding: 12px; border: 1px solid var(--border, #e2e4e8); border-radius: 10px; background: var(--surface, #fff); }
.clarification-status { font-size: 12px; color: var(--muted, #697386); }
.clarification-question { margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.clarification-options { display: flex; flex-wrap: wrap; gap: 6px; }
.clarification-card button { padding: 6px 10px; border: 1px solid var(--border, #d8dde5); border-radius: 7px; background: var(--surface, #fff); color: inherit; cursor: pointer; }
.clarification-card button:disabled { opacity: .5; cursor: default; }
.clarification-form { display: grid; gap: 8px; margin-top: 8px; justify-items: start; }
.clarification-form textarea { box-sizing: border-box; width: 100%; resize: vertical; border: 1px solid var(--border, #d8dde5); border-radius: 7px; padding: 8px; font: inherit; }
.clarification-answer { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.clarification-error { font-size: 12px; color: #b42318; }
</style>
