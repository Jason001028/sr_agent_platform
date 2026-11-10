/** One loading operation owns its pause gate, cancellation and UI lifetime. */
export type LoadState = 'running' | 'pausing' | 'paused' | 'stopped';
export class LoadControl {
  readonly aborter = new AbortController();
  get signal() { return this.aborter.signal; }
  paused = false;
  stage = '';
  state: LoadState = 'running';
  private pauseStarted = 0;
  private pauseTotal = 0;
  get pausedMilliseconds() { return this.pauseTotal + (this.pauseStarted ? Date.now() - this.pauseStarted : 0); }
  private listeners = new Set<() => void>();
  subscribe(fn: () => void) { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; }
  private notify() { for (const fn of this.listeners) fn(); }
  pause() { if (!this.signal.aborted && !this.paused) { this.paused = true; this.state = 'pausing'; this.notify(); } }
  resume() { if (!this.signal.aborted) { if (this.pauseStarted) this.pauseTotal += Date.now() - this.pauseStarted; this.pauseStarted = 0; this.paused = false; this.state = 'running'; this.notify(); } }
  stop() { this.state = 'stopped'; this.aborter.abort(); this.notify(); }
  throwIfStopped() { if (this.signal.aborted) throw new DOMException('加载已停止', 'AbortError'); }
  acknowledgePause() { if (this.paused && !this.signal.aborted && this.state !== 'paused') { this.pauseStarted = Date.now(); this.state = 'paused'; this.notify(); } }
  async checkpoint(stage?: string) {
    this.throwIfStopped();
    if (stage && stage !== this.stage) { this.stage = stage; this.notify(); }
    if (this.paused) {
      this.acknowledgePause();
      await new Promise<void>((resolve, reject) => {
        const off = this.subscribe(() => {
          if (this.signal.aborted) { off(); reject(new DOMException('加载已停止', 'AbortError')); }
          else if (!this.paused) { off(); resolve(); }
        });
      });
    }
    this.throwIfStopped();
  }
  async wait<T>(promise: Promise<T>, stage?: string): Promise<T> {
    // Attach rejection handlers immediately, even if already paused.
    const result = promise.then(value => ({ value }), error => ({ error }));
    await this.checkpoint(stage);
    let abort!: () => void;
    const stopped = new Promise<never>((_, reject) => {
      abort = () => reject(new DOMException('加载已停止', 'AbortError'));
      this.signal.addEventListener('abort', abort, { once: true });
      if (this.signal.aborted) abort();
    });
    try {
      const settled = await Promise.race([result, stopped]);
      await this.checkpoint(stage);
      if ('error' in settled) throw settled.error;
      return settled.value;
    } finally { this.signal.removeEventListener('abort', abort); }
  }
  /** Pause detaches this client's fetch. Resume reuses the server's keyed job
   * or completed cache, rather than pretending to suspend shared server work. */
  async request<T>(run: (signal: AbortSignal) => Promise<T>, stage: string): Promise<T> {
    for (;;) {
      await this.checkpoint(stage);
      const attempt = new AbortController();
      let pausedRequest = false;
      const off = this.subscribe(() => {
        if (this.paused || this.signal.aborted) {
          pausedRequest = this.paused && !this.signal.aborted;
          attempt.abort();
        }
      });
      try { return await this.wait(run(attempt.signal), stage); }
      catch (error) {
        this.throwIfStopped();
        if (!pausedRequest) throw error;
        await this.checkpoint(stage);
      } finally { off(); }
    }
  }
}
export function isLoadAbort(error: unknown): boolean {
  return (error as { name?: string } | null)?.name === 'AbortError';
}
