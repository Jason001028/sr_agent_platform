/**
 * api.test.ts — 阶段5 API 客户端纯函数（SSE 帧切分/解析 + URL + 类型守卫）
 * ------------------------------------------------------------------
 * 以纯函数为主（stepSse 残片处理 / parseSseEvents 坏帧丢弃 / apiUrl joinBase 行为）；
 * fetchSceneJpg 那组把全局 fetch 打桩，钉的是「走哪条 URL、顺序如何、onPhase 何时响」
 * —— 不碰真实网络。聊天流式 fetch 走浏览器 .e2e 回归覆盖。
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import {
  stepSse, parseSseEvents, apiUrl, sessionsUrl, sessionMessagesUrl,
  queueEventsUrl, fetchSceneJpg, fetchDropSceneJpg, apiResolveScene,
} from '../api.js';
import type { PlatformSseEvent, ChatSseEvent } from '../api.js';
import type { SceneRow } from '../scene.js';

const CFG = { apiBase: '', staticBase: '' };
const CFG_BASE = { apiBase: 'http://127.0.0.1:8000', staticBase: 'http://static:9000' };

describe('stepSse 帧切分', () => {
  it('整段单帧（\n\n 收尾）', () => {
    const { frames, rest } = stepSse('data: {"type":"ping"}\n\n');
    expect(frames).toEqual(['{"type":"ping"}']);
    expect(rest).toBe('');
  });

  it('多帧顺序切分', () => {
    const text = 'data: {"type":"turn_start"}\n\n'
      + 'data: {"type":"assistant","content":"hi"}\n\n';
    const { frames, rest } = stepSse(text);
    expect(frames).toEqual([
      '{"type":"turn_start"}',
      '{"type":"assistant","content":"hi"}',
    ]);
    expect(rest).toBe('');
  });

  it('无收尾空行 → 残片留在 rest（增量喂入）', () => {
    const { frames, rest } = stepSse('data: {"type":"assistant",');
    expect(frames).toEqual([]);
    expect(rest).toBe('data: {"type":"assistant",');

    // 残片 + 补齐收尾 → 上一轮 rest 累积后整帧浮出（tail 从 "content 起，引号完整）
    const tail = '"content":"hi"}\n\n';
    const second = stepSse(rest + tail);
    expect(second.frames).toEqual(['{"type":"assistant","content":"hi"}']);
    expect(second.rest).toBe('');
  });

  it('半帧多段喂入（网络 chunk 任意切）', () => {
    const full = 'data: {"type":"tool_call","name":"run_sr","args":{}}\n\n'
      + 'data: {"type":"tool_result","ok":true}\n\n';
    let buf = '';
    const collected: string[] = [];
    // 模拟三个字节块任意分界：每次喂 7 字节
    for (let i = 0; i < full.length; i += 7) {
      buf += full.slice(i, i + 7);
      const { frames, rest } = stepSse(buf);
      buf = rest;
      for (const f of frames) {
        const ev = JSON.parse(f) as PlatformSseEvent;
        if (ev.type === 'tool_call') collected.push(ev.name);
        else if (ev.type === 'tool_result') collected.push(String(ev.ok));
      }
    }
    expect(collected).toEqual(['run_sr', 'true']);
    expect(buf).toBe('');
  });

  it('事件间带注释行/空行：只收 data 行', () => {
    const { frames } = stepSse(': keepalive\n\ndata: {"type":"ping"}\n\n');
    expect(frames).toEqual(['{"type":"ping"}']);
  });
});

describe('parseSseEvents 解析', () => {
  it('多事件 + 坏帧（非 JSON / 缺 type）丢弃不炸', () => {
    const text = 'data: {"type":"turn_start","run_id":"r1","session_id":"s1"}\n\n'
      + 'data: not json\n\n'
      + 'data: {"nope":1}\n\n'
      + 'data: {"type":"turn_done","content":"ok","error":null}\n\n';
    const evs = parseSseEvents(text);
    expect(evs).toHaveLength(2);
    expect(evs[0]).toEqual({ type: 'turn_start', run_id: 'r1', session_id: 's1' });
    const done = evs[1] as ChatSseEvent;
    expect(done.type).toBe('turn_done');
    if (done.type === 'turn_done') {
      expect(done.content).toBe('ok');
      expect(done.error).toBeNull();
    }
  });

  it('event 类型守卫：能收窄 chat / job_update', () => {
    const job = parseSseEvents(
      'data: {"type":"job_update","task_id":7,"state":"RUNNING","ok":true}\n\n')[0];
    expect(job.type).toBe('job_update');
    const chat = parseSseEvents(
      'data: {"type":"tool_result","name":"search_scenes","ok":true,"error":null}\n\n')[0];
    expect(chat.type).toBe('tool_result');
  });
});

describe('apiUrl 拼接', () => {
  it('同源默认：apiBase 为空 → 直接 /api/…', () => {
    expect(apiUrl(CFG, '/api/queue')).toBe('/api/queue');
    expect(sessionsUrl(CFG)).toBe('/api/chat/sessions');
    expect(queueEventsUrl(CFG)).toBe('/api/queue/events');
  });

  it('异源注入：joinBase 去重斜杠', () => {
    expect(apiUrl(CFG_BASE, '/api/queue'))
      .toBe('http://127.0.0.1:8000/api/queue');
    expect(sessionMessagesUrl(CFG_BASE, 'abc123'))
      .toBe('http://127.0.0.1:8000/api/chat/sessions/abc123/messages');
  });

  it('session id 含特殊字符安全进 URL', () => {
    expect(sessionMessagesUrl(CFG, 'a/b c+d=='))
      .toBe('/api/chat/sessions/a%2Fb%20c%2Bd%3D%3D/messages');
  });
});

/* ---------------- fetchSceneJpg 取字节 + 首次烘焙回调 ---------------- */
describe('fetchSceneJpg', () => {
  /** 一条最小可用的场景行；W/H 是元数据尺寸，与 JPG 像素尺寸无关。 */
  const row = (over: Partial<SceneRow>): SceneRow => ({
    id: '~YWJj', name: 'GF07A03', satellite: null, sensor: null, date: null,
    size_bytes: 0, fake: false, W: 200, H: 100, rel: null,
    jpgUrl: null, hasPreview: false, lq_path: null, ...over,
  });
  /** 平台烤出来的那份预览（`.preview.jpg` 是判据，见 isBakedPreviewUrl）。 */
  const BAKED = '/disk-array/a/b.preview.jpg';

  let urls: string[];
  /** 打桩 fetch：记下每个 URL，按 URL 返回对应字节；非 2xx 交给 http() 抛。 */
  function stubFetch(bodies: Record<string, string>): void {
    urls = [];
    vi.stubGlobal('fetch', (u: string) => {
      urls.push(u);
      if (!(u in bodies)) return Promise.resolve(new Response('nope', { status: 404 }));
      return Promise.resolve(new Response(bodies[u], { status: 200 }));
    });
  }

  afterEach(() => { vi.unstubAllGlobals(); });

  it('库外场景（jpgUrl 为 null）：/preview 的响应体就是图，不再打静态 URL', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview?div=2': 'PREVIEW' });
    const r = row({ jpgUrl: null, hasPreview: false });
    expect(await (await fetchSceneJpg(CFG, r, 2)).text()).toBe('PREVIEW');
    expect(urls).toEqual(['/api/scenes/~YWJj/preview?div=2']);
    expect(r.hasPreview).toBe(true);          // 取到手就记上，下次不再当首次
  });

  it('库行未生成：先打 /preview?div=N 懒生成，再静态取图（两次请求，顺序固定）', async () => {
    // 那张图由 nginx 直出（几 MB 起，不该占 API 进程的内存/带宽），所以烘焙那一下
    // 是「白打」的 —— 稳态下只有一次（档位对得上那条走上面的分支）。
    stubFetch({
      'http://127.0.0.1:8000/api/scenes/~YWJj/preview?div=8': 'ok',
      'http://static:9000/disk-array/a/b.preview.jpg?div=8': 'STATIC',
    });
    const r = row({ jpgUrl: BAKED, hasPreview: false, previewDiv: null });
    expect(await (await fetchSceneJpg(CFG_BASE, r, 8)).text()).toBe('STATIC');
    expect(urls).toEqual([
      'http://127.0.0.1:8000/api/scenes/~YWJj/preview?div=8',
      'http://static:9000/disk-array/a/b.preview.jpg?div=8',
    ]);
    expect(r.hasPreview).toBe(true);
    expect(r.previewDiv).toBe(8);             // 记上实际档位，同一会话内不再重烤
  });

  it('库行档位对得上：只取静态 jpgUrl（带 ?div= 击穿 nginx 的 max-age）', async () => {
    stubFetch({ 'http://static:9000/disk-array/a/b.preview.jpg?div=4': 'STATIC' });
    const r = row({ jpgUrl: BAKED, hasPreview: true, previewDiv: 4 });
    expect(await (await fetchSceneJpg(CFG_BASE, r, 4)).text()).toBe('STATIC');
    expect(urls).toEqual([
      'http://static:9000/disk-array/a/b.preview.jpg?div=4',
    ]);
  });

  it('**换档位后必须重烤**：盘上那份仍在（hasPreview 为真）但档位不符', async () => {
    // 只看 hasPreview 的话这里会跳过重烤、直接取静态 URL —— 界面滑了，盘上那张图
    // 一个字节都不变，用户看到的还是旧档位。这是这条子逻辑的核心。
    stubFetch({
      'http://127.0.0.1:8000/api/scenes/~YWJj/preview?div=16': 'ok',
      'http://static:9000/disk-array/a/b.preview.jpg?div=16': 'REBAKED',
    });
    const r = row({ jpgUrl: BAKED, hasPreview: true, previewDiv: 4 });
    expect(await (await fetchSceneJpg(CFG_BASE, r, 16)).text()).toBe('REBAKED');
    expect(urls).toEqual([
      'http://127.0.0.1:8000/api/scenes/~YWJj/preview?div=16',
      // `?div=16` 就是这里的要害：不带它，nginx 的 max-age=3600 会把旧档位那张
      // 端上来，重烤了也看不见。
      'http://static:9000/disk-array/a/b.preview.jpg?div=16',
    ]);
    expect(r.previewDiv).toBe(16);
  });

  it('旧格式戳（previewDiv 为 null）也算「档位不符」→ 重烤一轮', async () => {
    stubFetch({
      '/api/scenes/~YWJj/preview?div=4': 'ok',
      '/disk-array/a/b.preview.jpg?div=4': 'REBAKED',
    });
    const r = row({ jpgUrl: BAKED, hasPreview: true, previewDiv: null });
    expect(await (await fetchSceneJpg(CFG, r, 4)).text()).toBe('REBAKED');
    expect(urls).toEqual(['/api/scenes/~YWJj/preview?div=4',
      '/disk-array/a/b.preview.jpg?div=4']);
  });

  it('**源本身就是显示件**（jpgUrl 不是 .preview.jpg）：档位对它无意义，永不重烤', async () => {
    // 这类行 hasPreview 恒 true、previewDiv 恒 null —— 若一并按「档位不符」判，
    // 每次打开都会白打一次 /preview，而它只会把源文件原样回一遍。
    stubFetch({ 'http://static:9000/disk-array/a/b.jpg': 'SOURCE' });
    const r = row({ jpgUrl: '/disk-array/a/b.jpg', hasPreview: true,
      previewDiv: null });
    expect(await (await fetchSceneJpg(CFG_BASE, r, 8)).text()).toBe('SOURCE');
    expect(urls).toEqual(['http://static:9000/disk-array/a/b.jpg']);
  });

  it('onPhase 只在「本次会触发服务端烘焙」时响一次', async () => {
    const phase = vi.fn();
    stubFetch({
      '/api/scenes/~YWJj/preview?div=4': 'ok',
      '/disk-array/a/b.preview.jpg?div=4': 'STATIC',
    });
    // 档位不符 → 一定会先打 /preview（重烤），提示用户等
    await fetchSceneJpg(CFG, row({ jpgUrl: BAKED, hasPreview: true,
      previewDiv: 2 }), 4, phase);
    expect(phase).toHaveBeenCalledTimes(1);
    expect(phase.mock.calls[0][0]).toContain('首次打开');
    expect(phase.mock.calls[0][0]).toContain('1/4');

    // 档位对得上 → 响都不该响，否则每次打开都吓人一跳
    phase.mockClear();
    await fetchSceneJpg(CFG, row({ jpgUrl: BAKED, hasPreview: true,
      previewDiv: 4 }), 4, phase);
    expect(phase).not.toHaveBeenCalled();
  });

  it('onPhase 对库外那条只在**缓存不在**时响（命中不该说「正在烘焙」）', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview?div=4': 'HIT' });
    const phase = vi.fn();
    const r = row({ jpgUrl: null, hasPreview: true });
    expect(await (await fetchSceneJpg(CFG, r, 4, phase)).text()).toBe('HIT');
    expect(phase).not.toHaveBeenCalled();

    phase.mockClear();
    await fetchSceneJpg(CFG, row({ jpgUrl: null, hasPreview: false }), 4, phase);
    expect(phase).toHaveBeenCalledTimes(1);
  });

  it('onPhase 是可选的（旧调用方不传也不炸）', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview?div=4': 'PREVIEW' });
    expect(await (await fetchSceneJpg(CFG, row({}), 4)).text()).toBe('PREVIEW');
  });

  /* ---------------- 显示源比较规则：同名栅格赢的那一支 ---------------- */

  /** 一条「源是显示件 jpg、但同名栅格更清晰」的行。
   *
   * 尺寸取 e2e 夹具那组：栅格 1600×800 ÷4 = 400 > 显示件长边 320 → 栅格赢。
   * 换成 24000/8192 的真机量级则 ÷4 输（6000 < 8192）—— 那正是默认档位下
   * 这条规则基本不触发的原因，见 scene.test.ts 的 rasterPreviewWins 一组。 */
  const rasterRow = (over: Partial<SceneRow> = {}): SceneRow => row({
    jpgUrl: '/disk-array/a/PAN.jpg', hasPreview: true, previewDiv: null,
    rasterPreview: {
      id: 'raster-id', name: 'PAN.tif', rel: 'a/PAN.tif',
      jpgUrl: '/disk-array/a/PAN.preview.jpg',
      rasterW: 1600, rasterH: 800, jpgW: 320, jpgH: 160,
      hasPreview: false, previewDiv: null,
    },
    ...over,
  });

  it('栅格赢：先 /preview 烤栅格那份，再静态取图；**源 jpg 的三个字段一字不动**', async () => {
    // 请求用的是**这条行自己的 id** —— 后端在 /preview 那一层把 jpg 换成同名栅格，
    // 前端不必先取一次栅格的 id、更不必多一次往返。
    stubFetch({
      '/api/scenes/~YWJj/preview?div=4': 'ok',
      '/disk-array/a/PAN.preview.jpg?div=4': 'RASTER',
    });
    const r = rasterRow();
    expect(await (await fetchSceneJpg(CFG, r, 4)).text()).toBe('RASTER');
    expect(urls).toEqual(['/api/scenes/~YWJj/preview?div=4',
      '/disk-array/a/PAN.preview.jpg?div=4']);
    // 栅格那份预览的记账：就地改，语义与栅格行一致
    expect(r.rasterPreview!.hasPreview).toBe(true);
    expect(r.rasterPreview!.previewDiv).toBe(4);
    // 源 jpg 自己那三个字段必须原封不动：碰了会让「源是显示件」这一判定漂移，
    // 用户下次从场景库打开就会跳过懒生成、去打一个 404 的静态 URL。
    expect(r.hasPreview).toBe(true);
    expect(r.jpgUrl).toBe('/disk-array/a/PAN.jpg');
    expect(r.previewDiv).toBeNull();
  });

  it('栅格赢但那份预览已在且档位对得上：只取静态 URL，一次请求', async () => {
    stubFetch({ '/disk-array/a/PAN.preview.jpg?div=4': 'RASTER' });
    const r = rasterRow();
    r.rasterPreview!.hasPreview = true;
    r.rasterPreview!.previewDiv = 4;
    expect(await (await fetchSceneJpg(CFG, r, 4)).text()).toBe('RASTER');
    expect(urls).toEqual(['/disk-array/a/PAN.preview.jpg?div=4']);
  });

  it('栅格赢且档位不符：重烤（同「换档位必须重烤」那条，只是换成栅格那份记账）', async () => {
    // 尺寸得挑成「÷4 与 ÷8 都赢」：**谁赢本身就跟档位有关**（24000 源对 320 的
    // 显示件，÷4 赢、÷32 输），所以换档位有可能直接翻到源 jpg 那一支去。
    // 判据每个档位各算一次，不缓存、不跨档位沿用。
    stubFetch({
      '/api/scenes/~YWJj/preview?div=8': 'ok',
      '/disk-array/a/PAN.preview.jpg?div=8': 'REBAKED',
    });
    const r = rasterRow();
    Object.assign(r.rasterPreview!, { rasterW: 24000, rasterH: 24000,
      jpgW: 2000, jpgH: 1000 });
    r.rasterPreview!.hasPreview = true;
    r.rasterPreview!.previewDiv = 4;
    expect(await (await fetchSceneJpg(CFG, r, 8)).text()).toBe('REBAKED');
    expect(urls).toEqual(['/api/scenes/~YWJj/preview?div=8',
      '/disk-array/a/PAN.preview.jpg?div=8']);
    expect(r.rasterPreview!.previewDiv).toBe(8);

    // 同一行滑到 ÷32：round(24000/32)=750 < 1000 → 栅格输，落回源 jpg（行为同今天）
    stubFetch({ '/disk-array/a/PAN.jpg': 'SOURCE' });
    expect(await (await fetchSceneJpg(CFG, r, 32)).text()).toBe('SOURCE');
    expect(urls).toEqual(['/disk-array/a/PAN.jpg']);
  });

  it('库外栅格（rp.jpgUrl 为 null）：/preview 的响应体就是图，不再打静态 URL', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview?div=4': 'RASTER' });
    const r = rasterRow();
    r.rasterPreview!.jpgUrl = null;
    r.rasterPreview!.rel = null;
    expect(await (await fetchSceneJpg(CFG, r, 4)).text()).toBe('RASTER');
    expect(urls).toEqual(['/api/scenes/~YWJj/preview?div=4']);
    expect(r.rasterPreview!.hasPreview).toBe(true);
  });

  it('栅格**输**（÷4：6000 < 8192）→ 调用序列与今天逐字节一致', async () => {
    // 真机量级那一组：规则不触发时才谈得上「行为不变」。这条是回归钉子 ——
    // 现有 e2e 夹具在 ÷4 下全判 jpg 赢，所以它们的断言一个字都不用改。
    stubFetch({ '/disk-array/a/PAN.jpg': 'SOURCE' });
    const r = rasterRow({
      rasterPreview: {
        id: 'raster-id', name: 'PAN.tif', rel: 'a/PAN.tif',
        jpgUrl: '/disk-array/a/PAN.preview.jpg',
        rasterW: 6000, rasterH: 6000, jpgW: 8192, jpgH: 8192,
        hasPreview: false, previewDiv: null,
      },
    });
    expect(await (await fetchSceneJpg(CFG, r, 4)).text()).toBe('SOURCE');
    expect(urls).toEqual(['/disk-array/a/PAN.jpg']);
    expect(r.rasterPreview!.hasPreview).toBe(false);   // 碰都没碰
  });

  it('栅格赢时的 onPhase 说清「从哪张栅格烤」，且只在真要烤时响', async () => {
    const phase = vi.fn();
    stubFetch({
      '/api/scenes/~YWJj/preview?div=4': 'ok',
      '/disk-array/a/PAN.preview.jpg?div=4': 'RASTER',
    });
    await fetchSceneJpg(CFG, rasterRow(), 4, phase);
    expect(phase).toHaveBeenCalledTimes(1);
    expect(phase.mock.calls[0][0]).toContain('PAN.tif');
    expect(phase.mock.calls[0][0]).toContain('1/4');

    phase.mockClear();
    const hit = rasterRow();
    hit.rasterPreview!.hasPreview = true;
    hit.rasterPreview!.previewDiv = 4;
    await fetchSceneJpg(CFG, hit, 4, phase);
    expect(phase).not.toHaveBeenCalled();
  });
});

/* ---------------- 拖入入口：落盘阵的预览 + resolve 双指纹 ---------------- */
describe('fetchDropSceneJpg', () => {
  const row = (over: Partial<SceneRow>): SceneRow => ({
    id: '~YWJj', name: 'SC', satellite: null, sensor: null, date: null,
    size_bytes: 0, fake: false, W: 200, H: 100, rel: null,
    jpgUrl: null, hasPreview: false, lq_path: null, ...over,
  });

  let urls: string[];
  function stubFetch(bodies: Record<string, string>,
                     headers: Record<string, string> = {}): void {
    urls = [];
    vi.stubGlobal('fetch', (u: string) => {
      urls.push(u);
      if (!(u in bodies)) return Promise.resolve(new Response('nope', { status: 404 }));
      return Promise.resolve(new Response(bodies[u], { status: 200,
        headers }));
    });
  }

  afterEach(() => { vi.unstubAllGlobals(); });

  it('打的是 /preview-drop（不是生产那条 /preview），并带上档位', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview-drop?div=16': 'DROP' });
    expect(await (await fetchDropSceneJpg(CFG, '~YWJj', 16)).text()).toBe('DROP');
    expect(urls).toEqual(['/api/scenes/~YWJj/preview-drop?div=16']);
  });

  it('**绝不**改写 row 的 hasPreview / previewDiv（那两个字段锚在生产那份缓存上）', async () => {
    // 被这条链的产物置真之后，用户再从场景库打开同一场景就会跳过懒生成、直接打
    // 一个 404 的静态 URL，图再也出不来 —— 所以这条是硬约束。
    stubFetch({ '/api/scenes/~YWJj/preview-drop?div=2': 'DROP' });
    const r = row({ jpgUrl: '/disk-array/a/b.preview.jpg', hasPreview: false,
      previewDiv: 8 });
    await fetchDropSceneJpg(CFG, r.id, 2);
    expect(r.hasPreview).toBe(false);
    expect(r.previewDiv).toBe(8);
    expect(r.jpgUrl).toBe('/disk-array/a/b.preview.jpg');
  });

  it('onPhase 每次都提示要等服务端烘焙（这条链不保证缓存命中）', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview-drop?div=2': 'DROP' });
    const phase = vi.fn();
    await fetchDropSceneJpg(CFG, '~YWJj', 2, phase);
    expect(phase).toHaveBeenCalledTimes(1);
    expect(phase.mock.calls[0][0]).toContain('烘焙');
  });

  it('认兜底响应头：场景目录不可写时如实说明走的是临时缓存', async () => {
    stubFetch({ '/api/scenes/~YWJj/preview-drop?div=2': 'DROP' },
      { 'X-SR-Preview-Fallback': 'tmp' });
    const phase = vi.fn();
    expect(await (await fetchDropSceneJpg(CFG, '~YWJj', 2, phase)).text())
      .toBe('DROP');
    expect(phase).toHaveBeenCalledTimes(2);          // 烘焙提示 + 兜底说明
    expect(phase.mock.calls[1][0]).toContain('不可写');
  });
});

describe('apiResolveScene 双指纹透传', () => {
  let bodies: string[];
  beforeEach(() => {
    bodies = [];
    vi.stubGlobal('fetch', (_u: string, init: RequestInit) => {
      bodies.push(String(init.body));
      return Promise.resolve(new Response(JSON.stringify({ source: 'manual' }),
        { status: 200 }));
    });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('size_bytes 原样进 body（服务端靠它 + 名字判同一文件）', async () => {
    await apiResolveScene(CFG, { name: 'SC.tif', size_bytes: 2560256 });
    expect(JSON.parse(bodies[0])).toEqual({ name: 'SC.tif', size_bytes: 2560256 });
  });

  it('不给 size_bytes 就不出现在 body 里（粘路径那条老调用不受影响）', async () => {
    await apiResolveScene(CFG, { path: 'W:\\a\\b' });
    expect(JSON.parse(bodies[0])).toEqual({ path: 'W:\\a\\b' });
  });
});
