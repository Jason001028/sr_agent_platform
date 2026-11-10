import { expect, it } from 'vitest';
import { parseStrips, sparseSample, tiffTags } from '../tifDecode';
import type { Source } from '../source';

function distantDirectory(big: boolean, le: boolean) {
  const root = big ? 2 ** 32 + 128 : 4 * 1024 * 1024;
  const pieces: { at: number; bytes: Uint8Array }[] = [];
  const part = (at: number, size: number) => { const bytes = new Uint8Array(size); pieces.push({ at, bytes }); return new DataView(bytes.buffer); };
  const put = (v: DataView, at: number, size: number, n: number) => size === 8 ? v.setBigUint64(at, BigInt(n), le)
    : size === 4 ? v.setUint32(at, n, le) : v.setUint16(at, n, le);
  const header = part(0, 16); header.setUint16(0, le ? 0x4949 : 0x4d4d);
  put(header, 2, 2, big ? 43 : 42);
  if (big) { put(header, 4, 2, 8); put(header, 8, 8, root); } else put(header, 4, 4, root);
  const tags = [[256, 4], [257, 2], [258, 8], [259, 1], [262, 1], [277, 1], [278, 1], [273, root+1024], [279, root+1056]];
  const countSize = big ? 8 : 2, entrySize = big ? 20 : 12;
  const table = part(root, countSize + tags.length * entrySize); put(table, 0, countSize, tags.length);
  tags.forEach(([tag, value], i) => {
    const at = countSize+i*entrySize, array = tag === 273 || tag === 279;
    put(table, at, 2, tag); put(table, at+2, 2, big && array ? 16 : 4);
    put(table, at+4, big ? 8 : 4, array ? 2 : 1); put(table, at+(big ? 12 : 8), big && array ? 8 : 4, value);
  });
  const size = big ? 8 : 4;
  const offsets = part(root+1024, 2*size), lengths = part(root+1056, 2*size);
  for (let i = 0; i < 2; i++) { put(offsets, i*size, size, 64+i*4); put(lengths, i*size, size, 4); }
  const pixels = part(64, 8); for (let i = 0; i < 8; i++) pixels.setUint8(i, i+10);
  const reads: number[] = [];
  const source: Source = { size: root + 2048, async read(at, length) {
    reads.push(length); expect(length).toBeLessThan(128*1024);
    const bytes = new Uint8Array(length);
    for (const piece of pieces) {
      const start = Math.max(at, piece.at), end = Math.min(at+length, piece.at+piece.bytes.length);
      if (end > start) bytes.set(piece.bytes.subarray(start-piece.at, end-piece.at), start-at);
    }
    return bytes.buffer;
  } };
  return { source, reads };
}
for (const big of [false, true]) for (const le of [false, true]) {
  it(`reads distant IFD and external arrays without reading the gap (big=${big}, le=${le})`, async () => {
    const { source, reads } = distantDirectory(big, le);
    const tags = await tiffTags(source); expect(tags[256]).toBe(4); expect(tags[262]).toBe(1);
    const layout = await parseStrips(source); expect(layout.ok).toBe(true);
    if (!layout.ok) throw Error('layout missing');
    expect(layout.offs).toEqual([64, 68]);
    const result = await sparseSample(source, layout, 4); expect([...result.src]).toEqual([10,11,12,13,14,15,16,17]);
    expect(reads.reduce((a,b) => a+b, 0)).toBeLessThan(4096);
  });
}
