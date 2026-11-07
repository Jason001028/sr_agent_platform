<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useViewerStore } from '../stores/viewer';
import { apiBatchItems, apiBatchReview, HttpError } from '../lib/api';
import type { BatchItemRecord } from '../lib/api';
import { ISSUE_STATUSES, recordMissionItem } from '../lib/batchMission';
import BatchMissionItem from './BatchMissionItem.vue';
const props = withDefaults(defineProps<{ initialStatus?: string }>(), { initialStatus: '' });
const batch = useBatchGoalStore();
const items = ref<BatchItemRecord[]>([]);
const cursor = ref(0);
const previous = ref<number[]>([]);
const status = ref(props.initialStatus);
const busy = ref(false);
const error = ref('');
let epoch = 0;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
async function load() {
  const id = batch.goalId; const gen = ++epoch;
  if (!id) { items.value = []; return; }
  busy.value = true; error.value = '';
  try { const page = await apiBatchItems(id, cursor.value, status.value); if (gen === epoch) items.value = page.items; }
  catch (e) { if (gen === epoch) error.value = String(e); }
  finally { if (gen === epoch) busy.value = false; }
}
watch(() => batch.goalId, () => { clearTimeout(refreshTimer); refreshTimer = undefined; cursor.value = 0; previous.value = []; void load(); }, { immediate: true });
watch(status, () => { cursor.value = 0; previous.value = []; void load(); });
watch(() => props.initialStatus, value => { status.value = value; });
function scheduleRefresh() {
  if (refreshTimer != null) return;
  refreshTimer = setTimeout(() => { refreshTimer = undefined; if (busy.value) scheduleRefresh(); else void load(); }, 350);
}
watch([() => batch.snapshot?.goal.version, () => batch.snapshot?.goal.last_seq], scheduleRefresh);
onBeforeUnmount(() => { epoch++; clearTimeout(refreshTimer); });
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
  const message = await batch.openItem({ item_id: item.item_id, image_path: item.checkpoint.target_image?.path, overlay_ready: !!item.checkpoint.mask?.overlay });
  if (gen === epoch) error.value = message;
}
function next() { previous.value.push(cursor.value); cursor.value = items.value.at(-1)?.ordinal ?? cursor.value; void load(); }
function back() { cursor.value = previous.value.pop() ?? 0; void load(); }
</script>
<template>
  <section class="items" data-e2e="batch-items">
    <header><select v-model="status" aria-label="筛选执行项"><option value="">全部任务</option><option :value="ISSUE_STATUSES">异常任务</option><option value="OPENING,DETECTING,MASK_READY,SUBMITTING,WAITING_SR,VERIFYING">活跃任务</option><option value="MASK_READY">掩码复核</option><option value="SUCCEEDED,NO_ISSUE">已完成</option></select><button :disabled="busy" @click="load">刷新列表</button></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="busy && !items.length" class="quiet">正在读取任务…</p>
    <p v-else-if="!items.length" class="quiet">没有匹配的任务。</p>
    <div v-if="items.length" class="list">
      <BatchMissionItem v-for="item in items" :key="item.item_id" :item="recordMissionItem(item)">
        <details class="evidence"><summary>检测依据与参数</summary><pre>{{ JSON.stringify({ detection: item.checkpoint.detection, config: item.checkpoint.config, review: item.checkpoint.review }, null, 2) }}</pre><button v-for="(bbox, i) in regions(item).slice(0, 10)" :key="i" @click="focus(item, bbox)">定位区域 {{ i + 1 }}</button></details>
        <div v-if="item.status === 'MASK_READY' && item.checkpoint.review_required && !item.checkpoint.review" class="review-actions"><button :disabled="busy" @click="review(item, 'approve')">确认掩码并允许提交</button><button :disabled="busy" @click="review(item, 'exclude')">排除此项</button></div>
      </BatchMissionItem>
    </div>
    <footer v-if="items.length || previous.length"><button :disabled="busy || !previous.length" @click="back">上一页</button><span>第 {{ previous.length + 1 }} 页</span><button :disabled="busy || items.length < 20" @click="next">下一页</button></footer>
  </section>
</template>
<style scoped>
.items{display:grid;gap:9px;font-size:12px;min-width:0}header,footer{display:flex;align-items:center;gap:8px;justify-content:space-between}select{min-width:0;max-width:65%;font:inherit;background:var(--surface);color:var(--ink-sub);border:1px solid var(--line);border-radius:6px;padding:5px}button{font:inherit;cursor:pointer;color:var(--accent-deep);background:transparent;border:1px solid var(--line);border-radius:6px;padding:5px 7px}button:disabled{opacity:.4;cursor:default}.list{display:grid;gap:7px;min-width:0}footer{font-size:11px;color:var(--ink-sub);padding-top:4px}p{margin:0;font-size:11px;line-height:1.6}.quiet{color:var(--ink-sub)}.error{color:#a85d52;overflow-wrap:anywhere}.evidence summary{cursor:pointer}.evidence pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:11px}.review-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
</style>
