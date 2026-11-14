<script setup lang="ts">
/**
 * Agent 自动修复任务卡：计划 → 执行 → 结果，同一张卡、同一个 Goal。
 *
 * 状态来源只有两处：批次 Store（已有 SSE / snapshot）和 Repair Status REST。
 * 卡片不累计事件、不建立第二条 SSE、不生成第二套 Goal 状态；聊天关闭或页面
 * 刷新后重新读一次权威状态即可恢复。结果摘要由确定性中文模板生成，不调用 LLM。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import { useViewerStore } from '../stores/viewer';
import { snapshotWorkspace } from '../lib/viewerContext';
import { apiConfirmRepair, apiRepairStatus, apiStartRepair, HttpError } from '../lib/api';
import type { RepairPlan, RepairStatus, RepairStatusItem } from '../lib/api';
import { repairPlanData } from '../lib/repairPlan';
import { requestQualityReview } from '../lib/batchDock';
import { goalStatusLabel, itemStatusLabel, trackPercent } from '../lib/batchUi';
import { missionItemLabel, missionTrackText, terminalMission } from '../lib/batchMission';
import type { MissionItem } from '../lib/batchMission';

const props = defineProps<{ data: unknown }>();
const chat = useChatStore();
const batch = useBatchGoalStore();
const viewer = useViewerStore();

const plan = ref<RepairPlan | null>(repairPlanData(props.data));
const status = ref<RepairStatus | null>(null);
const goalId = ref('');
const action = ref<'idle' | 'confirming' | 'starting'>('idle');
const localConfirmed = ref(false);
const error = ref('');
let statusGen = 0;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let disposed = false;

const planId = computed(() => plan.value?.plan_id ?? '');
const linkedSnapshot = computed(() => {
  const snap = batch.snapshot;
  return snap && goalId.value && snap.goal.goal_id === goalId.value ? snap : null;
});
const goal = computed(() => linkedSnapshot.value?.goal ?? status.value?.goal ?? null);
const goalStatus = computed(() => goal.value?.status ?? '');
const phase = computed(() => goal.value?.phase ?? '');
const counts = computed(() => linkedSnapshot.value?.counts ?? status.value?.counts ?? null);
const progress = computed(() => linkedSnapshot.value?.progress ?? status.value?.progress ?? null);
const quality = computed(() => linkedSnapshot.value?.quality ?? status.value?.quality);
const items = computed<RepairStatusItem[]>(() => status.value?.items ?? []);
const planStatus = computed(() => plan.value?.status ?? '');
const started = computed(() => !!goalId.value || !!goal.value || planStatus.value === 'STARTED');
/* Convergence is explicit: terminal status **and** the Monitor's own
 * ``monitoring_done`` marker.  A missing value (older row / not yet read) is
 * never "done"; only ``true`` is. */
const monitorDone = computed<boolean | null>(() => {
  const snap = linkedSnapshot.value;
  if (snap) return snap.goal.monitoring_done === true;
  const row = status.value?.goal;
  if (!row) return null;
  const value = row.monitoring_done ?? row.monitor_done;
  return typeof value === 'boolean' ? value : null;
});
const settled = computed(() => !!goal.value
  && terminalMission(goalStatus.value) && monitorDone.value === true);
/** SR computation is done even when the batch is waiting for human quality review. */
const resultReady = computed(() => {
  if (settled.value) return true;
  const c = counts.value;
  return phase.value === 'waiting_quality_review' && !!c
    && c.in_flight_sr === 0 && c.detection_pending === 0;
});
const stage = computed<'plan' | 'running' | 'done'>(() =>
  !started.value ? 'plan' : resultReady.value ? 'done' : 'running');

const modeLabel = computed(() => plan.value?.mode === 'production' ? '生产' : '演示 / Fake SR');
const confirmed = computed(() => localConfirmed.value
  || plan.value?.status === 'CONFIRMED' || plan.value?.status === 'STARTED');
const targetCount = computed(() => (plan.value?.status === 'BLOCKED' ? plan.value.targets.total : plan.value?.targets?.applicable)
  ?? counts.value?.eligible ?? 0);
const blockedReason = computed(() => {
  const p = plan.value;
  if (!p) return '';
  if (p.status === 'BLOCKED') {
    return (p.blocking ?? []).map(row => row.message).filter(Boolean).join('；')
      || '计划存在阻断项';
  }
  if (p.status === 'EXPIRED') return '计划已过期，请重新准备并确认';
  if (p.status === 'SUPERSEDED') return '计划已失效，请重新准备并确认';
  if (!p.execution?.allowed) return p.execution?.reason || '当前执行环境不允许启动修复';
  return '';
});
const canAct = computed(() => {
  const current = plan.value;
  return stage.value === 'plan' && !!current
    && ['READY', 'CONFIRMED'].includes(current.status)
    && !!current.execution?.allowed;
});
const stateLabel = computed(() => {
  if (stage.value === 'plan') {
    if (planStatus.value === 'BLOCKED') return '受阻';
    if (planStatus.value === 'EXPIRED' || planStatus.value === 'SUPERSEDED') return '已失效';
    return confirmed.value ? '已确认' : '待确认';
  }
  if (goalStatus.value) {
    if (terminalMission(goalStatus.value) && !settled.value) return '收尾中';
    if (phase.value === 'waiting_quality_review') return '等待人工验收';
    if (phase.value === 'waiting_review') return '等待掩码复核';
    if (goalStatus.value === 'COMPLETED' && plan.value?.mode !== 'production') {
      return '演示完成';
    }
    return goalStatusLabel(goalStatus.value);
  }
  return started.value ? '读取任务状态…' : '';
});
const stateTone = computed(() => {
  if (['FAILED', 'BLOCKED'].includes(goalStatus.value) || ['BLOCKED'].includes(planStatus.value)) return 'error';
  if (goalStatus.value === 'CANCELLED' || goalStatus.value === 'PAUSED') return 'quiet';
  if (stage.value === 'done') {
    // Computation being over is not human acceptance.  Keep a waiting/rejected
    // production review visually distinct from a finished, approved result.
    if (rejectedReviews.value) return 'error';
    if (plan.value?.mode === 'production' && pendingReviews.value > 0) return 'active';
    return 'done';
  }
  return 'active';
});
const currentItem = computed(() => {
  const snap = linkedSnapshot.value;
  if (snap?.producer.current) {
    const row = snap.producer.current;
    return {
      item_id: row.item_id, name: row.name,
      label: missionItemLabel(row as MissionItem),
      image_path: row.image_path, overlay_ready: row.overlay_ready,
    };
  }
  const active = ['OPENING', 'DETECTING', 'MASK_READY', 'SUBMITTING',
    'WAITING_SR', 'VERIFYING', 'RETRY_WAIT', 'AWAITING_REVIEW'];
  const row = items.value.find(item => active.includes(item.status));
  if (!row) return null;
  return {
    item_id: row.item_id, name: row.name,
    label: itemDisplayLabel(row),
    image_path: row.image_path, overlay_ready: row.overlay_ready,
  };
});
function itemDisplayLabel(item: RepairStatusItem): string {
  if (item.status === 'WAITING_SR' && item.job_id != null) {
    const sr = String(item.sr_status ?? '').toUpperCase();
    return sr === 'RUNNING' ? 'SR 执行中'
      : sr === 'PENDING' || sr === 'QUEUED' ? 'SR 排队中' : '等待 SR 结果';
  }
  return itemStatusLabel(item.status);
}
const tracks = computed(() => {
  const p = progress.value;
  if (!p) return [];
  return [
    { key: 'detection', label: '检测', track: p.detection },
    { key: 'submission', label: 'SR 提交', track: p.submission },
    { key: 'verification', label: '验收', track: p.verification },
  ];
});
const primaryPercent = computed(() => {
  const c = counts.value;
  if (!c || !c.eligible) return 0;
  return Math.max(0, Math.min(100,
    ((c.verified + c.no_issue) / c.eligible) * 100));
});
const waitingQuality = computed(() => quality.value?.waiting_scenes ?? 0);
const approvedReviews = computed(() => status.value?.quality_reviews?.approved ?? 0);
const rejectedReviews = computed(() => status.value?.quality_reviews?.rejected ?? 0);
const pendingReviews = computed(() => status.value?.quality_reviews?.pending ?? 0);
const regionCounts = computed(() => status.value?.quality_regions ?? null);
/** Reviews that actually produced an SR artifact.  LTD / Smart-Compare can
 *  enqueue a whole-scene review with no candidate and no Job; that is a real
 *  human task but not a repair product. */
const productReviews = computed(() => {
  const row = status.value?.quality_products;
  return row ? row.approved + row.rejected + row.pending : 0;
});
const productPending = computed(() => status.value?.quality_products?.pending ?? 0);
const noCandidatePending = computed(() => Math.max(0, pendingReviews.value - productPending.value));
/** Backend-owned acceptance verdict; never inferred from a green status. */
const qualityPassed = computed(() => status.value?.acceptance?.quality_passed === true);
/** Only a scene with a bound quality review that still needs a decision is
 *  auditable.  A pre-submit ``MASK_READY`` mask review is a different gate;
 *  a session already rejected as a whole is historical, not pending work. */
const reviewableItems = computed<RepairStatusItem[]>(() => items.value.filter(
  item => !!item.quality_review_id && item.status === 'AWAITING_REVIEW'));
const reviewTarget = computed<{ item_id: string } | null>(() => {
  const item = reviewableItems.value[0];
  if (item) return item;
  // Snapshot fallback keeps the action available before the REST projection
  // arrives; the quality store still fetches the authoritative review.
  const row = (linkedSnapshot.value?.quality?.items ?? []).find(
    entry => entry.status === 'AWAITING_REVIEW');
  return row ? { item_id: row.item_id } : null;
});
const reviewCount = computed(() => reviewableItems.value.length
  || waitingQuality.value);
/** Artifact-producing scenes.  In production every repaired scene owns a
 *  quality review; in demo there is no produced artifact, only the demo
 *  contract verdict. */
const productCount = computed(() => {
  const c = counts.value;
  if (!c) return 0;
  if (plan.value?.mode !== 'production') return c.verified;
  return productReviews.value || c.verified;
});
/** The one scene whose SR output was actually verified for this Job.  A demo
 *  run has no artifacts layer, so ``output_path`` is absent rather than
 *  falling back to the original PAN. */
const resultItem = computed<RepairStatusItem | null>(() =>
  items.value.find(item => !!item.output_path) ?? null);
const outcomeWord = computed(() => {
  if (goalStatus.value === 'CANCELLED') return '已取消';
  if (goalStatus.value === 'FAILED') return '失败';
  if (goalStatus.value === 'BLOCKED') return '受阻';
  return '计算已结束';
});
const summary = computed(() => {
  const c = counts.value;
  if (!resultReady.value || !c || !plan.value) return '';
  const demo = plan.value.mode !== 'production';
  const success = demo ? '通过演示契约验证' : '生成修复产物并完成文件契约验证';
  let text = `本批次 ${c.total} 景影像的${demo ? '演示 / Fake SR' : '超分'} ${outcomeWord.value}，`;
  text += `${productCount.value} 景${success}`;
  if (c.no_issue) text += `，${c.no_issue} 景无需修复`;
  if (c.failed + c.blocked) text += `，${c.failed + c.blocked} 景失败或受阻`;
  if (c.excluded) text += `，${c.excluded} 景按清单排除`;
  text += '。';
  if (demo) {
    text += '演示 / Fake SR 不产生真实 SR 产物，结果只证明契约链路，'
      + '不代表真实 SR 质量验收。';
    return text;
  }
  if (pendingReviews.value) {
    text += `${pendingReviews.value} 景等待人工质量复核`;
    if (regionCounts.value?.pending) {
      text += `（${regionCounts.value.pending} 个区域待审）`;
    }
    text += '。';
  }
  if (noCandidatePending.value) {
    text += `${noCandidatePending.value} 景未检出候选，等待整景人工确认（不生成 SR 产物）。`;
  }
  if (rejectedReviews.value) {
    text += `${rejectedReviews.value} 景人工审核驳回，需人工处理。`;
  }
  const reviewed = approvedReviews.value + pendingReviews.value + rejectedReviews.value;
  if (qualityPassed.value && productReviews.value) {
    text += '全部必要区域已通过人工质量审核，修复质量验收完成。';
  } else if (qualityPassed.value) {
    text += reviewed
      ? '未检出候选的场景已完成整景人工确认，本批次没有需要修复的产物。'
      : '本批次没有需要人工质量验收的修复产物。';
  } else if (reviewed && approvedReviews.value === reviewed) {
    text += '已审核区域均已通过，但批次仍有未完成的修复或失败项，不能宣告最终验收。';
  } else if (reviewed) {
    text += 'SR 计算完成不等于质量审核通过，请以逐区域人工验收为准。';
  } else {
    text += '本批次没有需要人工质量验收的修复产物。';
  }
  return text;
});

async function ensureSession(): Promise<string | null> {
  if (!chat.sessionId) await chat.newSession();
  if (!chat.sessionId) { error.value = chat.error || '无法创建修复会话'; return null; }
  return chat.sessionId;
}
function scheduleStatusRefresh(delay = 800): void {
  if (disposed || refreshTimer != null) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    void refreshStatus();
  }, delay);
}
async function refreshStatus(): Promise<void> {
  if (!planId.value || !chat.sessionId || disposed) return;
  const token = ++statusGen;
  try {
    const next = await apiRepairStatus(planId.value, chat.sessionId);
    if (disposed || token !== statusGen) return;
    status.value = next;
    if (next.plan) plan.value = next.plan;
    if (next.goal?.goal_id) goalId.value = next.goal.goal_id;
    else if (next.plan?.goal_id) goalId.value = next.plan.goal_id;
    await linkBatch();
  } catch (e) {
    if (disposed || token !== statusGen) return;
    if (e instanceof HttpError && e.status === 404) {
      error.value = '任务状态不可读取（计划可能属于其他会话）';
    } else {
      error.value = e instanceof Error ? e.message : String(e);
    }
  }
}
async function linkBatch(): Promise<void> {
  if (!goalId.value || batch.goalId) return;
  try { await batch.attach(goalId.value); } catch { /* REST status remains usable */ }
}
async function confirmPlan(): Promise<void> {
  const current = plan.value;
  if (!current || action.value !== 'idle') return;
  const sessionId = await ensureSession();
  if (!sessionId) return;
  action.value = 'confirming';
  error.value = '';
  try {
    plan.value = await apiConfirmRepair(current.plan_id, { session_id: sessionId });
    localConfirmed.value = true;
    void refreshStatus();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally { action.value = 'idle'; }
}
async function startPlan(): Promise<void> {
  const current = plan.value;
  if (!current || action.value !== 'idle') return;
  const sessionId = await ensureSession();
  if (!sessionId) return;
  action.value = 'starting';
  error.value = '';
  try {
    if (!confirmed.value) {
      await apiConfirmRepair(current.plan_id, { session_id: sessionId });
    }
    const startedResult = await apiStartRepair(current.plan_id, {
      session_id: sessionId, plan_version: current.version,
      workspace_version: viewer.workspaceVersion,
      workspace: snapshotWorkspace(viewer.recsOrdered, viewer.workspaceVersion),
    });
    plan.value = startedResult.plan;
    goalId.value = startedResult.goal.goal_id;
    localConfirmed.value = true;
    await batch.selectGoal(startedResult.goal.goal_id);
    await refreshStatus();
  } catch (e) {
    if (e instanceof HttpError && e.status === 409) {
      await refreshStatus();
      error.value = '计划状态已变化，已重新读取；请核对后再次确认。';
    } else {
      error.value = e instanceof Error ? e.message : String(e);
    }
  } finally { action.value = 'idle'; }
}
async function openTask(): Promise<void> {
  const gid = goalId.value;
  if (!gid) return;
  if (batch.goalId !== gid) {
    const ok = await batch.selectGoal(gid);
    if (!ok) { error.value = '无法打开该任务，请稍后重试。'; return; }
  }
  window.dispatchEvent(new CustomEvent('batch-task-open',
    { detail: { section: 'monitor' } }));
}
async function openResult(): Promise<void> {
  const item = resultItem.value;
  const gid = goalId.value;
  // Only the SR output named by the passed artifacts contract may be shown;
  // the frozen target/backup paths are deliberately not accepted here.
  if (!item?.output_path || !gid) return;
  if (batch.goalId !== gid && !await batch.selectGoal(gid)) return;
  error.value = '';
  const problem = await batch.openItem({
    item_id: item.item_id, image_path: item.output_path,
    // The detection overlay is bound to the input identity; it must not be
    // presented as if it described the newly produced SR raster.
    overlay_ready: false,
  });
  if (problem) error.value = problem;
}
async function startReview(): Promise<void> {
  const gid = goalId.value;
  if (!gid) return;
  if (batch.goalId !== gid && !await batch.selectGoal(gid)) return;
  error.value = '';
  // One direct landing: the existing Batch Panel opens its quality block and
  // the existing quality store selects this Item's first pending region and
  // opens the NOSR / SR pair.  No second review UI is created.
  requestQualityReview(reviewTarget.value?.item_id);
}
function onVisibility(): void {
  if (document.visibilityState === 'visible') scheduleStatusRefresh(0);
}
watch(() => linkedSnapshot.value?.goal.last_seq, () => scheduleStatusRefresh());
watch(() => batch.goalId, (value) => {
  if (value && value === goalId.value) scheduleStatusRefresh(100);
});
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility);
  void refreshStatus();
});
onBeforeUnmount(() => {
  disposed = true;
  statusGen++;
  document.removeEventListener('visibilitychange', onVisibility);
  if (refreshTimer != null) clearTimeout(refreshTimer);
});
</script>
<template>
  <div v-if="plan" class="repair-task" data-e2e="agent-repair-task" :data-stage="stage">
    <header class="task-head">
      <strong>{{ stage === 'done' ? '修复结果' : stage === 'running' ? '自动修复' : '修复计划' }} · {{ targetCount }} 景</strong>
      <span class="state" :class="stateTone">{{ stateLabel }}</span>
    </header>

    <template v-if="stage === 'plan'">
      <p class="meta">
        {{ modeLabel }} · {{ plan.strategy?.detection }}
        · 掩码 {{ plan.strategy?.mask === 'existing' ? '使用已保存人工掩码' : '自动生成' }}
        <template v-if="plan.targets?.excluded"> · 排除 {{ plan.targets?.excluded }} 景</template>
        <template v-if="plan.targets?.unresolved?.length"> · {{ plan.targets?.unresolved?.length }} 个目标未解析</template>
      </p>
      <p v-if="blockedReason" class="warn">{{ blockedReason }}</p>
      <p v-if="error" class="warn">{{ error }}</p>
      <div v-if="canAct" class="actions">
        <button v-if="!confirmed" data-e2e="repair-plan-confirm"
          :disabled="action !== 'idle'" @click="confirmPlan">
          {{ action === 'confirming' ? '正在确认…' : '确认计划' }}
        </button>
        <button class="primary" data-e2e="repair-plan-start"
          :disabled="action !== 'idle'" @click="startPlan">
          {{ action === 'starting' ? '正在启动…' : '确认并启动' }}
        </button>
      </div>
    </template>

    <template v-else>
      <p v-if="currentItem" class="current">
        <span class="current-name" :title="currentItem.name">{{ currentItem.name }}</span>
        <span class="current-state">{{ currentItem.label }}</span>
      </p>
      <div class="overall" role="progressbar" :aria-valuenow="Math.round(primaryPercent)"
        aria-valuemin="0" aria-valuemax="100" :aria-label="`总进度 ${Math.round(primaryPercent)}%`">
        <i :style="{ width: `${primaryPercent}%` }"/>
      </div>
      <div v-if="counts" class="counts">
        <span>检测 <b>{{ counts.detected }}</b>/{{ counts.eligible }}</span>
        <span>已提交 <b>{{ counts.submitted }}</b></span>
        <span>已验收 <b>{{ counts.verified }}</b></span>
        <span v-if="counts.failed" class="bad">失败 <b>{{ counts.failed }}</b></span>
        <span v-if="counts.blocked" class="bad">受阻 <b>{{ counts.blocked }}</b></span>
        <span v-if="reviewCount" class="wait">待复核 <b>{{ reviewCount }}</b></span>
      </div>
      <div v-if="regionCounts && regionCounts.total" class="counts review-regions" data-e2e="repair-region-counts">
        <span>待审区域 <b>{{ regionCounts.pending }}</b></span>
        <span>区域通过 <b>{{ regionCounts.approved }}</b></span>
        <span v-if="regionCounts.rejected" class="bad">区域驳回 <b>{{ regionCounts.rejected }}</b></span>
      </div>
      <div v-if="tracks.length && stage === 'running'" class="tracks">
        <div v-for="track in tracks" :key="track.key" class="track" :class="{ inactive: !track.track.applicable }">
          <span>{{ track.label }}</span>
          <b>{{ missionTrackText(track.track) }}</b>
          <span class="bar"><i :style="{ width: `${trackPercent(track.track) ?? 0}%` }"/></span>
        </div>
      </div>

      <template v-if="stage === 'done'">
        <p class="summary" data-e2e="repair-result-summary">{{ summary }}</p>
        <dl class="result-counts">
          <div><dt>修复产物</dt><dd>{{ productCount }}</dd></div>
          <div><dt>无需修复</dt><dd>{{ counts?.no_issue ?? 0 }}</dd></div>
          <div><dt>失败 / 受阻</dt><dd>{{ (counts?.failed ?? 0) + (counts?.blocked ?? 0) }}</dd></div>
          <div><dt>待审核景</dt><dd>{{ pendingReviews }}</dd></div>
          <div><dt>已通过景</dt><dd>{{ approvedReviews }}</dd></div>
          <div v-if="rejectedReviews"><dt>已驳回景</dt><dd>{{ rejectedReviews }}</dd></div>
        </dl>
      </template>
      <p v-if="goalStatus === 'CANCELLED' && !settled" class="note">
        已请求取消；已提交的 SR 作业仍由后台监控到真实终态。
      </p>
      <p v-if="stage === 'running' && !settled" class="note">
        任务在后台执行，关闭聊天或刷新页面不会停止。
      </p>
      <p v-if="error" class="warn">{{ error }}</p>
      <div v-if="goalId || resultItem || reviewTarget" class="actions" data-e2e="repair-task-actions">
        <button v-if="goalId" @click="openTask">查看任务</button>
        <button v-if="resultItem" data-e2e="repair-view-result" @click="openResult">查看成果</button>
        <button v-if="reviewTarget" data-e2e="repair-start-review" @click="startReview">开始复核</button>
      </div>
    </template>
  </div>
</template>
<style scoped>
.repair-task { display: grid; gap: 7px; margin: 6px 0; padding: 10px; border: 1px solid var(--line); border-radius: 10px; font-size: 12px; background: var(--surface-2); min-width: 0; }
.task-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.task-head strong { font-weight: 600; color: var(--ink); }
.state { font-size: 10px; line-height: 15px; padding: 0 7px; border-radius: 999px; background: #edf4ef; color: var(--accent-deep); white-space: nowrap; }
.state.active { background: #edf4ef; color: #285b50; }
.state.done { background: #e8f3ec; color: #245b50; }
.state.quiet { background: var(--surface); color: var(--ink-sub); }
.state.error { background: #f8eeeb; color: #9b5145; }
p { margin: 0; line-height: 1.55; color: var(--ink-sub); overflow-wrap: anywhere; }
.meta, .current, .counts { font-size: 11px; }
.current { display: flex; gap: 8px; align-items: baseline; }
.current-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.current-state { margin-left: auto; color: var(--accent-deep); white-space: nowrap; }
.overall { height: 4px; border-radius: 4px; background: #e6ece8; overflow: hidden; }
.overall i { display: block; height: 100%; background: #4f7d6d; transition: width var(--motion-fast, 180ms) var(--ease-standard, ease); }
.counts { display: flex; flex-wrap: wrap; gap: 4px 10px; color: var(--ink-sub); }
.counts b { color: var(--ink); font-variant-numeric: tabular-nums; }
.counts .bad, .counts .bad b { color: #9b5145; }
.counts .wait, .counts .wait b { color: #8a6a2f; }
.tracks { display: grid; gap: 3px; padding-top: 2px; }
.track { display: grid; grid-template-columns: 52px auto minmax(30px, 1fr); align-items: center; gap: 6px; font-size: 10px; color: var(--ink-sub); }
.track b { font-size: 10px; font-weight: 500; color: var(--ink); font-variant-numeric: tabular-nums; }
.track.inactive { opacity: .55; }
.track .bar { height: 2px; border-radius: 2px; background: #e6ece8; overflow: hidden; }
.track .bar i { display: block; height: 100%; background: #7ba38d; transition: width var(--motion-fast, 180ms) var(--ease-standard, ease); }
.summary { color: var(--ink); background: #f0f6f2; border-radius: 7px; padding: 7px 8px; }
.result-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(56px, 1fr)); gap: 4px; margin: 0; }
.result-counts div { min-width: 0; }
.result-counts dt { font-size: 9px; color: var(--ink-sub); white-space: nowrap; }
.result-counts dd { margin: 1px 0 0; font-size: 13px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.note { font-size: 10px; }
.warn { color: #9b5145; }
.actions { display: flex; flex-wrap: wrap; gap: 6px; }
button { min-height: 26px; padding: 0 9px; border: 1px solid var(--line); border-radius: 7px; color: var(--accent-deep); background: var(--surface); cursor: pointer; font: inherit; font-size: 11px; }
button.primary { background: var(--accent-deep); border-color: var(--accent-deep); color: #fff; }
button:disabled { opacity: .55; cursor: default; }
@media (prefers-reduced-motion: reduce) { .overall i, .track .bar i { transition: none; } }
</style>
