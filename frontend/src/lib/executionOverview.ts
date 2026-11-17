import type { ChatMsg } from '../stores/chat.js';
import type { ChatPhase } from './api.js';
import { projectAgentActivity } from './agentActivity';

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
  const activity = projectAgentActivity(messages, sending);
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    for (const phase of m.role === 'assistant' ? m.progress ?? (m.phase ? [m.phase] : []) : []) {
      if (phase === 'tool_call') continue;
      const active = m.streaming && m.phase === phase && sending;
      rows.push({ text: phase === 'thinking' ? (active ? '正在分析任务' : '任务分析阶段') : (active ? '正在整理回答' : '回答整理阶段'), state: active ? 'thinking' : 'ended' });
    }
    for (const step of activity.steps.filter(step => step.callIndex === start + 1 + i)) {
      rows.push({ text: `${step.label} · ${step.summary}`, state: step.state });
    }
    if (m.isError) rows.push({ text: '回合异常中止', state: 'failed' });
  }
  return rows;
}
