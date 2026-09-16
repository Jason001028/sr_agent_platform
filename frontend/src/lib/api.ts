/**
 * api.ts — 阶段5 平台 REST/SSE 客户端（纯函数 + fetch 封装）
 * ------------------------------------------------------------------
 * 契约基准：docs/planning/api-contract.md。
 * 纯函数可单测（SSE 帧切分/解析、状态映射）；fetch 封装读 loadSrConfig()
 * 的运行期 apiBase（默认同源 nginx 反代；e2e/异源注入 window.__SR_CFG__）。
 * 聊天走 POST + fetch ReadableStream 手工切 SSE（EventSource 只支持 GET）。
 */
import { loadSrConfig, joinBase } from './scene.js';
import type { SrConfig } from './scene.js';

export { loadSrConfig } from './scene.js';

/* ---------------- 类型（对齐 api-contract.md） ---------------- */
export type QueueState =
  | 'SUBMITTING' | 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'UNKNOWN';

export interface ChatSession {
  session_id: string;
  created_at: number;
  updated_at: number;
  status: string;
}

/** GET /api/chat/sessions/{id}/messages 单条投影（role ∈ user/assistant/tool）。 */
export interface HistoryMsg {
  seq: number;
  role: 'user' | 'assistant' | 'tool';
  content?: string | null;
  tool_calls?: { name: string; arguments: Record<string, unknown> }[] | null;
  tool_name?: string | null;
  tool_call_id?: string;
  ok?: boolean;
}

export interface HistoryResponse {
  session_id: string;
  status: string;
  messages: HistoryMsg[];
}

/** 聊天 SSE 事件（§3.2 schema；字段即事件字段，逐一对齐）。 */
export type ChatSseEvent =
  | { type: 'turn_start'; run_id: string; session_id: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; ok: boolean; data: unknown; error: string | null }
  | { type: 'assistant'; content: string }
  | { type: 'turn_done'; content: string; error: string | null }
  | { type: 'error'; error: string };

/** 队列 SSE 事件（§3.3 job_update）。 */
export interface JobUpdateEvent {
  type: 'job_update';
  task_id: number;
  job_id: number;
  state: string;
  prev_state: string | null;
  ok: boolean;
  error: string | null;
}

export type PlatformSseEvent = ChatSseEvent | JobUpdateEvent | { type: 'ping' };

/** 队列任务（GET /api/queue 行；params 已按契约投影子集）。 */
export interface QueueTask {
  task_id: number;
  fingerprint: string;
  session_id: string | null;
  job_id: number | null;
  state: QueueState | string;
  params: {
    lq_path: string;
    mask_path: string | null;
    sr_scale: number;
    suffix: string;
    gpu: number;
    cloud_limit: number;
    delete_ori: boolean;
    grid_align: boolean;
  };
  config_xml: string | null;
  batch_script: string | null;
  log_dir: string | null;
  created_at: number;
  updated_at: number;
}

/** POST /api/queue 请求体（run_sr 参数，lq_path 必填）。 */
export interface QueueSubmitBody {
  lq_path: string;
  mask_path?: string | null;
  sr_scale?: number;
  /** 输出文件名后缀。省略或留空 → 后端读 SR 团队配置文件里的 <Suffix>
   *  （services/run_sr.py::default_suffix，如 260318），前端不预填、也猜不到。 */
  suffix?: string;
  gpu?: number;
  cloud_limit?: number;
  delete_ori?: boolean;
  grid_align?: boolean;
  options_yml?: string | null;
}

export interface QueueSubmitResult {
  task_id: number;
  job_id: number | null;
  status: string;
  state: string;
  previous_state?: string;
  previous_exit_code?: string;
  previous_job_id?: number;
  config_xml: string | null;
  log_dir: string | null;
  /** 无沙箱就地跑（本地执行器恒如此）：结果按 <输入名>_<suffix>.tif 落进场景目录，
   *  输入 tif 不改名也不删除；同名旧输出先被改名为 <同名>_NOSR.tif 再覆盖。 */
  in_place?: boolean;
  /** 上一条的人话版本，提交成功后原样展示给操作者。 */
  notice?: string;
}

/* 注：后端 POST /api/masks（浏览器画掩码 → 服务端烘焙落盘）仍然存在且可用，
   但最小原型已不再调用它 —— 掩码改为「目录里已有的 <目录名>_mask.tif」，
   前端提交时只带 lq_path，掩码由后端推导（platform.derived_mask_path）。 */

/* ---------------- URL 拼接 ---------------- */
export const apiUrl = (cfg: SrConfig, path: string): string =>
  joinBase(cfg.apiBase, path);

export const sessionsUrl = (cfg: SrConfig): string => apiUrl(cfg, '/api/chat/sessions');
export const sessionMessagesUrl = (cfg: SrConfig, sid: string): string =>
  apiUrl(cfg, `/api/chat/sessions/${encodeURIComponent(sid)}/messages`);
export const queueUrl = (cfg: SrConfig): string => apiUrl(cfg, '/api/queue');
export const queueEventsUrl = (cfg: SrConfig): string => apiUrl(cfg, '/api/queue/events');

/* ---------------- SSE 帧切分 / 解析（纯函数） ---------------- */
/** 切一段 SSE 文本成完整帧的 data 行；残片（无收尾空行）留在 rest。 */
export function stepSse(buf: string): { frames: string[]; rest: string } {
  const frames: string[] = [];
  let rest = buf;
  for (;;) {
    const end = rest.indexOf('\n\n');
    if (end < 0) break;
    const block = rest.slice(0, end);
    rest = rest.slice(end + 2);
    const data = block
      .split('\n')
      .map((l) => (l.startsWith('data:') ? l.slice(5).trimStart() : ''))
      .filter((l) => l !== '')
      .join('\n');
    if (data) frames.push(data);
  }
  return { frames, rest };
}

/** 解析整段 SSE（每帧一行 JSON）成事件；无法解析的帧丢弃。 */
export function parseSseEvents(text: string): PlatformSseEvent[] {
  const { frames } = stepSse(text);
  const events: PlatformSseEvent[] = [];
  for (const data of frames) {
    try {
      const ev = JSON.parse(data) as PlatformSseEvent;
      if (ev && typeof ev.type === 'string') events.push(ev);
    } catch {
      /* 跳过非 JSON 帧（心跳注释/空行等） */
    }
  }
  return events;
}

/** 读一个 fetch Response body（ReadableStream），按 SSE 帧边界回调。 */
export async function readSseStream(
  body: ReadableStream<Uint8Array> | null,
  onEvent: (e: PlatformSseEvent) => void,
): Promise<void> {
  if (!body) return;
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const { frames, rest } = stepSse(buf);
    buf = rest;
    for (const data of frames) {
      try {
        const ev = JSON.parse(data) as PlatformSseEvent;
        if (ev && typeof ev.type === 'string') onEvent(ev);
      } catch {
        /* 忽略坏帧 */
      }
    }
  }
  buf += dec.decode();
  const { frames } = stepSse(buf);
  for (const data of frames) {
    try {
      const ev = JSON.parse(data) as PlatformSseEvent;
      if (ev && typeof ev.type === 'string') onEvent(ev);
    } catch {
      /* ignore */
    }
  }
}

/* ---------------- fetch 封装（读运行期 apiBase） ---------------- */
async function http(url: string, init?: RequestInit): Promise<Response> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(detail);
  }
  return resp;
}

export async function apiCreateSession(cfg: SrConfig): Promise<string> {
  const r = await http(sessionsUrl(cfg), { method: 'POST' });
  return ((await r.json()) as { session_id: string }).session_id;
}

export async function apiListSessions(cfg: SrConfig): Promise<ChatSession[]> {
  const r = await http(sessionsUrl(cfg));
  return ((await r.json()) as { sessions: ChatSession[] }).sessions;
}

export async function apiSessionMessages(
  cfg: SrConfig, sid: string,
): Promise<HistoryResponse> {
  const r = await http(sessionMessagesUrl(cfg, sid));
  return (await r.json()) as HistoryResponse;
}

/** 发一条消息：POST 即流，SSE 逐事件回调；Promise 在流结束（turn_done/error）时完成。 */
export async function apiChatSend(
  cfg: SrConfig, sid: string, content: string,
  onEvent: (e: PlatformSseEvent) => void,
): Promise<void> {
  const r = await fetch(sessionMessagesUrl(cfg, sid), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  await readSseStream(r.body, onEvent);
}

export async function apiListQueue(cfg: SrConfig): Promise<QueueTask[]> {
  const r = await http(queueUrl(cfg));
  return ((await r.json()) as { tasks: QueueTask[] }).tasks;
}

export async function apiSubmitQueue(
  cfg: SrConfig, body: QueueSubmitBody,
): Promise<QueueSubmitResult> {
  const r = await http(queueUrl(cfg), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await r.json()) as QueueSubmitResult;
}

export async function apiCancelQueue(
  cfg: SrConfig, taskId: number,
): Promise<{ task_id: number; cancelled: boolean; state: string }> {
  const r = await http(apiUrl(cfg, `/api/queue/${taskId}/cancel`), {
    method: 'POST',
  });
  return (await r.json()) as { task_id: number; cancelled: boolean; state: string };
}

/** 订阅队列 SSE：连接后在 onEvent 收到 job_update。返回断开函数。 */
export function subscribeQueueEvents(
  cfg: SrConfig,
  onEvent: (e: PlatformSseEvent) => void,
  onError?: (err: Error) => void,
): () => void {
  const ctrl = new AbortController();
  void (async () => {
    try {
      const r = await fetch(queueEventsUrl(cfg), { signal: ctrl.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await readSseStream(r.body, onEvent);
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      if (onError) onError(e as Error);
    }
  })();
  return () => ctrl.abort();
}

/** 新会话随手一张（ChatPage 顶部用）。 */
export const freshApi = () => loadSrConfig();
