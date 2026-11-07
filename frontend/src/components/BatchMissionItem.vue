<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useViewerStore } from '../stores/viewer';
import { missionItemLabel, missionIssue } from '../lib/batchMission';
import type { MissionItem } from '../lib/batchMission';
import BatchFeedbackIcon from './BatchFeedbackIcon.vue';
const props = defineProps<{ item: MissionItem }>();
const batch = useBatchGoalStore();
const viewer = useViewerStore();
const viewerOrdinal = computed(() => {
  const path = props.item.image_path?.replace(/\\/g, '/').replace(/\/+$/, '') ?? '';
  const directory = /\.[a-z0-9]+$/i.test(path) ? path.slice(0, path.lastIndexOf('/')) : path;
  const rec = viewer.recs?.find(r => r.sceneDir?.replace(/\\/g, '/').replace(/\/+$/, '') === directory);
  return rec ? viewer.sceneOrdinalOf(rec) : 0;
});
const details = ref(false), opening = ref(false), error = ref('');
let epoch = 0;
watch([() => batch.goalId, () => props.item.item_id], () => { epoch++; details.value = false; opening.value = false; error.value = ''; });
async function locate() {
  if (opening.value || !props.item.image_path) return;
  const gen = epoch; opening.value = true;
  try { const message = await batch.openItem(props.item); if (gen === epoch) error.value = message; }
  catch (e) { if (gen === epoch) error.value = String(e); }
  finally { if (gen === epoch) opening.value = false; }
}
</script>
<template>
  <article class="mission-item" :class="{ issue: missionIssue(item) }" :data-item="item.item_id">
    <div class="item-line">
      <span class="ordinal" :title="viewerOrdinal ? `Viewer 序列 ${viewerOrdinal} · 批次项 ${item.ordinal}` : `批次项 ${item.ordinal}（当前 Viewer 未关联）`">{{ viewerOrdinal ? '#' : '项' }}{{ String(viewerOrdinal || item.ordinal).padStart(2, '0') }}</span>
      <button class="item-content" :aria-expanded="details" :title="item.name" @click="details = !details">
        <span class="item-name">{{ item.name }}</span><span class="item-state">{{ missionItemLabel(item) }}</span>
      </button>
      <BatchFeedbackIcon v-if="batch.goalId" :goal-id="batch.goalId" :item-id="item.item_id" :attempt="item.attempt" :status="item.status" :cue="batch.feedback?.[item.item_id]" :enabled="!['PAUSED', 'CANCELLED'].includes(batch.snapshot?.goal.status ?? '')" />
      <button class="locate" :aria-label="`定位影像 ${item.ordinal}`" :disabled="opening || !item.image_path" title="定位影像与掩码" @click="locate">
        <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="5"/><path d="M10 1v5m0 8v5M1 10h5m8 0h5"/></svg>
      </button>
    </div>
    <div v-if="details" class="item-details">
      <p class="full-name">{{ item.name }}</p>
      <p v-if="item.candidate_count != null && item.candidate_count > 0">疑似伪影区域 {{ item.candidate_count }} 个</p>
      <p v-if="item.job_id != null">作业编号 {{ item.job_id }} <RouterLink to="/queue">查看作业 ↗</RouterLink></p>
      <p v-else>尚未生成 SR 作业</p>
      <pre v-if="item.error" class="item-error">{{ item.error }}</pre>
      <p v-else-if="missionIssue(item)">当前快照未提供错误详情。</p>
      <slot />
    </div>
    <p v-if="error" class="item-error" role="alert">{{ error }}</p>
  </article>
</template>
<style scoped>
.mission-item{min-width:0;border:1px solid var(--line,#e4eae6);border-radius:9px;background:var(--surface,#fff);animation:mission-item-in 200ms ease-out}
.item-line{display:flex;align-items:center;gap:8px;padding:9px 10px;min-width:0}.ordinal{flex:none;color:#658076;font-size:11px;font-variant-numeric:tabular-nums}
button{font:inherit;border:0;background:transparent;cursor:pointer;color:inherit}.item-content{display:grid;gap:4px;flex:1;min-width:0;text-align:left;padding:0}.item-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.item-state{font-size:11px;color:var(--ink-sub)}
.locate{display:grid;place-items:center;flex:none;width:27px;height:27px;color:var(--accent-deep);border-radius:7px}.locate:hover:enabled{background:#edf4ef}.locate:disabled{opacity:.35;cursor:default}.locate svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.5}.issue .item-state{color:#a85d52}
.item-details{padding:0 10px 10px 38px;font-size:11px;color:var(--ink-sub);line-height:1.65}.item-details p{margin:4px 0}.full-name{overflow-wrap:anywhere}.item-error{margin:6px 0 0;font:inherit;font-size:11px;color:#a85d52;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}a{color:var(--accent-deep)}
button:focus-visible{outline:2px solid var(--accent-deep);outline-offset:3px;border-radius:4px}@keyframes mission-item-in{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}@media(prefers-reduced-motion:reduce){.mission-item{animation:none}}
</style>
