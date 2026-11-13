import type { DiffDisplay, DiffFeather } from './diffLocate';
import { decodeToneWeight } from './diffTone';

export const HEAT_COLORS = ['#0B0B1D', '#3B0F70', '#8C2981', '#DE4968', '#FCA636', '#FCFFA4'];
export const HEAT_GRADIENT = `linear-gradient(to right, ${HEAT_COLORS.join(', ')})`;
export const UNANALYZED_COLOR = '#202C39';
export const NODATA_COLOR = '#637180';
const stops = HEAT_COLORS.map(c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16)));
export const SMART_HEAT_COLORS = ['#2451a4', '#13bfc6', '#f4d74d', '#f18b32', '#d6332f'];
export const SMART_HEAT_GRADIENT = `linear-gradient(to right, ${SMART_HEAT_COLORS.join(', ')})`;
const smartStops = SMART_HEAT_COLORS.map(c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16)));

/** Color only: neither intensity bytes nor validity are modified. */
export function colorizeHeatPixels(data: Uint8ClampedArray, valid?: Uint8ClampedArray): Uint8ClampedArray {
  const rgba = new Uint8ClampedArray(data.length);
  for (let i = 0; i < data.length; i += 4) {
    if (!data[i + 3] || (valid && !valid[i])) continue;
    const position = data[i] / 255 * (stops.length - 1);
    const index = Math.min(stops.length - 2, Math.floor(position));
    const t = position - index;
    for (let c = 0; c < 3; c++) rgba[i + c] = Math.round(stops[index][c] * (1 - t) + stops[index + 1][c] * t);
    rgba[i + 3] = 255;
  }
  return rgba;
}

/**
 * 单通道差异字节 → RGBA。
 *
 * `weights` 是逐像素的归因 norm 字节（0 = 1.0×，255 = 2.5×）；
 * 给出时先做 `强度 × 权重` 再查色表，超过 1 的一律截断到最暖色。没有权重时
 * 与 `colorizeHeatPixels` 的着色完全一致。
 */
export function colorizeHeatBytes(
  intensity: Uint8Array,
  width: number,
  height: number,
  valid?: Uint8Array | null,
  weights?: Uint8Array | null,
  options?: { smart?: boolean; threshold?: number; selectedBbox?: [number, number, number, number] | null },
): Uint8ClampedArray {
  const n = width * height;
  const rgba = new Uint8ClampedArray(n * 4);
  const palette = options?.smart ? smartStops : stops;
  for (let i = 0; i < n; i++) {
    if (valid && !valid[i]) continue;
    let v = intensity[i];
    if (v / 255 < (options?.threshold ?? 0)) continue;
    if (weights) {
      const scaled = v * decodeToneWeight(weights[i]);
      v = scaled >= 255 ? 255 : scaled;
    }
    const position = v / 255 * (palette.length - 1);
    const index = Math.min(palette.length - 2, Math.floor(position));
    const t = position - index;
    const o = i * 4;
    for (let c = 0; c < 3; c++) rgba[o + c] = Math.round(palette[index][c] * (1 - t) + palette[index + 1][c] * t);
    rgba[o + 3] = 255;
    const box = options?.selectedBbox;
    if (box && (i % width < box[0] || i % width >= box[0]+box[2] || Math.floor(i/width) < box[1] || Math.floor(i/width) >= box[1]+box[3])) rgba[o+3] = 85;
  }
  return rgba;
}

export interface HeatViewLayer {
  heat: CanvasImageSource;
  coverage: [number, number, number, number];
}

/** The main canvas and navigator share the same display source and ROI mapping.
 * Coordinates here are thumbnail coordinates; the caller supplies its camera transform. */
export function drawDiffSource(
  ctx: CanvasRenderingContext2D, original: CanvasImageSource, width: number, height: number,
  layer: HeatViewLayer | null, display: DiffDisplay, opacity: number, feather: DiffFeather,
): void {
  ctx.save();
  try {
    ctx.beginPath();
    ctx.rect(0, 0, width, height);
    ctx.clip();
    if (layer && display === 'heat') {
      ctx.fillStyle = UNANALYZED_COLOR;
      ctx.fillRect(0, 0, width, height);
    } else {
      ctx.drawImage(original, 0, 0, width, height);
    }
    if (!layer || display === 'regions') return;
    const [x, y, w, h] = layer.coverage;
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    if (display === 'heat') {
      ctx.fillStyle = NODATA_COLOR;
      ctx.fillRect(x, y, w, h);
    } else {
      ctx.globalAlpha = Math.max(0, Math.min(1, opacity));
      // Feather the display only; never filter the intensity data used by analysis.
      const radius = feather === 'off' ? 0 : Math.max(w, h) / (feather === 'light' ? 1024 : 512);
      if (radius) ctx.filter = `blur(${radius}px)`;
    }
    ctx.drawImage(layer.heat, x, y, w, h);
  } finally {
    ctx.restore();
  }
}
