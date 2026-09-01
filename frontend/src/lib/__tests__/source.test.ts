/**
 * source.test.ts — 双数据源抽象契约
 * ------------------------------------------------------------------
 * FileSource：blob.slice 语义（越界 clamp 到实际可得部分）；HttpSource 在
 * Phase 4 接后端端点前必须抛「未实现」，防止在未接端点前被误用。
 */
import { describe, it, expect } from 'vitest';
import { FileSource, HttpSource, readHead } from '../source.js';

describe('FileSource', () => {
  it('read 前缀', async () => {
    const buf = new Uint8Array([1, 2, 3, 4, 5]);
    const src = new FileSource(new Blob([buf]), 't.bin');
    expect(src.size).toBe(5);
    expect(src.name).toBe('t.bin');
    const head = await readHead(src, 3);
    expect([...head]).toEqual([1, 2, 3]);
  });

  it('read 越界 clamp 到实际可得部分', async () => {
    const buf = new Uint8Array([1, 2, 3]);
    const src = new FileSource(new Blob([buf]));
    const d = await src.read(2, 10);
    expect([...new Uint8Array(d)]).toEqual([3]);
  });

  it('read 全文件等于 blob', async () => {
    const buf = new Uint8Array([9, 8, 7, 6]);
    const src = new FileSource(new Blob([buf]));
    const d = await src.read(0, src.size);
    expect([...new Uint8Array(d)]).toEqual([9, 8, 7, 6]);
  });
});

describe('HttpSource', () => {
  it('Phase 4 前 read 抛「未实现」', async () => {
    const src = new HttpSource('http://example.com/y.tif', 100, 'y.tif');
    expect(src.size).toBe(100);
    await expect(src.read(0, 10)).rejects.toThrow(/尚未实现/);
  });
});
