/**
 * chat.test.ts — 聊天 store 纯函数（SSE 归并 / 历史投影映射）
 * ------------------------------------------------------------------
 * Node 环境：只测 reduceChatSse 与 msgFromHistory（不触碰 pinia store 实例）。
 * SSE 事件序列与后端契约 §3.2 对齐：turn_start → tool_call → tool_result →
 * assistant → turn_done；history 投影 role 序列 = user/assistant/tool/assistant。
 */
import { describe, it, expect } from 'vitest';
import { reduceChatSse, msgFromHistory } from '../chat.js';
import type { ChatMsg } from '../chat.js';
import type { HistoryMsg } from '../../lib/api.js';

const roles = (msgs: ChatMsg[]): string[] => msgs.map((m) => m.role);

describe('msgFromHistory（GET messages 投影 → 展示）', () => {
  it('user / assistant 文本直映', () => {
    const u = msgFromHistory({ seq: 1, role: 'user', content: '看看场景' });
    expect(u).toMatchObject({ role: 'user', content: '看看场景', seq: 1 });
    const a = msgFromHistory({ seq: 4, role: 'assistant', content: '已检索…' });
    expect(a).toMatchObject({ role: 'assistant', content: '已检索…', seq: 4 });
    expect(a.toolCalls).toBeNull();
  });

  it('assistant 工具声明 → toolCalls 数组（arguments 原样）', () => {
    const a = msgFromHistory({
      seq: 2, role: 'assistant', content: null,
      tool_calls: [{ name: 'search_scenes', arguments: {} }],
    });
    expect(a.role).toBe('assistant');
    expect(a.toolCalls).toEqual([{ name: 'search_scenes', args: {} }]);
    expect(a.content).toBe('');
  });

  it('tool 行保留 tool_name / ok / 失败原因 detail', () => {
    const okRow = msgFromHistory({
      seq: 3, role: 'tool', tool_name: 'search_scenes', tool_call_id: 'c1', ok: true,
    } as HistoryMsg);
    expect(okRow).toMatchObject({ role: 'tool', toolName: 'search_scenes', ok: true });

    const bad = msgFromHistory({
      seq: 3, role: 'tool', tool_name: 'run_sr', ok: false,
      content: 'slurm not available',
    } as HistoryMsg);
    expect(bad.ok).toBe(false);
    expect(bad.detail).toBe('slurm not available');
  });
});

describe('reduceChatSse（SSE 逐帧归并）', () => {
  it('mock 全套：turn_start 无载体 → tool_call 占位 → tool_result → assistant → turn_done 去重', () => {
    let msgs: ChatMsg[] = [];
    const push = (ev: Parameters<typeof reduceChatSse>[1]) => {
      msgs = reduceChatSse(msgs, ev);
    };
    push({ type: 'turn_start', run_id: 'r1', session_id: 's' });
    push({ type: 'tool_call', name: 'search_scenes', args: {} });
    push({
      type: 'tool_result', name: 'search_scenes', ok: true,
      data: { source: 'fake', count: 12, results: [] }, error: null,
    });
    const reply = '已检索盘阵场景 12 个（mock 模型）';
    push({ type: 'assistant', content: reply });
    push({ type: 'turn_done', content: reply, error: null });

    expect(roles(msgs)).toEqual(['assistant', 'tool', 'assistant']);
    expect(msgs[0].toolCalls).toEqual([{ name: 'search_scenes', args: {} }]);
    expect(msgs[1].toolName).toBe('search_scenes');
    expect(msgs[1].ok).toBe(true);
    expect(msgs[1].detail).toContain('12 个场景');
    expect(msgs[2].content).toBe(reply);
  });

  it('同回合多次工具声明 → 并入同一占位气泡', () => {
    let msgs: ChatMsg[] = [];
    msgs = reduceChatSse(msgs, { type: 'tool_call', name: 'a', args: {} });
    msgs = reduceChatSse(msgs, { type: 'tool_call', name: 'b', args: { x: 1 } });
    expect(roles(msgs)).toEqual(['assistant']);
    expect(msgs[0].toolCalls).toHaveLength(2);
    expect(msgs[0].toolCalls![1].name).toBe('b');
  });

  it('tool_result ok=false → detail 用 error', () => {
    let msgs: ChatMsg[] = [];
    msgs = reduceChatSse(msgs, { type: 'tool_call', name: 'run_sr', args: {} });
    msgs = reduceChatSse(msgs, {
      type: 'tool_result', name: 'run_sr', ok: false,
      data: null, error: 'slurm not available',
    });
    expect(msgs[1].ok).toBe(false);
    expect(msgs[1].detail).toBe('slurm not available');
  });

  it('无 assistant 文本事件 → turn_done 回填占位气泡', () => {
    let msgs: ChatMsg[] = [];
    msgs = reduceChatSse(msgs, { type: 'tool_call', name: 'a', args: {} });
    msgs = reduceChatSse(msgs, { type: 'turn_done', content: '结果', error: null });
    expect(roles(msgs)).toEqual(['assistant']);
    expect(msgs[0].content).toBe('结果');
    expect(msgs[0].toolCalls).toBeNull();      // 已填文 → 不再当声明气泡
  });

  it('error 事件 → isError 警示气泡', () => {
    let msgs: ChatMsg[] = [];
    msgs = reduceChatSse(msgs, { type: 'error', error: 'LLM 调用失败' });
    expect(roles(msgs)).toEqual(['assistant']);
    expect(msgs[0].isError).toBe(true);
    expect(msgs[0].content).toContain('LLM 调用失败');
  });

  it('job_update / ping 不进入聊天 timeline', () => {
    const msgs: ChatMsg[] = [{ seq: null, role: 'user', content: 'hi' }];
    expect(reduceChatSse(msgs, { type: 'job_update', task_id: 1, job_id: 2, state: 'R', prev_state: 'P', ok: true, error: null })).toBe(msgs);
    expect(reduceChatSse(msgs, { type: 'ping' })).toBe(msgs);
  });
});

describe('timeline 形状（与后端 history 一致）', () => {
  it('一轮工具回合历史投影 + 用户消息', () => {
    const history: HistoryMsg[] = [
      { seq: 1, role: 'user', content: '看看场景' },
      {
        seq: 2, role: 'assistant', content: null,
        tool_calls: [{ name: 'search_scenes', arguments: {} }],
      },
      { seq: 3, role: 'tool', tool_name: 'search_scenes', tool_call_id: 'c', ok: true },
      { seq: 4, role: 'assistant', content: '已检索盘阵场景 12 个（mock 模型）' },
    ];
    const msgs = history.map(msgFromHistory);
    expect(roles(msgs)).toEqual(['user', 'assistant', 'tool', 'assistant']);
    expect(msgs[3].content).toContain('mock 模型');
  });
});
