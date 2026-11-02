import { describe, it, expect } from 'vitest';
import { localPreviewSize, sourceFingerprint } from '../local_preview.js';
import { decodeOne, sparseDecode } from '../decode.js';
import { probeImage } from '../tifDecode.js';
import { FileSource } from '../source.js';
import { readFileSync } from 'node:fs';

describe('local TIFF preview', () => {
  it('applies selected levels in real TIFF chunked and sparse decoding', async () => {
    const raw = readFileSync(new URL('../../../fixtures/bigtiff_strips.tif', import.meta.url));
    const file = new Blob([new Uint8Array(raw)]);
    const source = new FileSource(file, 'test.tif');
    const probe = await probeImage(file);
    const kit = {
      createCanvas: (width: number, height: number) => ({ width, height, getContext: () => null }),
      createImageData: (data: Uint8ClampedArray, width: number, height: number) => ({ data, width, height }),
    };
    for (const div of [2, 4]) {
      const chunked = await decodeOne(source, probe, file, kit, { previewDiv: div });
      const sparse = await sparseDecode(source, probe, kit, { previewDiv: div });
      expect(chunked.srcw).toBe(Math.round(probe.W / div));
      expect(sparse.srcw).toBe(chunked.srcw);
      expect(sparse.srch).toBe(chunked.srch);
      expect(chunked.src.length).toBe(chunked.srcw * chunked.srch);
    }
  });
  it('changes source sampling with the selected level', () => {
    expect(localPreviewSize(4096, 2048, 1, 4)).toEqual({ w: 1024, h: 512 });
    expect(localPreviewSize(4096, 2048, 1, 2)).toEqual({ w: 2048, h: 1024 });
  });
  it('bounds huge and multiband sources by canvas and float buffer limits', () => {
    for (const bands of [1, 3, 4]) {
      const { w, h } = localPreviewSize(100000, 80000, bands, 2);
      expect(Math.max(w, h)).toBeLessThanOrEqual(8192);
      expect(w * h * bands * 4).toBeLessThanOrEqual(256 * 1024 * 1024);
      expect(w / h).toBeCloseTo(1.25, 3);
    }
  });
  it('samples only bounded parts and distinguishes same-size files', async () => {
    const bytes = new Uint8Array(20000);
    const a = new File([bytes], 'same.tif');
    bytes[10000] = 1;
    const b = new File([bytes], 'same.tif');
    const first = await sourceFingerprint(a), second = await sourceFingerprint(b);
    expect(first.name).toBe(second.name);
    expect(first.size).toBe(second.size);
    expect(first.samples[0]).toBe(second.samples[0]);
    expect(first.samples[1]).not.toBe(second.samples[1]);
    expect(first.samples.every(s => atob(s).length <= 4096)).toBe(true);
  });
});
