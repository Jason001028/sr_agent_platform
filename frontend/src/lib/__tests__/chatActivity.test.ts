import { describe, expect, it } from 'vitest';
import { advanceActivity, startActivity } from '../chatActivity';

describe('execution activity', () => {
  it('uses only frozen attachment and region facts for the initial label', () => {
    expect(startActivity().label).toBe('正在分析任务');
    expect(startActivity(true).label).toBe('正在分析影像内容');
    expect(startActivity(true, true).label).toBe('正在分析选中区域');
  });
  it('tracks search, result analysis and answer without exposing reasoning', () => {
    let s = startActivity();
    s = advanceActivity(s, { type: 'tool_call', id: '1', name: 'search_scenes', args: {} });
    expect(s.label).toBe('正在检索匹配场景');
    s = advanceActivity(s, { type: 'tool_result', id: '1', name: 'search_scenes', ok: true, data: {}, error: null });
    expect(s.label).toBe('正在结合检索结果分析');
    s = advanceActivity(s, { type: 'llm_phase', phase: 'thinking' });
    expect(advanceActivity(s, { type: 'reasoning', delta: 'private thought' })).toBe(s);
    s = advanceActivity(s, { type: 'token', delta: 'answer' });
    expect(s.label).toBe('正在整理回答');
  });
  it('retains outstanding calls by ID and deduplicates announcements', () => {
    const call = { type: 'tool_call' as const, id: 'a', name: 'sr_job_status', args: {} };
    let s = advanceActivity(startActivity(), call);
    expect(advanceActivity(s, call)).toBe(s);
    s = advanceActivity(s, { ...call, id: 'b' });
    expect(s.label).toBe('正在执行 2 项工具操作');
    s = advanceActivity(s, { type: 'tool_result', id: 'a', name: call.name, ok: false, data: null, error: 'failure' });
    expect(s.label).toBe('正在查询超分任务状态');
    s = advanceActivity(s, { type: 'tool_result', id: 'b', name: call.name, ok: false, data: null, error: 'failure' });
    expect(s.label).toBe('正在分析工具失败原因');
  });
  it('does not describe an unfinished SR operation as successful', () => {
    let s = advanceActivity(startActivity(), { type: 'tool_call', name: 'run_sr', args: {} });
    expect(s.label).toBe('正在提交超分任务');
    s = advanceActivity(s, { type: 'turn_done', content: '', error: 'disconnected' });
    expect(s.label).toBe('本次处理异常中止');
    expect(s.pending).toEqual([]);
    expect(advanceActivity(s, { type: 'token', delta: 'late' })).toBe(s);
    expect(startActivity().terminal).toBe(false);
  });
  it('clears terminal success and uses a neutral fallback for unknown tools', () => {
    let s = advanceActivity(startActivity(), { type: 'tool_call', name: 'custom', args: {} });
    expect(s.label).toBe('正在执行工具操作');
    s = advanceActivity(s, { type: 'turn_done', content: 'done', error: null });
    expect(s.label).toBe('');
    expect(s.terminal).toBe(true);
  });
});
