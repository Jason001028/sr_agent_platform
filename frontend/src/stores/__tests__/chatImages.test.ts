/**
 * chatImages.test.ts — 聊天截图的待发托盘与发送路径（chat store 实例）
 * ------------------------------------------------------------------
 * 断言四件事：
 *  1. 托盘上限 2、可增删；
 *  2. `init` / `loadHistory` **不**清托盘（否则「查看器加好图 → 去 /chat」会被抹掉），
 *     只有切到别的会话 / 当前会话被删才清；
 *  3. 纯图（空话术）允许发送，请求体带 `images`，发送即清托盘；
 *  4. 历史投影把 `images` 映射进展示消息，`loadHistory` 一次换算 URL。
 */
import { beforeEach, describe, expect, it, vi, afterEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useChatStore } from '../chat.js';
import { msgFromHistory } from '../chat.js';

const shot = (name: string) => ({ dataUrl: `data:image/jpeg;base64,${name}`, name });

function sseResponse(frames: string[]): Response {
  return new Response(frames.map((f) => `data: ${f}\n\n`).join(''), { status: 200 });
}

/** /messages 与 /usage 的 GET 打桩（loadHistory 会依次调这两个）。 */
function stubHistory(messages: unknown[] = []): void {
  vi.stubGlobal('fetch', (u: string) => {
    if (u.endsWith('/usage')) {
      return Promise.resolve(new Response(JSON.stringify({ usage: null }), { status: 200 }));
    }
    return Promise.resolve(new Response(JSON.stringify({ messages }), { status: 200 }));
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('待发截图托盘', () => {
  it('最多 2 张，第 3 张返回 false 且不入列', () => {
    const chat = useChatStore();
    expect(chat.addPendingImage(shot('a'))).toBe(true);
    expect(chat.addPendingImage(shot('b'))).toBe(true);
    expect(chat.addPendingImage(shot('c'))).toBe(false);
    expect(chat.pendingImages.map((p) => p.name)).toEqual(['a', 'b']);
  });

  it('按 id 删除；清空', () => {
    const chat = useChatStore();
    chat.addPendingImage(shot('a'));
    chat.addPendingImage(shot('b'));
    const first = chat.pendingImages[0].id;
    chat.removePendingImage(first);
    expect(chat.pendingImages.map((p) => p.name)).toEqual(['b']);
    chat.clearPendingImages();
    expect(chat.pendingImages).toEqual([]);
  });

  it('loadHistory 不清托盘（查看器加图 → 去 /chat 的路径）', async () => {
    const chat = useChatStore();
    chat.addPendingImage(shot('a'));
    stubHistory();
    await chat.loadHistory('s1');
    expect(chat.pendingImages).toHaveLength(1);
  });

  it('切到别的会话才清；切回同一会话不清', async () => {
    const chat = useChatStore();
    chat.addPendingImage(shot('a'));
    stubHistory();
    chat.sessionId = 's1';
    await chat.selectSession('s1');            // 同一会话：保留
    expect(chat.pendingImages).toHaveLength(1);
    await chat.selectSession('s2');            // 换会话：清空
    expect(chat.pendingImages).toEqual([]);
  });
});

describe('发送', () => {
  it('纯图消息：请求体带 images、乐观气泡带 images、托盘清空', async () => {
    const chat = useChatStore();
    chat.sessionId = 's1';
    chat.addPendingImage(shot('a'));
    chat.addPendingImage(shot('b'));
    let body: { content: string; images?: { data: string; name: string }[] } | null = null;
    vi.stubGlobal('fetch', (_u: string, init: RequestInit) => {
      body = JSON.parse(String(init.body));
      return Promise.resolve(sseResponse(['{"type":"turn_done","content":"收到"}']));
    });

    await chat.send('');

    expect(body!.content).toBe('');
    expect(body!.images).toEqual([
      { data: 'data:image/jpeg;base64,a', name: 'a' },
      { data: 'data:image/jpeg;base64,b', name: 'b' },
    ]);
    const user = chat.messages.find((m) => m.role === 'user');
    expect(user?.content).toBe('');
    expect(user?.images?.map((i) => i.name)).toEqual(['a', 'b']);
    expect(chat.pendingImages).toEqual([]);
  });

  it('无文字又无图 → 不发请求', async () => {
    const chat = useChatStore();
    chat.sessionId = 's1';
    const fetchStub = vi.fn();
    vi.stubGlobal('fetch', fetchStub);
    await chat.send('   ');
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it('无图时不带 images，viewer_context 是空观察（没有当前影像/工作区条目）', async () => {
    const chat = useChatStore();
    chat.sessionId = 's1';
    let body: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', (_u: string, init: RequestInit) => {
      body = JSON.parse(String(init.body));
      return Promise.resolve(sseResponse(['{"type":"turn_done","content":"ok"}']));
    });
    await chat.send('你好');
    expect(body!.content).toBe('你好');
    expect(body!.images).toBeUndefined();
    expect(body!.stream).toBeUndefined();
    const ctx = body!.viewer_context as Record<string, unknown>;
    expect(ctx.current).toBeNull();
    expect(ctx.displayed).toEqual([]);
    expect(ctx.selected_ids).toEqual([]);
    expect((ctx.workspace as Record<string, unknown>).items).toEqual([]);
    expect(ctx.attachment_count).toBe(0);
  });
});

describe('历史投影', () => {
  it('msgFromHistory 把 images 映射进展示消息；老行没有 images → null', () => {
    const withImg = msgFromHistory({
      seq: 1, role: 'user', content: '',
      images: [{ url: '/api/chat/sessions/s1/images/0123456789abcdef0123456789abcdef.jpg', name: 'GF07' }],
    });
    expect(withImg.images).toEqual([{
      url: '/api/chat/sessions/s1/images/0123456789abcdef0123456789abcdef.jpg',
      name: 'GF07',
    }]);
    expect(msgFromHistory({ seq: 2, role: 'user', content: 'hi' }).images).toBeNull();
  });

  it('loadHistory 后 user 消息带着可用的图片 src', async () => {
    const chat = useChatStore();
    const url = '/api/chat/sessions/s1/images/0123456789abcdef0123456789abcdef.jpg';
    stubHistory([{ seq: 1, role: 'user', content: '', images: [{ url, name: 'GF07' }] }]);
    await chat.loadHistory('s1');
    expect(chat.messages[0].images?.[0]).toEqual({ url, name: 'GF07' });
  });
});


it.each(['fast', 'low', 'balanced', 'deep'] as const)('sends thinking profile %s', async (profile) => {
  const chat = useChatStore();
  expect(chat.thinkingProfile).toBe('balanced');
  chat.sessionId = 's1';
  chat.thinkingProfile = profile;
  let body: Record<string, unknown> = {};
  vi.stubGlobal('fetch', (_url: string, init?: RequestInit) => {
    body = JSON.parse(String(init?.body));
    return Promise.resolve(sseResponse(['{"type":"turn_done","ok":true}']));
  });
  await chat.send('hello');
  expect(body.thinking_profile).toBe(profile);
  expect(chat.thinkingProfile).toBe(profile);
});

it('allows changing the next profile while preserving an in-flight request', async () => {
  const chat = useChatStore();
  chat.sessionId = 's1';
  let finish!: (response: Response) => void;
  let body: Record<string, unknown> = {};
  vi.stubGlobal('fetch', (_url: string, init?: RequestInit) => {
    body = JSON.parse(String(init?.body));
    return new Promise<Response>(resolve => { finish = resolve; });
  });
  const request = chat.send('hello');
  expect(chat.sending).toBe(true);
  chat.thinkingProfile = 'deep';
  expect(body.thinking_profile).toBe('balanced');
  finish(sseResponse(['{"type":"turn_done","ok":true}']));
  await request;
  expect(chat.thinkingProfile).toBe('deep');
});
