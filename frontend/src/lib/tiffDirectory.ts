import type { Source } from './source';

/** Read a bounded IFD by file offsets, including BigTIFF IFDs after 4 GiB.
 * Never allocate the gap between the header, IFD and external value arrays. */
export async function readTiffDirectory(source: Source) {
  const read = async (offset: number, length: number) => {
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(length) || offset < 0 || length < 0
      || length > 64 * 1024 * 1024 || offset + length > source.size) throw new Error('TIFF directory range is invalid');
    const bytes = await source.read(offset, length);
    if (bytes.byteLength < length) throw new Error('TIFF directory is truncated');
    return new DataView(bytes);
  };
  if (source.size < 8) return null;
  const head = await read(0, Math.min(16, source.size));
  const le = head.getUint16(0) === 0x4949;
  if (!le && head.getUint16(0) !== 0x4d4d) return null;
  const uint = (view: DataView, offset: number, size: number) => {
    const value = size === 1 ? view.getUint8(offset) : size === 2 ? view.getUint16(offset, le)
      : size === 4 ? view.getUint32(offset, le) : Number(view.getBigUint64(offset, le));
    if (!Number.isSafeInteger(value)) throw new Error('TIFF integer exceeds safe offset range');
    return value;
  };
  const magic = uint(head, 2, 2), big = magic === 43;
  if (magic !== 42 && !big) return null;
  if (big && (head.byteLength < 16 || uint(head, 4, 2) !== 8 || uint(head, 6, 2) !== 0)) throw new Error('Invalid BigTIFF header');
  const offset = uint(head, big ? 8 : 4, big ? 8 : 4), countSize = big ? 8 : 2, entrySize = big ? 20 : 12;
  const count = uint(await read(offset, countSize), 0, countSize);
  if (count > 4096) throw new Error('TIFF directory has too many entries');
  const table = await read(offset + countSize, count * entrySize);
  const entries = new Map<number, { type: number; count: number; at: number }>();
  for (let i = 0; i < count; i++) {
    const start = i * entrySize;
    entries.set(uint(table, start, 2), { type: uint(table, start + 2, 2), count: uint(table, start + 4, big ? 8 : 4), at: start + (big ? 12 : 8) });
  }
  const sizes: Record<number, number> = { 1: 1, 3: 2, 4: 4, 13: 4, 16: 8, 18: 8 };
  const values = async (tag: number, n = 1): Promise<number[] | null> => {
    const entry = entries.get(tag), size = entry && sizes[entry.type];
    if (!entry || !size || n > entry.count || n < 0 || n * size > 64 * 1024 * 1024) return null;
    const inline = entry.count * size <= (big ? 8 : 4);
    const view = inline ? table : await read(uint(table, entry.at, big ? 8 : 4), n * size);
    const base = inline ? entry.at : 0;
    return Array.from({ length: n }, (_, i) => uint(view, base + i * size, size));
  };
  const scalar = async (tag: number, fallback: number) => (await values(tag))?.[0] ?? fallback;
  return { le, entries, values, scalar };
}
