/**
 * streaming.test.ts — 聊天流式输出的浏览器本地偏好
 * ------------------------------------------------------------------
 * Node 环境没有 localStorage，这里用桩替代。语义：缺省开；写 0 才关；
 * 隐私模式读写抛错时按默认开处理，且写入不把异常抛给调用方。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isStreamingEnabled, setStreamingEnabled } from '../streaming.js';

function fakeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length(): number { return map.size; },
    clear: () => { map.clear(); },
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => { map.delete(k); },
    setItem: (k: string, v: string) => { map.set(k, v); },
  };
}

describe('聊天流式输出偏好', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('缺省开', () => {
    vi.stubGlobal('localStorage', fakeStorage());
    expect(isStreamingEnabled()).toBe(true);
  });

  it('关掉后读回 false，再开恢复 true', () => {
    vi.stubGlobal('localStorage', fakeStorage());
    setStreamingEnabled(false);
    expect(isStreamingEnabled()).toBe(false);
    setStreamingEnabled(true);
    expect(isStreamingEnabled()).toBe(true);
  });

  it('隐私模式读写抛错 → 读按默认开、写不抛给调用方', () => {
    vi.stubGlobal('localStorage', {
      getItem() { throw new Error('denied'); },
      setItem() { throw new Error('denied'); },
    });
    expect(isStreamingEnabled()).toBe(true);
    expect(() => setStreamingEnabled(false)).not.toThrow();
  });
});
