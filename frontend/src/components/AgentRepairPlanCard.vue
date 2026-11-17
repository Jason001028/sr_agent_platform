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
import { apiConfirmRepair, apiRepairStatus, apiStartRepair, apiRepairTargets, HttpError } from '../lib/api';
import type { RepairPlan, RepairPlanTargetItem, RepairStatus, RepairStatusItem } from '../lib/api';
import RepairTargetDrawer from './RepairTargetDrawer.vue';
import BatchProgressTracks from './BatchProgressTracks.vue';
import AppIcon from './AppIcon.vue';
import { batchDisplayProgress, displayPercent } from '../lib/batchDisplayProgress';
import { repairPlanData } from '../lib/repairPlan';
import { requestQualityReview, requestMaskReview } from '../lib/batchDock';
import router from '../router';
import { goalStatusLabel, itemStatusLabel } from '../lib/batchUi';
import { missionItemLabel, terminalMission } from '../lib/batchMission';
import type { MissionItem } from '../lib/batchMission';

const props = defineProps<{ data: unknown }>();
const chat = useChatStore();
const batch = useBatchGoalStore();
const viewer = useViewerStore();

const plan = ref<RepairPlan | null>(repairPlanData(props.data));
const status = ref<RepairStatus | null>(null);
const goalId = ref('');
const action = ref<'idle' | 'starting'>('idle');
const synchronized = ref(false);
const drawerOpen = ref(false);
const drawerLoading = ref(false);
const drawerError = ref('');
const fullTargets = ref<RepairPlanTargetItem[]>([]);
const error = ref('');
let statusGen = 0;
let targetGen = 0;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let disposed = false;

const planId = computed(() => plan.value?.plan_id ?? '');
const linkedSnapshot = computed(() => {
  const snap = batch.snapshot;
  return snap && goalId.value && snap.goal.goal_id === goalId.value ? snap : null;
});
const goal = computed(() => linkedSnapshot.value?.goal ?? status.value?.goal ?? null);
const goalStatus = computed(() => goal.value?.status ?? '');
const maskReviewOpening = ref(false);
const needsMaskReview = computed(() => phase.value === 'waiting_review'
  && started.value && !terminalMission(goalStatus.value) && goalStatus.value !== 'PAUSED');
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

const modeLabel = computed(() => plan.value?.mode === 'production' ? '真实 SR' : '历史演示 / Fake SR');
const confirmed = computed(() => plan.value?.status === 'CONFIRMED' || plan.value?.status === 'STARTED');
const detectionLabel = computed(() => ({ absdiff: '绝对差异', ssim: '结构相似度', smart: 'LTD 纹理退化', existing: '人工掩码' }[plan.value?.strategy?.detection_method ?? ''] ?? '未指定'));
const scopeLabel = computed(() => ({ current: '当前影像', selection: '选中影像', workspace: 'Viewer 工作区', draft: '修复草稿' }[(plan.value as RepairPlan & { workspace?: { scope?: string } })?.workspace?.scope ?? ''] ?? (plan.value?.scope === 'single' ? '当前影像' : '工作区清单')));
const startLabel = computed(() => plan.value?.mode === 'production' ? '启动 SR' : '启动演示');
const targetRows = computed(() => {
  const normalize = (p?: string | null) => p?.replace(/\\/g, '/').replace(/\/$/, '') ?? '';
  return (fullTargets.value.length ? fullTargets.value : plan.value?.targets.items ?? [])
    .slice().sort((a, b) => a.ordinal - b.ordinal).map(row => {
      const rec = viewer.recsOrdered.find(r => !!row.scene_ref && normalize(r.sceneDir) === normalize(row.scene_ref));
      return { ...row, viewerLabel: rec ? viewer.sceneOrdinalOf(rec) : 0 };
    });
});
async function openTargets() {
  drawerOpen.value = true;
  drawerError.value = '';
  const current = plan.value;
  if (drawerLoading.value || !current || !chat.sessionId || fullTargets.value.length === current.targets.total || current.targets.items.length >= current.targets.total) return;
  drawerLoading.value = true;
  const token = ++targetGen;
  const sid = chat.sessionId;
  try {
    const rows: RepairPlanTargetItem[] = [];
    let offset = 0;
    do {
      const page = await apiRepairTargets(current.plan_id, sid, current.version, offset);
      if (disposed || token !== targetGen || chat.sessionId !== sid || plan.value?.plan_id !== current.plan_id || plan.value?.version !== current.version) return;
      if (page.version !== current.version || page.plan_id !== current.plan_id) throw new Error('计划身份已变化，请重新读取清单');
      rows.push(...page.items);
      if (!page.has_more) break;
      if (page.next_offset <= offset) throw new Error('清单分页未前进，请重新查看');
      offset = page.next_offset;
    } while (rows.length < 500);
    fullTargets.value = rows;
  } catch (e) { if (!disposed && token === targetGen) drawerError.value = e instanceof Error ? e.message : String(e); }
  finally { if (token === targetGen) drawerLoading.value = false; }
}
function resetTargets() {
  targetGen++; fullTargets.value = []; drawerOpen.value = false;
  drawerLoading.value = false; drawerError.value = '';
}
const targetCount = computed(() => (plan.value?.status === 'BLOCKED' ? plan.value.targets.total : plan.value?.targets?.applicable)
  ?? counts.value?.eligible ?? 0);
const blockedReason = computed(() => {
  const p = plan.value;
  if (!p) return '';
  if (p.mode !== 'production' && stage.value === 'plan') return '历史演示计划不能启动真实修复，请重新准备自动修复计划。';
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
    && current.mode === 'production'
    && ['READY', 'CONFIRMED'].includes(current.status)
    && !!current.execution?.allowed;
});
const stateLabel = computed(() => {
  if (stage.value === 'plan') {
    if (plan.value?.mode !== 'production') return '历史演示计划';
    if (planStatus.value === 'BLOCKED') return '受阻';
    if (planStatus.value === 'EXPIRED' || planStatus.value === 'SUPERSEDED') return '已失效';
    if (!plan.value?.execution?.allowed) return '暂不可执行';
    return confirmed.value ? '已确认' : '待确认';
  }
  if (goalStatus.value) {
    if (terminalMission(goalStatus.value) && !settled.value) return '收尾中';
    if (phase.value === 'waiting_quality_review') return '等待人工验收';
    if (phase.value === 'waiting_review') return '等待掩码复核';
    if (goalStatus.value === 'COMPLETED' && plan.value?.mode !== 'production') {
      return '演示完成';
    }
    return goalStatus.value === 'RUNNING' ? (plan.value?.mode === 'production' ? '已启动' : '演示已启动') : goalStatusLabel(goalStatus.value);
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
/* Breathing indicates an ongoing workflow, including mask review and external
 * execution; it does not assert that a GPU is busy or quality has passed. */
const stateIcon = computed<'running' | 'paused' | 'waiting' | 'done' | 'failed' | 'idle'>(() => {
  if (['FAILED', 'BLOCKED'].includes(goalStatus.value) || planStatus.value === 'BLOCKED'
    || rejectedReviews.value > 0) return 'failed';
  if (['PAUSED', 'CANCELLED'].includes(goalStatus.value)) return 'paused';
  if (phase.value === 'waiting_quality_review'
    || (terminalMission(goalStatus.value) && !settled.value)) return 'waiting';
  if (stage.value === 'running' && (phase.value === 'waiting_review'
    || ['RUNNING', 'WAITING_EXTERNAL', 'VERIFYING'].includes(goalStatus.value))) return 'running';
  if (['QUEUED', 'RETRY_WAIT', 'WAITING_USER'].includes(goalStatus.value)
    || phase.value === 'waiting_review' || phase.value === 'waiting_quality_review'
    || (terminalMission(goalStatus.value) && !settled.value)) return 'waiting';
  if (stage.value === 'done') {
    if (plan.value?.mode === 'production' && pendingReviews.value > 0) return 'waiting';
    return 'done';
  }
  return 'idle';
});
const currentItem = computed(() => {
  const snap = linkedSnapshot.value;
  if (snap?.producer.current) {
    const row = snap.producer.current;
    return {
      item_id: row.item_id, name: row.name,
      label: plan.value?.mode !== 'production' && row.status === 'WAITING_SR' ? '等待演示结果' : missionItemLabel(row as MissionItem),
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
function itemDisplayLabel(item: Pick<RepairStatusItem, 'status' | 'job_id' | 'sr_status'>): string {
  if (item.status === 'WAITING_SR' && item.job_id != null) {
    const sr = String(item.sr_status ?? '').toUpperCase();
    if (plan.value?.mode !== 'production') return sr === 'RUNNING' ? '演示执行中' : sr === 'PENDING' || sr === 'QUEUED' ? '演示排队中' : '等待演示结果';
    return sr === 'RUNNING' ? 'SR 执行中'
      : sr === 'PENDING' || sr === 'QUEUED' ? 'SR 排队中' : '等待 SR 结果';
  }
  return itemStatusLabel(item.status);
}
const processed = computed(() => batchDisplayProgress(counts.value).processed);
const primaryPercent = computed(() => displayPercent(processed.value));
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
  const sid = chat.sessionId;
  try {
    const next = await apiRepairStatus(planId.value, chat.sessionId);
    if (disposed || token !== statusGen || sid !== chat.sessionId) return;
    status.value = next;
    if (next.plan) plan.value = next.plan;
    synchronized.value = true;
    if (next.goal?.goal_id) goalId.value = next.goal.goal_id;
    else if (next.plan?.goal_id) goalId.value = next.plan.goal_id;
    await linkBatch();
  } catch (e) {
    if (disposed || token !== statusGen) return;
    if (e instanceof HttpError && e.status === 404) {
      error.value = '无法读取任务状态';
    } else {
      error.value = e instanceof Error ? e.message : String(e);
    }
  }
}
async function linkBatch(): Promise<void> {
  if (!goalId.value || batch.goalId) return;
  try { await batch.attach(goalId.value); } catch { /* REST status remains usable */ }
}
async function startPlan(): Promise<void> {
  const current = plan.value;
  if (!current || action.value !== 'idle' || !synchronized.value || !canAct.value) return;
  action.value = 'starting';
  error.value = '';
  try {
    const sessionId = await ensureSession();
    if (!sessionId || disposed) return;
    if (!confirmed.value) {
      const checked = await apiConfirmRepair(current.plan_id, { session_id: sessionId });
      if (disposed || chat.sessionId !== sessionId || planId.value !== current.plan_id) return;
      plan.value = checked;
      if (checked.version !== current.version) {
        await refreshStatus();
        error.value = '计划版本已变化，请核对新计划后再次确认。';
        return;
      }
    }
    const startedResult = await apiStartRepair(current.plan_id, {
      session_id: sessionId, plan_version: current.version,
      workspace_version: viewer.workspaceVersion,
      workspace: snapshotWorkspace(viewer.recsOrdered, viewer.workspaceVersion),
    });
    if (disposed || chat.sessionId !== sessionId || planId.value !== current.plan_id) return;
    plan.value = startedResult.plan;
    goalId.value = startedResult.goal.goal_id;
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
async function openMaskReview(): Promise<void> {
  if (!needsMaskReview.value || !goalId.value || maskReviewOpening.value) return;
  const gid = goalId.value;
  maskReviewOpening.value = true;
  error.value = '';
  try {
    if (batch.goalId !== gid && !await batch.selectGoal(gid)) throw new Error('无法打开该任务，请稍后重试。');
    if (gid !== goalId.value || !needsMaskReview.value) return;
    await router.push('/viewer');
    requestMaskReview(gid);
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { maskReviewOpening.value = false; }
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
watch(() => plan.value?.version, resetTargets);
watch(() => chat.sessionId, () => { statusGen++; synchronized.value = false; resetTargets(); void refreshStatus(); });
watch(() => props.data, value => {
  const next = repairPlanData(value);
  if (!next || next.plan_id === plan.value?.plan_id && next.version === plan.value?.version) return;
  statusGen++; plan.value = next; status.value = null; goalId.value = '';
  synchronized.value = false; resetTargets();
  void refreshStatus();
});
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility);
  void refreshStatus();
});
onBeforeUnmount(() => {
  disposed = true;
  targetGen++;
  statusGen++;
  document.removeEventListener('visibilitychange', onVisibility);
  if (refreshTimer != null) clearTimeout(refreshTimer);
});
</script>
<template>
  <div v-if="plan" class="repair-task" data-e2e="agent-repair-task" :data-stage="stage">
    <header class="task-head">
      <span class="task-title"><AppIcon v-if="stage !== 'plan'" name="layers" :size="18" tint /><strong>{{ stage === 'done' ? '修复结果' : stage === 'running' ? '自动修复' : '修复计划' }} · {{ targetCount }} 景</strong></span>
      <span class="task-state">
        <span class="state-ring" :class="stateIcon" :data-state-icon="stateIcon" data-e2e="repair-state-ring" aria-hidden="true">
          <svg viewBox="0 0 20 20">
            <circle class="ring" cx="10" cy="10" r="8" />
            <path v-if="stateIcon === 'paused'" class="glyph" d="M8 6.5v7M12 6.5v7" />
            <path v-else-if="stateIcon === 'waiting'" class="glyph" d="M10 5.5V10l3 2.2" />
            <path v-else-if="stateIcon === 'done'" class="glyph" d="m6.2 10.2 2.6 2.6 5-5.4" />
            <path v-else-if="stateIcon === 'failed'" class="glyph" d="m7.2 7.2 5.6 5.6m0-5.6-5.6 5.6" />
            <circle v-else-if="stateIcon === 'idle'" class="glyph-dot" cx="10" cy="10" r="1.5" />
          </svg>
        </span>
        <span class="state" :class="stateTone">{{ stateLabel }}</span>
      </span>
    </header>

    <Transition name="repair-stage" mode="out-in">
    <section v-if="stage === 'plan'" key="plan" class="plan-body">
      <dl class="plan-facts">
        <div><dt>目标影像</dt><dd>{{ targetCount }} 景<span v-if="plan.targets.excluded"> · 排除 {{ plan.targets.excluded }}</span></dd></div>
        <div><dt>修复范围</dt><dd>{{ scopeLabel }}</dd></div>
        <div><dt>检测方法</dt><dd>{{ detectionLabel }}</dd></div>
        <div><dt>掩码策略</dt><dd>{{ plan.strategy?.mask === 'existing' ? '已保存人工掩码' : '自动生成' }}</dd></div>
        <div><dt>执行模式</dt><dd :class="{ demo: plan.mode !== 'production' }">{{ modeLabel }}</dd></div>
        <div><dt>计划状态</dt><dd>{{ stateLabel }}</dd></div>
      </dl>
      <button class="list-link" data-e2e="repair-targets-open" @click="openTargets">查看全部 {{ plan.targets.total }} 景 <span aria-hidden="true">↗</span></button>
      <p class="mode-note">{{ plan.mode === 'production' ? '确认后提交真实 SR，以产物及人工验收为准。' : '仅演示检测与任务流程，不运行真实 SR，不产生修复成果。' }}</p>
      <details v-if="blockedReason" class="blocked-detail"><summary class="warn" :title="blockedReason">{{ blockedReason }}</summary><p>{{ blockedReason }}</p></details>
      <p v-if="error" class="warn action-error" role="alert">{{ error }}</p>
      <div v-if="canAct" class="actions plan-actions">
        <button class="primary" data-e2e="repair-plan-start"
          :disabled="action !== 'idle' || !synchronized" @click="startPlan">
          {{ action === 'starting' ? '正在确认并启动…' : !synchronized ? '正在核对计划…' : `确认并启动 · ${startLabel}` }}
        </button>
      </div>
      <button v-if="!synchronized && error" class="list-link" @click="refreshStatus">重新读取计划</button>
    </section>

    <section v-else :key="stage" class="execution-body">
      <p v-if="plan.mode !== 'production'" class="mode-note">Demo / Fake SR · 不产生真实修复成果</p>
      <p v-if="currentItem" class="current" :title="`${currentItem.name} · ${currentItem.label}`">
        <span class="current-prefix">当前影像</span><span class="current-name">{{ currentItem.name }}</span>
      </p>
      <p class="overall-summary" data-e2e="repair-overall-summary">
        <template v-if="primaryPercent != null">已处理 <b>{{ processed.done }} / {{ processed.total }}</b> 景 · {{ Math.round(primaryPercent) }}%</template>
        <template v-else>{{ processed.known ? '无适用影像' : '统计中' }}</template>
      </p>
      <BatchProgressTracks :counts="counts" :demo="plan.mode !== 'production'" />
      <div v-if="stage === 'done' && regionCounts?.total" class="counts review-regions" data-e2e="repair-region-counts">
        <span>待审区域 <b>{{ regionCounts.pending }}</b></span>
        <span>区域通过 <b>{{ regionCounts.approved }}</b></span>
        <span v-if="regionCounts.rejected" class="bad">区域驳回 <b>{{ regionCounts.rejected }}</b></span>
      </div>
      <template v-if="stage === 'done'">
        <p class="summary" data-e2e="repair-result-summary">{{ summary }}</p>
        <dl class="result-counts">
          <div><dt>{{ plan.mode === 'production' ? '修复产物' : '演示验证' }}</dt><dd>{{ productCount }}</dd></div>
          <div><dt>无需修复</dt><dd>{{ counts?.no_issue ?? 0 }}</dd></div>
          <div><dt>失败 / 受阻</dt><dd>{{ (counts?.failed ?? 0) + (counts?.blocked ?? 0) }}</dd></div>
          <div><dt>待审核景</dt><dd>{{ pendingReviews }}</dd></div>
          <div><dt>已通过景</dt><dd>{{ approvedReviews }}</dd></div>
          <div v-if="rejectedReviews"><dt>已驳回景</dt><dd>{{ rejectedReviews }}</dd></div>
        </dl>
      </template>
      <p v-if="goalStatus === 'CANCELLED' && !settled" class="note">
        已请求取消；已接收的{{ plan.mode === 'production' ? ' SR ' : '演示' }}作业仍由后台监控到终态。
      </p>
      <p v-if="error" class="warn">{{ error }}</p>
      <div v-if="goalId || resultItem || reviewTarget" class="actions" data-e2e="repair-task-actions">
        <button v-if="goalId" class="primary" @click="openTask">查看进度</button>
        <span v-if="stage === 'running'" class="background-note">在后台执行，可随时查看进度</span>
        <button v-if="needsMaskReview" class="mask-review-button" data-e2e="repair-mask-review" :disabled="maskReviewOpening" @click="openMaskReview">{{ maskReviewOpening ? '正在前往…' : '前往复核' }}</button>
        <button v-if="stage === 'done' && resultItem" data-e2e="repair-view-result" @click="openResult">查看成果</button>
        <button v-if="stage === 'done' && reviewTarget" data-e2e="repair-start-review" @click="startReview">开始复核</button>
      </div>
    </section>
    </Transition>
    <Teleport to="body"><Transition name="repair-drawer">
      <RepairTargetDrawer v-if="drawerOpen" :rows="targetRows" :total="plan.targets.total" :loading="drawerLoading" :error="drawerError" @close="drawerOpen = false" />
    </Transition></Teleport>
  </div>
</template>
<style scoped>
.repair-task { display:grid; grid-template-columns:minmax(0,1fr); gap:10px; margin:6px 0; padding:12px; border:1px solid var(--tb-border); border-radius:14px; font-size:12px; background:var(--tb-bg); color:var(--tb-fg); min-width:0; width:100%; max-width:100%; box-sizing:border-box; overflow:hidden; }
.plan-body, .execution-body { min-width:0; display:grid; gap:10px; }
.plan-facts { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px 16px; margin:0; }
.plan-facts div { min-width:0; } .plan-facts dt { font-size:10px; color:var(--tb-muted); margin-bottom:3px; }
.plan-facts dd { margin:0; font-weight:500; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .plan-facts dd.demo { color:var(--tb-active-fg); }
.mode-note { font-size:10px; color:var(--tb-muted); line-height:1.6; }
button.list-link { justify-self:start; border:0; padding:0; min-height:24px; background:transparent; color:var(--tb-active-fg); }
.plan-actions .primary { width:100%; min-height:36px; font-size:12px; }
.blocked-detail { min-width:0; } .blocked-detail summary { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:pointer; }
.blocked-detail p { max-height:100px; overflow:auto; margin-top:6px; }
.action-error { max-height:90px; overflow:auto; }
.repair-stage-enter-active { transition:opacity 180ms ease, transform 180ms ease; } .repair-stage-enter-from { opacity:0; transform:translateY(5px); }
.repair-drawer-enter-active, .repair-drawer-leave-active { transition:opacity 200ms ease; }
.repair-drawer-enter-active :deep(.repair-drawer), .repair-drawer-leave-active :deep(.repair-drawer) { transition:transform 200ms ease; }
.repair-drawer-enter-from, .repair-drawer-leave-to { opacity:0; } .repair-drawer-enter-from :deep(.repair-drawer), .repair-drawer-leave-to :deep(.repair-drawer) { transform:translateX(100%); }
.task-head { display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:8px;min-width:0; }
.task-title{display:flex;align-items:center;gap:7px;min-width:0;overflow:hidden;color:#376b5d}
.task-head strong { min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600;color:var(--ink); }
.task-state { display: inline-flex; align-items: center; gap: 6px; flex: none; min-width: 0; }
.state-ring { display: grid; place-items: center; width: 20px; height: 20px; flex: none; color: #255c51; }
.state-ring svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; overflow: visible; }
.state-ring .glyph { stroke-width: 1.5; }
.state-ring .glyph-dot { fill: currentColor; stroke: none; }
.state-ring.paused { color: #7d8d85; }
.state-ring.waiting { color: #8a6a2f; }
.state-ring.done { color: #245b50; }
.state-ring.failed { color: #9b5145; }
.state-ring.running svg { animation: state-ring-breathe var(--motion-breathe) var(--ease-loop) infinite; }
@keyframes state-ring-breathe {
  0%, 100% { opacity: .45; filter: drop-shadow(0 0 0 rgba(37, 92, 81, 0)); }
  50% { opacity: 1; filter: drop-shadow(0 0 2px rgba(37, 92, 81, .22)); }
}
.state { font-size: 10px; line-height: 15px; padding: 0 7px; border-radius: 999px; background: #edf4ef; color: var(--accent-deep); white-space: nowrap; }
.state.active { background: #edf4ef; color: #285b50; }
.state.done { background: #e8f3ec; color: #245b50; }
.state.quiet { background: var(--surface); color: var(--ink-sub); }
.state.error { background: #f8eeeb; color: #9b5145; }
p { margin: 0; line-height: 1.55; color: var(--ink-sub); overflow-wrap: anywhere; }
.meta, .current, .counts { font-size: 11px; }
.current { display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px;align-items:baseline;min-width:0;overflow:hidden; }
.current-prefix{font-size:10px;color:var(--tb-muted);white-space:nowrap}
.current-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.counts { display: flex; flex-wrap: wrap; gap: 4px 10px; color: var(--ink-sub); }
.counts b { color: var(--ink); font-variant-numeric: tabular-nums; }
.counts .bad, .counts .bad b { color: #9b5145; }
.counts .wait, .counts .wait b { color: #8a6a2f; }
.summary { color: var(--ink); background: #f0f6f2; border-radius: 7px; padding: 7px 8px; }
.result-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(56px, 1fr)); gap: 4px; margin: 0; }
.result-counts div { min-width: 0; }
.result-counts dt { font-size: 9px; color: var(--ink-sub); white-space: nowrap; }
.result-counts dd { margin: 1px 0 0; font-size: 13px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.note { font-size: 10px; }
.overall-summary{font-size:11px;padding-top:9px;border-top:1px solid var(--line);font-variant-numeric:tabular-nums}
.overall-summary b{font-weight:500;color:var(--ink)}
.execution-body{grid-template-columns:minmax(0,1fr)}
.execution-body>.actions{padding-top:10px;border-top:1px solid var(--line);align-items:center;min-width:0;gap:7px}
.execution-body>.actions button{flex:none}
.background-note{flex:1;min-width:0;font-size:9px;line-height:1.5;color:var(--tb-muted)}
.warn { color: #9b5145; }
.actions { display: flex; flex-wrap: wrap; gap: 6px; }
button { min-height: 26px; padding: 0 9px; border: 1px solid var(--line); border-radius: 7px; color: var(--accent-deep); background: var(--surface); cursor: pointer; font: inherit; font-size: 11px; }
button.primary { background:var(--nav-bg); border-color:var(--nav-bg); color:var(--nav-fg); }
button.mask-review-button{margin-left:auto;background:#b85120;border-color:#b85120;color:#fff;font-weight:500}
button.mask-review-button:hover:enabled{background:#9e4319;border-color:#9e4319}
button:disabled { opacity: .55; cursor: default; }
@media (prefers-reduced-motion: reduce) { *, :deep(*) { transition:none !important; } }
</style>
