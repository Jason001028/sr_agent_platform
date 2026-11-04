<script setup lang="ts">
/** Producer 当前代表项 + 在途数量（修订 7）。
 *  缩略图只用现成 JPG（不生成预览、不读 TIFF）；点开才加载整幅图。 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal.js';
import { useViewerStore } from '../stores/viewer.js';
import { apiResolveScene, fetchExistingSceneJpg } from '../lib/api.js';
import { loadSrConfig } from '../lib/scene.js';
import type { BatchSnapshot } from '../lib/api.js';
import { itemStatusLabel } from '../lib/batchUi.js';

const props = defineProps<{ snapshot: BatchSnapshot }>();
const batch = useBatchGoalStore();
const viewer = useViewerStore();

const current = computed(() => props.snapshot.producer.current);
const detecting = computed(() => props.snapshot.producer.detecting_count);
const preparing = computed(() => props.snapshot.producer.preparing_count);
const othersDetecting = computed(() => Math.max(0, detecting.value - 1));
const thumbUrl = ref('');
const thumbFailed = ref(false);
const opening = ref(false);
let objectUrl = '';
let disposed = false;

function clearThumb(): void {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl);
    objectUrl = '';
  }
  thumbUrl.value = '';
}

watch(() => current.value?.image_path, async (path) => {
  clearThumb();
  thumbFailed.value = false;
  if (!path) return;
  try {
    const resolved = await apiResolveScene(loadSrConfig(), { path });
    const blob = await fetchExistingSceneJpg(loadSrConfig(), resolved.row);
    if (disposed || !blob || path !== current.value?.image_path) return;
    objectUrl = URL.createObjectURL(blob);
    thumbUrl.value = objectUrl;
  } catch {
    // A thumbnail is optional; never turn its absence into a Panel error.
    if (!disposed) thumbFailed.value = true;
  }
}, { immediate: true });

onBeforeUnmount(() => { disposed = true; clearThumb(); });

async function open(mask: boolean): Promise<void> {
  const item = current.value;
  if (!item || opening.value) return;
  opening.value = true;
  const error = await batch.openItem(item);
  if (!error && !mask) batch.clearOverlay();
  if (error) viewer.showErr(error);
  opening.value = false;
}

async function refresh(): Promise<void> {
  await batch.refreshSnapshot();
}
</script>

<template>
  <section class="batch-current" data-e2e="batch-current">
    <div class="bc-head">
      <span class="bc-title">当前检测 / 提交</span>
      <button type="button" class="bc-refresh" :disabled="batch.controlBusy" @click="refresh">
        刷新快照
      </button>
    </div>

    <template v-if="current">
      <div class="bc-body">
        <div class="bc-thumb">
          <img v-if="thumbUrl" :src="thumbUrl" alt="" draggable="false">
          <span v-else class="bc-thumb-empty">{{ thumbFailed ? '无现成预览' : '…' }}</span>
        </div>
        <div class="bc-info">
          <div class="bc-name" :title="current.name">#{{ current.ordinal }} {{ current.name }}</div>
          <div class="bc-meta">
            <span class="bc-status" :data-status="current.status">
              {{ itemStatusLabel(current.status) }}
            </span>
            <span v-if="current.attempt > 0" class="bc-attempt">第 {{ current.attempt }} 次</span>
            <span v-if="current.candidate_count" class="bc-candidates">
              {{ current.candidate_count }} 个候选
            </span>
          </div>
          <div class="bc-actions">
            <button type="button" :disabled="opening || !current.image_path" @click="open(false)">
              定位影像
            </button>
            <button type="button" :disabled="opening || !current.image_path" @click="open(true)">
              查看掩码叠加
            </button>
          </div>
        </div>
      </div>
      <p v-if="othersDetecting > 0" class="bc-parallel">
        另有 {{ othersDetecting }} 景同时检测中（在途共 {{ preparing }} 景）
      </p>
    </template>
    <p v-else class="bc-empty">
      当前没有检测或提交中的场景。
      <span v-if="snapshot.counts.in_flight_sr">
        仍有 {{ snapshot.counts.in_flight_sr }} 个 SR 作业在后台等待。
      </span>
    </p>
  </section>
</template>

<style scoped>
.batch-current {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-card);
}
.bc-head { display: flex; align-items: center; justify-content: space-between; }
.bc-title { color: var(--ink-sub); font-size: 11px; font-weight: 600; }
.bc-refresh {
  border: 0;
  background: none;
  padding: 0;
  color: var(--ink-faint);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
  text-decoration: underline;
}
.bc-refresh:disabled { cursor: default; opacity: 0.5; }
.bc-body { display: flex; gap: 8px; min-width: 0; }
.bc-thumb {
  flex: none;
  width: 66px;
  height: 66px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border-radius: var(--r-ctrl);
  background: var(--surface-3);
  border: 1px solid var(--line);
}
.bc-thumb img { width: 100%; height: 100%; object-fit: cover; }
.bc-thumb-empty { color: var(--ink-faint); font-size: 9px; text-align: center; padding: 4px; }
.bc-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.bc-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
  font-size: 11.5px;
  font-weight: 600;
}
.bc-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 10px; color: var(--ink-sub); }
.bc-status { color: var(--accent-deep); font-weight: 600; }
.bc-status[data-status='FAILED'], .bc-status[data-status='BLOCKED'] { color: var(--err); }
.bc-status[data-status='RETRY_WAIT'] { color: var(--warn); }
.bc-attempt, .bc-candidates { color: var(--ink-faint); }
.bc-actions { display: flex; gap: 5px; flex-wrap: wrap; }
.bc-actions button {
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface-2);
  color: var(--accent-deep);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
}
.bc-actions button:hover:not(:disabled) { background: var(--accent-soft); }
.bc-actions button:disabled { opacity: 0.5; cursor: default; }
.bc-parallel { margin: 0; color: var(--ink-sub); font-size: 10px; }
.bc-empty { margin: 0; color: var(--ink-faint); font-size: 11px; line-height: 1.5; }
</style>
