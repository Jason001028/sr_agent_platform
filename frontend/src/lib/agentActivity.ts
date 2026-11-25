/** Pure presentation projection. No execution, timers, persistence or reasoning inference. */
import type { ChatMsg } from '../stores/chat';
import type { InspectionProgress } from './api';
import { itemStatusLabel } from './batchUi';
import { toolActionLabel } from './chatActivity';
import { repairPlanData, repairPlanId } from './repairPlan';

export type ActivityState = 'running' | 'success' | 'failed' | 'interrupted';
export type BusinessKind = 'inspection' | 'plan' | 'scenes' | 'receipt' | 'annotation' | 'confirmation';
export interface ActivityStep {
  key: string; name: string; label: string; args: unknown; callId?: string;
  callIndex: number; resultIndex?: number; result?: ChatMsg;
  state: ActivityState; summary: string; summaryTitle?: string; business: BusinessKind | null;
  inspection?: Record<string, unknown>;
  inspectionProgress?: string;
  liveInspection?: InspectionProgress;
  startedAt?: number;
  attention?: string;
  target?: string;
  resultLink?: { key: string; label: string };
}
export interface ActivityGroup { key: string; steps: ActivityStep[] }
export interface AgentActivityProjection {
  steps: ActivityStep[];
  groups: Map<number, ActivityGroup[]>;
  business: Map<number, ActivityStep[]>;
}
export function toolPayload(result?: ChatMsg): Record<string, unknown> | null {
  const raw = result?.data;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const object = raw as Record<string, unknown>;
  const nested = object.data;
  return typeof object.ok === 'boolean' && nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown> : object;
}
export function toolOutcome(result?: ChatMsg): boolean | undefined {
  const envelope = result?.data as { ok?: boolean } | null;
  if (result?.ok === false || envelope?.ok === false) return false;
  if (result?.ok === true || envelope?.ok === true) return true;
  return undefined;
}
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function sceneStatus(value: unknown): string | undefined {
  return typeof value === 'string' ? itemStatusLabel(value) : undefined;
}

function sceneLabel(data: Record<string, unknown>): string | undefined {
  if (typeof data.name === 'string' && data.name.trim()) return data.name;
  if (typeof data.ordinal === 'number') return `第 ${data.ordinal} 景`;
}

const scopeLabels: Record<string, string> = { workspace: '当前工作区', current: '当前影像', selection: '已选影像' };
const detectionLabels: Record<string, string> = { ssim: 'SSIM', smart: 'LTD', absdiff: '绝对差异' };

export function inspectionCoverage(data?: Record<string, unknown>) {
  const eligible = typeof data?.total === 'number' ? data.total : 0;
  const omitted: { name: string; reason: string; kind: string; count?: number }[] = Array.isArray(data?.not_inspected)
    ? data.not_inspected
    : (Array.isArray(data?.diagnostics) ? data.diagnostics : [])
        .filter(row => row?.code === 'completed_scene_skipped' || row?.severity === 'error')
        .map(row => ({ name: row.name || '未命名影像', reason: row.message || '影像未进入检测',
          kind: row.code === 'completed_scene_skipped' ? 'skipped' : row.code === 'duplicate_scene' ? 'excluded' : 'unresolved' }));
  const knownOmissions = omitted.reduce((sum, row) => sum + (row.count ?? 1), 0);
  const requested = typeof data?.requested_total === 'number' ? data.requested_total : eligible + knownOmissions;
  const notInspected = Math.max(knownOmissions, requested - eligible);
  return { requested, omitted, notInspected };
}

function callTarget(step: ActivityStep): string | undefined {
  const args = record(step.args);
  if (step.name === 'inspect_repair_workspace') {
    const data = toolPayload(step.result);
    const scope = scopeLabels[String(data?.scope ?? args?.scope ?? 'workspace')];
    const method = detectionLabels[String(data?.detection_method ?? step.liveInspection?.method)];
    if (step.state === 'running' && step.liveInspection?.phase === 'detecting_scene') {
      const { position, total, scene } = step.liveInspection;
      return [`第 ${position}/${total} 景`, scene, method].filter(Boolean).join(' · ');
    }
    const cursor = typeof args?.cursor === 'number' ? args.cursor : 0;
    const limit = typeof args?.limit === 'number' ? args.limit : 5;
    return [scope, method, step.state === 'running' && `从第 ${cursor + 1} 景起，最多 ${limit} 景`].filter(Boolean).join(' · ');
  }
  if (step.name === 'get_current_image_info') return '当前影像';
  if (step.name === 'prepare_repair') return scopeLabels[String(args?.scope ?? 'workspace')];
  if (step.name === 'get_repair_workspace') return '当前工作区';
  if (step.name === 'query_batch_repair') return '修复批次';
  if (step.name === 'run_sr' && typeof args?.lq_path === 'string') return args.lq_path.split(/[\\/]/).filter(Boolean).at(-1);
  if (step.name === 'sr_job_status' && step.state === 'running' && args?.job_id != null) return `Job ${args.job_id}`;
  if (step.name === 'get_repair_item' && step.state === 'running' && typeof args?.ordinal === 'number') return `第 ${args.ordinal} 景`;
  if (step.name === 'search_scenes') {
    const criteria = [args?.query ?? args?.q, args?.satellite, args?.sensor].filter(value => typeof value === 'string' && value);
    return criteria.length ? criteria.join(' · ') : undefined;
  }
}

function resultAttention(step: ActivityStep): string | undefined {
  if (step.state !== 'success') return;
  const data = toolPayload(step.result);
  if (step.name === 'prepare_repair') {
    const plan = repairPlanData(step.result?.data);
    if (plan?.status === 'BLOCKED' || plan?.execution?.allowed === false && plan.status === 'READY') return '计划受阻';
    if (plan?.status === 'READY' && plan.confirmation_required && !step.result?.autoStartRepair) return '需要用户确认';
  }
  if (step.name === 'sr_job_status' && ['FAILED', 'CANCELLED', 'UNKNOWN'].includes(String(data?.state).toUpperCase())) return '作业状态需关注';
  if (step.name === 'query_batch_repair') {
    const counts = record(data?.counts);
    const waiting = record(data?.quality)?.waiting_scenes ?? counts?.awaiting_review;
    if ([counts?.failed, counts?.blocked, waiting, record(data?.quality)?.rejected_scenes]
      .some(count => typeof count === 'number' && count > 0)) return '批次状态需关注';
  }
  if (step.name === 'get_repair_workspace') {
    const batch = record(data?.batch);
    if (Array.isArray(batch?.exceptions) && batch.exceptions.length || Number(batch?.waiting_review) > 0) {
      return '工作区批次需关注';
    }
  }
  if (step.name === 'get_repair_item' && ['FAILED', 'BLOCKED', 'AWAITING_REVIEW', 'REVIEW_REJECTED'].includes(String(data?.status))) return '本景状态需关注';
  if (step.name === 'request_clarification') return '需要用户确认';
}

function querySummary(data: Record<string, unknown> | null): string {
  const counts = record(data?.counts);
  const quality = record(data?.quality);
  const values = [
    [counts?.failed, '景失败'], [counts?.blocked, '景受阻'],
    [quality?.waiting_scenes ?? counts?.awaiting_review, '景待人工验收'], [quality?.rejected_scenes, '景验收驳回'],
    [data?.active_jobs_total, '个作业进行中'],
  ] as const;
  const active = values.flatMap(([count, label]) => typeof count === 'number' && count > 0 ? [`${count} ${label}`] : []);
  if (active.length) return active.join(' · ');
  if (typeof counts?.total === 'number') return typeof counts.verified === 'number'
    ? `${counts.total} 景 · ${counts.verified} 景已验证` : `共 ${counts.total} 景`;
  return '已读取批次状态';
}

export function toolDetails(step: ActivityStep): { label: string; value: string }[] {
  const args = record(step.args);
  const data = toolPayload(step.result);
  const rows: { label: string; value: string }[] = [];
  const add = (label: string, value: unknown) => {
    if (typeof value === 'number' || typeof value === 'string' && value.trim()) rows.push({ label, value: String(value) });
  };
  const scope = data?.scope ?? args?.scope;
  if (typeof scope === 'string') add('范围', scopeLabels[scope]);
  const method = data?.detection_method;
  if (typeof method === 'string') add('检测方法', detectionLabels[method]);
  if (typeof data?.tone_weight === 'boolean') add('亮度归因', data.tone_weight ? '开启' : '关闭');
  if (step.name === 'inspect_repair_workspace' && typeof data?.cursor === 'number' && Array.isArray(data.items) && data.items.length) {
    add('本次返回', `第 ${data.cursor + 1}–${data.cursor + data.items.length} 景`);
  }
  if (step.name === 'list_repair_items') add('状态筛选', args?.status);
  if (step.name === 'get_repair_item' && typeof data?.review_valid === 'boolean') {
    add('复核记录校验', data.review_valid ? '有效' : '失效');
    if (!data.review_valid) add('原因', data.review_invalid_reason);
  }
  add('作业编号', data?.job_id);
  const items = step.name === 'query_batch_repair' ? record(data?.quality)?.items : data?.items;
  if (Array.isArray(items)) for (const value of items) {
    const item = record(value);
    const scene = item && sceneLabel(item);
    if (item && scene) add(scene, item.status === 'failed' ? item.reason ?? '检测失败'
      : typeof item.candidate_count === 'number' ? `${item.candidate_count} 处候选` : sceneStatus(item.status));
  }
  if (data?.has_more === true && step.name === 'list_repair_items') add('分页', '还有更多修复项');
  return rows;
}
function businessKind(step: ActivityStep): BusinessKind | null {
  if (step.state !== 'success') return null;
  const data = toolPayload(step.result);
  if (data?.kind === 'repair_inspection.v1') return 'inspection';
  if (repairPlanId(step.result?.data)) return 'plan';
  if (step.name === 'search_scenes' && data && (Array.isArray(data.results) || typeof data.count === 'number')) return 'scenes';
  if (data?.kind === 'batch_repair_draft.v1' && Array.isArray(data.candidates) && data.candidates.length) return 'receipt';
  if (step.name === 'present_spatial_annotations') return 'annotation';
  if (step.name === 'request_clarification') return 'confirmation';
  return null;
}
function stepSummary(step: ActivityStep): string {
  const data = toolPayload(step.result);
  if (step.state === 'running') {
    if (step.liveInspection?.phase === 'resolving_inspection') return '正在核对影像与 NOSR 配对';
    if (step.liveInspection?.phase === 'detecting_scene') return '正在读取 PAN / NOSR 并检测';
    return '等待工具返回';
  }
  if (step.state === 'interrupted') return '结果未确认';
  if (step.state === 'failed') {
    const error = record(step.result?.data)?.error;
    const detail = typeof error === 'string' ? error : step.result?.detail;
    const readable = detail && !/^[\s]*[{[]/.test(detail)
      ? detail.replace(/\s+/g,' ').trim() : '';
    if (step.name === 'read_tool_result') {
      return readable
        ? `结果读取失败：${readable}` : '结果读取失败';
    }
    return readable || '工具调用失败';
  }
  if (step.name === 'read_tool_result') return typeof data?.shown === 'number'
    ? `本次返回 ${data.shown} 项${data.has_more ? ' · 还有更多' : ''}` : '已读取保存结果';
  if (step.name === 'query_batch_repair') return querySummary(data);
  if (step.name === 'get_repair_workspace' && typeof data?.total === 'number') {
    const batch = record(data.batch);
    return [`共 ${data.total} 景`, batch && querySummary({
      counts: batch.counts, quality: { waiting_scenes: batch.waiting_review, rejected_scenes: batch.rejected },
    })].filter(Boolean).join(' · ');
  }
  if (step.name === 'list_repair_items' && Array.isArray(data?.items)) {
    return `本次读取 ${data.items.length} 景${data.has_more ? ' · 还有更多' : ''}`;
  }
  if (step.name === 'get_repair_item' && data) {
    return [sceneLabel(data), sceneStatus(data.status)].filter(Boolean).join(' · ') || '已读取本景状态';
  }
  if (step.name === 'get_current_image_info') {
    const image = record(data?.image);
    const name = image && sceneLabel(image);
    return name ? `已读取 ${name} 的影像信息` : '已读取当前影像信息';
  }
  if (step.name === 'prepare_repair') {
    const plan = repairPlanData(step.result?.data);
    if (plan?.status === 'BLOCKED') return '计划受阻';
    if (plan?.status === 'SKIPPED') return '无待修复影像';
    const count = plan?.targets?.applicable;
    const state = plan?.status === 'READY' && plan.execution?.allowed === false ? '计划暂不可执行'
      : step.attention === '需要用户确认' ? '计划已就绪，等待确认' : '计划已就绪';
    return typeof count === 'number' ? `${state} · ${count} 景` : '计划已返回';
  }
  if (step.name === 'search_scenes' && typeof data?.count === 'number') return `找到 ${data.count} 个匹配场景`;
  if (step.name === 'start_repair') return typeof data?.targets === 'number'
    ? `已收到 ${data.targets} 景的启动回执` : '已收到启动回执';
  if (step.name === 'run_sr' || step.name === 'sr_job_status') {
    const job = typeof data?.job_id === 'number' || typeof data?.job_id === 'string' ? `Job ${data.job_id}` : '';
    if (step.name === 'run_sr') {
      const reused = data?.reused === true || data?.idempotent === true || String(data?.status).startsWith('RESUMED_');
      return [reused ? '已复用作业' : '已返回提交回执', job].filter(Boolean).join(' · ');
    }
    const states: Record<string, string> = { QUEUED: '排队中', PENDING: '等待执行', RUNNING: '执行中',
      COMPLETED: '作业已结束', FAILED: '作业失败', CANCELLED: '作业已取消', UNKNOWN: '状态待核对' };
    const status = typeof data?.state === 'string' ? states[data.state.toUpperCase()] : undefined;
    return [job, status || '已读取作业状态'].filter(Boolean).join(' · ');
  }
  if (step.name === 'present_spatial_annotations') return '已返回影像批注';
  if (step.name === 'request_clarification') return '已发出确认请求';
  if (data?.kind === 'batch_repair_draft.v1' && Array.isArray(data.candidates)) return `草稿包含 ${data.candidates.length} 景`;
  // 质检分页：total 是整批景数，不是本页条数。每页只报“已到第几景”，
  // 末页不带省略号；27 景按 5 景一页即显示 5/27 景…
  if (data?.kind === 'repair_inspection.v1' && Array.isArray(data.items)
      && typeof data.total === 'number') {
    const shown = data.items.length;
    const end = typeof data.cursor === 'number' ? data.cursor + shown
      : typeof data.next_cursor === 'number' ? data.next_cursor : data.total;
    const failed = data.items.filter(row => row?.status === 'failed').length;
    const candidates = data.items.filter(row => row?.status === 'candidates').length;
    return [`${end}/${data.total} 景${data.has_more ? '…' : ''}`,
      candidates ? `${candidates} 景有候选` : '', failed ? `${failed} 景检测失败` : ''].filter(Boolean).join(' · ');
  }
  if (typeof data?.total === 'number') return `已读取 ${data.total} 项`;
  return '执行完成';
}

function collectInspectionReports(segment: ActivityStep[], active: boolean) {
  interface InspectionRun {
    key: string;
    pages: ActivityStep[];
    rows: Map<number, unknown>;
    final: boolean;
    data: Record<string, unknown>;
    total: number;
  }
  const current = new Map<string, InspectionRun>();
  const runs: InspectionRun[] = [];
  for (const step of segment) {
    if (step.business !== 'inspection') continue;
    step.business = null;
    const page = toolPayload(step.result)!;
    const args = step.args as { scope?: string; cursor?: number } | null;
    const { total, items } = page;
    if (typeof total !== 'number' || !Number.isSafeInteger(total) || total < 0 || !Array.isArray(items)) continue;
    const cursor = page.cursor ?? args?.cursor ?? (typeof page.next_cursor === 'number'
      ? page.next_cursor - items.length : total - items.length);
    if (typeof cursor !== 'number' || !Number.isSafeInteger(cursor) || cursor < 0 || cursor + items.length > total) continue;
    const key = JSON.stringify([page.scope ?? args?.scope ?? 'workspace', page.detection_method, page.tone_weight, total]);
    let run = current.get(key);
    if (!run || cursor === 0 && (run.rows.has(0) || run.total === 0)) {
      run = { key, pages: [], rows: new Map(), final: false, data: page, total };
      current.set(key, run);
      runs.push(run);
    }
    run.pages.push(step);
    if (run.final && run.rows.size === total) continue;
    items.forEach((row, index) => run.rows.set(cursor + index, row));
    run.final ||= page.has_more === false && cursor + items.length === total;
  }
  for (const run of runs) {
    const anchor = run.pages[0];
    const complete = run.final && run.rows.size === run.total;
    const progress = `已返回 ${run.rows.size}/${run.total} 景`;
    const failed = [...run.rows.values()].filter(row => record(row)?.status === 'failed').length;
    const { notInspected } = inspectionCoverage(run.data);
    for (const page of run.pages) page.inspectionProgress = [progress, failed && `${failed} 景检测失败`,
      notInspected && `${notInspected} 景未检测`].filter(Boolean).join(' · ');
    if (!complete && active && current.get(run.key) === run) continue;
    anchor.business = 'inspection';
    if (failed || notInspected) anchor.attention = '质检有未完成项';
    anchor.inspection = {
      ...run.data, partial: !complete,
      items: [...run.rows].sort(([a], [b]) => a - b).map(([, row]) => row),
    };
    for (const page of run.pages) page.resultLink = { key: anchor.key, label: complete ? '查看质检结果' : '查看已返回结果' };
  }
}

export function projectAgentActivity(messages: ChatMsg[], sending: boolean): AgentActivityProjection {
  const steps: ActivityStep[] = [];
  const segments: ActivityStep[][] = [];
  let turn = -1, current: ActivityStep[] = [];
  let legacyOrdinal = 0;
  let byId = new Map<string, ActivityStep>();
  let seenResults = new Set<string>();
  let seenDeclarations = new Set<string>();
  const finishTurn = () => { if (current.length) segments.push(current); current = []; };
  messages.forEach((message, index) => {
    if (message.role === 'user') {
      finishTurn(); turn++; legacyOrdinal = 0; byId = new Map(); seenResults = new Set(); seenDeclarations = new Set(); return;
    }
    if (message.role === 'assistant') {
      (message.toolCalls ?? []).forEach((call, ordinal) => {
        const identity = call.id ? `id:${call.id}` : `legacy:${message.seq ?? index}:${ordinal}`;
        if (seenDeclarations.has(identity)) return;
        seenDeclarations.add(identity);
        const existing = call.id ? byId.get(call.id) : undefined;
        if (existing) { existing.args = call.args; existing.name = call.name; existing.label = toolActionLabel(call.name); return; }
        const stableIdentity = call.id ? identity : `legacy:${legacyOrdinal++}`;
        const step: ActivityStep = {key:`turn:${turn}:${stableIdentity}`,name:call.name,label:toolActionLabel(call.name),
          startedAt:call.startedAt,liveInspection:call.inspectionProgress,
          args:call.args,callId:call.id,callIndex:index,state:'interrupted',summary:'',business:null};
        current.push(step); steps.push(step);
        if (call.id) byId.set(call.id,step);
      });
    }
    if (message.role !== 'tool') return;
    const resultIdentity = message.toolCallId ? `id:${message.toolCallId}` : message.seq != null ? `seq:${message.seq}` : '';
    if (resultIdentity && seenResults.has(resultIdentity)) return;
    if (resultIdentity) seenResults.add(resultIdentity);
    let step = message.toolCallId ? byId.get(message.toolCallId) : undefined;
    // A result with a different explicit ID never attaches to another ID.
    if (!step) step = current.find(s => !s.result && s.name === message.toolName && (!message.toolCallId || !s.callId));
    if (!step) {
      const name = message.toolName ?? 'unknown';
      step = {key:`turn:${turn}:result:${resultIdentity || index}`,name,label:toolActionLabel(name),args:null,
        callId:message.toolCallId,callIndex:index,state:'interrupted',summary:'',business:null};
      current.push(step); steps.push(step);
    }
    step.result = message; step.resultIndex = index;
    if (message.toolCallId) byId.set(message.toolCallId,step);
  });
  finishTurn();
  const latestUser = messages.map(m => m.role).lastIndexOf('user');
  const groups = new Map<number, ActivityGroup[]>(), business = new Map<number, ActivityStep[]>();
  const plans = new Map<string, { anchor: ActivityStep; latest: ActivityStep; autoStart: boolean }>();
  for (const step of steps) {
    const outcome = toolOutcome(step.result);
    // Only unresolved calls in the active assistant phase are still running.
    const endedAfterCall = messages.slice(step.callIndex + 1).some(m => m.role === 'user' || m.isError
      || m.role === 'assistant' && !!m.content && !m.streaming);
    step.state = outcome === false ? 'failed' : outcome === true ? 'success'
      : !step.result && sending && step.callIndex > latestUser && !endedAfterCall ? 'running' : 'interrupted';
    step.attention = resultAttention(step);
    step.summary = stepSummary(step); step.business = businessKind(step);
    step.target = callTarget(step);
    if (step.business === 'plan') {
      const plan = repairPlanData(step.result?.data)!;
      const previous = plans.get(plan.plan_id);
      const sameRevision = typeof plan.version === 'number'
        && repairPlanData(previous?.latest.result?.data)?.version === plan.version;
      const autoStart = step.result?.autoStartRepair === true
        || (step.name === 'read_tool_result' && sameRevision && previous?.autoStart === true);
      plans.set(plan.plan_id, { anchor: previous?.anchor ?? step, latest: step, autoStart });
    }
  }
  const resultLabels = { plan: '查看计划', scenes: '查看场景', receipt: '查看修复清单' };
  for (const segment of segments) {
    collectInspectionReports(segment, sending && segment[0].callIndex > latestUser);
    let previousPlan: ActivityStep['resultLink'];
    let pending: ActivityGroup | null = null, anchor = -1;
    const flush = () => { if (pending) groups.set(anchor,[...(groups.get(anchor) ?? []),pending]); pending = null; };
    let previousIndex = -1;
    for (const step of segment) {
      const proseBoundary = messages.slice(previousIndex + 1,step.callIndex).some(m => m.role === 'assistant' && !!m.content)
        || previousIndex >= 0 && previousIndex !== step.callIndex && messages[step.callIndex].role === 'assistant' && !!messages[step.callIndex].content;
      if (proseBoundary) flush();
      previousIndex = step.callIndex;
      if (!pending) { anchor = step.callIndex; pending = {key:step.key,steps:[]}; }
      pending.steps.push(step);
      if (step.business) {
        const plan = step.business === 'plan' ? plans.get(repairPlanId(step.result?.data)!) : undefined;
        if (step.business === 'plan' || step.business === 'scenes' || step.business === 'receipt') {
          step.resultLink = { key: plan?.anchor.key ?? step.key, label: resultLabels[step.business] };
          if (plan) previousPlan = step.resultLink;
        }
        if (!plan || plan.anchor === step) {
          const at = step.resultIndex!;
          const presented = plan ? { ...plan.latest, key: step.key } : step;
          if (plan?.autoStart && presented.result && !presented.result.autoStartRepair) {
            presented.result = { ...presented.result, autoStartRepair: true };
          }
          business.set(at,[...(business.get(at) ?? []),presented]);
        }
      }
      if (step.name === 'read_tool_result' && step.state === 'failed' && previousPlan) {
        step.resultLink = { ...previousPlan, label: '查看本轮计划' };
      }
      if (step.name === 'start_repair' && step.state === 'success') {
        const planId = toolPayload(step.result)?.plan_id;
        const plan = typeof planId === 'string' ? plans.get(planId) : undefined;
        if (plan) step.resultLink = { key: plan.anchor.key, label: '查看任务' };
      }
    }
    flush();
  }
  return {steps,groups,business};
}
