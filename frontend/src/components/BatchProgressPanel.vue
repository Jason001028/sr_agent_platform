<script setup lang="ts">
/** Batch Progress Panel 编排层（PR F）。
 *  首屏顺序：概览/统计 → 三维进度 → Producer 当前项 → Monitor SR 队列；
 *  控制与事件时间线在下，事件默认折叠。 */
import { computed, ref } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal.js';
import { useChatStore } from '../stores/chat.js';
import { useViewerStore } from '../stores/viewer.js';
import BatchProgressTracks from './BatchProgressTracks.vue';
import BatchCurrentItem from './BatchCurrentItem.vue';
import BatchJobQueue from './BatchJobQueue.vue';
import BatchControls from './BatchControls.vue';
import BatchEventTimeline from './BatchEventTimeline.vue';
import BatchItemsList from './BatchItemsList.vue';
const fullQueue = ref(false);

const batch = useBatchGoalStore();
const chat = useChatStore();
const viewer = useViewerStore();

const SYNC_TEXT: Record<string, string> = {
  idle: '未连接',
  loading: '读取中',
  live: '实时',
  reconnecting: '重连中',
  error: '同步中断',
  done: '已收尾',
};

const syncText = computed(() => SYNC_TEXT[batch.syncState] ?? batch.syncState);
const syncTone = computed(() => {
  if (batch.syncState === 'live') return 'ok';
  if (batch.syncState === 'done') return 'done';
  if (batch.syncState === 'error') return 'err';
  if (batch.syncState === 'reconnecting') return 'warn';
  return '';
});

const goalOptions = computed(() => {
  const list = [...batch.availableGoals];
  const snap = batch.snapshot;
  if (snap && !list.some((goal) => goal.goal_id === snap.goal.goal_id)) {
    list.unshift({
      goal_id: snap.goal.goal_id,
      session_id: snap.goal.session_id,
      status: snap.goal.status,
      title: snap.goal.title,
      kind: 'batch',
      version: snap.goal.version,
    });
  }
  return list;
});

function optionLabel(goal: { goal_id: string; title?: string }): string {
  const title = goal.title && goal.title.trim();
  return title || goal.goal_id.slice(0, 12);
}

const overlayText = computed(() => {
  const target = viewer.batchOverlayTarget;
  if (!target || !batch.snapshot) return '';
  const snap = batch.snapshot;
  const candidates = [
    snap.producer.current,
    ...snap.active_jobs,
    ...(snap.recent_jobs ?? []),
  ];
  const item = candidates.find((row) => row?.item_id === target.itemId);
  return item ? `#${item.ordinal} ${item.name}` : '当前场景';
});

function onGoalChange(event: Event): void {
  const id = (event.target as HTMLSelectElement).value;
  if (id && id !== batch.goalId) void batch.selectGoal(id);
}

function toggleFollow(event: Event): void {
  batch.setFollow((event.target as HTMLInputElement).checked);
}



</script>

<template>
  <div class="batch-panel" data-e2e="batch-panel">
    <details class="bp-settings"><summary>批次与查看设置</summary>
    <header class="bp-head">
      <select
        v-if="goalOptions.length"
        class="bp-select"
        :value="batch.goalId ?? ''"
        aria-label="选择批次"
        @change="onGoalChange"
      >
        <option v-if="!batch.goalId" value="" disabled>选择批次…</option>
        <option v-for="goal in goalOptions" :key="goal.goal_id" :value="goal.goal_id">
          {{ optionLabel(goal) }}
        </option>
      </select>
      <span v-else class="bp-select-empty">没有可关联的批次</span>
      <span class="bp-sync" :class="syncTone" :title="'已消费到 seq ' + batch.consumedSeq">
        {{ syncText }}
      </span>
      <button type="button" class="bp-refresh" :disabled="!batch.goalId"
              @click="batch.refreshSnapshot()">刷新</button>
    </header>

    <div class="bp-follow">
      <label class="bp-follow-toggle">
        <input
          type="checkbox"
          data-e2e="batch-follow"
          :checked="batch.follow"
          @change="toggleFollow"
        >
        跟随当前场景
      </label>
      <span v-if="overlayText" class="bp-overlay">
        掩码叠加：{{ overlayText }}
        <button type="button" title="关闭掩码叠加" @click="batch.clearOverlay()">×</button>
      </span>
    </div>
    </details>
    <p v-if="batch.followNote" class="bp-follow-note">{{ batch.followNote }}</p>
    <p v-if="batch.followError" class="bp-follow-error">{{ batch.followError }}</p>

    <p v-if="batch.loadError" class="bp-error">{{ batch.loadError }}</p>
    <p v-if="batch.syncError" class="bp-warn">{{ batch.syncError }}</p>

    <template v-if="batch.snapshot">
      <BatchProgressTracks :snapshot="batch.snapshot" />
      <p class="bp-follow-note">Producer 检测与 SR Monitor 可并行推进。</p>
      <BatchCurrentItem :snapshot="batch.snapshot" />
      <BatchJobQueue :snapshot="batch.snapshot" compact />
      <details @toggle="fullQueue = ($event.target as HTMLDetailsElement).open"><summary>完整执行队列（分页）</summary><BatchItemsList v-if="fullQueue" /></details>
      <BatchEventTimeline />
      <BatchControls :snapshot="batch.snapshot" variant="monitor" />
    </template>

    <div v-else-if="batch.syncState === 'loading'" class="bp-loading">正在读取批次…</div>
    <div v-else class="bp-empty">
      <p>尚未关联批次，请先核验待修复清单。</p>
      <button type="button" @click="batch.associate(chat.sessionId)">重新查找批次</button>
    </div>
  </div>
</template>

<style scoped>
.batch-panel {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 9px;
}
.bp-head { display: flex; align-items: center; gap: 6px; }
.bp-settings { order: 10; font-size: 11px; color: var(--ink-sub); border-top: 1px solid var(--line); }
.bp-settings > summary { min-height: 34px; align-content: center; cursor: pointer; }
.bp-settings .bp-follow { margin-top: 8px; }
.bp-select {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 11px;
}
.bp-select-empty { flex: 1; color: var(--ink-faint); font-size: 11px; }
.bp-sync {
  flex: none;
  padding: 1px 7px;
  border: 1px solid var(--line);
  border-radius: var(--r-pill);
  background: var(--surface-2);
  color: var(--ink-faint);
  font-size: 9.5px;
}
.bp-sync.ok { color: var(--ok); border-color: var(--ok-line); background: var(--ok-bg); }
.bp-sync.done { color: var(--ink-sub); }
.bp-sync.warn { color: var(--warn); border-color: var(--warn-line); background: var(--warn-bg); }
.bp-sync.err { color: var(--err); border-color: var(--err-line); background: var(--err-bg); }
.bp-refresh {
  flex: none;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface-2);
  color: var(--accent-deep);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
}
.bp-refresh:disabled { opacity: 0.45; cursor: default; }
.bp-follow { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.bp-follow-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-sub);
  font-size: 10.5px;
  cursor: pointer;
}
.bp-follow-toggle input { margin: 0; }
.bp-overlay {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--accent-deep);
  font-size: 10px;
}
.bp-overlay button {
  border: 0;
  background: none;
  padding: 0 2px;
  color: var(--ink-faint);
  font: inherit;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.bp-follow-note { margin: 0; color: var(--ink-faint); font-size: 10px; line-height: 1.4; }
.bp-follow-error { margin: 0; color: var(--err); font-size: 10px; line-height: 1.4; }
.bp-error { margin: 0; color: var(--err); font-size: 10.5px; }
.bp-warn { margin: 0; color: var(--warn); font-size: 10px; line-height: 1.4; }
.bp-loading { color: var(--ink-faint); font-size: 11px; padding: 6px 2px; }
.bp-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 14px 12px;
  color: var(--ink-sub);
  font-size: 11px;
  line-height: 1.6;
  background: var(--surface);
  border: 1px dashed var(--line);
  border-radius: var(--r-card);
}
.bp-empty p { margin: 0; }
.bp-empty-hint { color: var(--ink-faint); font-size: 10.5px; }
.bp-empty button {
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface-2);
  color: var(--accent-deep);
  font: inherit;
  font-size: 10.5px;
  cursor: pointer;
}
</style>
