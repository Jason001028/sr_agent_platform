import { computeStats, sparseSample, stretchRgba } from './tifDecode';
import type { BandStats, CanvasKit, ProbeInfo, SparseLayoutOk, StretchMode } from './tifDecode';
import type { Source } from './source';
import type { DecodedRec } from './decode';
import { localPreviewSize } from './local_preview';
import { rowPublisher } from './progressivePreview';
import type { PreviewFrame } from './progressivePreview';

/** One sampling pass. Navigation rows are rows of the requested output grid,
 * prioritized within the original coalesced reads, never a second TIFF decode. */
export async function decodeSparseProgressively(source: Source, sp: SparseLayoutOk, probe: ProbeInfo,
  kit: CanvasKit, div: number, mode: StretchMode, checkpoint: (stage?: string) => Promise<void>,
  progress: (fraction: number) => void, publish: (frame: PreviewFrame) => Promise<void>,
  maxEdge?: number): Promise<DecodedRec> {
  const started = performance.now(), target = localPreviewSize(sp.W, sp.H, 1, div, maxEdge);
  const max = Math.max(target.w, target.h), ps = Math.min(1, max / Math.max(sp.W, sp.H));
  const width = Math.max(1, Math.round(sp.W*ps)), height = Math.max(1, Math.round(sp.H*ps));
  const seedCount = Math.min(32, height), seedRows = Array.from({ length: seedCount }, (_, i) =>
    seedCount <= 1 ? 0 : Math.round(i*(height-1)/(seedCount-1)));
  const wanted = new Set(seedRows), seeds = new Map<number, Float32Array>();
  let stats: BandStats[] | undefined, publisher: ReturnType<typeof rowPublisher> | undefined, preparing: Promise<void> | undefined;
  const invert = probe.photometric === 0;
  const prepare = async () => {
    await checkpoint('生成整图概览（复用已读采样）');
    const sw = Math.min(256, width), values = new Float32Array(sw*seedCount);
    for (let y = 0; y < seedCount; y++) for (let x = 0; x < sw; x++) {
      values[y*sw+x] = seeds.get(seedRows[y])![sw <= 1 ? 0 : Math.round(x*(width-1)/(sw-1))];
    }
    stats = computeStats(values, sw, seedCount, 1);
    await publish({ kind: 'overview', W: sp.W, H: sp.H, width: sw, height: seedCount, canvasWidth: width, canvasHeight: height,
      y: 0, rows: seedCount, rgba: stretchRgba(values, sw, seedCount, 1, stats, mode, invert),values,stats,invert,dnDomain:{bits:probe.bits,sampleFormat:probe.sampleFormat} });
    await checkpoint('稀疏条带回填（单次采样）');
    seeds.clear();
    publisher = rowPublisher(1, stats, mode, invert, (rgba,w,rows,y,values) => publish({ kind: 'rows', W: sp.W, H: sp.H,
      width: w, height: rows, canvasWidth: width, canvasHeight: height, rows, y, rgba,values,stats,invert }), { bestEffort: true, intervalMs: 150 });
  };
  await checkpoint('稀疏条带读取（单次采样）');
  const sampled = await sparseSample(source, sp, max, progress, checkpoint, async (values,w,rows,y) => {
    if (publisher) { await publisher.rows(values,w,rows,y); return; }
    for (let i = 0; i < rows; i++) if (wanted.has(y+i)) seeds.set(y+i, values.subarray(i*w,(i+1)*w));
    if (seeds.size === seedCount && !preparing) preparing = prepare();
    if (preparing) await preparing;
    // Already-read navigation rows remain in sampled.src. Intermediate patches
    // may be skipped; the final authoritative image includes every row.
  }, seedRows);
  await preparing;
  await publisher?.flush();
  await checkpoint('统计像素');
  return { W: sp.W, H: sp.H, thumb: kit.createCanvas(sampled.sw,sampled.sh), src: sampled.src,
    srcw: sampled.sw, srch: sampled.sh, nbands: 1, invert,
    stats: stats ?? computeStats(sampled.src,sampled.sw,sampled.sh,1), route: 'sparse', ms: performance.now()-started,
    sparseInfo: `抽样 ${sampled.stripsTouched} 行，读取 ${(sampled.bytesRead/1048576).toFixed(1)} MB（单次采样）` };
}
