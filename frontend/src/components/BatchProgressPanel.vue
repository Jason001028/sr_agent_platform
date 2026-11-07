<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { focusMissionItems, missionSummary, ISSUE_STATUSES, terminalMission } from '../lib/batchMission';
import BatchCurrentItem from './BatchCurrentItem.vue';
import BatchJobQueue from './BatchJobQueue.vue';
import BatchItemsList from './BatchItemsList.vue';
import BatchQualityPanel from './BatchQualityPanel.vue';
import BatchResultReport from './BatchResultReport.vue';
const props = withDefaults(defineProps<{ fullQueue?: boolean }>(), { fullQueue: false });
const emit = defineEmits<{ 'update:fullQueue': [value: boolean] }>();
const batch = useBatchGoalStore();
const snap = computed(() => batch.snapshot);
const mission = computed(() => snap.value ? missionSummary(snap.value) : null);
const focus = computed(() => snap.value ? focusMissionItems(snap.value) : []);
const filter = ref('');
const qualityOpen = ref(false);
watch(() => batch.goalId, () => { filter.value = ''; qualityOpen.value = false; });
function openAll() { filter.value = mission.value?.issueFocus ? ISSUE_STATUSES : ''; emit('update:fullQueue', true); }
const qualityCount = computed(() => (snap.value?.quality?.waiting_scenes ?? 0) + (snap.value?.quality?.rejected_scenes ?? 0));
</script>
<template>
  <div v-if="snap" class="batch-panel" data-e2e="batch-panel">
    <section v-if="focus.length || fullQueue" class="mission-queue" data-e2e="mission-queue">
      <header><h3>{{ fullQueue ? '任务列表' : mission?.issueFocus ? '异常任务' : '当前任务' }}</h3>
        <button v-if="fullQueue" class="link" @click="emit('update:fullQueue', false)">收起列表</button>
        <button v-else class="link" @click="openAll">{{ mission?.issueFocus ? '查看全部异常' : '查看全部' }} ↗</button>
      </header>
      <BatchItemsList v-if="fullQueue" :initial-status="filter" />
      <BatchCurrentItem v-else-if="snap.producer.current && !mission?.issueFocus" :snapshot="snap" />
      <BatchJobQueue v-else :snapshot="snap" />
    </section>
    <button v-else-if="snap.counts.total > 0 && !terminalMission(snap.goal.status)" class="link standalone" @click="openAll">查看任务列表 ↗</button>
    <details v-if="qualityCount > 0" class="quality-entry" @toggle="qualityOpen = ($event.target as HTMLDetailsElement).open"><summary>人工质量验收 · {{ qualityCount }} 景</summary><BatchQualityPanel v-if="qualityOpen" /></details>
    <BatchResultReport v-if="snap.report.available && snap.goal.status === 'COMPLETED'" />
    <p v-if="batch.followNote" class="notice" role="status">{{ batch.followNote }}</p>
    <p v-if="batch.followError" class="error" role="alert">{{ batch.followError }}</p>
  </div>
</template>
<style scoped>
.batch-panel{display:grid;gap:16px;min-width:0;padding:0 16px 16px}header{display:flex;align-items:center;gap:8px;margin:0 0 9px}h3{font-size:12px;font-weight:600;margin:0}.issue-count{font-size:11px;color:#a85d52;background:#f8efed;border-radius:20px;padding:1px 7px}button{font:inherit;cursor:pointer}.link{border:0;padding:4px 0;background:transparent;color:var(--accent-deep);font-size:11px;margin-left:auto}.standalone{margin:0;text-align:left;justify-self:start}.notice,.error{font-size:11px;line-height:1.6;margin:0;overflow-wrap:anywhere}.notice{color:var(--ink-sub)}.error{color:#a85d52}summary{cursor:pointer;color:var(--accent-deep);font-size:12px;padding:8px 0}.quality-entry :deep(.quality-tasks){max-height:none;overflow:visible}
</style>
