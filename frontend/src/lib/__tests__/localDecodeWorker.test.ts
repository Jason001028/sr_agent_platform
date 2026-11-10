import { afterEach, expect, it, vi } from 'vitest';
import { decodeLocalFile } from '../localDecodeWorker';
import { LoadControl } from '../loadControl';
import type { PreviewFrame } from '../progressivePreview';
class FakeWorker {
  static latest: FakeWorker;
  constructor() { FakeWorker.latest = this; }
  postMessage = vi.fn(); terminate = vi.fn();
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: { message: string }) => void) | null = null;
  send(data: unknown) { this.onmessage?.({ data }); }
}
const frame: PreviewFrame = { kind: 'overview', W: 2, H: 2, width: 2, height: 2, canvasWidth: 2, canvasHeight: 2,
  y: 0, rows: 2, rgba: new Uint8ClampedArray(16) };
const pixels = () => ({ W: 2, H: 2, src: new Float32Array(4), srcw: 2, srch: 2, nbands: 1, invert: false,
  stats: null, route: 'sparse', ms: 1, rgba: new Uint8ClampedArray(16), paintedMode: 'equal', layout: 'strips' });
afterEach(() => vi.unstubAllGlobals());
it('publishes final pixels before optional JPEG, and export errors preserve those pixels', async () => {
  vi.stubGlobal('Worker', FakeWorker);
  const control = new LoadControl(), display = { preview: vi.fn(), pixels: vi.fn() };
  let completed = false;
  const job = decodeLocalFile(new File(['x'],'a.tif'), 2, 'equal', control, vi.fn(), display).then(value => { completed = true; return value; });
  const worker = FakeWorker.latest;
  worker.send({ type: 'preview', sequence: 1, frame });
  await vi.waitFor(() => expect(display.preview).toHaveBeenCalledWith(frame));
  worker.send({ type: 'pixels', sequence: 2, result: pixels() });
  await vi.waitFor(() => expect(display.pixels).toHaveBeenCalledOnce());
  expect(completed).toBe(false);
  worker.send({ type: 'done', exportError: 'encoder unavailable' });
  expect((await job).exportError).toBe('encoder unavailable'); expect(worker.terminate).toHaveBeenCalledOnce();
});
it('holds frame publication and its acknowledgement while paused, and ignores messages after Stop', async () => {
  vi.stubGlobal('Worker', FakeWorker);
  const control = new LoadControl(), display = { preview: vi.fn(), pixels: vi.fn() };
  const job = decodeLocalFile(new File(['x'],'a.tif'), 2, 'equal', control, vi.fn(), display);
  const stopped = expect(job).rejects.toMatchObject({ name: 'AbortError' });
  const worker = FakeWorker.latest;
  control.pause(); worker.send({ type: 'preview', sequence: 1, frame });
  await vi.waitFor(() => expect(control.state).toBe('paused'));
  expect(display.preview).not.toHaveBeenCalled();
  expect(worker.postMessage).not.toHaveBeenCalledWith({ type: 'ack', sequence: 1 });
  control.resume(); await vi.waitFor(() => expect(display.preview).toHaveBeenCalledOnce());
  control.stop(); await stopped;
  worker.send({ type: 'pixels', sequence: 2, result: pixels() });
  expect(display.pixels).not.toHaveBeenCalled();
});
