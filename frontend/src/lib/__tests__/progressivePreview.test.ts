import { afterEach, expect, it } from 'vitest';
import { rowPublisher, wholeJpegFits, stretchIncrementally, progressReporter } from '../progressivePreview';
import { applyPreviewTrueMaxEdge, PREVIEW_TRUE_MAX_EDGE } from '../previewLevel';
afterEach(() => { applyPreviewTrueMaxEdge(PREVIEW_TRUE_MAX_EDGE); });
import { computeStats, stretchRgba, chunkedCollect } from '../tifDecode';

it('limits progress traffic while always reporting start and completion', () => {
  let time=0;const values:number[]=[];const progress=progressReporter(value=>values.push(value),()=>time);
  progress(0);
  for(let i=1;i<80;i++){time=i;progress(i/100);}
  time=80;progress(.8);progress(1);
  expect(values).toEqual([0,.8,1]);
});

it('rejects unsafe whole JPEG before downloading without changing the requested level', () => {
  expect(wholeJpegFits(49474, 49454, 2)).toBe(false); // 24737 > 16384
  expect(wholeJpegFits(49474, 49454, 4)).toBe(true);  // 12368 ≤ 16384 且面积 < 16384²
  expect(wholeJpegFits(49474, 49454, 16)).toBe(true);
  expect(wholeJpegFits(49474, 49454, 8)).toBe(true); // previously over-eager local fallback
  expect(wholeJpegFits(16384, 16384, 1)).toBe(true);  // 真分辨率上限边界
  expect(wholeJpegFits(16385, 16384, 1)).toBe(false);
  for (const dimensions of [[NaN, 20, 2], [10, 0, 2], [10, 10, 0], [Infinity, 10, 2]])
    expect(wholeJpegFits(...dimensions as [number, number, number])).toBe(false);
});
it('re-admits whole JPEGs when the platform limit is lowered', () => {
  applyPreviewTrueMaxEdge(4096);
  expect(wholeJpegFits(49474, 49454, 4)).toBe(false); // 12368 > 4096
  expect(wholeJpegFits(49474, 49454, 16)).toBe(true); // 3092 ≤ 4096
  expect(wholeJpegFits(4096, 4096, 1)).toBe(true);
  expect(wholeJpegFits(4097, 4096, 1)).toBe(false);
});
it('best-effort patches never wait for the main thread inside the read callback', async () => {
  const stats = computeStats(new Float32Array([0,255]), 2, 1, 1);
  let release!: () => void, sent = 0;
  const publisher = rowPublisher(1, stats, 'linear', false, async () => {
    sent++; await new Promise<void>(resolve => { release=resolve; });
  }, { bestEffort: true, intervalMs: 0 });
  await publisher.rows(new Float32Array(64),1,64,0);
  await Promise.resolve(); expect(sent).toBe(1);
  // The next read can finish while the receiver has not acknowledged the first.
  await publisher.rows(new Float32Array(64),1,64,64);
  expect(sent).toBe(1);
  release(); await publisher.flush();
});

it('batches sparse rows, shares global stretch and preserves out-of-order row positions', async () => {
  const width = 7, height = 90, values = Float32Array.from({ length: width * height }, (_, i) => i);
  const stats = computeStats(values, width, height, 1), expected = stretchRgba(values, width, height, 1, stats, 'equal', false);
  const actual = new Uint8ClampedArray(expected.length); let packets = 0;
  const publisher = rowPublisher(1, stats, 'equal', false, async (rgba, w, rows, y) => {
    packets++; expect(rgba.length).toBe(w * rows * 4); actual.set(rgba, y * width * 4);
  });
  for (let y = 63; y >= 0; y--) await publisher.rows(values.subarray(y * width, (y + 1) * width), width, 1, y);
  for (let y = 64; y < height; y++) await publisher.rows(values.subarray(y * width, (y + 1) * width), width, 1, y);
  await publisher.flush();
  expect(packets).toBe(2); expect(actual).toEqual(expected);
});

it('waits for the receiver before continuing a full packet', async () => {
  let release!: () => void, received = false, finished = false;
  const values = new Float32Array(128).fill(7), stats = computeStats(values, 2, 64, 1);
  const publisher = rowPublisher(1, stats, 'linear', true, async () => {
    received = true; await new Promise<void>(resolve => { release = resolve; });
  });
  const work = publisher.rows(values, 2, 64, 0).then(() => { finished = true; });
  await Promise.resolve(); await Promise.resolve();
  expect(received).toBe(true); expect(finished).toBe(false);
  release(); await work; expect(finished).toBe(true);
});

it('banded progressive collection keeps the same DN pixels as the original collector', async () => {
  const image = { readRasters: async ({ window: [x0, y0, x1, y1] }: { window: number[] }) =>
    Float32Array.from({ length: (x1-x0)*(y1-y0) }, (_, i) => (y0 + Math.floor(i/(x1-x0))) * 100 + x0 + i%(x1-x0)) };
  const plan = { samples: [0], comps: 1, bits: 32, invert: false };
  const options = [image as never, 31, 19, plan, .5, 16, 10] as const;
  const baseline = await chunkedCollect(...options);
  const assembled = new Float32Array(baseline.length);
  const result = await chunkedCollect(...options, undefined, { bandRows: 3, onRows: async (data, w, h, y) => {
    assembled.set(data, y * w); expect(data.length).toBe(w*h);
  } });
  expect(result).toEqual(baseline); expect(assembled).toEqual(baseline);
});
it('incremental final stretch is byte-identical and checks cancellation between bands', async () => {
  const values = Float32Array.from({ length: 11 * 150 * 3 }, (_, i) => i % 301);
  const stats = computeStats(values, 11, 150, 3); let checkpoints = 0;
  const result = await stretchIncrementally(values, 11, 150, 3, stats, 'equal', true, async () => { checkpoints++; });
  expect(result).toEqual(stretchRgba(values, 11, 150, 3, stats, 'equal', true)); expect(checkpoints).toBe(4);
  let calls = 0;
  await expect(stretchIncrementally(values, 11, 150, 3, stats, 'linear', false, async () => {
    if (++calls === 2) throw new DOMException('stopped', 'AbortError');
  })).rejects.toMatchObject({ name: 'AbortError' });
});
