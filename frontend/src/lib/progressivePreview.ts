import type { BandStats, StretchMode } from './tifDecode';
import { stretchRgba } from './tifDecode';

/** Progress itself must not flood the UI with one event per disk read. */
export function progressReporter(send: (fraction: number) => void, now = () => performance.now()) {
  let last = -Infinity;
  return (fraction: number) => {
    const time = now();
    if (fraction > 0 && fraction < 1 && time-last < 80) return;
    last = time; send(fraction);
  };
}

/** Keep the final display stretch cooperative too; never hold a whole-scene
 * synchronous pixel loop between pause opportunities. */
export async function stretchIncrementally(src: Float32Array, width: number, height: number, nb: number,
  stats: BandStats[] | null, mode: StretchMode, invert: boolean, checkpoint: () => Promise<void>) {
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 64) {
    await checkpoint();
    const rows = Math.min(64, height-y);
    stretchRgba(src.subarray(y*width*nb, (y+rows)*width*nb), width, rows, nb, stats, mode, invert,
      rgba.subarray(y*width*4, (y+rows)*width*4));
  }
  await checkpoint();
  return rgba;
}

/** A display-only layer: no partial DN is exposed to ROI/repair consumers. */
export interface PreviewFrame {
  kind: 'overview' | 'rows';
  W: number; H: number;
  width: number; height: number;
  canvasWidth: number; canvasHeight: number;
  y: number; rows: number;
  rgba: Uint8ClampedArray;
}

// Conservative whole-JPEG admission. This does not change the selected div.
export function wholeJpegFits(W: number, H: number, div: number): boolean {
  if (![W, H, div].every(value => Number.isFinite(value) && value > 0)) return false;
  const w = Math.max(1, Math.round(W / div)), h = Math.max(1, Math.round(H / div));
  return Number.isFinite(w * h) && w <= 16384 && h <= 16384 && w * h <= 64 * 1024 * 1024;
}

/** Batch completed rows with backpressure; do not send a message per sparse row.
 * Out-of-order I/O is legal. A packet contains only completed contiguous rows. */
export function rowPublisher(nb: number, stats: BandStats[], mode: StretchMode, invert: boolean,
  send: (rgba: Uint8ClampedArray, width: number, rows: number, y: number) => Promise<void>,
  options?: { bestEffort: boolean; intervalMs?: number }) {
  let pending: { values: Float32Array; width: number; rows: number; y: number }[] = [];
  let count = 0, tail = Promise.resolve();
  let sending = false, nextAt = 0, failure: unknown;
  const flushPacket = async () => {
    if (!pending.length) return tail;
    const packet = pending; pending = []; count = 0;
    sending = true; nextAt = performance.now() + (options?.intervalMs ?? 150);
    tail = tail.then(async () => {
      packet.sort((a, b) => a.y - b.y);
      for (let i = 0; i < packet.length;) {
        const first = packet[i++];
        const group = [first]; let rows = first.rows;
        while (i < packet.length && packet[i].width === first.width && packet[i].y === first.y + rows) {
          group.push(packet[i]); rows += packet[i++].rows;
        }
        const values = new Float32Array(first.width * rows * nb);
        let offset = 0;
        for (const item of group) { values.set(item.values, offset); offset += item.values.length; }
        await send(stretchRgba(values, first.width, rows, nb, stats, mode, invert), first.width, rows, first.y);
      }
    });
    if (options?.bestEffort) void tail.then(() => { sending = false; }, error => { failure = error; sending = false; });
    return tail;
  };
  return {
    async rows(values: Float32Array, width: number, rows: number, y: number) {
      if (options?.bestEffort && (sending || performance.now() < nextAt)) return;
      // One packet's DN copies <= roughly 2 MiB for an 8192-wide mono image.
      for (let offset = 0; offset < rows; offset += 32) {
        const height = Math.min(32, rows - offset);
        pending.push({ values: values.slice(offset * width * nb, (offset + height) * width * nb), width, rows: height, y: y + offset });
        count += height;
        if (count >= 64) {
          const work = flushPacket();
          if (!options?.bestEffort) await work;
          else { void work.catch(error => { failure = error; }); return; }
        }
      }
    }, async flush() {
      if (!options?.bestEffort) return flushPacket();
      // Final authoritative pixels supersede any skipped intermediate updates.
      pending = []; count = 0;
      await tail;
      if (failure) throw failure;
    },
  };
}
