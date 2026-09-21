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
  dropPreviewUrl, isBakedPreviewUrl, previewDivLabel, previewNeedsBake,
  loadPreviewDiv, rasterPreviewWins, sceneSiblingsUrl, previewCacheKey,
} from './scene.js';
import type { SceneRow, SrConfig, RasterPreview } from './scene.js';
import { createBlobCache } from './blobCache.js';
import type { BlobCacheStats } from './blobCache.js';

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
    `updated_at` / `started_at` / `finished_at` = 后端这次写回 sr_tasks 的值，
    与 GET /api/queue 同名字段同源（耗时 = finished_at − started_at）。都是可选：
    老后端不发，写库失败时也不发 —— 缺省时前端保留本地快照。 */
export interface JobUpdateEvent {
  type: 'job_update';
  task_id: number;
  job_id: number;
  state: string;
  prev_state: string | null;
  ok: boolean;
  error: string | null;
  updated_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
}

/** 队列 SSE 事件（§4.x preview_update）：**服务端急烤产物预览**的结局。
    刻意与 job_update 分开 —— 那是「作业状态变了」，这是「预览烤好了」，两者无关
    （作业 COMPLETED 了预览也可能因为沙箱/产物缺失/目录不可写而没烤）。收到它只需
    改这一行的预览字段，不必重取整行。 */
export interface PreviewUpdateEvent {
  type: 'preview_update';
  task_id: number;
  /** null = 从没烤过；running 是认领后的中间态（只在重取时可能看到）。 */
  state: 'running' | 'done' | 'skipped' | 'failed' | null;
  note: string | null;
}

export type PlatformSseEvent =
  | ChatSseEvent | JobUpdateEvent | PreviewUpdateEvent | { type: 'ping' };

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
  /** 行的时间戳：created_at = 这一行**第一次**提交的时刻（同一指纹重交复用同一行，
   *  它不会刷新），updated_at = 最后一次写回。队列排序与「创建时间」列用它们。 */
  created_at: number;
  updated_at: number;
  /** **本次运行**的时间窗：首次被观测到 RUNNING → 终态落库。耗时列的唯一来源
   *  （不派生自 created_at —— 那是行的生日，复用行会退化成行龄）。NULL = 没观测到
   *  开始（整段运行期间后端不在）或升级前建的老行 → 界面显示「—」。 */
  started_at: number | null;
  finished_at: number | null;
  /** **产物预览的服务端急烤**进度（与 state 无关）：null = 没烤过，running = 正在烤，
   *  done = 盘上已有当前档位的产物预览，skipped / failed 见 preview_note 里那句人话。
   *  作业跑完不一定烤成 —— 沙箱私有副本、产物缺失（云限额跳过的作业是合法 COMPLETED
   *  但没有产物）、场景目录不可写都会如实记为 skipped。 */
  preview_state?: 'running' | 'done' | 'skipped' | 'failed' | null;
  /** 急烤结局的人话说明，形如 `<slug>: …`（slug 固定为 sandbox / product_missing /
   *  unwritable / source_changed / no_suffix / failed）。 */
  preview_note?: string | null;
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
     *  `row.lq_path` 与它同源同真假（场景库入口直接读的是 row.lq_path）。
     *  **拖进来的中间产物（`kind != 'input'`）也是 false**：那一类的 `row.lq_path`
     *  被服务端置空，前端别指望能提交（见 kind）。 */
    sr_capable: boolean;
    /** 拖进来的这份影像属于场景里的哪个环节（2026-09-21 起中间产物也能关联）。
     *  `'input'` = 本体（显示件 / 输入影像，可修复可提交）；`'product'` = 本次
     *  SR 产物；`'nosr'` = 上一次的产物。**只有 kind='input' 才是可修复对象** ——
     *  掩码与 SR 建在本体影像的网格上，产物的尺寸是它的倍数。 */
    kind: 'input' | 'product' | 'nosr';
    /** 本次产物的 suffix（从**文件名本身**切出来的那一段，不是查任务库来的）。
     *  kind='input' 时是空串。标签与提示用。 */
    suffix: string;
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

/* ---------------- 预览 JPG 的本地 blob 缓存（2026-09-20 新增） ----------------

   同一张图来回切（对比模式）不该每次都走一遍网络与读盘：磁盘上那份服务端早就烤好
   了，前端再取就是纯重复。缓存的是**压缩 blob** 而不是解码后的位图 —— 理由见
   lib/blobCache.ts 的文件头（一句话：rec 的像素本来就常驻，位图缓存只会翻倍）。

   按字节封顶，不按条数：÷2 档一张通常几 MB～几十 MB，128MB 约合十来张；条数在大图上
   完全不代表内存。超上限的条目由 LRU 淘汰，超过上限的单条干脆不进。 */
export const PREVIEW_BLOB_CACHE_MAX = 128 * 1024 * 1024;
const previewBlobs = createBlobCache(PREVIEW_BLOB_CACHE_MAX);

/** 缓存占用（设置浮层里那一行「本地预览缓存 N 项 / X MB」）。 */
export function previewCacheStats(): BlobCacheStats {
  return previewBlobs.stats();
}

/** 清空（设置浮层的「清空」按钮）。 */
export function clearPreviewCache(): void {
  previewBlobs.clear();
}

/** 丢弃并重建缓存。**只给单测用**：模块级单例会在用例之间串味。 */
export function resetPreviewCache(): void {
  previewBlobs.clear();
}

/** 存进缓存并原样返回（三处 return 都走它，免得漏存或存了不返）。 */
function cacheBlob(key: string, blob: Blob): Blob {
  previewBlobs.set(key, blob);
  return blob;
}

/** 取一条场景行的显示 JPG 字节。两条来源，调用方不必区分：
 *
 *  * 库行（jpgUrl 非空）：缓存没生成过、或**盘上那份不是当前档位**，就先打一次
 *    `/preview?div=N` 懒生成，再走静态 URL 取字节（nginx 直出，大图不经过
 *    API 进程）；档位已对上就直接取静态 URL。
 *  * 库外的场景（手工 resolve 的盘阵目录，jpgUrl 为空）：没有静态 URL，
 *    /api/scenes/{id}/preview 的**响应体本身**就是那张 JPEG。
 *
 *  两条路都必须真正取到字节 —— 库外那条早期版本把响应取到手又丢掉，结果手工
 *  场景一律打不开。首次要解压采样整幅大图，可能较慢。
 *
 *  库行这条为什么要「先把重烤那一下白打掉」，而不是直接拿它的响应体当图：那张图
 *  得由 nginx 直出（几 MB 到几十 MB，走 API 进程是白占一个工作进程的内存与带宽）。
 *  代价是重烤那一次下两遍（第一遍纯触发烘焙、丢掉响应）；稳态下只有一遍。
 *  换档位不会因此看到旧图 —— 静态 URL 带 `?div=N`（见 sceneImageUrl），
 *  nginx 的 `max-age=3600` 缓存键跟着变，第二遍拿到的一定是新烤的。
 *
 *  **重烤判据不能只看 `hasPreview`**：它不认档位，换完档位盘上那份旧图仍在，
 *  只看它就会跳过重烤、把旧档位的图端上来（而且是每次换档都这样）。
 *  `row.previewDiv` 是后端从 JPEG 注释戳里解出来的实际档位，`null` 表示
 *  「不知道是哪一档」（旧格式戳 / 源本身是显示件）—— 前者要重烤，后者不能重烤，
 *  所以还要用 `isBakedPreviewUrl` 把「源本身就是 JPG」那一类摘出去（见
 *  `previewNeedsBake`）。
 *
 *  `onPhase` 在「本次会触发服务端烘焙」时被调用一次（只有这一次，没有百分比）：
 *  首次烘焙要读一遍源图，2.4 万像素级的场景在盘阵上要几秒到几十秒，调用方拿它
 *  更新遮罩文案，别让界面看起来像卡死了。 */
export async function fetchSceneJpg(
  cfg: SrConfig,
  row: SceneRow,
  div: number = loadPreviewDiv(),
  onPhase?: (text: string) => void,
): Promise<Blob> {
  const rp = row.rasterPreview;
  if (rp && rasterPreviewWins(rp, div)) {
    return await fetchRasterPreview(cfg, row, rp, div, onPhase);
  }
  const baked = isBakedPreviewUrl(row.jpgUrl);
  const needBake = previewNeedsBake(row, div);
  // 本地已有一份（同一场景 + 同一档位，见 previewCacheKey）→ 直接用。
  // **命中也要照做网络路径那两行副作用**：下一同会话语义（previewNeedsBake）靠它们，
  // 少了就会出现「盘上明明有这一档的预览，却每次都判成要重烤」。
  const cached = previewBlobs.get(previewCacheKey(row, div));
  if (cached) {
    row.hasPreview = true;
    if (row.jpgUrl) row.previewDiv = div;
    return cached;
  }
  // 会不会**真的**触发服务端烘焙（onPhase 的判据）。与 previewNeedsBake 的差别只在
  // 库外那一支：库外每次都走 /preview，但缓存已在时它是命中、不是烘焙，别吓人。
  const willBake = !row.jpgUrl ? !row.hasPreview
    : (!row.hasPreview || (baked && row.previewDiv !== div));
  if (willBake) {
    onPhase?.(`首次打开：正在服务器烘焙 ${previewDivLabel(div)} 预览图`
      + '（直方图均衡），要读一遍大图，可能要等几十秒…');
  }
  if (!row.jpgUrl) {
    // 库外：没有静态 URL，这次请求的**响应体本身**就是那张 JPEG。
    const p = await http(scenePreviewUrl(cfg, row.id, div));
    row.hasPreview = true;
    return cacheBlob(previewCacheKey(row, div), await p.blob());
  }
  if (needBake) {
    await http(scenePreviewUrl(cfg, row.id, div));   // 只触发烘焙，字节丢掉
    row.hasPreview = true;
    row.previewDiv = div;      // 记上实际档位：同一会话内再打开不必重烤
  }
  const img = await http(sceneImageUrl(cfg, row.jpgUrl, div));
  return cacheBlob(previewCacheKey(row, div), await img.blob());
}

/** 显示件 jpg 的「同名栅格赢」那一支：取**栅格那份**预览（见 rasterPreviewWins）。
 *
 * 请求的还是**这条行自己的 id** —— 后端在 `/preview` 那一层把 jpg 换成同名栅格
 * （落点 `with_suffix(".preview.jpg")` 对 jpg 与 tif 是同一个文件名，也就是栅格行
 * 用的那一份），所以前端不必知道换没换，也不必先取一次栅格的 id、更不必多一次往返。
 *
 * 写回的是 `row.rasterPreview` 自己的 `hasPreview` / `previewDiv`（就地改，下一同
 * 会话语义与栅格行一致），**绝不碰** `row.hasPreview / row.jpgUrl / row.previewDiv`
 * —— 那三个字段说的是源 jpg 自己。碰了会让「源是显示件」这一判定漂移：用户再从
 * 场景库打开这个场景就会跳过懒生成、去打一个 404 的静态 URL（fetchDropSceneJpg
 * 那段注释记的就是同一个坑）。 */
async function fetchRasterPreview(
  cfg: SrConfig,
  row: SceneRow,
  rp: RasterPreview,
  div: number,
  onPhase?: (text: string) => void,
): Promise<Blob> {
  const needBake = !rp.hasPreview || rp.previewDiv !== div;
  // 键里带 `rp.name`：这一支端上来的是**栅格**那份预览，与源 jpg 那份不是同一串字节
  // （见 previewCacheKey）。
  const key = previewCacheKey(row, div, rp);
  const cached = previewBlobs.get(key);
  if (cached) {
    rp.hasPreview = true;
    rp.previewDiv = div;
    return cached;
  }
  if (needBake) {
    onPhase?.(`首次打开：正在服务器从同名栅格 ${rp.name} 烘焙 `
      + `${previewDivLabel(div)} 预览图（直方图均衡），要读一遍大图，`
      + '可能要等几十秒…');
  }
  if (!rp.jpgUrl) {
    // 库外栅格：没有静态 URL，这次请求的**响应体本身**就是那张 JPEG。
    const p = await http(scenePreviewUrl(cfg, row.id, div));
    rp.hasPreview = true;
    rp.previewDiv = div;
    return cacheBlob(key, await p.blob());
  }
  if (needBake) {
    await http(scenePreviewUrl(cfg, row.id, div));   // 只触发烘焙，字节丢掉
    rp.hasPreview = true;
    rp.previewDiv = div;
  }
  const img = await http(sceneImageUrl(cfg, rp.jpgUrl, div));
  return cacheBlob(key, await img.blob());
}

/** 拖入链取预览 JPG：GET /api/scenes/{id}/preview-drop?div=N。
 *
 * 产物落**源所在的盘阵场景目录**（`<stem>_preview.jpg`），场景目录不可写时才退回
 * 临时缓存 —— 那时响应带 `X-SR-Preview-Fallback: tmp`，这里据此在 `onPhase` 里
 * 如实带一句，用户就不会以为「明明能打开，怎么说没落盘阵」。
 *
 * 为什么不复用 `fetchSceneJpg`：那个函数会写 `row.previewDiv` / `hasPreview`，而
 * 那些字段的语义锚在**平台自己那份 `<stem>.preview.jpg`** 上（探的就是它）。
 * 被这条链的产物置真之后，用户再从场景库打开同一个场景就会跳过懒生成、直接打一个
 * 404 的静态 URL，图再也出不来。这里一个 SceneRow 字段都不碰。
 *
 * 也不走静态 URL（产物名不在 nginx 的 /disk-array 映射语义里，兜底更是落在临时根），
 * 响应体本身就是那张 JPEG。 */
export async function fetchDropSceneJpg(
  cfg: SrConfig,
  id: string,
  div: number = loadPreviewDiv(),
  onPhase?: (text: string) => void,
): Promise<Blob> {
  onPhase?.(`已关联到盘阵场景：正在服务器烘焙 ${previewDivLabel(div)} 预览图，`
    + '首次要读一遍大图…');
  const r = await http(dropPreviewUrl(cfg, id, div));
  if (r.headers.get('X-SR-Preview-Fallback') === 'tmp') {
    onPhase?.('该场景目录不可写，预览暂时落在服务器临时缓存，次日会清掉。');
  }
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

/* ---------------- 场景内三类图（GET /api/scenes/{id}/siblings） ---------------- */

/** 一类图。字段与 backend/api/app.py 里 `scene_siblings.item()` 一一对应，
 *  **名字也一样**（后端就是这么取的）——两边一起改的时候不容易漏。 */
export interface SceneSibling {
  kind: 'input' | 'product' | 'nosr';
  /** 这一类自己的不透明 id（供 /api/scenes/{id}/preview）。拼不出名字时为 null。 */
  id: string | null;
  name: string | null;
  rel: string | null;
  exists: boolean;
  sizeBytes: number | null;
  mtime: number | null;
  W: number | null;
  H: number | null;
  hasPreview: boolean;
  previewDiv: number | null;
  jpgUrl: string | null;
}

export interface SceneSiblings {
  sceneId: string;
  /** 场景目录绝对路径（= 提交 SR 的 lq_path 语义）。产物的 rec 靠它拿到盘阵关联。 */
  lqPath: string;
  suffix: string | null;
  /** 用到的 suffix 是哪来的：用户断言 / 最近跑过的任务 / 配置缺省。
   *  「按配置猜的名字」与「真跑过的名字」长得一样，不标出来就分不清。 */
  suffixFrom: 'query' | 'task' | 'default' | null;
  /** 服务端急烤用的档位，仅供界面标注；**不参与任何前端决策**。 */
  div: number;
  items: SceneSibling[];
  /** product 那一类试过哪些文件名（一个都没命中时用它说明「试过什么」）。 */
  productCandidates: string[];
}

/** 场景内三类图。**纯只读端点**：永不烘焙、永不写盘、永不列目录。
 *  找不到也是答案（`exists:false` + `productCandidates`），不抛。 */
export async function apiSceneSiblings(
  cfg: SrConfig, sceneId: string, suffix?: string,
): Promise<SceneSiblings> {
  const r = await http(sceneSiblingsUrl(cfg, sceneId, suffix));
  return (await r.json()) as SceneSiblings;
}

/** 把一类图装成 `fetchSceneJpg` 认的**库行**，好复用现有的取图路径。
 *
 *  `fetchSceneJpg` 只读 `id / name / W / H / hasPreview / previewDiv / jpgUrl /
 *  rasterPreview` 这几个字段（烘焙触发、`onPhase` 文案、`previewNeedsBake`、
 *  「栅格赢」判定全在里面），所以补上的 `satellite/sensor/date/size_bytes/fake/
 *  lq_path` 只是为了满足类型，在这条路上不参与任何判断。
 *
 *  `rasterPreview` 恒 null：那是「源是显示件 jpg 且同目录配着同名栅格」才有的东西，
 *  三类图（tif/jpg）都不适用 —— 不看它就走「按自己 id 烤自己那份预览」的正路。
 *
 *  `jpgUrl` 为空（场景不在 SR_SCENES_ROOT 之下，取不到静态 URL）时，
 *  `fetchSceneJpg` 走「响应体本身就是 JPEG」那条支，与手工场景同一条路。 */
export function siblingRow(res: SceneSiblings, item: SceneSibling): SceneRow {
  return {
    id: item.id ?? '',
    name: item.name ?? '',
    satellite: null, sensor: null, date: null,
    size_bytes: item.sizeBytes ?? 0,
    fake: false,
    W: item.W, H: item.H,
    rel: item.rel,
    jpgUrl: item.jpgUrl,
    hasPreview: item.hasPreview,
    previewDiv: item.previewDiv,
    lq_path: res.lqPath,
    rasterPreview: null,
  };
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

/** 把《待修复清单》整份原地写回盘阵：POST /api/qclist/write。
 *
 * 写盘**走后端**（契约见 docs/planning/api-contract.md §3.7）。原先走浏览器的
 * File System Access API，可那个 API 在规范里是 `[SecureContext]` 标的，而真机页面
 * 是 `http://内网IP` —— 在真机上这功能永远点不通，不是偶发。顺带修好编码：
 * 浏览器只有 UTF-8 编码器，GBK 清单以前只能降级成 UTF-8+BOM 写回，现在由后端编。
 *
 * `mtime` 是导入那一刻的 `File.lastModified / 1000`。路径是用户粘的、文件是盘子上的，
 * 两者只有这一处能对上；后端拿它对护栏，盘阵上的清单在导入之后被人改过就拒写。 */
export async function apiWriteQcList(
  cfg: SrConfig,
  body: { path: string; text: string; encoding: 'utf-8' | 'gbk'; mtime?: number },
): Promise<{ path: string; bytes: number; encoding: string }> {
  const r = await http(apiUrl(cfg, '/api/qclist/write'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return (await r.json()) as { path: string; bytes: number; encoding: string };
}

/** 新会话随手一张（ChatPage 顶部用）。 */
export const freshApi = () => loadSrConfig();
