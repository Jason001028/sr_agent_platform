<script setup lang="ts">
/** 事件时间线：默认折叠、最新在前、原始 payload 按需展开。
 *  事件只作解释材料；进度数字一律来自快照。 */
import { computed } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal.js';
import { eventLabel, eventTone, formatClock } from '../lib/batchUi.js';

const batch = useBatchGoalStore();

const ordered = computed(() =>
  [...batch.events].sort((a, b) => b.seq - a.seq));
const latest = computed(() => ordered.value[0] ?? null);

function payloadText(payload: Record<string, unknown>): string {
  try { return JSON.stringify(payload, null, 2); } catch { return String(payload); }
}
</script>

<template>
  <details class="batch-events" data-e2e="batch-events">
    <summary class="be-summary">
      <span class="be-title">事件时间线</span>
      <span class="be-count">{{ batch.events.length }}</span>
      <span v-if="latest" class="be-latest">
        {{ eventLabel(latest) }} · {{ formatClock(latest.created_at) }}
      </span>
    </summary>

    <button
      v-if="batch.hasEarlier"
      type="button"
      class="be-earlier"
      :disabled="batch.loadingEarlier"
      @click="batch.loadEarlier()"
    >{{ batch.loadingEarlier ? '加载中…' : '加载更早' }}</button>

    <p v-if="!ordered.length" class="be-empty">还没有事件。</p>
    <ol v-else class="be-list">
      <li
        v-for="event in ordered"
        :key="event.seq"
        class="be-row"
        :class="'is-' + eventTone(event)"
      >
        <span class="be-time">{{ formatClock(event.created_at) }}</span>
        <span class="be-dot" aria-hidden="true" />
        <div class="be-body">
          <div class="be-label">{{ eventLabel(event) }}</div>
          <details class="be-raw">
            <summary>{{ event.event_type }} · seq {{ event.seq }}</summary>
            <pre>{{ payloadText(event.payload) }}</pre>
          </details>
        </div>
      </li>
    </ol>
  </details>
</template>

<style scoped>
.batch-events {
  padding: 8px 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}
.be-summary {
  display: flex;
  align-items: baseline;
  gap: 6px;
  overflow: hidden;
  cursor: pointer;
  color: var(--ink-sub);
  font-size: 11px;
}
.be-title { flex: none; font-weight: 600; }
.be-count {
  flex: none;
  min-width: 16px;
  padding: 0 4px;
  border-radius: var(--r-pill);
  background: var(--surface-3);
  color: var(--ink-faint);
  font-size: 9.5px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.be-latest {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-faint);
  font-size: 10px;
}
.be-earlier {
  margin-top: 7px;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface-2);
  color: var(--accent-deep);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
}
.be-earlier:disabled { opacity: 0.5; cursor: default; }
.be-list {
  list-style: none;
  margin: 7px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 320px;
  overflow: auto;
}
.be-row { display: flex; align-items: flex-start; gap: 6px; }
.be-time {
  flex: none;
  width: 50px;
  color: var(--ink-faint);
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
  line-height: 1.5;
}
.be-dot {
  flex: none;
  width: 5px;
  height: 5px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--line);
}
.be-row.is-ok .be-dot { background: var(--ok); }
.be-row.is-warn .be-dot { background: var(--warn); }
.be-row.is-error .be-dot { background: var(--err); }
.be-body { flex: 1; min-width: 0; }
.be-label { color: var(--ink-sub); font-size: 10.5px; line-height: 1.5; word-break: break-all; }
.be-raw { margin-top: 1px; }
.be-raw summary {
  cursor: pointer;
  color: var(--ink-faint);
  font-size: 9px;
}
.be-raw pre {
  margin: 3px 0 0;
  padding: 5px 7px;
  max-height: 140px;
  overflow: auto;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  font-size: 9.5px;
  white-space: pre-wrap;
  word-break: break-all;
}
.be-empty { margin: 7px 0 0; color: var(--ink-faint); font-size: 10.5px; }
</style>
