/**
 * reasoningTrace.ts — 思考阶段计时与连续短阶段的合并视图
 * ------------------------------------------------------------------
 * 只消费现有 ChatMsg 投影与 ReasoningBlock 上报的耗时，不改 Agent Loop：
 *
 *   - 把同一用户回合内、被工具结果隔开的连续 reasoning 段视为一个候选组；
 *   - 只有每段都已结束且耗时不超过 `SHORT_THINKING_SEC` 才合并；
 *   - 合并后只保留最后一段的 ReasoningBlock，正文为各段推理拼接
 *     （工具调用 / 失败 / 人工确认等消息仍在原位置独立渲染）；
 *   - 任一段耗时未知或偏长就不合并，各段继续显示自己的「已思考 X 秒」。
 */
import type { ChatMsg } from '../stores/chat.js';
import type { ChatPhase } from './api.js';

/** 超过这个秒数的阶段不再并入「N 段思考」，避免把长思考压成一行。 */
export const SHORT_THINKING_SEC = 15;

export interface ReasoningTiming {
  /** 已结束阶段的累计秒数（同一消息内多段会累加）。 */
  seconds: number;
  /** 已结束的段数。 */
  segments: number;
}

export interface ReasoningView {
  text: string;
  streaming: boolean;
  phase: ChatPhase | null;
  /** 这一段（或合并组）代表的思考段数。 */
  count: number;
  /** 已知累计秒数；未知为 null。 */
  seconds: number | null;
  /** true = 本视图合并了多段短思考。 */
  merged: boolean;
}

/**
 * @param messages 当前渲染的 ChatMsg 列表
 * @param keys     与 messages 等长的稳定 key（与 v-for 的 :key 同源）
 * @param timings  key -> ReasoningBlock 上报的已完成耗时
 * @returns 需要渲染的索引 -> 视图；未返回的索引表示已被合并隐藏
 */
export function reasoningViews(
  messages: readonly ChatMsg[],
  keys: readonly string[],
  timings: Readonly<Record<string, ReasoningTiming | undefined>>,
): Map<number, ReasoningView> {
  const views = new Map<number, ReasoningView>();
  let run: number[] = [];

  const single = (index: number): void => {
    const timing = timings[keys[index]];
    views.set(index, {
      text: messages[index].reasoning ?? '',
      streaming: Boolean(messages[index].streaming),
      phase: messages[index].phase ?? null,
      count: timing?.segments ?? 1,
      seconds: timing?.seconds ?? null,
      merged: false,
    });
  };
  const shortAndDone = (index: number): boolean => {
    const timing = timings[keys[index]];
    return !!timing && timing.segments > 0
      && timing.seconds <= SHORT_THINKING_SEC;
  };
  const merged = (members: number[]): void => {
    const last = members[members.length - 1];
    const count = members.reduce(
      (sum, index) => sum + (timings[keys[index]]?.segments ?? 0), 0);
    const seconds = members.reduce(
      (sum, index) => sum + (timings[keys[index]]?.seconds ?? 0), 0);
    views.set(last, {
      text: members.map(index => messages[index].reasoning ?? '')
        .filter(Boolean).join('\n\n'),
      streaming: Boolean(messages[last].streaming),
      phase: messages[last].phase ?? null,
      count,
      seconds,
      merged: true,
    });
  };

  const flush = (): void => {
    if (!run.length) return;
    // Split on long/unmeasured stages; a trailing streaming stage must not
    // un-merge the short completed stages before it.
    let cursor = 0;
    while (cursor < run.length) {
      if (!shortAndDone(run[cursor])) {
        single(run[cursor]);
        cursor += 1;
        continue;
      }
      let end = cursor;
      while (end + 1 < run.length && shortAndDone(run[end + 1])) end += 1;
      const members = run.slice(cursor, end + 1);
      if (members.length > 1) merged(members);
      else single(members[0]);
      cursor = end + 1;
    }
    run = [];
  };

  messages.forEach((message, index) => {
    if (message.role === 'user') {
      flush();
      return;
    }
    if (message.role !== 'assistant') return;   // tool rows stay independent
    if (message.isError) {
      flush();
      return;
    }
    if (message.reasoning) {
      run.push(index);
    } else if (message.content) {
      // A visible answer ends the current reasoning run; an assistant message
      // that only declares tools (no reasoning) does not.
      flush();
    }
  });
  flush();
  return views;
}
