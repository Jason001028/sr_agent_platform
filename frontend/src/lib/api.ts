/**
 * api.ts — 阶段5 平台 REST/SSE 客户端（纯函数 + fetch 封装）
 * ------------------------------------------------------------------
 * 契约基准：docs/planning/api-contract.md。
 * 纯函数可单测（SSE 帧切分/解析、状态映射）；fetch 封装读 loadSrConfig()
 * 的运行期 apiBase（默认同源 nginx 反代；e2e/异源注入 window.__SR_CFG__）。
 * 聊天走 POST + fetch ReadableStream 手工切 SSE（EventSource 只支持 GET）。
 */
import {
  loadSrConfig, joinBase, sceneResolveUrl, scenePreviewUrl, sceneImageUrl,
  tmpPreviewUrl,
} from './scene.js';
import type { SceneRow, SrConfig } from './scene.js';

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

/** 队列 SSE 事件（§3.3 job_update）。
    `updated_at` = 后端这次写回 sr_tasks 的时间戳（终态行的耗时就是它减
    created_at）。老后端不发这个字段，故可选 —— 缺省时前端保留本地快照。 */
export interface JobUpdateEvent {
  type: 'job_update';
  task_id: number;
  job_id: number;
  state: string;
  prev_state: string | null;
  ok: boolean;
  error: string | null;
  updated_at?: number;
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

/* 注：掩码仍是「目录里已有的 <输入名>_mask.tif」，提交时只带 lq_path、由后端
   推导（services/scene_search.derived_mask_path）。apiBakeMask 是**手工场景**的
   补充出口：盘阵上那 90% 本来没有掩码的场景，画完直接写进服务端场景目录，
   之后提交就走同一条推导。两条路写出去的必须是同一个文件名（后端同源保证）。 */

/** POST /api/scenes/resolve 响应（打开盘阵任意合法场景目录）。 */
export interface SceneResolveResult {
  source: 'manual';
  row: SceneRow;
  resolved: {
    /** 场景目录（盘阵 POSIX） */
    dir: string;
    /** 输入影像绝对路径（提交 SR 时 lq_path 指向的就是它的父目录） */
    input: string;
    input_name: string;
    /** 平台推导的掩码路径：与提交侧去找的那份逐字节相同。
     *  裸 .tif 且父目录不是场景目录时为 null（那种情况没有可提交的场景）。 */
    mask_path: string | null;
    mask_exists: boolean;
    /** 服务账号对该目录有写权限（meta.xml 回写 / Debug 日志 / 掩码 / 预览缓存） */
    writable: boolean;
    /** 这个目录能不能提交 SR。目录形态恒为 true；**粘单个 .tif 时可能为 false**
     *  —— 随手贴的一张图不该拿到提交入口，否则 SR 在盘阵上根本跑不起来。
     *  `row.lq_path` 与它同源同真假（场景库入口直接读的是 row.lq_path）。 */
    sr_capable: boolean;
  };
}

/** POST /api/masks 响应（画好的掩码写进服务端场景目录）。 */
export interface MaskBakeResult {
  mask_path: string;
  mask_txt: string;
  lq_path: string;
  /** 预填到提交表单的草稿（suffix 已按 SR 团队配置取默认值，不为空） */
  task_draft: QueueSubmitBody & { lq_path: string; mask_path: string };
}

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

/** 取一条场景行的显示 JPG 字节。两条来源，调用方不必区分：
 *
 *  * 库行（jpgUrl 非空）：缓存没生成过就先 POST 一下懒生成，然后走静态 URL
 *    （nginx 直出，大图不经过 API 进程）。
 *  * 库外的场景（手工 resolve 的盘阵目录，jpgUrl 为空）：没有静态 URL，
 *    /api/scenes/{id}/preview 的**响应体本身**就是那张 JPEG。
 *
 *  两条路都必须真正取到字节 —— 库外那条早期版本把响应取到手又丢掉，结果手工
 *  场景一律打不开。首次要解压采样整幅大图，可能较慢。
 *
 *  `onPhase` 在「本次会触发服务端烘焙」时被调用一次（只有这一次，没有百分比）：
 *  首次烘焙要读一遍源图，2.4 万像素级的场景在盘阵上要几秒到几十秒，调用方拿它
 *  更新遮罩文案，别让界面看起来像卡死了。判据是 `row.hasPreview` —— 后端填的是
 *  缓存到底在不在的真值（库外同样准），所以第二次打开不会再提示。 */
export async function fetchSceneJpg(
  cfg: SrConfig,
  row: SceneRow,
  onPhase?: (text: string) => void,
): Promise<Blob> {
  if (!row.hasPreview) onPhase?.('首次打开：正在服务器烘焙 1/2 预览图（直方图均衡），'
    + '要读一遍大图，可能要等几十秒…');
  if (!row.jpgUrl) {
    const p = await http(scenePreviewUrl(cfg, row.id));
    row.hasPreview = true;
    return await p.blob();
  }
  if (!row.hasPreview) {
    await http(scenePreviewUrl(cfg, row.id));
    row.hasPreview = true;
  }
  const img = await http(sceneImageUrl(cfg, row.jpgUrl));
  return await img.blob();
}

/** 拖拽入口取**临时**预览 JPG：GET /api/scenes/{id}/preview-tmp。
 *
 * 为什么必须与 `fetchSceneJpg` 分开（而不是加个参数）：那个函数会写
 * `row.hasPreview = true`，而该字段的语义是「生产 `<stem>.preview.jpg` **此刻
 * 在不在**」。被临时路径置真之后，用户再从场景库打开同一个场景就会跳过懒生成、
 * 直接打一个 404 的静态 URL，图再也出不来。这里不碰任何 SceneRow 字段。
 *
 * 也不走静态 URL（`SR_TEMP_PREVIEWS_ROOT` 不在 nginx 的 /disk-array 映射里），
 * 响应体本身就是那张 JPEG。 */
export async function fetchTempSceneJpg(
  cfg: SrConfig,
  id: string,
  onPhase?: (text: string) => void,
): Promise<Blob> {
  onPhase?.('已关联到盘阵场景：正在服务器烘焙 1/2 预览图，首次要读一遍大图…');
  const r = await http(tmpPreviewUrl(cfg, id));
  return await r.blob();
}

/** 打开盘阵上任意一个合法场景目录：POST /api/scenes/resolve。
 *
 * 二选一：`{path}`（`W:\...` 或 `/DiskArray/...`，服务端归一）或
 * `{name}`（裸文件名，+ 可选 `date`；不给日期就由后端从文件名里的成像时间戳
 * 取，前端不再自解析 —— 命名规则与模板的唯一真源在 backend/pathguard.py）。
 * **没找到就抛**（4xx），`Error.message` 是后端给的候选与原因清单，调用方原样
 * 展示即可 —— 反推不准时必须让用户看见为什么、然后手粘目录，绝不静默换一条
 * 路径。
 *
 * 拖拽入口在 `{name}` 上再带一个 `size_bytes`（浏览器 File.size）：服务端要求
 * 盘阵上的输入影像**同名且字节数一致**才认（backend/api/app.py 的
 * `_fingerprint_mismatch`）。只看名字不够 —— 同目录里可能存在另一张图（RC 场景
 * 的输入影像是 PAN.tif），关联错了掩码坐标就整片落在别的图上；只看字节数也不够。
 * 不符时后端回 404 并把原因写进 detail，调用方应当**退回本地解码**而不是报死错。
 *
 * 首次调用可能要解压采样整幅大图（生成预览缓存），界面应提示「首次较慢」。 */
export async function apiResolveScene(
  cfg: SrConfig,
  body: { path: string }
      | { name: string; date?: string; size_bytes?: number },
  opts?: { signal?: AbortSignal },
): Promise<SceneResolveResult> {
  const r = await http(sceneResolveUrl(cfg), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: opts?.signal,
  });
  return (await r.json()) as SceneResolveResult;
}

/** 把浏览器里画好的掩码写进服务端场景目录：POST /api/masks。
 *
 * `polygons` 是**全分辨率**坐标（缩略图坐标先经 thumbPolysToOrig 换算）。
 * W/H 必须是源影像的真实尺寸 —— 栅格化按它建画布。 */
export async function apiBakeMask(
  cfg: SrConfig,
  body: { lq_path: string; W: number; H: number; polygons: unknown },
): Promise<MaskBakeResult> {
  const r = await http(apiUrl(cfg, '/api/masks'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await r.json()) as MaskBakeResult;
}

/** 新会话随手一张（ChatPage 顶部用）。 */
export const freshApi = () => loadSrConfig();
