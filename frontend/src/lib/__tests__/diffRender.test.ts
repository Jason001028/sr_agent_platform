import { describe, expect, it } from 'vitest';
import {
  colorizeHeatBytes, colorizeHeatPixels, drawDiffSource,
  HEAT_COLORS, NODATA_COLOR, UNANALYZED_COLOR,
} from '../diffRender';

describe('continuous difference rendering', () => {
  it('keeps Smart scores immutable while hiding below-threshold and invalid pixels', () => {
    const values=new Uint8Array([0,128,255]),before=values.slice();
    const rgba=colorizeHeatBytes(values,3,1,new Uint8Array([1,1,0]),null,{smart:true,threshold:.4});
    expect(rgba[3]).toBe(0);expect(rgba[7]).toBe(255);expect(rgba[11]).toBe(0);
    expect(values).toEqual(before);
    const dim=colorizeHeatBytes(values,3,1,null,null,{smart:true,selectedBbox:[1,0,1,1]});
    expect(dim[3]).toBe(85);expect(dim[7]).toBe(255);
  });
  it('maps every fixed stop, including opaque zero, without changing source bytes', () => {
    const values = new Uint8ClampedArray([0, 51, 102, 153, 204, 255].flatMap(v => [v, v, v, 255]));
    const before = values.slice();
    const colors = colorizeHeatPixels(values);
    HEAT_COLORS.forEach((hex, i) => {
      const expected = [1, 3, 5].map(j => parseInt(hex.slice(j, j + 2), 16));
      expect([...colors.slice(i * 4, i * 4 + 4)]).toEqual([...expected, 255]);
    });
    expect(values).toEqual(before);
    expect([...colorizeHeatPixels(new Uint8ClampedArray([25, 25, 25, 255]))]).toEqual([35, 13, 70, 255]);
  });

  it('keeps valid zero dark and distinguishes invalid zero using validity rather than intensity', () => {
    const pixels = new Uint8ClampedArray([0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255, 0]);
    const valid = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255, 255, 255, 255, 255]);
    expect([...colorizeHeatPixels(pixels, valid)]).toEqual([11, 11, 29, 255, 0, 0, 0, 0, 0, 0, 0, 0]);
  });

  it('multiplies intensity by the per-pixel weight before the color table', () => {
    // 权重字节 255/85/170 = 1.8×/0.8×/1.3×，乘 100 后正好是 180/80/130。
    const intensity = new Uint8Array([100, 100, 100]);
    const weights = new Uint8Array([255, 85, 170]);
    const got = colorizeHeatBytes(intensity, 3, 1, null, weights);
    const expected = colorizeHeatBytes(new Uint8Array([180, 80, 130]), 3, 1);
    expect([...got]).toEqual([...expected]);
  });

  it('clamps weighted intensity above 1 to the hottest stop', () => {
    const got = colorizeHeatBytes(new Uint8Array([200]), 1, 1, null, new Uint8Array([255]));
    const hottest = colorizeHeatBytes(new Uint8Array([255]), 1, 1);
    expect([...got]).toEqual([...hottest]);
  });

  it('keeps NoData transparent with or without weights', () => {
    const valid = new Uint8Array([1, 0]);
    const weights = new Uint8Array([255, 255]);
    const got = colorizeHeatBytes(new Uint8Array([100, 100]), 2, 1, valid, weights);
    expect(got[7]).toBe(0);
    expect(got[3]).toBe(255);
  });

  function context() {
    const draws: unknown[][] = [], fills: unknown[][] = [], rects: number[][] = [];
    const ctx = {
      globalAlpha: 1, filter: 'none', fillStyle: '',
      save() {}, restore() {}, beginPath() {}, clip() {},
      rect(...args: number[]) { rects.push(args); },
      fillRect(...args: number[]) { fills.push([this.fillStyle, ...args]); },
      drawImage(...args: unknown[]) { draws.push([this.globalAlpha, this.filter, ...args]); },
    };
    return { ctx: ctx as unknown as CanvasRenderingContext2D, draws, fills, rects };
  }
  const original = {} as CanvasImageSource, heat = {} as CanvasImageSource;
  const layer = { heat, coverage: [100, 50, 200, 120] as [number, number, number, number] };

  it('independent ROI uses the full image background and exact coverage without drawing the original', () => {
    const { ctx, draws, fills, rects } = context();
    drawDiffSource(ctx, original, 1000, 600, layer, 'heat', 0.1, 'medium');
    expect(draws).toEqual([[1, 'none', heat, 100, 50, 200, 120]]);
    expect(fills).toEqual([[UNANALYZED_COLOR, 0, 0, 1000, 600], [NODATA_COLOR, 100, 50, 200, 120]]);
    expect(rects).toEqual([[0, 0, 1000, 600], [100, 50, 200, 120]]);
  });

  it('overlay draws original plus the same heat data with independent alpha and display feather', () => {
    const { ctx, draws, fills } = context();
    drawDiffSource(ctx, original, 1000, 600, layer, 'overlay', 0.35, 'light');
    expect(draws).toEqual([[1, 'none', original, 0, 0, 1000, 600], [0.35, 'blur(0.1953125px)', heat, 100, 50, 200, 120]]);
    expect(fills).toEqual([]);
  });

  it('markers and hidden/unrelated layers render only the original', () => {
    for (const [display, heatLayer] of [['regions', layer], ['heat', null]] as const) {
      const { ctx, draws, fills } = context();
      drawDiffSource(ctx, original, 1000, 600, heatLayer, display, 0.5, 'off');
      expect(draws).toEqual([[1, 'none', original, 0, 0, 1000, 600]]);
      expect(fills).toEqual([]);
    }
  });
});
