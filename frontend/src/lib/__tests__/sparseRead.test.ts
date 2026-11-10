import { expect, it } from 'vitest';
import { sparseSample, type SparseLayoutOk } from '../tifDecode';
import type { Source } from '../source';

function layout(W: number, H: number, bits = 16, sf = 1, le = true, gap = 0): SparseLayoutOk {
  const bpp = bits / 8, rowBytes = W * bpp;
  return { ok: true, W, H, bits, sf, le, bpp, rowBytes, rps: 1, strips: H,
    offs: Array.from({ length: H }, (_, i) => 16 + i * (rowBytes + gap)), lens: Array(H).fill(rowBytes) };
}
function data(sp: SparseLayoutOk) {
  const bytes = new Uint8Array(Math.max(...sp.offs) + sp.rowBytes);
  const view = new DataView(bytes.buffer);
  for (let y = 0; y < sp.H; y++) for (let x = 0; x < sp.W; x++) {
    const offset = sp.offs[y] + x * sp.bpp, value = y * 31 + x;
    if (sp.bpp === 1) view.setInt8(offset, value - 128);
    else if (sp.bpp === 2) view.setInt16(offset, value - 1000, sp.le);
    else if (sp.sf === 3) view.setFloat32(offset, value / 4 - 50, sp.le);
    else view.setInt32(offset, value - 1000, sp.le);
  }
  return bytes;
}
function source(bytes: Uint8Array) {
  const reads: [number, number][] = [];
  const src: Source = { size: bytes.length, async read(offset, length) {
    reads.push([offset, length]);
    return bytes.slice(offset, offset + length).buffer;
  } };
  return { src, reads };
}

it('coalesces contiguous rows without reading extra bytes or changing any pixel', async () => {
  const sp = layout(256, 256), bytes = data(sp), { src, reads } = source(bytes);
  const progress: number[] = [];
  const result = await sparseSample(src, sp, 256, f => progress.push(f));
  expect(reads).toHaveLength(1); // Previously 256 separate Blob/network reads.
  expect(result.bytesRead).toBe(256 * 256 * 2);
  const expected = new Uint16Array(bytes.buffer, 16);
  expect([...result.src]).toEqual([...expected]);
  expect(progress.at(-1)).toBe(1);
});

for (const bits of [8, 16, 32]) for (const le of [true, false]) for (const sf of (bits === 32 ? [1, 2, 3] : [1, 2])) {
  it(`preserves endian and sample types: bits=${bits}, le=${le}, sf=${sf}`, async () => {
    const sp = layout(31, 17, bits, sf, le, 1);
    // Deliberately non-monotone file offsets and unaligned rows.
    sp.offs.reverse();
    const bytes = data(sp), { src } = source(bytes), dv = new DataView(bytes.buffer);
    const result = await sparseSample(src, sp, 13);
    for (let y = 0; y < result.sh; y++) for (let x = 0; x < result.sw; x++) {
      const sy = Math.round(y * (sp.H - 1) / (result.sh - 1));
      const sx = Math.round(x * (sp.W - 1) / (result.sw - 1));
      const at = sp.offs[sy] + sx * sp.bpp;
      const expected = bits === 8 ? (sf === 2 ? dv.getInt8(at) : dv.getUint8(at))
        : bits === 16 ? (sf === 2 ? dv.getInt16(at, le) : dv.getUint16(at, le))
        : sf === 3 ? dv.getFloat32(at, le) : sf === 2 ? dv.getInt32(at, le) : dv.getUint32(at, le);
      expect(result.src[y * result.sw + x]).toBe(Math.fround(expected));
    }
  });
}

it('does not fetch wide gaps, keeps reads bounded and reports monotonic progress', async () => {
  const sp = layout(64, 12, 16, 1, true, 128 * 1024);
  const { src, reads } = source(data(sp));
  const progress: number[] = [];
  const result = await sparseSample(src, sp, 64, f => progress.push(f));
  expect(reads).toHaveLength(12);
  expect(result.bytesRead).toBe(12 * sp.rowBytes);
  expect(progress).toEqual([...progress].sort((a, b) => a - b));
  expect(progress.at(-1)).toBe(1);
});

it('rejects truncated sampled rows rather than silently showing corrupted pixels', async () => {
  const sp = layout(64, 2), { src } = source(data(sp).slice(0, -1));
  await expect(sparseSample(src, sp, 64)).rejects.toThrow('truncated');
});

it('replenishes workers while one network read is stalled, with at most eight reads in flight', async () => {
  const sp = layout(32, 24, 16, 1, true, 128 * 1024), bytes = data(sp);
  let release!: () => void;
  const stalled = new Promise<void>(resolve => { release = resolve; });
  let started = 0, active = 0, peak = 0;
  const src: Source = { size: bytes.length, async read(offset, length) {
    const index = started++;
    peak = Math.max(peak, ++active);
    if (index === 0) await stalled;
    else await Promise.resolve();
    active--;
    return bytes.slice(offset, offset + length).buffer;
  } };
  const pending = sparseSample(src, sp, 32);
  for (let i = 0; i < 50; i++) await Promise.resolve();
  expect(started).toBe(24); // Old batch barrier would stall at eight.
  expect(peak).toBeLessThanOrEqual(8);
  release();
  expect((await pending).stripsTouched).toBe(24);
});

it('limits each combined read to 1 MiB for a full-resolution export', async () => {
  const sp = layout(1024, 1024), { src, reads } = source(data(sp));
  const result = await sparseSample(src, sp, 1024);
  expect(reads).toHaveLength(2);
  expect(Math.max(...reads.map(([, length]) => length))).toBeLessThanOrEqual(1024 * 1024);
  expect(result.bytesRead).toBe(sp.W * sp.H * sp.bpp);
});

it('keeps float NaN and infinity values for downstream statistics filtering', async () => {
  const sp = layout(3, 1, 32, 3, false), bytes = data(sp), dv = new DataView(bytes.buffer);
  dv.setFloat32(16, NaN, false); dv.setFloat32(20, Infinity, false); dv.setFloat32(24, -Infinity, false);
  const { src } = source(bytes);
  const result = await sparseSample(src, sp, 3);
  expect(Number.isNaN(result.src[0])).toBe(true);
  expect(result.src[1]).toBe(Infinity); expect(result.src[2]).toBe(-Infinity);
});
