import { expect, it, vi } from 'vitest';
import { LoadControl } from '../loadControl';
import { FileSource } from '../source';
import { sparseSample, type SparseLayoutOk } from '../tifDecode';

it('pauses at a checkpoint and resumes the same operation without restarting', async () => {
  const control = new LoadControl();
  control.pause();
  let complete = false;
  const pending = control.checkpoint('sampling').then(() => { complete = true; });
  await Promise.resolve();
  expect(control.state).toBe('paused'); expect(complete).toBe(false);
  control.resume(); await pending;
  expect(complete).toBe(true); expect(control.stage).toBe('sampling');
});
it('stops a paused operation and observes late I/O rejections', async () => {
  const control = new LoadControl();
  control.pause();
  let fail!: (e: Error) => void;
  const io = new Promise<never>((_, reject) => { fail = reject; });
  const pending = control.wait(io);
  const assertion = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  control.stop(); fail(new Error('late I/O failure'));
  await assertion;
});
it('does not finish a native operation while paused, and cancels waiting immediately', async () => {
  const control = new LoadControl();
  let finish!: (value: number) => void;
  const io = new Promise<number>(resolve => { finish = resolve; });
  const pending = control.wait(io, 'download');
  control.pause(); finish(42);
  for (let i = 0; i < 6; i++) await Promise.resolve();
  expect(control.state).toBe('paused');
  control.resume(); expect(await pending).toBe(42);
});
it('does not dispatch local reads while paused', async () => {
  vi.stubGlobal('FileReader', undefined);
  try {
    const control = new LoadControl(); control.pause();
    const blob = new Blob([new Uint8Array([1, 2, 3])]);
    const slice = vi.spyOn(blob, 'slice');
    const source = new FileSource(blob, 'a.tif', control);
    const pending = source.read(0, 2);
    await Promise.resolve(); expect(slice).not.toHaveBeenCalled();
    control.resume(); expect([...new Uint8Array(await pending)]).toEqual([1, 2]);
  } finally { vi.unstubAllGlobals(); }
});
it('holds sparse workers before new reads and resumes with identical pixels', async () => {
  const control = new LoadControl(); control.pause();
  const sp: SparseLayoutOk = { ok: true, W: 16, H: 16, bits: 8, sf: 1, le: true, bpp: 1,
    rps: 1, rowBytes: 16, strips: 16, offs: Array.from({ length: 16 }, (_, i) => 16 + i * 131072), lens: Array(16).fill(16) };
  const read = vi.fn(async (_offset: number, length: number) => new Uint8Array(length).fill(7).buffer);
  const pending = sparseSample({ size: 3e6, read }, sp, 16, undefined, () => control.checkpoint());
  await Promise.resolve(); expect(read).not.toHaveBeenCalled();
  control.resume();
  const result = await pending;
  expect(read).toHaveBeenCalledTimes(16); expect(result.src.every(v => v === 7)).toBe(true);
});
it('pauses a client download and resumes through a fresh request without treating pause as failure', async () => {
  const control = new LoadControl();
  const signals: AbortSignal[] = [];
  let finish!: (value: string) => void;
  const run = vi.fn((signal: AbortSignal) => {
    signals.push(signal);
    return new Promise<string>((resolve, reject) => {
      finish = resolve;
      signal.addEventListener('abort', () => reject(new DOMException('detached', 'AbortError')));
    });
  });
  const pending = control.request(run, 'preview');
  for (let i = 0; i < 5; i++) await Promise.resolve();
  expect(run).toHaveBeenCalledTimes(1);
  control.pause();
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(signals[0].aborted).toBe(true); expect(control.state).toBe('paused');
  expect(run).toHaveBeenCalledTimes(1);
  control.resume();
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(run).toHaveBeenCalledTimes(2);
  finish('cached JPEG'); expect(await pending).toBe('cached JPEG');
});
