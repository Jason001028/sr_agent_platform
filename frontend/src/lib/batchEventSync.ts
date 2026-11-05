/**
 * batchEventSync.ts — 事件分页追平与批次关联（PR F）
 * ------------------------------------------------------------------
 * 两条游标刻意分开：
 *   - `snapshot.goal.last_seq` 是**快照水位**，说明统计数字对应到哪一条事件；
 *   - `consumed` 是前端**已经消费到**的游标。
 * 重连 / 页面恢复时从 `consumed` 往后分页追平，绝不把 `consumed` 直接跳到快照水位
 * ——那会漏掉中间事件。SSE 只用于提示「有新事实」和一条可读时间线，不用于累加业务进度。
 */
import type { BatchEvent, BatchEventPage } from './api.js';

export const DEFAULT_EVENT_LIMIT = 20;
export const CATCHUP_PAGE_LIMIT = 200;
export const MAX_CATCHUP_PAGES = 50;
export const EVENT_BUFFER_LIMIT = 500;

/** 按 `goal_id + seq` 去重，按 seq 升序保留最近 `limit` 条。 */
export function mergeBatchEvents(
  existing: BatchEvent[], incoming: BatchEvent[],
  limit = EVENT_BUFFER_LIMIT,
): BatchEvent[] {
  const bySeq = new Map<number, BatchEvent>();
  for (const event of existing) bySeq.set(event.seq, event);
  for (const event of incoming) bySeq.set(event.seq, event);
  const merged = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  return merged.length > limit ? merged.slice(merged.length - limit) : merged;
}

export interface CatchUpResult {
  events: BatchEvent[];
  consumed: number;
  lastSeq: number;
  pages: number;
  /** True when the safety page cap or a missing page ended the walk before
   *  `target` was reached.  The caller must keep the cursor honest and retry. */
  truncated: boolean;
}

/** Walk forward from `consumed` to `target` using the server's `next_after`
 *  cursor.  Returns only newly fetched events; the caller merges by seq. */
export async function catchUpBatchEvents(
  fetchPage: (opts: { after: number; limit: number }) => Promise<BatchEventPage>,
  consumed: number,
  target: number,
  opts: {
    limit?: number;
    maxPages?: number;
  } = {},
): Promise<CatchUpResult> {
  const limit = opts.limit ?? CATCHUP_PAGE_LIMIT;
  const maxPages = opts.maxPages ?? MAX_CATCHUP_PAGES;
  const events: BatchEvent[] = [];
  let cursor = consumed;
  let lastSeq = target;
  let pages = 0;
  let truncated = false;
  while (cursor < target && pages < maxPages) {
    const page = await fetchPage({ after: cursor, limit });
    pages += 1;
    lastSeq = page.last_seq;
    if (page.events.length) {
      events.push(...page.events);
      const next = page.next_after ?? page.events[page.events.length - 1].seq;
      if (next <= cursor) { truncated = true; break; }
      cursor = next;
    } else {
      // No events at or below the target: keep consumed where it was, but stop.
      truncated = true;
      break;
    }
    if (!page.has_more && cursor < target) {
      // Server says it has nothing more, yet the snapshot watermark is newer.
      // Do not invent a cursor; a later snapshot/refresh will reconcile.
      truncated = true;
      break;
    }
  }
  // Hitting the safety cap mid-walk is a real truncation: the cursor stays
  // honest and the caller can retry from it.
  if (pages >= maxPages && cursor < target) truncated = true;
  return { events, consumed: cursor, lastSeq, pages, truncated };
}

/** Which persisted event types mean the snapshot numbers may have moved. */
export function eventNeedsSnapshot(eventType: string): boolean {
  switch (eventType) {
    case 'mask_ready':
    case 'sr_submission_reserved':
    case 'sr_receipt_recorded':
    case 'external_job_terminal':
    case 'item_verified':
    case 'item_state_changed':
    case 'batch_phase_changed':
    case 'batch_control_changed':
    case 'batch_user_control':
    case 'batch_item_reviewed':
    case 'batch_report_updated':
    case 'goal_verified':
      return true;
    default:
      return false;
  }
}

export interface BatchAssociationOptions {
  /** Explicit `batch_goal` URL parameter: always wins. */
  urlGoal?: string | null;
  /** Last goal the user opened in this browser (a user choice, not a guess). */
  remembered?: string | null;
  /** Current chat/work session; only an active batch from the same session is
   *  auto-bound.  With no session we refuse to bind an unrelated batch. */
  sessionId?: string | null;
}

/** Association priority: URL > user selection/local record > active batch in
 *  the same work session.  Never `goals[0]` blindly. */
export function pickAssociatedBatchId(
  goals: { goal_id: string; session_id: string | null; status: string }[],
  opts: BatchAssociationOptions,
): string | null {
  const url = (opts.urlGoal || '').trim();
  if (url) return url;
  const remembered = (opts.remembered || '').trim();
  if (remembered) return remembered;
  const session = (opts.sessionId || '').trim();
  if (!session) return null;
  const active = goals.find((goal) => goal.session_id === session
    && !['COMPLETED', 'FAILED', 'CANCELLED', 'BLOCKED'].includes(goal.status));
  return active ? active.goal_id : null;
}
