<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue';
import { apiBatchEvents, type BatchEvent } from '../lib/api';
import { mergeBatchEvents } from '../lib/batchEventSync';
import { projectRepairToolCalls } from '../lib/repairTrajectory';
import { loadSrConfig } from '../lib/scene';
import AgentActivityTimeline from './AgentActivityTimeline.vue';

const props = defineProps<{
  goalId: string;
  liveEvents: BatchEvent[];
  following: boolean;
  settled: boolean;
  sessionKey?: string | null;
  developerMode?: boolean;
  rows: ReadonlyArray<{ item_id: string; ordinal: number }>;
}>();
const emit = defineEmits<{ updated: [] }>();
const recent = shallowRef<BatchEvent[]>([]);
const older = shallowRef<BatchEvent[]>([]);
const before = ref<number | null>(null);
const unavailable = ref(false);
let after: number | undefined;

const matchingLiveEvents = computed(() => props.liveEvents.filter(event => event.goal_id === props.goalId));
watch(matchingLiveEvents, events => {
  // Keep the received calls when a different batch is selected.
  recent.value = mergeBatchEvents(events, recent.value, Number.MAX_SAFE_INTEGER);
}, { immediate: true });
const ordinals = computed(() => new Map(props.rows.map(row => [row.item_id, row.ordinal])));
const steps = computed(() => projectRepairToolCalls(
  mergeBatchEvents(older.value, recent.value, Number.MAX_SAFE_INTEGER), props.settled, ordinals.value));
const group = computed(() => ({ key: `repair-${props.goalId}`, steps: steps.value }));
watch(() => steps.value.at(-1)?.key, (key, previous) => {
  if (key && key !== previous) emit('updated');
});

watch(() => props.goalId, () => {
  after = undefined;
  before.value = null;
  recent.value = matchingLiveEvents.value;
  older.value = [];
  unavailable.value = false;
}, { flush: 'sync' });

async function loadEarlier(signal: AbortSignal, id: string) {
  let cursor = before.value;
  while (cursor !== null && !signal.aborted && props.goalId === id) {
    const page = await apiBatchEvents(loadSrConfig(), id, { before: cursor, limit: 200, signal });
    if (signal.aborted || props.goalId !== id || page.goal_id !== id) return;
    older.value = mergeBatchEvents(older.value, page.events.filter(event => event.goal_id === id), Number.MAX_SAFE_INTEGER);
    cursor = page.has_earlier && page.next_before !== null && page.next_before < cursor ? page.next_before : null;
    before.value = cursor;
    unavailable.value = false;
  }
}

watch(() => [props.goalId, props.following, props.settled] as const, ([id, following, settled], _, onCleanup) => {
  if (!id) return;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  onCleanup(() => { controller.abort(); clearTimeout(timer); });
  async function refresh() {
    try {
      const cursor = after;
      const page = await apiBatchEvents(loadSrConfig(), id, cursor === undefined
        ? { before: Number.MAX_SAFE_INTEGER, limit: 200, signal: controller.signal }
        : { after: cursor, limit: 200, signal: controller.signal });
      if (controller.signal.aborted || page.goal_id !== id) return;
      const next = page.events.filter(event => event.goal_id === id);
      const last = next.at(-1)?.seq;
      const initial = cursor === undefined;
      if (initial) before.value = page.has_earlier ? page.next_before : null;
      const advanced = last !== undefined && (initial || last > cursor);
      if (advanced) after = last;
      recent.value = mergeBatchEvents(recent.value, next, Number.MAX_SAFE_INTEGER);
      unavailable.value = false;
      if (before.value !== null) await loadEarlier(controller.signal, id);
      if (page.has_more && advanced) {
        timer = setTimeout(refresh, 200);
        return;
      }
    } catch {
      if (controller.signal.aborted) return;
      unavailable.value = true;
    }
    if (!settled && (!following || unavailable.value)) timer = setTimeout(refresh, 3500);
  }
  void refresh();
}, { immediate: true });
</script>

<template>
  <AgentActivityTimeline v-if="steps.length" :group="group" :session-key="sessionKey" :developer-mode="developerMode" />
  <p v-if="unavailable" class="tool-record-error" role="status">暂时无法读取工具调用记录</p>
</template>

<style scoped>
.tool-record-error { color:var(--ink-sub); font-size:12px; margin:4px 0; }
</style>
