<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import type { BatchTodo } from '../lib/api';

const props = defineProps<{
  rows: BatchTodo[];
  label: (row: BatchTodo) => string;
  describe?: (row: BatchTodo) => string;
  group: (row: BatchTodo) => 'active' | 'attention' | 'ended';
}>();
const viewport = ref<HTMLUListElement | null>(null);
const focusedId = ref<string>();
const completionQueue = ref<string[]>([]);
const workingStatuses = new Set(['OPENING', 'DETECTING', 'SUBMITTING', 'VERIFYING', 'WAITING_SR']);
let completionTimer: ReturnType<typeof setTimeout> | undefined;

function isCompleted(row: BatchTodo): boolean {
  return row.status === 'SUCCEEDED' || row.status === 'NO_ISSUE';
}

function showsCheck(row: BatchTodo): boolean {
  return isCompleted(row) && !completionQueue.value.includes(row.todo_id);
}

function focusRow(id?: string) {
  focusedId.value = id;
  void nextTick(() => {
    const list = viewport.value;
    if (!id || !list || list.contains(document.activeElement)) return;
    const row = Array.from(list.children).find(node => (node as HTMLElement).dataset.todoId === id);
    if (!row || typeof list.scrollTo !== 'function') return;
    const top = list.scrollTop + row.getBoundingClientRect().top - list.getBoundingClientRect().top;
    list.scrollTo({ top, behavior: reducedMotion() ? 'instant' : 'smooth' });
  });
}

function reducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

function playCompletions() {
  const id = completionQueue.value.shift();
  if (!id) return;
  focusRow(id);
  completionTimer = setTimeout(() => {
    completionTimer = undefined;
    if (completionQueue.value.length) {
      playCompletions();
      return;
    }
    const index = props.rows.findIndex(row => row.todo_id === id);
    const next = props.rows.slice(index + 1).find(row => props.group(row) !== 'ended')
      ?? props.rows.find(row => props.group(row) === 'active' && workingStatuses.has(row.status));
    focusRow(next?.todo_id ?? id);
  }, reducedMotion() ? 0 : 300);
}

watch(() => props.rows.map(row => ({ id: row.todo_id, status: row.status, phase: row.phase, done: isCompleted(row) })),
  (rows, previous) => {
    const before = new Map(previous?.map(row => [row.id, row]));
    const completed = rows.filter(row => row.done && before.has(row.id) && !before.get(row.id)?.done);
    completionQueue.value.push(...completed.map(row => row.id));
    if (completionTimer !== undefined) return;
    if (completionQueue.value.length) {
      playCompletions();
      return;
    }
    const active = rows.find(row => {
      const old = before.get(row.id);
      return workingStatuses.has(row.status) && (old?.status !== row.status || old.phase !== row.phase);
    });
    if (active) focusRow(active.id);
    else if (!rows.some(row => row.id === focusedId.value)) {
      focusRow(props.rows.find(row => props.group(row) !== 'ended')?.todo_id);
    }
  }, { immediate: true });

onBeforeUnmount(() => clearTimeout(completionTimer));
</script>

<template>
  <section class="todo-list" aria-label="执行待办">
    <header v-if="$slots.actions">
      <slot name="actions" />
    </header>
    <p v-if="!rows.length">暂无待办</p>
    <ul ref="viewport" class="todo-viewport">
      <li v-for="row in rows" :key="row.todo_id" :data-todo-id="row.todo_id"
        class="todo-row" :class="{ 'is-focused': row.todo_id === focusedId, 'is-complete': showsCheck(row) }">
        <span class="todo-checkbox" aria-hidden="true">
          <Transition name="todo-check">
            <span v-if="showsCheck(row)" class="todo-fill">
              <svg viewBox="0 0 16 16"><path d="m4 8 2.5 2.5L12 5" pathLength="1" /></svg>
            </span>
          </Transition>
        </span>
        <span class="todo-title todo-ordinal">第 {{ row.ordinal }} 景</span>
        <span class="todo-title todo-status" :title="describe?.(row) || row.error || label(row)">{{ label(row) }}</span>
        <span class="todo-title todo-name" :title="row.name">{{ row.name }}</span>
        <div v-if="$slots.default" class="todo-actions"><slot :row="row" /></div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.todo-list { min-width: 0; overflow-wrap: anywhere; }
header { display: flex; align-items: center; }
.todo-viewport { list-style: none; max-height: 12rem; overflow-y: auto; padding: 0; }
.todo-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 4px;
  border-bottom: 1px solid var(--line, #e4ebe6);
}
.is-focused { background: #edf5f1; color: var(--accent-deep, #285b50); }
.todo-ordinal { flex: none; white-space: nowrap; }
.todo-status { flex: 0 1 auto; max-width: 34%; }
.todo-name { flex: 1 1 0; }
.todo-status, .todo-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.todo-actions { display: flex; flex: none; white-space: nowrap; }
.todo-actions:empty { display: none; }
.todo-actions :deep(button) { padding: 2px 4px; font-size: 11px; }
.todo-checkbox {
  position: relative;
  flex: none;
  width: 16px;
  height: 16px;
  border: 1px solid var(--ink-sub, #829087);
  border-radius: 4px;
  box-sizing: border-box;
}
.is-complete .todo-checkbox { border-color: transparent; }
.todo-fill {
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  background: var(--tb-primary, #166c67);
  color: var(--tb-primary-fg, #fff);
}
.todo-fill svg { display: block; width: 100%; height: 100%; }
.todo-fill path {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.todo-title { min-width: 0; transition: color 180ms ease; }
.is-complete .todo-title {
  color: var(--ink-sub, #64736c);
  text-decoration: line-through;
}
.todo-check-enter-active { animation: todo-check-pop 300ms ease-out both; }
.todo-check-enter-active path {
  stroke-dasharray: 1;
  animation: todo-check-draw 300ms ease-out both;
}
@keyframes todo-check-pop {
  0% { transform: scale(.8); }
  55% { transform: scale(1.14); }
  78% { transform: scale(.96); }
  100% { transform: scale(1); }
}
@keyframes todo-check-draw {
  from { stroke-dashoffset: 1; }
  to { stroke-dashoffset: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .todo-check-enter-active, .todo-check-enter-active path { animation: none; }
  .todo-title { transition: none; }
}
p { margin-block: 5px; }
</style>
