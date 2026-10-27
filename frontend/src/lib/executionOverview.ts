import type { ChatMsg } from '../stores/chat.js';
import type { ChatPhase } from './api.js';

export function phaseLabel(phase?: ChatPhase | null): string {
  if (phase === 'tool_call') return '正在组织工具调用';
  if (phase === 'answering') return '正在整理回答';
  return '正在分析任务';
}

export function executionEntries(messages: ChatMsg[], sending: boolean): { text: string; state: string }[] {
  const start = messages.map(m => m.role).lastIndexOf('user');
  if (start < 0) return [];
  const msgs = messages.slice(start + 1);
  const rows: { text: string; state: string }[] = [];
  const interrupted = !sending || msgs.some(m => m.isError);
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (m.role !== 'assistant') continue;
    for (const phase of m.progress ?? (m.phase ? [m.phase] : [])) {
      if (phase === 'tool_call') continue;
      const active = m.streaming && m.phase === phase && sending;
      rows.push({ text: phase === 'thinking' ? (active ? '正在分析任务' : '任务分析阶段') : (active ? '正在整理回答' : '回答整理阶段'), state: active ? 'thinking' : 'ended' });
    }
    for (const t of m.toolCalls ?? []) {
      const nextAssistant = msgs.findIndex((n, j) => j > i && n.role === 'assistant');
      const result = msgs.slice(i + 1, nextAssistant < 0 ? undefined : nextAssistant).find(n => n.role === 'tool' && (t.id ? n.toolCallId === t.id : n.toolName === t.name));
      rows.push({ text: result ? `${t.name}：${result.ok ? '执行完成' : '执行失败'}${result.detail ? ' · ' + result.detail : ''}` : `正在执行 ${t.name}${interrupted ? '（结果未确认）' : ''}`,
        state: result ? (result.ok ? 'success' : 'failed') : interrupted ? 'interrupted' : 'running' });
    }
    if (m.isError) rows.push({ text: '回合异常中止', state: 'failed' });
  }
  return rows;
}
