import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearDeveloperMode, isDeveloperMode, verifyDeveloperPassword,
} from '../developerMode.js';

function storage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    removeItem: (key: string) => { data.delete(key); },
  };
}

describe('开发者模式验证', () => {
  beforeEach(() => vi.stubGlobal('localStorage', storage()));

  it('密码错误不授权，正确密码按机器保存一次', () => {
    expect(verifyDeveloperPassword('wrong', 'node-a')).toBe(false);
    expect(isDeveloperMode('node-a')).toBe(false);
    expect(verifyDeveloperPassword('cgwx', 'node-a')).toBe(true);
    expect(isDeveloperMode('node-a')).toBe(true);
    expect(isDeveloperMode('node-b')).toBe(false);
  });

  it('可以关闭当前机器的授权', () => {
    verifyDeveloperPassword('cgwx', 'node-a');
    clearDeveloperMode('node-a');
    expect(isDeveloperMode('node-a')).toBe(false);
  });
});
