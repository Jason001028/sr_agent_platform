import type { DiffDisplay, DiffFeather } from './diffLocate';

export const HEAT_COLORS = ['#0B0B1D', '#3B0F70', '#8C2981', '#DE4968', '#FCA636', '#FCFFA4'];
export const HEAT_GRADIENT = `linear-gradient(to right, ${HEAT_COLORS.join(', ')})`;
export const UNANALYZED_COLOR = '#202C39';
export const NODATA_COLOR = '#637180';
const stops = HEAT_COLORS.map(c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16)));

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
