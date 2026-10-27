import { describe, it, expect } from 'vitest';
import { reduceChatSse } from '../chat.js';
import type { ChatMsg } from '../chat.js';
import { executionEntries } from '../../lib/executionOverview.js';

const initial: ChatMsg[] = [{ seq: null, role: 'user', content: '检索场景' }];
describe('中文过程与原始推理分层', () => {
  it('无推理也显示实际阶段；正文保持独立', () => {
    let msgs = reduceChatSse(initial, { type: 'llm_phase', phase: 'thinking' });
    expect(executionEntries(msgs, true)[0].text).toBe('正在分析任务');
    msgs = reduceChatSse(msgs, { type: 'reasoning', delta: 'raw English thinking' });
    expect(executionEntries(msgs, true)[0].text).not.toContain('English');
    msgs = reduceChatSse(msgs, { type: 'llm_phase', phase: 'answering' });
    msgs = reduceChatSse(msgs, { type: 'token', delta: '正文' });
    msgs = reduceChatSse(msgs, { type: 'assistant', content: '正文' });
    expect(msgs[1].content).toBe('正文');
    expect(msgs[1].reasoning).toBe('raw English thinking');
    expect(executionEntries(msgs, false)).toHaveLength(2);
  });
  it('调用按ID去重，相同工具的不同调用保留；失败和断线不显示成功', () => {
    const call = { type: 'tool_call' as const, id: 'c1', name: 'search_scenes', args: {} };
    let msgs = reduceChatSse(initial, call);
    msgs = reduceChatSse(msgs, call);
    expect(msgs[1].toolCalls).toHaveLength(1);
    expect(executionEntries(msgs, false)[0].state).toBe('interrupted');
    const result = { type: 'tool_result' as const, id: 'c1', name: 'search_scenes', ok: false, data: null, error: '失败' };
    msgs = reduceChatSse(msgs, result);
    msgs = reduceChatSse(msgs, result);
    expect(msgs.filter(m => m.role === 'tool')).toHaveLength(1);
    expect(executionEntries(msgs, false)[0].state).toBe('failed');
    msgs = reduceChatSse(msgs, { ...call, id: 'c2' });
    expect(executionEntries(msgs, true)).toHaveLength(2);
  });
  it('只有工具结果才能确认成功', () => {
    let msgs = reduceChatSse(initial, { type: 'tool_call', id: 'c1', name: 'run_sr', args: {} });
    msgs = reduceChatSse(msgs, { type: 'assistant', content: '我已经成功执行' });
    expect(executionEntries(msgs, false)[0].state).toBe('interrupted');
  });
});
