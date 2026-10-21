/**
 * stores/chat.ts — Agent 聊天（阶段5：SSE 流式 Agent 过程）
 * ------------------------------------------------------------------
 * 会话模型（api-contract.md §3.2）：单会话 + 新建；服务端 store 为事实源。
 * SSE 事件经 reduceChatSse 逐帧归并成展示消息（与 GET messages 投影同一形状，
 * 刷新/切会话可无缝换源）。消息 timeline 复用 role=user/assistant/tool：
 *   工具回合渲染成  user ─ assistant(推理+声明工具) ─ tool(结果) ─ assistant(最终回复)。
 * 流式（2026-10-21）：llm_phase/reasoning/token 归并到队尾那条 streaming assistant；
 * reasoning 只在本回合展示、不落库，token/reasoning 按 50ms 批量 flush。
 * 并发回合由服务端 409 兜底；UI 发送中禁按钮。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { loadSrConfig } from '../lib/scene.js';
import {
  apiCreateSession, apiListSessions, apiSessionMessages, apiSessionUsage,
  apiChatSend, apiSubmitTrajectoryFeedback,
  apiDeleteSession, apiSetSessionPinned, chatImageUrl,
} from '../lib/api.js';
import type {
  ChatImage, ChatPhase, ChatSession, ChatUploadImage, ChatUsage, HistoryMsg,
  PlatformSseEvent, TrajectoryFeedbackRating,
} from '../lib/api.js';
import { SHOT_MAX_COUNT } from '../lib/chatShot.js';
import type { ChatShot } from '../lib/chatShot.js';
import { isStreamingEnabled } from '../lib/streaming.js';

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
  images?: ChatImage[] | null;     // user 行：截图附件（已带可用 src，渲染层不再换算）
  /** 本回合的 Qwen 推理内容（reasoning_content）。**不入库**：刷新后不再出现。 */
  reasoning?: string;
  /** 仍在流式接收；false/缺省 = 已定稿（历史投影没有这个字段）。 */
  streaming?: boolean;
  /** 当前模型阶段：推理 / 写回答 / 组织工具调用（仅流式期间有意义）。 */
  phase?: ChatPhase | null;
}

/** 输入区里待发的截图。`dataUrl` 同时当预览源和上传体，发送后即从托盘移除。 */
export interface PendingImage {
  id: number;
  dataUrl: string;
  name: string;
}

/* ================= 纯函数（vitest 可测） ================= */

/** GET messages 一行 → 展示消息（tool 行含 detail，无则显 ok/失败）。 */
export function msgFromHistory(h: HistoryMsg): ChatMsg {
  if (h.role === 'user') {
    const images: ChatImage[] | null = h.images && h.images.length
      ? h.images.map((im) => ({ url: im.url, name: im.name ?? null }))
      : null;
    return { seq: h.seq, role: 'user', content: h.content ?? '', images };
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

/** 收一条 SSE 事件，归并进展示消息副本（不可变，返回新数组）。
 *
 * 流式事件（llm_phase / reasoning / token）都归并到「队尾那条仍在流的 assistant」；
 * 定稿事件（assistant / turn_done）把它收尾。工具回合仍是
 * user ─ assistant(推理+可选文本+工具声明) ─ tool ─ assistant(最终回复)，
 * 与 GET messages 的投影形状一致。 */
export function reduceChatSse(
  msgs: ChatMsg[], ev: PlatformSseEvent,
): ChatMsg[] {
  switch (ev.type) {
    case 'turn_start':
    case 'job_update':
    case 'ping':
      return msgs;                       // 无渲染载体：sending 灯已是状态

    case 'llm_phase': {
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        if (last.phase === ev.phase) return msgs;
        return [...msgs.slice(0, -1), { ...last, phase: ev.phase }];
      }
      return [...msgs, {
        seq: null, role: 'assistant', content: '', reasoning: '', toolCalls: null,
        streaming: true, phase: ev.phase,
      }];
    }

    case 'reasoning': {
      if (!ev.delta) return msgs;
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        return [...msgs.slice(0, -1), {
          ...last,
          reasoning: (last.reasoning ?? '') + ev.delta,
          phase: last.phase ?? 'thinking',
        }];
      }
      return [...msgs, {
        seq: null, role: 'assistant', content: '', reasoning: ev.delta,
        toolCalls: null, streaming: true, phase: 'thinking',
      }];
    }

    case 'token': {
      if (!ev.delta) return msgs;
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        return [...msgs.slice(0, -1), {
          ...last, content: last.content + ev.delta, phase: 'answering',
        }];
      }
      return [...msgs, {
        seq: null, role: 'assistant', content: ev.delta, toolCalls: null,
        streaming: true, phase: 'answering',
      }];
    }

    case 'tool_call': {
      const use: ToolUse = { name: ev.name, args: ev.args };
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        // 同回合连续多次工具声明 → 并入同一个 assistant 气泡
        if (last.toolCalls && last.toolCalls.length) {
          return [...msgs.slice(0, -1), {
            ...last, toolCalls: [...last.toolCalls, use],
          }];
        }
        // 流式已落下推理/正文：这是同一条 assistant 消息的工具声明，
        // 不能再开一个气泡（刷新后 history 里它们本来就是一条）。
        if (last.streaming || last.content || last.reasoning) {
          return [...msgs.slice(0, -1), {
            ...last, toolCalls: [use], streaming: false, phase: null,
          }];
        }
      }
      return [...msgs, {
        seq: null, role: 'assistant', content: '', toolCalls: [use],
        streaming: false, phase: null,
      }];
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

    case 'assistant': {
      const content = ev.content ?? '';
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        // 以服务端最终文本为准（与已拼出的 token 通常逐字相同）
        return [...msgs.slice(0, -1), {
          ...last, content, streaming: false, phase: null,
        }];
      }
      if (!content.trim()) return msgs;
      return [...msgs, {
        seq: null, role: 'assistant', content, streaming: false, phase: null,
      }];
    }

    case 'turn_done': {
      // assistant 文本事件 + turn_done 都带最终回复 → 去重；占位气泡补文
      const content = (ev.content ?? '').trim();
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        return [...msgs.slice(0, -1), {
          ...last,
          content: content || last.content,
          streaming: false, phase: null,
        }];
      }
      if (last && last.role === 'assistant') {
        if (last.content === content) return msgs;          // 已就位
        if (!last.content && last.toolCalls) {
          const filled: ChatMsg = { ...last, content, toolCalls: null };
          return [...msgs.slice(0, -1), filled];
        }
        if (!content) return msgs;
      }
      if (!content) return msgs;
      return [...msgs, {
        seq: null, role: 'assistant', content, streaming: false, phase: null,
      }];
    }

    case 'error': {
      // 半截流式消息也要收尾，否则指示灯会一直转
      const last = msgs[msgs.length - 1];
      const base = last && last.role === 'assistant' && last.streaming
        ? [...msgs.slice(0, -1), { ...last, streaming: false, phase: null }]
        : msgs;
      return [...base, {
        seq: null, role: 'assistant', content: '⚠ ' + (ev.error ?? '回合异常中止'),
        isError: true,
      }];
    }

    default:
      return msgs;
  }
}

/** 会话排序：置顶优先，其余按最近活动倒序（与服务端 list_sessions 一致）。 */
export function sortSessions(list: ChatSession[]): ChatSession[] {
  return [...list].sort((a, b) =>
    Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
    || b.updated_at - a.updated_at);
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
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
  const agentStatus = ref<'idle' | 'thinking' | 'responding'>('idle');
  /** 流式增量批次号：token/reasoning 批量合并后 +1，供滚动跟随用。 */
  const streamTick = ref(0);
  const error = ref('');
  const loading = ref(false);
  const usage = ref<ChatUsage | null>(null);
  const lastRunId = ref<string | null>(null);
  const feedbackRating = ref<TrajectoryFeedbackRating | null>(null);
  /** 待发截图（≤2）：从查看器加进来的，发送即清。 */
  const pendingImages = ref<PendingImage[]>([]);
  let pendingSeq = 0;
  let agentStatusTimer: ReturnType<typeof setTimeout> | null = null;

  function hasSession(): boolean { return sessionId.value !== null; }

  /** 加一张待发截图；已满 → 返回 false（由调用方提示，不静默丢弃）。 */
  function addPendingImage(shot: ChatShot): boolean {
    if (pendingImages.value.length >= SHOT_MAX_COUNT) return false;
    pendingImages.value = [
      ...pendingImages.value,
      { id: ++pendingSeq, dataUrl: shot.dataUrl, name: shot.name },
    ];
    return true;
  }

  function removePendingImage(id: number): void {
    pendingImages.value = pendingImages.value.filter((p) => p.id !== id);
  }

  function clearPendingImages(): void {
    pendingImages.value = [];
  }

  async function refreshSessions(): Promise<void> {
    const cfg = loadSrConfig();
    sessions.value = sortSessions(await apiListSessions(cfg));
  }

  async function loadHistory(sid: string): Promise<void> {
    const cfg = loadSrConfig();
    const body = await apiSessionMessages(cfg, sid);
    // 历史图存的是平台相对 URL，这里一次性换算成 <img> 能直接用的 src
    messages.value = body.messages.map((h) => {
      const msg = msgFromHistory(h);
      if (msg.images) {
        msg.images = msg.images.map((im) => ({ ...im, url: chatImageUrl(cfg, im.url) }));
      }
      return msg;
    });
    usage.value = await apiSessionUsage(cfg, sid);
    lastRunId.value = null;
    feedbackRating.value = null;
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
      const now = Date.now() / 1000;
      sessions.value = sortSessions([
        { session_id: sid, created_at: now, updated_at: now,
          status: 'active', pinned: false },
        ...sessions.value,
      ]);
      sessionId.value = sid;
      messages.value = [];
      usage.value = null;
      lastRunId.value = null;
      feedbackRating.value = null;
      error.value = '';
    } catch (e) {
      error.value = '新建会话失败：' + (e instanceof Error ? e.message : String(e));
    }
  }

  async function selectSession(sid: string): Promise<void> {
    if (sending.value) return;            // 流式回合中不切会话
    // 只有真的换了会话才清待发图：登录后首次进页 / init 复用同一会话时不能误清
    if (sid !== sessionId.value) clearPendingImages();
    sessionId.value = sid;
    await loadHistory(sid);
  }

  async function send(content: string): Promise<void> {
    const sid = sessionId.value;
    const text = content.trim();
    if (!sid || (!text && !pendingImages.value.length)) return;
    if (sending.value) { error.value = '上一回合仍在进行，请稍候'; return; }
    error.value = '';
    sending.value = true;
    if (agentStatusTimer !== null) { clearTimeout(agentStatusTimer); agentStatusTimer = null; }
    agentStatus.value = 'thinking';
    // 发送即取走托盘：失败也不回填，避免重试时同一张图发两遍
    const pending = pendingImages.value;
    pendingImages.value = [];
    const upload: ChatUploadImage[] = pending.map((p) => ({ data: p.dataUrl, name: p.name }));
    const optimistic: ChatImage[] = pending.map((p) => ({ url: p.dataUrl, name: p.name }));
    // 乐观上屏用户消息；SSE 事件逐帧归并在其后
    messages.value = [...messages.value, {
      seq: null, role: 'user', content: text,
      images: optimistic.length ? optimistic : null,
    }];
    try {
      const cfg = loadSrConfig();
      // token/reasoning 每帧都可能到：先攒 50ms 再合并，避免每个 token 都
      // 触发一次 Markdown 全量解析。其它事件到达前先 flush，timeline 顺序不变。
      let buf: PlatformSseEvent[] = [];
      let timer: ReturnType<typeof setTimeout> | null = null;
      const apply = (ev: PlatformSseEvent): void => {
        if (ev.type === 'usage') usage.value = ev.aggregate;
        if (ev.type === 'turn_done' && ev.usage) usage.value = ev.usage;
        if (ev.type === 'turn_done' && ev.run_id) {
          lastRunId.value = ev.run_id;
          feedbackRating.value = null;
        }
        messages.value = reduceChatSse(messages.value, ev);
      };
      const flush = (): void => {
        if (timer !== null) { clearTimeout(timer); timer = null; }
        if (!buf.length) return;
        const batch = buf;
        buf = [];
        let next = messages.value;
        for (const ev of batch) next = reduceChatSse(next, ev);
        messages.value = next;
        streamTick.value += 1;
      };
      const onEvent = (ev: PlatformSseEvent): void => {
        if (ev.type === 'token' && ev.delta) agentStatus.value = 'responding';
        if ((ev.type === 'assistant' || ev.type === 'turn_done') && 'content' in ev && ev.content?.trim()) {
          agentStatus.value = 'responding';
        }
        if (ev.type === 'token' || ev.type === 'reasoning') {
          buf.push(ev);
          if (timer === null) timer = setTimeout(flush, 50);
          return;
        }
        flush();
        apply(ev);
      };
      try {
        // 开（默认）就不带 stream 字段，请求体保持老形状；只有关掉时才显式发 false。
        await apiChatSend(cfg, sid, text, onEvent,
                          upload.length ? upload : undefined,
                          isStreamingEnabled() ? undefined : false);
      } finally {
        flush();                          // 失败时也别丢掉已到的半截文本
      }
    } catch (e) {
      error.value = '发送失败：' + (e instanceof Error ? e.message : String(e));
      // 回合失败也补一个失败态气泡，保持 timeline 连续
      messages.value = reduceChatSse(messages.value,
                                     { type: 'error', error: '回合异常中止' });
    } finally {
      finalizeStreaming();                // 没有 turn_done 的异常路径也要熄灯
      sending.value = false;
      agentStatusTimer = setTimeout(() => {
        agentStatus.value = 'idle';
        agentStatusTimer = null;
      }, 320);
    }
  }

  /** 把所有仍在流式的消息定稿（异常/客户端断流兜底）。 */
  function finalizeStreaming(): void {
    if (!messages.value.some((m) => m.streaming)) return;
    messages.value = messages.value.map((m) =>
      (m.streaming ? { ...m, streaming: false, phase: null } : m));
  }

  async function submitFeedback(rating: TrajectoryFeedbackRating, reason?: string): Promise<void> {
    const sid = sessionId.value;
    const runId = lastRunId.value;
    if (!sid || !runId || feedbackRating.value) return;
    try {
      await apiSubmitTrajectoryFeedback(loadSrConfig(), sid, runId, rating, reason);
      feedbackRating.value = rating;
    } catch (e) {
      error.value = '反馈提交失败：' + (e instanceof Error ? e.message : String(e));
    }
  }

  /** 删除后维持「总有当前会话」：优先切到列表第一个，列表空了就新建。 */
  async function afterDelete(removed: string[]): Promise<void> {
    sessions.value = sessions.value.filter((s) => !removed.includes(s.session_id));
    if (!sessionId.value || !removed.includes(sessionId.value)) return;
    clearPendingImages();                  // 当前会话没了，托盘里的图也没了归处
    const next = sessions.value[0];
    if (next) {
      sessionId.value = next.session_id;
      await loadHistory(next.session_id);
      return;
    }
    sessionId.value = null;
    messages.value = [];
    usage.value = null;
    lastRunId.value = null;
    feedbackRating.value = null;
    await newSession();
  }

  async function deleteSession(sid: string): Promise<void> {
    error.value = '';
    try {
      await apiDeleteSession(loadSrConfig(), sid);
      await afterDelete([sid]);
    } catch (e) {
      error.value = '删除会话失败：' + errText(e);
    }
  }

  /** 批量删除：逐个提交，失败的不从列表移除，并汇总提示。 */
  async function deleteSessions(ids: string[]): Promise<void> {
    if (!ids.length) return;
    error.value = '';
    const cfg = loadSrConfig();
    const removed: string[] = [];
    const failures: string[] = [];
    for (const sid of ids) {
      try {
        await apiDeleteSession(cfg, sid);
        removed.push(sid);
      } catch (e) {
        failures.push(errText(e));
      }
    }
    await afterDelete(removed);
    if (failures.length) {
      error.value = `部分会话删除失败（${failures.length}/${ids.length}）：${failures[0]}`;
    }
  }

  async function pinSession(sid: string, pinned: boolean): Promise<void> {
    error.value = '';
    try {
      const updated = await apiSetSessionPinned(loadSrConfig(), sid, pinned);
      sessions.value = sortSessions(sessions.value.map((s) =>
        s.session_id === sid ? { ...s, ...updated } : s));
    } catch (e) {
      error.value = '置顶操作失败：' + errText(e);
    }
  }

  /** 批量置顶：逐个提交，成功的合并回列表，失败汇总提示。 */
  async function pinSessions(ids: string[], pinned: boolean): Promise<void> {
    if (!ids.length) return;
    error.value = '';
    const cfg = loadSrConfig();
    const updatedRows: ChatSession[] = [];
    const failures: string[] = [];
    for (const sid of ids) {
      try {
        updatedRows.push(await apiSetSessionPinned(cfg, sid, pinned));
      } catch (e) {
        failures.push(errText(e));
      }
    }
    const byId = new Map(updatedRows.map((r) => [r.session_id, r]));
    sessions.value = sortSessions(sessions.value.map((s) => {
      const updated = byId.get(s.session_id);
      return updated ? { ...s, ...updated } : s;
    }));
    if (failures.length) {
      error.value = `部分会话置顶失败（${failures.length}/${ids.length}）：${failures[0]}`;
    }
  }

  return {
    sessions, sessionId, messages, sending, agentStatus, streamTick, error, loading,
    usage, lastRunId, feedbackRating,
    pendingImages, addPendingImage, removePendingImage, clearPendingImages,
    hasSession, init, refreshSessions, loadHistory, newSession, selectSession, send,
    submitFeedback, deleteSession, deleteSessions, pinSession, pinSessions,
  };
});
