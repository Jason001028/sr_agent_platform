<script setup lang="ts">
import RepairEvidenceEntry from './RepairEvidenceEntry.vue';
import RepairLandSummary from './RepairLandSummary.vue';
/**
 * Agent 自动修复任务卡：计划 → 执行 → 结果，同一张卡、同一个 Goal。
 *
 * 状态来源只有两处：批次 Store（已有 SSE / snapshot）和 Repair Status REST。
 * 卡片不累计事件、不建立第二条 SSE、不生成第二套 Goal 状态；聊天关闭或页面
 * 刷新后重新读一次权威状态即可恢复。结果摘要由确定性中文模板生成，不调用 LLM。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { isDeveloperMode } from '../lib/developerMode.js';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useChatStore } from '../stores/chat';
import AgentTodoList from './AgentTodoList.vue';
import HoldConfirmButton from './HoldConfirmButton.vue';
import type { BatchTodo } from '../lib/api';
import { useViewerStore } from '../stores/viewer';
import { snapshotWorkspace } from '../lib/viewerContext';
import { apiPrepareRepair, apiConfirmRepair, apiRepairStatus, apiStartRepair, apiRepairTargets, HttpError } from '../lib/api';
import type { RepairPlan, RepairPlanTargetItem, RepairStatus, RepairStatusItem } from '../lib/api';
import ExplorerButton from './ExplorerButton.vue';
import RepairTargetDrawer from './RepairTargetDrawer.vue';
import BatchProgressTracks from './BatchProgressTracks.vue';
import AppIcon from './AppIcon.vue';
import { installButtonPress } from '../lib/buttonPress';
import { batchDisplayProgress, displayPercent } from '../lib/batchDisplayProgress';
import { repairPlanData } from '../lib/repairPlan';
import { requestQualityReview, requestMaskReview } from '../lib/batchDock';
import router from '../router';
import { goalStatusLabel, itemStatusLabel } from '../lib/batchUi';
import { missionItemLabel, terminalMission } from '../lib/batchMission';
import type { MissionItem } from '../lib/batchMission';

const props = defineProps<{ data: unknown; standalone?: boolean }>();
const emit = defineEmits<{ back: []; planRevised: [plan: RepairPlan] }>();
const developerMode = isDeveloperMode();
const chat = useChatStore();
const batch = useBatchGoalStore();
const viewer = useViewerStore();
const cardRoot = ref<HTMLElement | null>(null);
let disposePress: (() => void) | undefined;
onMounted(() => {
  if (cardRoot.value && typeof window.matchMedia === 'function') disposePress = installButtonPress(cardRoot.value,
    '.plan-actions button.primary, .conflict-section .actions button',
    button => button.classList.contains('primary')
      ? { scale: .975, pressMs: 90, releaseMs: 220, overshoot: 1.003 }
      : { scale: .985, pressMs: 90, releaseMs: 90 });
});
onBeforeUnmount(() => disposePress?.());
const cancelBusy = ref(false), controlNotice = ref('');
const pausePending = ref<'pause' | 'resume' | null>(null);
const canStop = computed(() => started.value && !!goalId.value && !terminalMission(goalStatus.value));

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
const canTogglePause = computed(() => synchronized.value && !!goalId.value && !!goalStatus.value && !terminalMission(goalStatus.value));
const pauseLabel = computed(() => pausePending.value === 'pause' ? '正在暂停…'
  : pausePending.value === 'resume' ? '正在继续…' : goalStatus.value === 'PAUSED' ? '继续执行' : '暂停执行');
watch([goalId, goalStatus], () => { controlNotice.value = ''; });
const maskReviewOpening = ref(false);
const needsMaskReview = computed(() => phase.value === 'waiting_review'
  && started.value && !terminalMission(goalStatus.value) && goalStatus.value !== 'PAUSED');
const phase = computed(() => goal.value?.phase ?? '');
const counts = computed(() => linkedSnapshot.value?.counts ?? status.value?.counts ?? null);
const progress = computed(() => linkedSnapshot.value?.progress ?? status.value?.progress ?? null);
const quality = computed(() => linkedSnapshot.value?.quality ?? status.value?.quality);
const items = computed<RepairStatusItem[]>(() => status.value?.items ?? []);
const todoLabels: Record<string, string> = {
  PENDING: '待检查', OPENING: '正在检查', DETECTING: '正在检查',
  MASK_PENDING: '等待生成 Mask', BUILDING_MASK: '正在生成 Mask',
  MASK_READY: '等待提交或区域复核', SUBMITTING: '正在提交',
  WAITING_SR: '修复中', VERIFYING: '等待回检', AWAITING_REVIEW: '待人工验收',
  SUCCEEDED: '已通过', SKIPPED: '已跳过', NO_ISSUE: '无需修复',
  BLOCKED: '受阻', FAILED: '失败', RETRY_WAIT: '等待重试', REVIEW_REJECTED: '人工驳回',
};
const actionLabels: Record<string, string> = {
  detect_repair_item: '检测', build_repair_mask: '生成 Mask', submit_repair_item: '提交 SR',
  verify_repair_output: '验证产物', assess_repair_effect: '效果回检',
};
function todoLabel(row: BatchTodo): string {
  return todoLabels[row.phase ?? ''] ?? todoLabels[row.status] ?? row.status;
}
function needsItemMaskReview(itemId: string): boolean {
  return items.value.some(item => item.item_id === itemId && item.status === 'MASK_READY'
    && item.review_required && !item.review_decision);
}
function todoGroup(row: BatchTodo): 'active' | 'attention' | 'ended' {
  if (['AWAITING_REVIEW', 'BLOCKED'].includes(row.status) || needsItemMaskReview(row.item_id)) return 'attention';
  if (['SUCCEEDED', 'SKIPPED', 'NO_ISSUE', 'FAILED', 'REVIEW_REJECTED'].includes(row.status)) return 'ended';
  return 'active';
}
const decisionsByItem = computed(() => {
  const decisions = new Map<string, NonNullable<typeof batch.events>>();
  if (!linkedSnapshot.value) return decisions;
  for (const event of batch.events ?? []) {
    const id = event.payload?.item_id;
    if (event.event_type !== 'agent_action_selected' || typeof id !== 'string') continue;
    const rows = decisions.get(id) ?? [];
    rows.push(event);
    decisions.set(id, rows);
  }
  return decisions;
});
const latestAgentEvent = computed(() => (linkedSnapshot.value ? batch.events ?? [] : [])
  .slice().reverse().find(event => ['agent_action_selected', 'agent_decision_failed'].includes(event.event_type)));
const effectCounts = computed(() => {
  const rechecks = linkedSnapshot.value?.todos?.flatMap(row => row.effect_review ? [row.effect_review] : []) ?? [];
  return {
    measured: rechecks.filter(row => row.status === 'measured').length,
    blocked: rechecks.filter(row => row.status === 'blocked').length,
    waiting: rechecks.filter(row => !['measured', 'blocked'].includes(row.status)).length,
  };
});
const metricLabels: Record<string, string> = {
  edge_width_px: '边缘宽度（像素）', edge_halo_fraction: '过冲比例',
  hf_mad_dn: '高频残差 MAD', row_banding_dn: '横向条带',
  column_banding_dn: '纵向条带', mask_boundary_jump_dn: 'Mask 边界亮度跳变',
};
function describeTodo(row: BatchTodo): string {
  const lines = [todoLabel(row)];
  if (row.job_id) lines.push(`Job ${row.job_id}`);
  if (row.error) lines.push(row.error);
  if (row.effect_review?.evidence_gap) lines.push(row.effect_review.evidence_gap);
  for (const region of row.effect_review?.regions ?? []) {
    lines.push(`${region.region_id} · ${region.conclusion === 'unchanged' ? '未见变化' : '证据不足'}`);
    for (const [key, metric] of Object.entries(region.metrics ?? {})) {
      lines.push(`${metricLabels[key] ?? key} ${metric.before.toFixed(2)} → ${metric.after.toFixed(2)}`);
    }
  }
  for (const entry of decisionsByItem.value.get(row.item_id) ?? []) {
    const action = String(entry.payload.action);
    lines.push([actionLabels[action] ?? action, entry.payload.reason].filter(Boolean).join(' · '));
  }
  return lines.join('\n');
}
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
      fullTargets.value = rows.slice();
      if (!page.has_more) {
        if (rows.length !== current.targets.total) throw new Error('清单返回景数与计划不一致，请重新读取。');
        break;
      }
      if (page.next_offset <= offset) throw new Error('清单分页未前进，请重新查看');
      offset = page.next_offset;
    } while (true);
  } catch (e) { if (!disposed && token === targetGen) drawerError.value = e instanceof Error ? e.message : String(e); }
  finally { if (token === targetGen) drawerLoading.value = false; }
}
function resetTargets(keepOpen = false) {
  targetGen++; fullTargets.value = [];
  if (!keepOpen) drawerOpen.value = false;
  drawerLoading.value = false; drawerError.value = '';
  if (drawerOpen.value) void openTargets();
}
const targetCount = computed(() => (plan.value?.status === 'BLOCKED' ? plan.value.targets.total : plan.value?.targets?.applicable)
  ?? counts.value?.eligible ?? 0);
const blockedReason = computed(() => {
  const p = plan.value;
  if (!p) return '';
  if (p.status === 'SKIPPED') return '';
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
const detailsOpen = ref(false), conflictsOpen = ref(false), preparing = ref(false);
const conflicts = computed(() => plan.value?.blocking ?? []);
const firstConflict = computed(() => conflicts.value.find(r => r.code === 'pan_backup_exists') ?? conflicts.value[0]);
const landBlock = computed(() => conflicts.value.find(row => row.code === 'land_constraint_unavailable'));
const otherConflicts = computed(() => conflicts.value.filter(r => r !== firstConflict.value));
const firstConflictLabel = computed(() => [firstConflict.value?.name, firstConflict.value?.filename].filter(Boolean).join(' · '));
const historical = computed(() => ['EXPIRED', 'SUPERSEDED'].includes(planStatus.value) || plan.value?.mode !== 'production');
const historyExpanded = ref(false);
const canExclude = computed(() => conflicts.value.some(r => r.code === 'pan_backup_exists' && r.scene_ref && Number.isInteger(r.line)));
const copyNotice = ref('');
const primaryFeedback = computed(() => preparing.value ? '正在重新准备…'
  : action.value === 'starting' ? '正在确认并启动…'
  : !synchronized.value ? '正在核对计划…'
  : error.value ? '操作失败 · 点击重试'
  : canExclude.value && planStatus.value === 'BLOCKED' ? '排除冲突项并重新准备' : '确认并启动');
async function copyConflictPath() {
  try { await navigator.clipboard.writeText(firstConflict.value?.conflict_path || firstConflict.value?.scene_ref || ''); copyNotice.value = '已复制路径'; }
  catch { copyNotice.value = '复制失败，请在详情中复制'; detailsOpen.value = true; }
}
async function excludeConflicts() {
  const p = plan.value;
  if (!p || preparing.value || !canExclude.value || !chat.sessionId) return;
  preparing.value = true; error.value = '';
  try {
    const next = await apiPrepareRepair({ session_id: chat.sessionId, manifest_ref: p.manifest_ref,
      exclusions: [...(p.exclusions ?? []), ...conflicts.value.filter(r => r.code === 'pan_backup_exists' && r.scene_ref && Number.isInteger(r.line)).map(r => ({ line: r.line!, reason: '已有 PAN 备份' }))],
      mode: 'production', strategy: p.strategy_policy ?? 'automatic',
      detection_method: (p.strategy.detection_method === 'existing' ? 'absdiff' : p.strategy.detection_method) as 'absdiff' | 'ssim' | 'smart',
      mask_policy: p.mask?.policy === 'existing' ? 'existing' : 'auto', mask_path: p.mask?.path, title: p.title });
    if (disposed) return;
    resetTargets(); plan.value = next; status.value = null; synchronized.value = false;
    emit('planRevised', next);
    detailsOpen.value = false; conflictsOpen.value = false;
    await refreshStatus();
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { preparing.value = false; }
}
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
    if (planStatus.value === 'BLOCKED') return conflicts.value.length ? `${conflicts.value.length} 项冲突` : '待处理冲突';
    if (planStatus.value === 'SKIPPED') return '已跳过';
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
/* Rotation indicates an ongoing workflow, including mask review and external
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
const taskTodos = computed(() => linkedSnapshot.value?.todos ?? []);
const todoFinished = computed(() => taskTodos.value.filter(row => todoGroup(row) === 'ended').length);
const todoAttention = computed(() => taskTodos.value.filter(row => todoGroup(row) === 'attention').length);
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
  if (!goalId.value || batch.goalId === goalId.value) return;
  if (batch.goalId && !props.standalone) return;
  try {
    if (batch.goalId) await batch.selectGoal(goalId.value);
    else await batch.attach(goalId.value);
  } catch { /* REST status remains usable */ }
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
      session_id: sessionId, plan_version: current.version, local_agent: true,
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
  await router.push('/viewer');
  await nextTick();
  window.dispatchEvent(new CustomEvent('batch-task-open',
    { detail: { section: 'monitor' } }));
}
async function openMaskReview(itemId?: string): Promise<void> {
  const canOpen = () => itemId ? needsItemMaskReview(itemId) : needsMaskReview.value;
  if (!canOpen() || !goalId.value || maskReviewOpening.value) return;
  const gid = goalId.value;
  maskReviewOpening.value = true;
  error.value = '';
  try {
    if (batch.goalId !== gid && !await batch.selectGoal(gid)) throw new Error('无法打开该任务，请稍后重试。');
    if (gid !== goalId.value || !canOpen()) return;
    await router.push('/viewer');
    if (itemId) requestMaskReview(gid, itemId);
    else requestMaskReview(gid);
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { maskReviewOpening.value = false; }
}
async function togglePause(): Promise<void> {
  if (!canTogglePause.value || pausePending.value || cancelBusy.value || batch.controlBusy) return;
  const gid = goalId.value;
  const sessionId = chat.sessionId;
  const action = goalStatus.value === 'PAUSED' ? 'resume' : 'pause';
  const current = () => !disposed && goalId.value === gid && chat.sessionId === sessionId;
  pausePending.value = action;
  error.value = '';
  controlNotice.value = '';
  try {
    if (batch.goalId !== gid && !await batch.selectGoal(gid)) {
      throw new Error('无法读取该任务，请稍后重试。');
    }
    if (!current() || batch.goalId !== gid) return;
    const notice = await batch.control(action);
    if (!current() || batch.goalId !== gid) return;
    const problem = batch.controlError;
    await refreshStatus();
    if (!current()) return;
    if (problem) error.value = problem;
    else controlNotice.value = notice;
  } catch (e) {
    if (current()) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    pausePending.value = null;
  }
}
const taskControl = computed<{ icon: 'pause' | 'play' | 'square'; label: string; busy: boolean } | null>(() => canTogglePause.value ? {
  icon: pausePending.value || cancelBusy.value || batch.controlBusy
    ? 'square' : goalStatus.value === 'PAUSED' ? 'play' : 'pause',
  label: pauseLabel.value,
  busy: Boolean(pausePending.value || cancelBusy.value || batch.controlBusy),
} : null);

defineExpose({ taskControl, togglePause });

async function stopTask(): Promise<void> {
  if (!canStop.value || cancelBusy.value || pausePending.value || batch.controlBusy) return;
  const gid = goalId.value;
  cancelBusy.value = true; error.value = '';
  try {
    if (batch.goalId !== gid && !await batch.selectGoal(gid)) throw new Error('无法打开该任务，请稍后重试。');
    if (goalId.value !== gid || batch.goalId !== gid) throw new Error('当前任务已变化，请重新操作。');
    const notice = await batch.control('cancel');
    if (goalId.value !== gid) return;
    if (batch.controlError) error.value = batch.controlError;
    else controlNotice.value = notice;
    await refreshStatus();
  } catch (e) { if (goalId.value === gid) error.value = e instanceof Error ? e.message : String(e); }
  finally { cancelBusy.value = false; }
}
async function openResult(item = resultItem.value): Promise<void> {
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
  else await router.push('/viewer');
}
async function startReview(itemId = reviewTarget.value?.item_id, regionId?: string, effect = false): Promise<void> {
  const gid = goalId.value;
  if (!gid) return;
  if (batch.goalId !== gid && !await batch.selectGoal(gid)) return;
  error.value = '';
  // One direct landing: the existing Batch Panel opens its quality block and
  // the existing quality store selects this Item's first pending region and
  // opens the NOSR / SR pair.  No second review UI is created.
  await router.push('/viewer');
  await nextTick();
  if (effect) requestQualityReview(itemId, regionId, gid, undefined, true);
  else requestQualityReview(itemId, regionId, gid);
}
function onVisibility(): void {
  if (document.visibilityState === 'visible') scheduleStatusRefresh(0);
}
watch(() => linkedSnapshot.value?.goal.last_seq, () => scheduleStatusRefresh());
watch(() => batch.goalId, (value) => {
  if (value && value === goalId.value) scheduleStatusRefresh(100);
});
watch(() => [plan.value?.plan_id, plan.value?.version] as const, ([id], [previousId]) => {
  resetTargets(id === previousId);
});
watch(() => chat.sessionId, () => { statusGen++; synchronized.value = false; resetTargets(); void refreshStatus(); });
watch(() => props.data, value => {
  const next = repairPlanData(value);
  if (!next || next.plan_id === plan.value?.plan_id && next.version === plan.value?.version) return;
  statusGen++; plan.value = next; status.value = null; goalId.value = '';
  synchronized.value = false;
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
  <RepairEvidenceEntry v-if="goalId && !standalone" :goal-id="goalId" />
  <div v-if="plan" ref="cardRoot" class="repair-task" :class="{ 'standalone-task': standalone }"
    data-e2e="agent-repair-task" :data-stage="stage">
    <header v-if="standalone" class="standalone-head">
      <div><button type="button" class="task-back" @click="emit('back')">‹ 返回聊天</button><h2>修复待办</h2></div>
      <span class="state" :class="stateTone">{{ goalStatus === 'RUNNING' && stateLabel === '已启动' ? '执行中' : stateLabel }}</span>
    </header>
    <header v-else class="task-head">
      <span class="task-title"><AppIcon v-if="stage !== 'plan'" name="layers" :size="18" tint /><strong>{{ stage === 'done' ? '修复结果' : stage === 'running' ? '自动修复' : '修复计划' }} · {{ targetCount }} 景</strong></span>
      <span class="task-state">
        <span class="state-ring" :class="stateIcon" :data-state-icon="stateIcon" data-e2e="repair-state-ring" aria-hidden="true">
          <svg viewBox="0 0 20 20">
            <circle v-if="stateIcon === 'running'" class="loading-arc" cx="10" cy="10" r="8" pathLength="100" stroke-dasharray="75 25" />
            <circle v-else class="ring" cx="10" cy="10" r="8" />
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
    <section v-if="stage === 'plan'" :key="planId" class="plan-body">
      <strong v-if="standalone">{{ scopeLabel }} · {{ targetCount }} 景</strong>
      <p class="plan-meta"><template v-if="!standalone">{{ scopeLabel }} · </template>{{ detectionLabel }} · {{ plan.strategy.mask === 'existing' ? '已有掩码' : '自动掩码' }}</p>
      <p v-if="plan.skipped_completed?.length" class="plan-meta">已跳过 {{ plan.skipped_completed.length }} 景已有 PAN 备份的影像</p>
      <button v-if="historical" class="list-link" :aria-expanded="historyExpanded" @click="historyExpanded = !historyExpanded">{{ historyExpanded ? '收起历史计划' : '查看历史计划' }}</button>
      <template v-if="!historical || historyExpanded">
      <RepairLandSummary v-if="plan.land_constraint" :plan="plan" />
      <div v-if="blockedReason" class="conflict-section">
        <strong class="conflict-title">{{ firstConflict?.code === 'pan_backup_exists' ? '检测到已有 PAN 备份' : landBlock ? '生产自动修复受阻' : '计划待处理' }}</strong>
        <template v-if="landBlock">
          <p class="warn" data-e2e="repair-land-block">{{ landBlock.message || plan.execution.reason || '可信陆地约束不可用' }}</p>
          <p class="note">仍可在 Viewer 使用「差异定位 → 自动绘制」生成掩码。核对并保存后，可按单景使用已有掩码另建计划。</p>
        </template>
        <p v-if="firstConflictLabel" class="conflict-name" :title="firstConflictLabel">{{ firstConflictLabel }}</p>
        <div class="actions">
          <span v-if="firstConflict?.scene_ref" class="folder-action"><ExplorerButton :directory="firstConflict.scene_ref" label="定位文件夹" /></span>
          <button v-if="firstConflict?.conflict_path || firstConflict?.scene_ref" @click="copyConflictPath">复制路径</button>
        </div>
        <p v-if="copyNotice" role="status">{{ copyNotice }}</p>
        <button class="list-link detail-link" :aria-expanded="detailsOpen" @click="detailsOpen = !detailsOpen">{{ detailsOpen ? '收起详情' : '查看详情' }}<span class="chevron" :class="{ expanded: detailsOpen }" aria-hidden="true">›</span></button>
        <Transition name="repair-stage"><div v-if="detailsOpen" class="conflict-details"><p>{{ firstConflict?.conflict_path }}</p><p>{{ firstConflict?.message || blockedReason }}</p></div></Transition>
        <button v-if="conflicts.length > 1" class="list-link" :aria-expanded="conflictsOpen" @click="conflictsOpen = !conflictsOpen">{{ conflictsOpen ? '收起冲突列表' : `查看全部 ${conflicts.length} 项冲突` }}<span class="chevron" :class="{ expanded: conflictsOpen }" aria-hidden="true">›</span></button>
        <Transition name="repair-stage"><ul v-if="conflictsOpen" class="conflict-details"><li v-for="(row, index) in otherConflicts" :key="index"><p class="conflict-name" :title="[row.name, row.filename].filter(Boolean).join(' · ')">{{ row.name }} · {{ row.filename || '待处理冲突' }}</p><details><summary>查看详情</summary><p>{{ row.conflict_path }}</p><p>{{ row.message }}</p></details></li></ul></Transition>
      </div>
      <div class="plan-footer"><button class="list-link" data-e2e="repair-targets-open" :aria-expanded="drawerOpen" @click="openTargets">查看目标清单 · {{ plan.targets.total }} 景 <span class="chevron" :class="{ expanded: drawerOpen }" aria-hidden="true">›</span></button></div>
      <div v-if="canExclude && planStatus === 'BLOCKED'" class="actions plan-actions"><button class="primary" data-e2e="repair-plan-exclude" :aria-busy="preparing" :disabled="preparing || !synchronized" @click="excludeConflicts"><Transition name="button-label" mode="out-in"><span :key="primaryFeedback">{{ primaryFeedback }}</span></Transition></button></div>
      <p v-if="error" class="warn action-error" role="alert">{{ error }}</p>
      <div v-if="canAct" class="actions plan-actions">
        <button class="primary" data-e2e="repair-plan-start"
          :aria-busy="action === 'starting'" :disabled="action !== 'idle' || !synchronized" @click="startPlan">
          <Transition name="button-label" mode="out-in"><span :key="primaryFeedback">{{ primaryFeedback }}</span></Transition>
        </button>
      </div>
      <button v-if="!synchronized && error" class="list-link" @click="refreshStatus">重新读取计划</button>
      <details v-if="developerMode" class="developer-details"><summary>开发者详情</summary><pre>{{ plan }}</pre></details>
      </template>
    </section>

    <section v-else :key="stage" class="execution-body">
      <RepairLandSummary v-if="!standalone && plan.land_constraint" :plan="plan" />
      <p v-if="plan.mode !== 'production'" class="mode-note">Demo / Fake SR · 不产生真实修复成果</p>
      <p v-if="!standalone && currentItem" class="current" :title="`${currentItem.name} · ${currentItem.label}`">
        <span class="current-prefix">当前影像</span><span class="current-name">{{ currentItem.name }}</span>
      </p>
      <p v-if="!standalone" class="overall-summary" data-e2e="repair-overall-summary">
        <template v-if="primaryPercent != null">已处理 <b>{{ processed.done }} / {{ processed.total }}</b> 景 · {{ Math.round(primaryPercent) }}%</template>
        <template v-else>{{ processed.known ? '无适用影像' : '统计中' }}</template>
      </p>
      <p v-if="!standalone && (effectCounts.measured || effectCounts.blocked || effectCounts.waiting)" class="overall-summary">
        效果回检 · {{ effectCounts.measured }} 景已测量 · {{ effectCounts.blocked }} 景受阻 · {{ effectCounts.waiting }} 景待完成
      </p>
      <p v-if="latestAgentEvent?.event_type === 'agent_decision_failed' && !terminalMission(goalStatus)" class="note">
        Agent 暂未取得有效动作，稍后重试。
      </p>
      <AgentTodoList v-if="linkedSnapshot?.todos" :rows="linkedSnapshot.todos"
        :label="todoLabel" :describe="describeTodo" :group="todoGroup">
        <template v-if="!standalone" #actions>
          <button v-if="canTogglePause" :disabled="!!pausePending || cancelBusy || batch.controlBusy" @click="togglePause">
            {{ pauseLabel }}
          </button>
        </template>
        <template #default="{ row }">
          <button v-if="needsItemMaskReview(row.item_id)" :disabled="maskReviewOpening" title="复核候选区域"
            @click="openMaskReview(row.item_id)">复核</button>
          <button v-else-if="row.effect_review?.regions?.[0] && items.some(item => item.item_id === row.item_id && item.quality_review_id)"
            @click="startReview(row.item_id, row.effect_review.regions[0].region_id, true)">查看区域</button>
          <button v-else-if="standalone && row.status === 'AWAITING_REVIEW' && items.some(item => item.item_id === row.item_id && item.quality_review_id)"
            @click="startReview(row.item_id)">前往复核</button>
          <button v-else-if="items.some(item => item.item_id === row.item_id && item.output_path)"
            @click="openResult(items.find(item => item.item_id === row.item_id))">查看成果</button>
        </template>
      </AgentTodoList>
      <p v-else-if="standalone && !error" class="note" role="status">正在读取逐景状态…</p>
      <BatchProgressTracks v-if="!standalone" :counts="counts" :demo="plan.mode !== 'production'" />
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
      <p v-if="controlNotice" class="note" role="status">{{ controlNotice }}</p>
      <div v-if="goalId || resultItem || reviewTarget" class="actions"
        :class="{ 'standalone-footer': standalone }" data-e2e="repair-task-actions">
        <span v-if="standalone && linkedSnapshot?.todos" class="standalone-count">
          已结束 {{ todoFinished }} / {{ targetCount }} 景<span v-if="todoAttention"> · 待处理 {{ todoAttention }} 景</span>
        </span>
        <button v-if="!standalone && goalId" class="primary" @click="openTask">查看进度</button>
        <HoldConfirmButton v-if="canStop" :key="goalId" class="stop-task" data-e2e="repair-stop-task"
          label="中止任务" :disabled="cancelBusy || !!pausePending || batch.controlBusy" @confirm="stopTask">
          {{ cancelBusy ? '正在中止…' : '中止任务' }}
        </HoldConfirmButton>
        <button v-if="needsMaskReview" class="mask-review-button" data-e2e="repair-mask-review" :disabled="maskReviewOpening" @click="openMaskReview()">{{ maskReviewOpening ? '正在前往…' : '前往复核' }}</button>
        <button v-if="stage === 'done' && resultItem" data-e2e="repair-view-result" @click="openResult()">查看成果</button>
        <button v-if="stage === 'done' && reviewTarget" data-e2e="repair-start-review" @click="startReview()">开始复核</button>
      </div>
    </section>
    </Transition>
    <RepairEvidenceEntry v-if="standalone && goalId" :goal-id="goalId" />
    <Teleport to="body"><Transition name="repair-drawer">
      <RepairTargetDrawer v-if="drawerOpen" :rows="targetRows" :total="plan.targets.total" :loading="drawerLoading" :error="drawerError" @close="drawerOpen = false" />
    </Transition></Teleport>
  </div>
</template>
<style scoped>
.repair-task.standalone-task { border:0; border-radius:0; margin:0; padding:16px; background:var(--surface,#fff); gap:14px; }
.standalone-head { display:flex; justify-content:space-between; align-items:end; gap:8px; padding-bottom:12px; border-bottom:1px solid var(--tb-border,#e4ebe6); }
.standalone-head h2 { margin:6px 0 0; font-size:20px; line-height:1.25; }
.standalone-task .task-back { border:0; padding:0; background:none; color:var(--tb-primary,#166c67); }
.standalone-task :deep(.todo-viewport) { margin-block:0; max-height:15rem; }
.standalone-count { margin-inline-end:auto; color:var(--tb-muted,#566960); }
.standalone-footer .stop-task { margin-inline-start:auto; }
.repair-task { display:grid; grid-template-columns:minmax(0,1fr); gap:10px; margin:6px 0; padding:12px; border:1px solid var(--tb-border); border-radius:12px; font-size:12px; background:var(--tb-bg); color:var(--tb-fg); min-width:0; width:100%; max-width:100%; box-sizing:border-box; overflow:hidden; }
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
.state-ring.running svg { animation: state-ring-spin 1200ms linear infinite; transform-origin:50% 50%; filter:blur(.35px); }
@keyframes state-ring-spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .state-ring.running svg { animation:none; } }
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
button.stop-task{color:#9b5145;border-color:#d9b7ae;background:var(--surface)}
button:disabled { opacity: .55; cursor: default; }
@media (prefers-reduced-motion: reduce) { *, :deep(*) { transition:none !important; } }
</style>

<style scoped>
.plan-meta{font-size:11px;color:var(--tb-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.conflict-section{display:grid;gap:8px;padding:12px;min-width:0;border-radius:8px;background:#f5f3ef}
.conflict-title{color:var(--tb-fg);font-size:12px;font-weight:500}.conflict-name{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--tb-muted)}
.conflict-details{max-height:180px;overflow:auto;overflow-wrap:anywhere;margin:0;padding:0;list-style:none;user-select:text}.conflict-details li{padding:8px 0;border-bottom:1px solid var(--tb-border)}
.folder-action{display:inline-flex;min-width:0}.plan-footer{display:flex;gap:8px;padding-top:2px}.plan-footer button.list-link{color:var(--tb-muted);font-size:11px}
.conflict-section .actions{gap:7px}
.conflict-section .actions>button,.folder-action :deep(button.explorer-open){position:static;display:inline-flex;align-items:center;justify-content:center;gap:5px;width:auto;height:32px;min-height:32px;padding:0 9px;border:1px solid var(--tb-border);border-radius:7px;background:#fff;color:var(--tb-fg);font:inherit;font-size:11px}
.conflict-section button.list-link{color:var(--tb-muted);font-size:10px;min-height:18px}
.plan-actions button.primary{height:38px;border-radius:10px;background:var(--nav-bg,#173f3b);border-color:var(--nav-bg,#173f3b);color:#fff;font-weight:600;box-shadow:0 1px 2px rgb(23 63 59 / 12%);transition:background-color 120ms ease,opacity 180ms ease}
.plan-actions button.primary:hover:enabled{background:#24544e;border-color:#24544e}
.chevron{display:inline-block;margin-left:4px;line-height:1;transition:transform 180ms ease;transform:rotate(0deg)}
.chevron.expanded{transform:rotate(90deg)}
.button-label-enter-active,.button-label-leave-active{transition:opacity 90ms ease,transform 90ms ease}
.button-label-enter-from{opacity:0;transform:translateY(2px)}
.button-label-leave-to{opacity:0;transform:translateY(-2px)}
.plan-actions button.primary>span{display:inline-block}
.conflict-section .repair-stage-enter-active,.conflict-section .repair-stage-leave-active{transition:opacity 180ms ease,transform 180ms ease}
.repair-task[data-stage="plan"]{gap:9px;background:var(--tb-bg);border-color:var(--tb-border)}
.repair-task[data-stage="plan"] .state{padding:0;background:transparent;color:var(--tb-muted);font-size:10px}
.repair-task[data-stage="plan"] .state.error{color:#a36b50}
.repair-task[data-stage="plan"] .state-ring{width:13px;height:13px}
.repair-task[data-stage="plan"] .state-ring svg{width:13px;height:13px}
.repair-task[data-stage="plan"] .task-state{gap:4px}
.developer-details{color:var(--tb-muted);font-size:10px;min-width:0}.developer-details pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto}
.plan-body{gap:10px}
.repair-stage-enter-active{transition:opacity 220ms ease,transform 220ms ease}
.repair-stage-leave-active{transition:opacity 180ms ease}.repair-stage-leave-to{opacity:0}
</style>
