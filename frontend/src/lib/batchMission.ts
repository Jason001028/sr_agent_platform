/** Read-only presentation of batch_snapshot.v1. Never schedules work. */
import type { BatchSnapshot, BatchProgressTrack, BatchItemRecord } from './api';
import { itemStatusLabel } from './batchUi';

export interface MissionItem {
  item_id: string; ordinal: number; name: string; status: string; attempt: number;
  image_path?: string | null; job_id?: number | string | null; error?: string | null;
  unknown?: boolean; verification_ok?: boolean | null;
  sr_status?: string | null;
  candidate_count?: number;
  overlay_ready?: boolean;
}
export const ISSUE_STATUSES = 'FAILED,BLOCKED,RETRY_WAIT,REVIEW_REJECTED';
export const terminalMission = (s: string) => ['COMPLETED', 'FAILED', 'BLOCKED', 'CANCELLED'].includes(s);
export const missionIssue = (i: MissionItem) => !!i.unknown || i.verification_ok === false || ISSUE_STATUSES.split(',').includes(i.status);
export function missionItems(snapshot: BatchSnapshot): MissionItem[] {
  const rows = new Map<string, MissionItem>();
  // Authoritative active/current rows override their potentially older recent copies.
  for (const item of [...(snapshot.recent_jobs ?? []), ...snapshot.active_jobs, snapshot.producer.current]) {
    if (item) rows.set(item.item_id, item);
  }
  return [...rows.values()];
}
export function missionItemLabel(item: MissionItem): string {
  if (item.unknown) return '作业状态待确认';
  if (item.status === 'FAILED' && item.job_id == null) return '处理异常 · 未生成作业';
  if (item.status === 'BLOCKED' && item.job_id == null) return '处理受阻 · 未生成作业';
  if (item.status === 'WAITING_SR' && item.job_id != null) {
    const sr = String(item.sr_status ?? '').toUpperCase();
    return sr === 'RUNNING' ? 'SR 执行中' : sr === 'PENDING' || sr === 'QUEUED' ? 'SR 排队中' : '等待 SR 结果';
  }
  return itemStatusLabel(item.status) === item.status ? '状态待确认' : itemStatusLabel(item.status);
}
export function missionTrackText(t: BatchProgressTrack): string {
  if (!t.known) return '统计中';
  if (!t.applicable || t.total <= 0) return '—';
  return `${t.done} / ${t.total}`;
}
export function missionStage(snapshot: BatchSnapshot): 'detection' | 'submission' | 'verification' {
  const c = snapshot.counts, phase = snapshot.goal.phase;
  if (c.detecting > 0 || ['dispatching', 'detecting', 'waiting_review'].includes(phase ?? '')) return 'detection';
  if (c.verifying > 0 || ['verifying', 'waiting_quality_review'].includes(phase ?? '')) return 'verification';
  if (c.in_flight_sr > 0 || c.submitting > 0 || ['submitting', 'monitoring'].includes(phase ?? '')) return 'submission';
  return c.submitted > 0 ? 'verification' : 'detection';
}
export function missionSummary(s: BatchSnapshot) {
  const c = s.counts, status = s.goal.status, stage = missionStage(s);
  const errors = c.failed + c.blocked + (s.quality?.rejected_scenes ?? 0);
  const failed = ['FAILED', 'BLOCKED'].includes(status);
  const issueFocus = !['COMPLETED', 'CANCELLED'].includes(status) && (failed || (!s.producer.current && !s.active_jobs.length && errors > 0));
  const names = { detection: '检测进度', submission: 'SR 提交进度', verification: '验收进度' };
  let label = names[stage], value = missionTrackText(s.progress[stage]), unit = '';
  let state = stage === 'detection' ? '正在检测' : stage === 'submission' ? (c.in_flight_sr > 0 ? '正在修复' : '正在提交') : '正在验收';
  let tone = 'active';
  if (status === 'QUEUED') state = '等待开始';
  if (status === 'PAUSED') { state = '已暂停'; tone = 'quiet'; }
  if (status === 'WAITING_USER' || s.goal.phase === 'waiting_review') state = '等待掩码复核';
  if (s.goal.phase === 'waiting_quality_review') state = '等待人工验收';
  if (status === 'RETRY_WAIT') state = '等待重试';
  if (status === 'CANCELLED') { state = '已取消'; tone = 'quiet'; }
  if (issueFocus) {
    tone = status === 'PAUSED' ? 'quiet' : 'error';
    if (failed) state = '执行中止';
    else if (!['PAUSED', 'WAITING_USER', 'RETRY_WAIT'].includes(status) && !s.goal.phase?.startsWith('waiting_')) state = '需要处理';
    label = '异常景数'; value = errors > 0 ? String(errors) : '—'; unit = '景异常';
  }
  if (status === 'COMPLETED') {
    tone = 'done'; state = '任务完成';
    label = c.verified > 0 ? '已验收' : '无需修复'; value = String(c.verified > 0 ? c.verified : c.no_issue); unit = '景';
  }
  return { stage, label, value, unit, state, tone, errors, issueFocus,
    caption: `${label} · 共 ${c.total} 景`,
    noJobNotice: issueFocus && c.submitted === 0
      ? (c.detected < c.total ? '检测尚未完成，尚无已提交的 SR 作业。' : '尚未提交 SR，请查看各景处理原因。') : '',
  };
}
export function focusMissionItems(s: BatchSnapshot): MissionItem[] {
  if (['COMPLETED', 'CANCELLED'].includes(s.goal.status)) return [];
  const rows = missionItems(s);
  if (missionSummary(s).issueFocus) return rows.filter(missionIssue).slice(0, 3);
  return (s.producer.current ? [s.producer.current] : s.active_jobs.slice(0, 1));
}
export function recordMissionItem(item: BatchItemRecord): MissionItem {
  return { ...item, attempt: item.checkpoint.attempt ?? 0,
    candidate_count: item.checkpoint.detection?.candidates?.length,
    overlay_ready: !!item.checkpoint.mask?.overlay,
    image_path: item.checkpoint.target_image?.path, job_id: item.external_job_id ?? item.checkpoint.job_id ?? null };
}
