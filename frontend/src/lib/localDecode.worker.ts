import { LoadControl } from './loadControl';
import { FileSource } from './source';
import { probeImage, isSparseCandidate, parseStrips } from './tifDecode';
import type { CanvasKit, StretchMode } from './tifDecode';
import { decodeOne } from './decode';
import { localPreviewSize } from './local_preview';
import { stretchIncrementally, progressReporter } from './progressivePreview';
import type { PreviewFrame } from './progressivePreview';
import { decodeSparseProgressively } from './sparseProgressive';

const scope = self as unknown as {
  onmessage: ((e: MessageEvent) => void) | null;
  postMessage(message: unknown, transfer?: Transferable[]): void;
};
let control: LoadControl | undefined;
let phaseSequence = 0;
const acknowledgements = new Map<number, () => void>();
scope.onmessage = e => {
  if (e.data.type === 'pause') { control?.pause(); control?.acknowledgePause(); }
  else if (e.data.type === 'resume') control?.resume();
  else if (e.data.type === 'ack') { acknowledgements.get(e.data.sequence)?.(); acknowledgements.delete(e.data.sequence); }
  else if (e.data.type === 'start') void run(e.data).catch(error => {
    scope.postMessage({ type: 'error', message: error instanceof Error ? error.message : String(error) });
  });
};
async function run(input: { file: File; div: number; maxEdge?: number;
                            mode?: StretchMode; paused: boolean }) {
  const started = Date.now();
  const gate = control = new LoadControl();
  if (input.paused) gate.pause();
  gate.subscribe(() => { if (gate.state === 'paused') scope.postMessage({ type: 'paused' }); });
  let lastYield = performance.now();
  const checkpoint = async (stage?: string) => {
    // Give control messages a bounded opportunity to run without adding one
    // timer per sampled row (which would dominate network/local sparse reads).
    if (stage || gate.paused || performance.now() - lastYield >= 8) {
      await new Promise(resolve => setTimeout(resolve, 0));
      lastYield = performance.now();
    }
    if (stage) {
      const sequence = ++phaseSequence;
      await new Promise<void>(resolve => {
        acknowledgements.set(sequence, resolve);
        scope.postMessage({ type: 'phase', stage, sequence });
      });
    }
    await gate.checkpoint(stage);
  };
  await checkpoint('读取 TIFF 属性');
  const probe = await probeImage(input.file);
  await checkpoint('读取影像');
  const kit = {
    createCanvas: (width: number, height: number) => new OffscreenCanvas(width, height),
    createImageData: (data: Uint8ClampedArray, width: number, height: number) =>
      new ImageData(data as Uint8ClampedArray<ArrayBuffer>, width, height),
  } as unknown as CanvasKit;
  const source = new FileSource(input.file, input.file.name, gate);
  const mode = input.mode ?? 'equal';
  const publish = async (frame: PreviewFrame) => {
    await gate.checkpoint();
    const sequence = ++phaseSequence;
    await new Promise<void>(resolve => {
      acknowledgements.set(sequence, resolve);
      scope.postMessage({ type: 'preview', sequence, frame }, [frame.rgba.buffer as ArrayBuffer]);
    });
    await checkpoint();
  };
  const target = localPreviewSize(probe.W, probe.H, probe.spp >= 3 ? 3 : 1, input.div,
                                  input.maxEdge);
  const progress = progressReporter(value => scope.postMessage({ type: 'progress', value }));
  const layout = isSparseCandidate(probe) && Math.max(target.w,target.h) > 1024 ? await parseStrips(source) : null;
  const decoded = layout?.ok
    ? await decodeSparseProgressively(source, layout, probe, kit, input.div, mode, checkpoint,
      progress, publish, input.maxEdge)
    : await decodeOne(source, probe, input.file, kit, {
    previewDiv: input.div, previewMaxEdge: input.maxEdge, checkpoint,
    onOverlay: title => scope.postMessage({ type: 'phase', stage: title }),
    onProgress: progress,
  });
  await checkpoint('拉伸预览');
  const rgba = await stretchIncrementally(decoded.src, decoded.srcw, decoded.srch, decoded.nbands, decoded.stats, mode, decoded.invert, checkpoint);
  // Publish final DN/display before optional export encoding.
  const full = decoded.thumb as unknown as OffscreenCanvas;
  full.getContext('2d')!.putImageData(new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, decoded.srcw, decoded.srch), 0, 0);
  const { thumb: _thumb, ...data } = decoded;
  await checkpoint('装载本地预览');
  const displaySequence = ++phaseSequence;
  await new Promise<void>(resolve => {
    acknowledgements.set(displaySequence, resolve);
    scope.postMessage({ type: 'pixels', sequence: displaySequence, result: { ...data, ms: Math.max(0, Date.now() - started - gate.pausedMilliseconds),
      rgba, paintedMode: mode, layout: probe.layout } }, [decoded.src.buffer as ArrayBuffer, rgba.buffer as ArrayBuffer]);
  });
  await checkpoint('生成本地 JPG');
  // JPEG is a display artifact, not a replacement for DN or selected sampling.
  // 4096 只约束**导出文件**；查看器显示档位按平台设置的 `maxEdge` 另算
  // （主线程把当前上限随 start 消息传进来，worker 不读自己的设置副本）。
  try {
    const scale = Math.min(1, 4096 / Math.max(decoded.srcw, decoded.srch));
    const jpgCanvas = new OffscreenCanvas(Math.max(1, Math.round(decoded.srcw * scale)), Math.max(1, Math.round(decoded.srch * scale)));
    jpgCanvas.getContext('2d')!.drawImage(full, 0, 0, jpgCanvas.width, jpgCanvas.height);
    const jpeg = await jpgCanvas.convertToBlob({ type: 'image/jpeg', quality: .85 });
    await checkpoint();
    scope.postMessage({ type: 'done', jpeg });
  } catch (error) {
    await checkpoint();
    scope.postMessage({ type: 'done', exportError: error instanceof Error ? error.message : String(error) });
  }
}
