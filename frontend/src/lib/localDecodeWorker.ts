import { LoadControl } from './loadControl';
import type { DecodedRec } from './decode';
import type { StretchMode } from './tifDecode';
import type { PreviewFrame } from './progressivePreview';
import { getPreviewTrueMaxEdge } from './previewLevel';

export type LocalPixels = Omit<DecodedRec, 'thumb'> & {
  rgba: Uint8ClampedArray; paintedMode: StretchMode; layout: string;
};
export type LocalDecoded = LocalPixels & { jpeg?: Blob; exportError?: string };

/** A dedicated worker is terminated on Stop; late messages cannot publish. */
export function decodeLocalFile(file: File, div: number, mode: StretchMode | undefined,
  control: LoadControl, progress: (fraction: number) => void,
  display?: { preview: (frame: PreviewFrame) => void; pixels: (result: LocalPixels) => void },
  maxEdge = getPreviewTrueMaxEdge()): Promise<LocalDecoded> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./localDecode.worker.ts', import.meta.url), { type: 'module' });
    let settled = false;
    let pixels: LocalPixels | undefined;
    const finish = (error?: unknown, value?: LocalDecoded) => {
      if (settled) return;
      settled = true; off(); control.signal.removeEventListener('abort', abort);
      worker.terminate();
      if (error) reject(error); else resolve(value!);
    };
    const abort = () => finish(new DOMException('加载已停止', 'AbortError'));
    const off = control.subscribe(() => {
      if (control.signal.aborted) abort();
      else worker.postMessage({ type: control.paused ? 'pause' : 'resume' });
    });
    control.signal.addEventListener('abort', abort, { once: true });
    worker.onerror = e => finish(new Error(e.message || '本地预览工作线程失败'));
    worker.onmessage = e => {
      if (settled) return;
      const message = e.data;
      if (message.type === 'phase') {
        void control.checkpoint(message.stage).then(() => {
          if (!settled && message.sequence !== undefined) worker.postMessage({ type: 'ack', sequence: message.sequence });
        }, abort);
      }
      else if (message.type === 'paused') control.acknowledgePause();
      else if (message.type === 'progress') progress(message.value);
      else if (message.type === 'error') finish(new Error(message.message));
      else if (message.type === 'preview' || message.type === 'pixels') {
        void control.checkpoint().then(() => {
          if (settled) return;
          if (message.type === 'preview') display?.preview(message.frame);
          else { pixels = message.result; display?.pixels(pixels!); }
          if (!settled) worker.postMessage({ type: 'ack', sequence: message.sequence });
        }).catch(error => finish(error));
      }
      else if (message.type === 'done') {
        void control.checkpoint().then(() => {
          if (!pixels) { finish(new Error('解码线程未返回像素')); return; }
          finish(undefined, { ...pixels, jpeg: message.jpeg, exportError: message.exportError });
        }, abort);
      }
    };
    worker.postMessage({ type: 'start', file, div, maxEdge, mode, paused: control.paused });
    if (control.signal.aborted) abort();
  });
}
