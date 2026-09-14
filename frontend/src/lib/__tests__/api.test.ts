/**
 * api.test.ts — 阶段5 API 客户端纯函数（SSE 帧切分/解析 + URL + 类型守卫）
 * ------------------------------------------------------------------
 * Node 环境（无 DOM，不 fetch）：只测纯函数 —— stepSse 残片处理 / parseSseEvents
 * 坏帧丢弃 / apiUrl joinBase 行为。聊天流式 fetch 走浏览器 .e2e 回归覆盖。
 */
import { describe, it, expect } from 'vitest';
import {
  stepSse, parseSseEvents, apiUrl, sessionsUrl, sessionMessagesUrl,
  queueEventsUrl,
} from '../api.js';
import type { PlatformSseEvent, ChatSseEvent } from '../api.js';

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
