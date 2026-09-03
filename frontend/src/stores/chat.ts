/**
 * stores/chat.ts — Agent 聊天（阶段5：SSE 流式 Agent 过程）
 * ------------------------------------------------------------------
 * 会话模型（api-contract.md §3.2）：单会话 + 新建；服务端 store 为事实源。
 * SSE 事件经 reduceChatSse 逐帧归并成展示消息（与 GET messages 投影同一形状，
 * 刷新/切会话可无缝换源）。消息 timeline 复用 role=user/assistant/tool：
 *   工具回合渲染成  user ─ assistant(声明工具) ─ tool(结果) ─ assistant(最终回复)。
 * 并发回合由服务端 409 兜底；UI 发送中禁按钮。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { loadSrConfig } from '../lib/scene.js';
import {
  apiCreateSession, apiListSessions, apiSessionMessages, apiChatSend,
} from '../lib/api.js';
import type { ChatSession, HistoryMsg, PlatformSseEvent } from '../lib/api.js';

/** 单条工具声明（wire tool_calls[].function → {name, arguments}）。 */
export interface ToolUse {
  name: string;
  args: Record<string, unknown>;
}

/** 展示消息：SSE 与历史投影的统一形状。seq=null 表示仍在流（未落库）。 */
export interface ChatMsg {
  seq: number | null;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  toolCalls?: ToolUse[] | null;   // assistant 声明调用（content 常空）
  toolName?: string | null;        // tool 行：工具名
  ok?: boolean;                    // tool 行：成功/失败
  detail?: string;                 // tool 行：结果摘要 / 失败原因
  isError?: boolean;               // 回合级 error（LLM 失败/循环超限）
}

/* ================= 纯函数（vitest 可测） ================= */

/** GET messages 一行 → 展示消息（tool 行含 detail，无则显 ok/失败）。 */
export function msgFromHistory(h: HistoryMsg): ChatMsg {
  if (h.role === 'user') {
    return { seq: h.seq, role: 'user', content: h.content ?? '' };
  }
  if (h.role === 'assistant') {
    const tcs = h.tool_calls
      ? h.tool_calls.map((tc) => ({ name: tc.name, args: tc.arguments ?? {} }))
      : null;
    return { seq: h.seq, role: 'assistant', content: h.content ?? '', toolCalls: tcs };
  }
  return {
    seq: h.seq, role: 'tool', content: '',
    toolName: h.tool_name ?? null, ok: h.ok,
    detail: (h.ok === false && h.content) ? h.content : (h.content ?? ''),
  };
}

/** 收一条 SSE 事件，归并进展示消息副本（不可变，返回新数组）。 */
export function reduceChatSse(
  msgs: ChatMsg[], ev: PlatformSseEvent,
): ChatMsg[] {
  switch (ev.type) {
    case 'turn_start':
    case 'job_update':
    case 'ping':
      return msgs;                       // 无渲染载体：sending 灯已是状态
    case 'tool_call': {
      const use: ToolUse = { name: ev.name, args: ev.args };
      const last = msgs[msgs.length - 1];
      // 同回合连续多次工具声明 → 并入前一个 assistant 占位气泡
      if (last && last.role === 'assistant' && last.toolCalls && !last.content) {
        const merged: ChatMsg = { ...last, toolCalls: [...last.toolCalls, use] };
        return [...msgs.slice(0, -1), merged];
      }
      return [...msgs, { seq: null, role: 'assistant', content: '', toolCalls: [use] }];
    }
    case 'tool_result': {
      const ok = Boolean(ev.ok);
      const detail = ok
        ? summarizeData(ev.name, ev.data)
        : (ev.error ?? '调用失败');
      return [...msgs, {
        seq: null, role: 'tool', content: '', toolName: ev.name,
        ok, detail,
      }];
    }
    case 'assistant':
      if (!ev.content?.trim()) return msgs;
      return [...msgs, { seq: null, role: 'assistant', content: ev.content }];
    case 'turn_done': {
      // assistant 文本事件 + turn_done 都带最终回复 → 去重；占位气泡补文
      const content = (ev.content ?? '').trim();
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        if (last.content === content) return msgs;          // 已就位
        if (!last.content && last.toolCalls) {
          const filled: ChatMsg = { ...last, content, toolCalls: null };
          return [...msgs.slice(0, -1), filled];
        }
        if (!content) return msgs;
      }
      if (!content) return msgs;
      return [...msgs, { seq: null, role: 'assistant', content }];
    }
    case 'error':
      return [...msgs, {
        seq: null, role: 'assistant', content: '⚠ ' + (ev.error ?? '回合异常中止'),
        isError: true,
      }];
    default:
      return msgs;
  }
}

/** 工具结果 → 一行摘要（search_scenes 解包 source/count，其它截断原样）。 */
function summarizeData(name: string, data: unknown): string {
  if (name === 'search_scenes' && data && typeof data === 'object') {
    const d = data as { source?: string; count?: number; results?: unknown[] };
    return `已检索 ${d.count ?? (d.results ? d.results.length : '?')} 个场景（source=${d.source ?? '?'}）`;
  }
  const s = (() => {
    try { return JSON.stringify(data); } catch { return String(data); }
  })();
  return s.length > 90 ? s.slice(0, 90) + '…' : s;
}

/* ================= store ================= */

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([]);
  const sessionId = ref<string | null>(null);
  const messages = ref<ChatMsg[]>([]);
  const sending = ref(false);
  const error = ref('');
  const loading = ref(false);

  function hasSession(): boolean { return sessionId.value !== null; }

  async function refreshSessions(): Promise<void> {
    const cfg = loadSrConfig();
    sessions.value = await apiListSessions(cfg);
  }

  async function loadHistory(sid: string): Promise<void> {
    const cfg = loadSrConfig();
    const body = await apiSessionMessages(cfg, sid);
    messages.value = body.messages.map(msgFromHistory);
    error.value = '';
  }

  /** 首挂载：取最近会话；无则新建一个。 */
  async function init(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      await refreshSessions();
      if (sessionId.value) {
        await loadHistory(sessionId.value);
        return;
      }
      const first = sessions.value[0];
      if (first) {
        sessionId.value = first.session_id;
        await loadHistory(first.session_id);
      } else {
        await newSession();
      }
    } catch (e) {
      error.value = '加载失败：' + (e instanceof Error ? e.message : String(e));
    } finally {
      loading.value = false;
    }
  }

  async function newSession(): Promise<void> {
    try {
      const cfg = loadSrConfig();
      const sid = await apiCreateSession(cfg);
      sessions.value = [{ session_id: sid, created_at: 0, updated_at: 0, status: 'active' },
                        ...sessions.value];
      sessionId.value = sid;
      messages.value = [];
      error.value = '';
    } catch (e) {
      error.value = '新建会话失败：' + (e instanceof Error ? e.message : String(e));
    }
  }

  async function selectSession(sid: string): Promise<void> {
    if (sending.value) return;            // 流式回合中不切会话
    sessionId.value = sid;
    await loadHistory(sid);
  }

  async function send(content: string): Promise<void> {
    const sid = sessionId.value;
    const text = content.trim();
    if (!sid || !text) return;
    if (sending.value) { error.value = '上一回合仍在进行，请稍候'; return; }
    error.value = '';
    sending.value = true;
    // 乐观上屏用户消息；SSE 事件逐帧归并在其后
    messages.value = [...messages.value, { seq: null, role: 'user', content: text }];
    try {
      const cfg = loadSrConfig();
      await apiChatSend(cfg, sid, text, (ev) => {
        messages.value = reduceChatSse(messages.value, ev);
      });
    } catch (e) {
      error.value = '发送失败：' + (e instanceof Error ? e.message : String(e));
      // 回合失败也补一个失败态气泡，保持 timeline 连续
      messages.value = reduceChatSse(messages.value,
                                     { type: 'error', error: '回合异常中止' });
    } finally {
      sending.value = false;
    }
  }

  return {
    sessions, sessionId, messages, sending, error, loading,
    hasSession, init, refreshSessions, loadHistory, newSession, selectSession, send,
  };
});
