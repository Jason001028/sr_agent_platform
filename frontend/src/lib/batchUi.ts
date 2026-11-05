/**
 * batchUi.ts — Batch Panel 的纯展示映射（PR F）
 * ------------------------------------------------------------------
 * 只做「后端事实 → 中文文案 / 色调」的转换，不发请求、不读文件。进度数字一律来自
 * `batch_snapshot.v1`，这里不重新计算业务进度。
 */
import type {
  BatchEvent, BatchProgressTrack, BatchSnapshot,
} from './api.js';

export const BATCH_GEO_META = {
  contractVersion: 'batch_snapshot.v1',
};

const GOAL_STATUS: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  WAITING_EXTERNAL: '等待外部超分',
  WAITING_USER: '等待确认',
  VERIFYING: '验证中',
  RETRY_WAIT: '等待重试',
  PAUSED: '已暂停',
  CANCELLED: '已取消',
  COMPLETED: '已完成',
  FAILED: '失败',
  BLOCKED: '阻塞',
};

const ITEM_STATUS: Record<string, string> = {
  PENDING: '待处理',
  OPENING: '打开影像',
  DETECTING: '检测中',
  MASK_READY: '掩码就绪',
  SUBMITTING: '提交 SR',
  WAITING_SR: '等待超分',
  VERIFYING: '验证中',
  RETRY_WAIT: '等待重试',
  SUCCEEDED: '已完成',
  NO_ISSUE: '无需修复',
  FAILED: '失败',
  BLOCKED: '受阻',
  SKIPPED: '已跳过',
};

const PHASE: Record<string, string> = {
  dispatching: '派发中',
  detecting: '检测中',
  submitting: '提交超分',
  monitoring: '等待超分',
  verifying: '验证中',
  draining: '收尾中',
  waiting_review: '等待人工复核',
  done: '已完成',
  paused: '已暂停',
  cancelled: '已取消',
  blocked: '已阻塞',
};

export function goalStatusLabel(status: string): string {
  return GOAL_STATUS[status] ?? status;
}

export function itemStatusLabel(status: string): string {
  return ITEM_STATUS[status] ?? status;
}

export function phaseLabel(phase: string | null | undefined): string {
  if (!phase) return '';
  return PHASE[phase] ?? phase;
}

export function isTerminalStatus(status: string): boolean {
  return status === 'COMPLETED' || status === 'FAILED'
    || status === 'CANCELLED' || status === 'BLOCKED';
}

/** 「DEMO / MOCK」必须显眼：演示契约不是生产 SR 成功。 */
export function modeBadge(snapshot: BatchSnapshot | null): {
  text: string; tone: 'demo' | 'production' | 'unknown';
} {
  if (!snapshot) return { text: '状态未知', tone: 'unknown' };
  if (snapshot.goal.mode === 'demo') return { text: 'DEMO / MOCK', tone: 'demo' };
  if (snapshot.goal.mode === 'production') {
    return { text: '生产批次', tone: 'production' };
  }
  return { text: snapshot.goal.mode || '状态未知', tone: 'unknown' };
}

/** 百分比只用于画条；`null` = 分母不适用或还不是可信比例。 */
export function trackPercent(track: BatchProgressTrack): number | null {
  if (!track.applicable || track.total <= 0) return null;
  return Math.max(0, Math.min(100, (track.done / track.total) * 100));
}

/** 第二、三条轨道的说明文字（修订 4：R=0 且检测未完成 ≠ 不适用）。 */
export function repairTrackText(track: BatchProgressTrack): string {
  if (track.applicable) return '';
  return track.known ? '不适用' : '暂无需修复项·统计中';
}

export function trackText(track: BatchProgressTrack): string {
  if (!track.applicable) return track.known ? '不适用' : '统计中';
  return `${track.done} / ${track.total}`;
}

/** 演示验证不能写成「生产验证成功」。 */
export function verificationNote(snapshot: BatchSnapshot): string {
  const counts = snapshot.counts;
  if (counts.verified_demo > 0) return '演示契约验证，不代表生产 SR 成功';
  // Unknown jobs outweigh a success note: the operator still has work to check.
  if (counts.unknown_jobs > 0) return '存在状态未知的外部作业';
  if (counts.verified_production > 0) return '生产契约验证通过';
  return '';
}

export function formatClock(ts: number | null | undefined): string {
  if (!ts) return '—';
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function formatDateTime(ts: number | null | undefined): string {
  if (!ts) return '—';
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function durationText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const total = Math.round(seconds);
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`;
  return `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`;
}

/** 事件列表的一句话摘要。payload 只用于展示，不作为进度来源。 */
export function eventLabel(event: BatchEvent): string {
  const payload = event.payload || {};
  const ordinal = event.step_ordinal;
  const scene = ordinal ? `场景 #${ordinal}` : '';
  const from = typeof payload.from === 'string' ? itemStatusLabel(payload.from) : '';
  const to = typeof payload.to === 'string' ? itemStatusLabel(payload.to) : '';
  switch (event.event_type) {
    case 'goal_created': return '批次已创建';
    case 'batch_imported': return '清单已冻结';
    case 'item_state_changed':
      return [scene, from && to ? `${from} → ${to}` : to || from].filter(Boolean).join(' · ');
    case 'mask_ready': return [scene, '掩码已发布'].filter(Boolean).join(' · ');
    case 'sr_submission_reserved':
      return [scene, '已预留 SR 提交'].filter(Boolean).join(' · ');
    case 'sr_receipt_recorded': {
      const job = payload.job_id != null ? `Job ${payload.job_id}` : 'Job';
      return [scene, `SR 任务已受理 · ${job}`].filter(Boolean).join(' · ');
    }
    case 'external_job_terminal': {
      const state = payload.state ? ` ${payload.state}` : '';
      return [scene, `外部作业结束${state}`].filter(Boolean).join(' · ');
    }
    case 'item_verified':
      return [scene, payload.ok === true ? '验证通过' : '验证未通过'].filter(Boolean).join(' · ');
    case 'batch_phase_changed':
      return `批次阶段 · ${phaseLabel(String(payload.phase ?? payload.status ?? ''))}`;
    case 'batch_control_changed':
      return `监控更新 · ${payload.monitoring_done ? '已收尾' : '进行中'}`;
    case 'batch_user_control': {
      const action = payload.action === 'pause' ? '暂停'
        : payload.action === 'resume' ? '继续' : '取消';
      return `用户${action}批次`;
    }
    case 'batch_report_updated': return '批次报告已生成';
    case 'goal_verified': return '批次验证完成';
    case 'goal_paused': return '批次已暂停';
    case 'goal_resumed': return '批次已继续';
    case 'goal_cancelled': return '批次已取消';
    default: return event.event_type;
  }
}

export function eventTone(event: BatchEvent): 'normal' | 'ok' | 'warn' | 'error' {
  const payload = event.payload || {};
  if (event.event_type === 'item_verified') {
    return payload.ok === true ? 'ok' : 'error';
  }
  if (event.event_type === 'external_job_terminal') {
    const state = String(payload.state ?? '').toUpperCase();
    if (state.includes('FAIL') || state.includes('CANCEL')) return 'error';
    return 'ok';
  }
  if (event.event_type === 'batch_user_control') return 'warn';
  if (event.event_type === 'batch_control_changed') return 'warn';
  return 'normal';
}
