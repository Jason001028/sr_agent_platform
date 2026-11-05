import { describe, expect, it } from 'vitest';
import {
  ANALYSIS_MAX_EDGE, commonGrid, gridCoverageToThumb, gridPointToThumb,
  gridRectToSource, gridRectToThumb, jointRange, quantizeToRgba, sampleGray,
  thumbRectToGrid,
} from '../diffLocate';
import type { DiffResult } from '../diffLocate';

function result(patch: Partial<DiffResult['analysisGrid']> = {}, mode: 'preview' | 'source' = 'preview'): DiffResult {
  return {
    operation: 'difference-localization',
    method: 'absdiff',
    source: mode === 'source' ? 'source-roi' : 'preview',
    imageA: {
      id: 1, name: 'A', sourceWidth: 1000, sourceHeight: 500,
      thumbWidth: 200, thumbHeight: 100, gridWidth: 100, gridHeight: 50,
    },
    imageB: {
      id: 2, name: 'B', sourceWidth: 1000, sourceHeight: 500,
      thumbWidth: 200, thumbHeight: 100, gridWidth: 100, gridHeight: 50,
    },
    analysisGrid: {
      width: 100, height: 50, origin: [0, 0], scaleA: [1, 1], scaleB: [1, 1], mode, ...patch,
    },
    differenceMap: '', binaryMask: '', regions: [], truncated: false,
    coverage: { validPixels: 0, gridPixels: 0, validRatio: 0 },
    parameters: {
      method: 'absdiff', sensitivity: 'standard', threshold: 0.12, minAreaPx: 64,
      blur: true, openKernel: 3, closeKernel: 5, ssimWindow: 7, ssimGaussian: false,
    },
    warnings: [], elapsedMs: 0,
  };
}

describe('commonGrid', () => {
  it('refuses mismatched aspect ratios', () => {
    expect(commonGrid(1000, 500, 500, 1000).refusal).not.toBe('');
  });

  it('caps the long edge at the analysis limit', () => {
    const plan = commonGrid(40000, 20000, 40000, 20000);
    expect(plan.width).toBe(ANALYSIS_MAX_EDGE);
    expect(plan.height).toBe(ANALYSIS_MAX_EDGE / 2);
  });

  it('uses the longer of the two images and keeps the aspect', () => {
    const plan = commonGrid(400, 200, 800, 400);
    expect(plan.width).toBe(800);
    expect(plan.height).toBe(400);
  });

  it('refuses unknown dimensions', () => {
    expect(commonGrid(0, 0, 100, 100).refusal).not.toBe('');
  });
});

describe('sampleGray', () => {
  it('passes a same-size constant image through', () => {
    const src = new Float32Array([1, 2, 3, 4]);
    const { gray, valid } = sampleGray(src, 2, 2, 2, 2);
    expect(Array.from(gray)).toEqual([1, 2, 3, 4]);
    expect(Array.from(valid)).toEqual([1, 1, 1, 1]);
  });

  it('marks NoData as invalid and leaves gray at zero', () => {
    const src = new Float32Array([1, NaN, 3, 4]);
    const { gray, valid } = sampleGray(src, 2, 2, 2, 2);
    expect(valid[1]).toBe(0);
    expect(gray[1]).toBe(0);
    expect(valid[0]).toBe(1);
  });

  it('upsamples with bilinear interpolation', () => {
    const { gray } = sampleGray(new Float32Array([0, 1, 0, 1]), 2, 2, 4, 4);
    expect(gray[0]).toBeCloseTo(0, 5);
    expect(gray[3]).toBeCloseTo(1, 5);
  });
});

describe('shared normalization', () => {
  it('computes one range across both images', () => {
    const a = { gray: new Float32Array([0, 10]), valid: new Uint8Array([1, 1]) };
    const b = { gray: new Float32Array([20, 100]), valid: new Uint8Array([1, 1]) };
    const { lo, hi } = jointRange([a, b]);
    expect(lo).toBeGreaterThanOrEqual(0);
    expect(hi).toBeLessThanOrEqual(100);
    expect(hi).toBeGreaterThan(lo);
  });

  it('ignores pixels valid in only one image', () => {
    // 共同区 A=100、B=110；非共同区只有 A 有效且值为 100000。
    // 范围必须只取共同有效区，否则共同区会被量化成同一个值。
    const a = {
      gray: new Float32Array([100, 101, 102, 103, 104, 105, 100000]),
      valid: new Uint8Array([1, 1, 1, 1, 1, 1, 1]),
    };
    const b = {
      gray: new Float32Array([110, 111, 112, 113, 114, 115, -9999]),
      valid: new Uint8Array([1, 1, 1, 1, 1, 1, 0]),
    };
    const { lo, hi } = jointRange([a, b]);
    expect(hi).toBeLessThanOrEqual(115);   // 非共同区的 100000 不参与
    expect(lo).toBeGreaterThanOrEqual(100);
    const qa = quantizeToRgba(a.gray, a.valid, lo, hi);
    const qb = quantizeToRgba(b.gray, b.valid, lo, hi);
    expect(qa[0]).not.toBe(qa[5 * 4]);     // 共同区 A 的灰度差仍可区分
    expect(qb[0]).not.toBe(qb[5 * 4]);
  });

  it('finds a sparse common-valid block that fixed-stride sampling misses', () => {
    const W = 2048;
    const H = 2048;
    const n = W * H;
    const make = () => {
      const a = { gray: new Float32Array(n), valid: new Uint8Array(n) };
      const b = { gray: new Float32Array(n), valid: new Uint8Array(n) };
      return { a, b };
    };
    const fill = (
      pair: { a: { gray: Float32Array; valid: Uint8Array }; b: { gray: Float32Array; valid: Uint8Array } },
      y0: number, x0: number,
    ) => {
      for (let y = y0; y < y0 + 8; y++) {
        for (let x = x0; x < x0 + 8; x++) {
          const i = y * W + x;
          pair.a.gray[i] = 100; pair.a.valid[i] = 1;
          pair.b.gray[i] = 110; pair.b.valid[i] = 1;
        }
      }
    };
    const first = make();
    fill(first, 100, 100);                 // i=0,41,82… 碰不到这块
    const r1 = jointRange([first.a, first.b]);
    expect(r1.hasData).toBe(true);
    expect(r1.lo).toBe(100);
    expect(r1.hi).toBe(110);
    const idx = (100 * W + 100) * 4;
    const qa = quantizeToRgba(first.a.gray, first.a.valid, r1.lo, r1.hi);
    const qb = quantizeToRgba(first.b.gray, first.b.valid, r1.lo, r1.hi);
    expect(qa[idx]).toBe(0);               // 不再被截成 255
    expect(qb[idx]).toBe(255);

    // 同一块挪到别处，范围必须一致。
    const second = make();
    fill(second, 1500, 1500);
    const r2 = jointRange([second.a, second.b]);
    expect(r2).toEqual({ lo: r1.lo, hi: r1.hi, hasData: true });
  });

  it('reports no common valid pixels explicitly', () => {
    const a = { gray: new Float32Array([100, 200]), valid: new Uint8Array([1, 0]) };
    const b = { gray: new Float32Array([110, 210]), valid: new Uint8Array([0, 1]) };
    expect(jointRange([a, b]).hasData).toBe(false);
  });

  it('quantizes invalid pixels to transparent', () => {
    const rgba = quantizeToRgba(
      new Float32Array([0, 1]), new Uint8Array([0, 1]), 0, 1);
    expect(rgba[3]).toBe(0);
    expect(rgba[7]).toBe(255);
    expect(rgba[4]).toBe(255);
  });
});

describe('coordinate mapping', () => {
  it('maps the full grid to thumb coordinates', () => {
    const r = result();
    expect(gridPointToThumb(r, 50, 25, 200, 100)).toEqual([100, 50]);
    expect(gridRectToThumb(r, [10, 5, 20, 10], 200, 100)).toEqual([20, 10, 40, 20]);
  });

  it('accounts for a cropped analysis scope', () => {
    const r = result({ width: 50, height: 25, fullWidth: 100, fullHeight: 50, scopeX: 40, scopeY: 10 });
    expect(gridPointToThumb(r, 0, 0, 200, 100)).toEqual([80, 20]);
    expect(gridCoverageToThumb(r, 200, 100)).toEqual([80, 20, 100, 50]);
    const back = thumbRectToGrid(r, [80, 20, 60, 25], 200, 100);
    expect(back[0]).toBeCloseTo(0, 9);
    expect(back[1]).toBeCloseTo(0, 9);
    expect(back[2]).toBeCloseTo(30, 9);
    expect(back[3]).toBeCloseTo(12.5, 9);
  });

  it('maps source-ROI results without treating preview pixels as source coordinates', () => {
    const r = result({
      mode: 'source', sourceRoi: [200, 100, 400, 200], imageASource: [1000, 500],
    }, 'source');
    expect(gridPointToThumb(r, 0, 0, 200, 100)).toEqual([40, 20]);
    expect(gridCoverageToThumb(r, 200, 100)).toEqual([40, 20, 80, 40]);
    expect(gridRectToSource(r, [50, 25, 10, 10])).toEqual([400, 200, 40, 40]);
  });

  it('converts preview grid rectangles back to source pixels', () => {
    const r = result();
    expect(gridRectToSource(r, [10, 5, 20, 10])).toEqual([100, 50, 200, 100]);
  });
});
