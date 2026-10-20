/**
 * chatImageUrl.test.ts — 聊天图片地址换算
 * ------------------------------------------------------------------
 * 历史消息里存的是平台相对 URL，渲染前要补上 apiBase；而流式回合的乐观消息
 * 用的是 data URL，必须原样放过（不能再拼一次前缀）。
 */
import { describe, it, expect } from 'vitest';
import { chatImageUrl } from '../api.js';

const CFG = { apiBase: '', staticBase: '' };
const CFG_BASE = { apiBase: 'http://127.0.0.1:8000', staticBase: '' };
const REL = '/api/chat/sessions/s1/images/0123456789abcdef0123456789abcdef.jpg';

describe('chatImageUrl', () => {
  it('相对 URL 拼 apiBase', () => {
    expect(chatImageUrl(CFG_BASE, REL)).toBe('http://127.0.0.1:8000' + REL);
  });

  it('apiBase 为空时原样返回（同源反代）', () => {
    expect(chatImageUrl(CFG, REL)).toBe(REL);
  });

  it('data: / blob: / http(s): 原样', () => {
    const data = 'data:image/jpeg;base64,AAAA';
    expect(chatImageUrl(CFG_BASE, data)).toBe(data);
    expect(chatImageUrl(CFG_BASE, 'blob:http://x/1')).toBe('blob:http://x/1');
    expect(chatImageUrl(CFG_BASE, 'http://img/1.jpg')).toBe('http://img/1.jpg');
    expect(chatImageUrl(CFG_BASE, 'https://img/1.jpg')).toBe('https://img/1.jpg');
  });

  it('空串 → 空串（调用方据此不渲染 img）', () => {
    expect(chatImageUrl(CFG_BASE, '')).toBe('');
  });
});
