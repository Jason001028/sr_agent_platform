/**
 * batchEventSync.test.ts — PR F 事件同步与批次关联（纯函数）
 * ------------------------------------------------------------------
 * 重点守住两条：追平从「已消费游标」出发而不跳到快照水位；批次关联不盲取第一条。
 */
import { describe, expect, it } from 'vitest';
import {
  catchUpBatchEvents, eventNeedsSnapshot, mergeBatchEvents,
  pickAssociatedBatchId,
} from '../batchEventSync';
import type { BatchEvent, BatchEventPage } from '../api';

function event(seq: number, type = 'item_state_changed'): BatchEvent {
  return {
    goal_id: 'g1', seq, event_type: type,
    payload: { from: 'DETECTING', to: 'MASK_READY' }, created_at: seq,
  };
}

describe('mergeBatchEvents', () => {
  it('按 seq 去重并升序，保留最新 limit 条', () => {
    const merged = mergeBatchEvents(
      [event(1), event(3), event(5)],
      [event(3), event(4), event(6)],
    );
    expect(merged.map((row) => row.seq)).toEqual([1, 3, 4, 5, 6]);
    const capped = mergeBatchEvents([], [1, 2, 3, 4].map((seq) => event(seq)), 2);
    expect(capped.map((row) => row.seq)).toEqual([3, 4]);
  });
});

describe('catchUpBatchEvents', () => {
  it('从已消费游标逐页前进，不跳过快照水位之前的事件', async () => {
    const seen: number[] = [];
    const all = [6, 7, 8, 9, 10, 11, 12].map((seq) => event(seq));
    const fetchPage = async ({ after, limit }: { after: number; limit: number }) => {
      seen.push(after);
      const rows = all.filter((row) => row.seq > after).slice(0, limit);
      const last = rows.length ? rows[rows.length - 1].seq : after;
      const hasMore = all.some((row) => row.seq > last);
      return {
        goal_id: 'g1', events: rows, last_seq: 12,
        next_after: rows.length ? last : null, has_more: hasMore,
        next_before: null, has_earlier: true,
      } satisfies BatchEventPage;
    };
    const result = await catchUpBatchEvents(fetchPage, 5, 12, { limit: 3 });
    expect(seen).toEqual([5, 8, 11]);
    expect(result.events.map((row) => row.seq)).toEqual([6, 7, 8, 9, 10, 11, 12]);
    expect(result.consumed).toBe(12);
    expect(result.truncated).toBe(false);
  });

  it('游标不前进时视为中断，保留消费位置', async () => {
    const result = await catchUpBatchEvents(
      async ({ after }) => ({
        goal_id: 'g1', events: [event(after)], last_seq: 10,
        next_after: after, has_more: true, next_before: null, has_earlier: false,
      }),
      5, 10,
    );
    expect(result.consumed).toBe(5);
    expect(result.truncated).toBe(true);
  });

  it('水位之前返回空页：保留游标并标记缺口，不能宣称已追平', async () => {
    const result = await catchUpBatchEvents(
      async () => ({
        goal_id: 'g1', events: [], last_seq: 9,
        next_after: null, has_more: false, next_before: null, has_earlier: false,
      }),
      5, 12,
    );
    expect(result.consumed).toBe(5);
    expect(result.truncated).toBe(true);
  });

  it('还有事件但服务端说完结、又没到水位：标记中断', async () => {
    const result = await catchUpBatchEvents(
      async () => ({
        goal_id: 'g1', events: [event(6)], last_seq: 9,
        next_after: 6, has_more: false, next_before: null, has_earlier: false,
      }),
      5, 12, { limit: 1 },
    );
    expect(result.consumed).toBe(6);
    expect(result.truncated).toBe(true);
  });

  it('页数上限触发中断而不是悄悄跳到 target', async () => {
    let seq = 0;
    const result = await catchUpBatchEvents(
      async () => {
        seq += 1;
        return {
          goal_id: 'g1', events: [event(seq)], last_seq: 999,
          next_after: seq, has_more: true, next_before: null, has_earlier: false,
        };
      },
      0, 999, { limit: 1, maxPages: 3 },
    );
    expect(result.pages).toBe(3);
    expect(result.consumed).toBe(3);
    expect(result.truncated).toBe(true);
  });
});

describe('eventNeedsSnapshot', () => {
  it('业务状态事件触发快照刷新；创建/导入不重复算进度', () => {
    expect(eventNeedsSnapshot('item_state_changed')).toBe(true);
    expect(eventNeedsSnapshot('external_job_terminal')).toBe(true);
    expect(eventNeedsSnapshot('batch_user_control')).toBe(true);
    expect(eventNeedsSnapshot('goal_created')).toBe(false);
    expect(eventNeedsSnapshot('batch_imported')).toBe(false);
  });
});

describe('pickAssociatedBatchId', () => {
  const goals = [
    { goal_id: 'g-old', session_id: 's1', status: 'COMPLETED' },
    { goal_id: 'g-active', session_id: 's1', status: 'RUNNING' },
    { goal_id: 'g-other', session_id: 's2', status: 'RUNNING' },
  ];

  it('URL 指定永远优先', () => {
    expect(pickAssociatedBatchId(goals, {
      urlGoal: 'g-url', remembered: 'g-old', sessionId: 's1',
    })).toBe('g-url');
  });

  it('本地记录次之（用户明确选择过）', () => {
    expect(pickAssociatedBatchId(goals, {
      remembered: 'g-old', sessionId: 's1',
    })).toBe('g-old');
  });

  it('同一会话只挑活动批次，且不盲取第一条', () => {
    expect(pickAssociatedBatchId(goals, { sessionId: 's1' })).toBe('g-active');
    expect(pickAssociatedBatchId(goals, { sessionId: 's3' })).toBeNull();
    expect(pickAssociatedBatchId(goals, { sessionId: null })).toBeNull();
    expect(pickAssociatedBatchId(goals, {})).toBeNull();
  });
});
