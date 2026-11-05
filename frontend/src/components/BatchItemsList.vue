<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useViewerStore } from '../stores/viewer';
import { apiBatchItems, apiBatchReview, HttpError } from '../lib/api';
import type { BatchItemRecord } from '../lib/api';
import { itemStatusLabel } from '../lib/batchUi';
const batch = useBatchGoalStore();
const items = ref<BatchItemRecord[]>([]);
const cursor = ref(0);
const previous = ref<number[]>([]);
const status = ref('');
const busy = ref(false);
const error = ref('');
let epoch = 0;
async function load() {
  const id = batch.goalId; const gen = ++epoch;
  if (!id) { items.value = []; return; }
  busy.value = true; error.value = '';
  try { const page = await apiBatchItems(id, cursor.value, status.value); if (gen === epoch) items.value = page.items; }
  catch (e) { if (gen === epoch) error.value = String(e); }
  finally { if (gen === epoch) busy.value = false; }
}
watch(() => batch.goalId, () => { cursor.value = 0; previous.value = []; void load(); }, { immediate: true });
watch(status, () => { cursor.value = 0; previous.value = []; void load(); });
onBeforeUnmount(() => { epoch++; });
async function review(item: BatchItemRecord, decision: 'approve' | 'exclude') {
  if (!batch.goalId || busy.value) return;
  const gid = batch.goalId;
  const gen = epoch;
  busy.value = true;
  let notice = '';
  try { await apiBatchReview(gid, item, decision); if (gen === epoch && gid === batch.goalId) await batch.refreshSnapshot(); }
  catch (e) { notice = e instanceof HttpError && e.status === 409 ? '复核对象已变化，已重读；请核对当前掩码后重新操作。' : String(e); }
  finally { if (gen === epoch && gid === batch.goalId) { await load(); error.value = notice; } }
}
function regions(item: BatchItemRecord): [number, number, number, number][] {
  return (item.checkpoint.detection?.candidates ?? []).map(c => (c as { bbox?: unknown }).bbox)
    .filter((bbox): bbox is [number, number, number, number] => Array.isArray(bbox) && bbox.length === 4 && bbox.every(n => typeof n === 'number' && Number.isFinite(n)));
}
async function focus(item: BatchItemRecord, bounds: [number, number, number, number]) {
  const viewer = useViewerStore();
  if (viewer.drawMode) { error.value = '请先退出绘制 / 编辑后再定位区域。'; return; }
  const gen = epoch;
  await open(item);
  if (gen === epoch && !error.value) viewer.focusSourceBounds(viewer.activeSide, bounds);
}
async function open(item: BatchItemRecord) {
  const gen = epoch;
  const message = await batch.openItem({ item_id: item.item_id, image_path: item.checkpoint.target_image?.path });
  if (gen === epoch) error.value = message;
}
function next() { previous.value.push(cursor.value); cursor.value = items.value.at(-1)?.ordinal ?? cursor.value; void load(); }
function back() { cursor.value = previous.value.pop() ?? 0; void load(); }
</script>
<template>
  <section class="items" data-e2e="batch-items">
    <header><strong>冻结执行清单</strong><select v-model="status" aria-label="筛选执行项"><option value="">全部</option><option value="MASK_READY">掩码就绪 / 待复核</option><option value="WAITING_SR,VERIFYING,SUBMITTING">SR 活动项</option><option value="FAILED,BLOCKED">异常</option></select><button :disabled="busy" @click="load">刷新</button></header>
    <p>执行范围只读；人工复核仅操作尚未提交的掩码，不修改运行中或历史 Item。</p>
    <div class="list">
      <article v-for="item in items" :key="item.item_id">
        <strong>#{{ item.ordinal }} {{ item.name }}</strong><span>{{ itemStatusLabel(item.status) }}</span>
        <small>{{ item.item_id }} · 尝试 {{ item.checkpoint.attempt ?? 0 }}</small>
        <div><button :disabled="busy" @click="open(item)">查看影像 / 掩码</button></div>
        <details><summary>检测依据与参数</summary><p>{{ item.checkpoint.target_image?.path }}</p><pre>{{ JSON.stringify({ detection: item.checkpoint.detection, config: item.checkpoint.config, review: item.checkpoint.review }, null, 2) }}</pre><p>{{ item.error }}</p><button v-for="(bbox, i) in regions(item).slice(0, 10)" :key="i" @click="focus(item, bbox)">定位区域 {{ i + 1 }}</button></details>
        <div v-if="item.status === 'MASK_READY' && item.checkpoint.review_required && !item.checkpoint.review"><button :disabled="busy" @click="review(item, 'approve')">确认此掩码并允许提交</button><button :disabled="busy" @click="review(item, 'exclude')">排除此项</button></div>
      </article>
    </div>
    <footer><button :disabled="busy || !previous.length" @click="back">上一页</button><span>每页最多 20 景</span><button :disabled="busy || items.length < 20" @click="next">下一页</button></footer>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.items { padding: 9px; font-size: 12px; display: grid; gap: 7px; }
header, footer { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
button, select { min-height: 28px; border: 1px solid var(--line); border-radius: 8px; color: var(--accent-deep); background: var(--surface); font: inherit; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.list { max-height: 320px; overflow-y: auto; }
article { display: grid; gap: 5px; padding: 9px 0; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
small, p { color: var(--ink-sub); font-size: 11px; margin: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 10px; max-height: 220px; overflow: auto; }
</style>
