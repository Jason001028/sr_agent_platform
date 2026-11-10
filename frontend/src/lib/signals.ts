/**
 * signals.ts — AbortSignal.timeout / AbortSignal.any 的兜底实现
 * ------------------------------------------------------------------
 * 内网机上的旧 Chromium（timeout 需 103+，any 需 116+）没有这两个静态方法。
 * 调用点都在 fetch 之前求值，缺一个就当场抛 TypeError —— 请求一个字节都发不
 * 出去，错误又被各处的 catch 吞成一句"没关联上"，最后直接退回上传。
 * 2026-11-10 现场确认：同事机器上 `AbortSignal.timeout is not a function`，
 * /api/scenes/resolve 用 performance.getEntriesByType('resource') 查是空集合。
 *
 * 这里只在**缺失时**安装，不覆盖浏览器原生实现。入口在 main.ts 最先调用一次。
 */

/** 原生 @types 里这两个是只读静态方法，安装时要按可变对象写。 */
type SignalStatics = {
  timeout?: (milliseconds: number) => AbortSignal;
  any?: (signals: AbortSignal[]) => AbortSignal;
};

/** 与原生 AbortSignal.timeout 同形：到点后以 name='TimeoutError' 的 DOMException 中止。 */
export function timeoutSignal(milliseconds: number): AbortSignal {
  const controller = new AbortController();
  setTimeout(() => {
    controller.abort(new DOMException('signal timed out', 'TimeoutError'));
  }, milliseconds);
  return controller.signal;
}

/**
 * 与原生 AbortSignal.any 同形：任意一个输入中止，输出立即中止并带上同一个 reason；
 * 已经中止的输入会让输出当场中止。输入为空时返回一个永不中止的信号。
 */
export function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  const detach: (() => void)[] = [];
  const stop = (reason: unknown) => {
    for (const off of detach.splice(0)) off();
    controller.abort(reason);
  };
  for (const signal of signals) {
    if (controller.signal.aborted) break;
    if (signal.aborted) {
      stop(signal.reason);
      break;
    }
    const onAbort = () => stop(signal.reason);
    signal.addEventListener('abort', onAbort, { once: true });
    detach.push(() => signal.removeEventListener('abort', onAbort));
  }
  return controller.signal;
}

/** 幂等：已有原生实现就不动，缺哪个补哪个。 */
export function installAbortSignalPolyfills(): void {
  const statics = AbortSignal as unknown as SignalStatics;
  if (typeof statics.timeout !== 'function') statics.timeout = timeoutSignal;
  if (typeof statics.any !== 'function') statics.any = anySignal;
}
