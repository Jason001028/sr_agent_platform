// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { anySignal, installAbortSignalPolyfills, timeoutSignal } from '../signals';

describe('AbortSignal 兜底实现', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('timeoutSignal 到点中止，且 reason 的 name 是 TimeoutError', () => {
    const signal = timeoutSignal(20000);
    expect(signal.aborted).toBe(false);
    vi.advanceTimersByTime(19999);
    expect(signal.aborted).toBe(false);
    vi.advanceTimersByTime(1);
    expect(signal.aborted).toBe(true);
    expect((signal.reason as DOMException).name).toBe('TimeoutError');
  });

  it('anySignal 跟随第一个中止的输入，并保留 reason', () => {
    const a = new AbortController();
    const b = new AbortController();
    const any = anySignal([a.signal, b.signal]);
    expect(any.aborted).toBe(false);
    const reason = new DOMException('加载已停止', 'AbortError');
    b.abort(reason);
    expect(any.aborted).toBe(true);
    expect(any.reason).toBe(reason);
  });

  it('anySignal 遇到已经中止的输入时立刻中止', () => {
    const done = new AbortController();
    done.abort(new DOMException('x', 'AbortError'));
    const any = anySignal([new AbortController().signal, done.signal]);
    expect(any.aborted).toBe(true);
    expect((any.reason as DOMException).name).toBe('AbortError');
  });

  it('anySignal 输入为空时永不中止', () => {
    const any = anySignal([]);
    expect(any.aborted).toBe(false);
  });

  it('安装是幂等的，有原生实现时不覆盖', () => {
    const nativeTimeout = AbortSignal.timeout;
    const nativeAny = AbortSignal.any;
    installAbortSignalPolyfills();
    installAbortSignalPolyfills();
    if (nativeTimeout) expect(AbortSignal.timeout).toBe(nativeTimeout);
    if (nativeAny) expect(AbortSignal.any).toBe(nativeAny);
    expect(typeof AbortSignal.timeout).toBe('function');
    expect(typeof AbortSignal.any).toBe('function');
  });
});
