import { expect, it, vi } from 'vitest';
import { TileScheduler, tileRequests } from '../tileScheduler';
import { TileRenderer, tileAxis } from '../tileRenderer';
import { bootstrapTileLevel, openTileSource, overviewDiv } from '../tileSource';
import type { DecodedTile, TileSource } from '../tileSource';
import { nativePreviewScale, rasterTransform, tileLevel } from '../viewMath';

const source = {
  key: 'source/v1',
  asset: { id: 'one', sourceRev: 'v1', W: 49475, H: 49454 },
  info: { width: 49475, height: 49454, tileSize: 512, maxLevel: 16, rasterVersion: 'v1' },
} as TileSource;

function bitmap(width = 512, height = 512) {
  return { width, height, close: vi.fn() } as unknown as ImageBitmap;
}

function decoded(level = 0, x = 0, y = 0): DecodedTile {
  const span = 512 * 2 ** level;
  return { bitmap: bitmap(), tile: {
    level, tileX: x, tileY: y, x: x * span, y: y * span, width: 512, height: 512,
    sourceWidth: span, sourceHeight: span,
  } as DecodedTile['tile'] };
}

function pane(ox = 0, oy = 0, scale = 1) {
  return { source, rect: { x: 32, y: 0, w: 510, h: 510 },
    view: { ox, oy, scale }, tw: source.info.width, th: source.info.height };
}

it('uses physical scale, a hysteresis band and finite source-aligned viewport windows', () => {
  expect(tileLevel(.5, 16)).toBe(1);
  expect(tileLevel(.69, 16, 1)).toBe(1);
  expect(tileLevel(.85, 16, 1)).toBe(0);
  expect(overviewDiv(49475, 49454, 2)).toBe(16);
  expect(overviewDiv(49475, 49454, 32)).toBe(32);
  expect(tileRequests(pane(0, 0, .5), 2).level).toBe(0);
  const edge = tileRequests(pane(-49152, -49152), 1);
  expect(edge.requests.filter(req => req.priority < 1).map(req => [req.x, req.y])).toEqual([[96, 96]]);
  expect(tileRequests(pane(600), 1).requests).toEqual([]);
  expect(tileAxis(1024, 7, 4, 2)).toEqual([
    { start: 1024, end: 1030, from: 0, count: 3 },
    { start: 1030, end: 1031, from: 3, count: 1 },
  ]);
});

it('uses the band_count field from the real P1 JSON contract', async () => {
  const info = { ...source.info, sourceVersion: 'v1', band: 1, band_count: 1 };
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(info)));
  vi.stubGlobal('fetch', fetch);
  try {
    expect(await openTileSource(source.asset)).toMatchObject({ asset: source.asset, info });
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/assets/one/tiles?band=1'), expect.any(Object));
  } finally {
    vi.unstubAllGlobals();
  }
});

it('uses a cheap tiled bootstrap and maps a rounded overview at native scale on both axes', () => {
  const info = { ...source.info, layout:'tiled', overviews:[], block_size:[512,512] } as TileSource['info'];
  expect(bootstrapTileLevel(info)).toBe(10);
  expect(bootstrapTileLevel({ ...info, overviews:[[387,387]] })).toBe(7);
  for (const dpr of [1,2]) {
    const scale = nativePreviewScale(49,49,49475,49454,dpr);
    expect(scale).toBeGreaterThan(64);
    const v = { scale, ox:-1024, oy:-2048 };
    const t = rasterTransform(v,49,49,49475,49454,900,700,dpr);
    expect([t.x*dpr,t.y*dpr]).toEqual([1,1]);
    expect((350-t.oy)/t.y).toBeCloseTo((350-v.oy)/(scale*49/49454),8);
  }
});

it('deduplicates, cancels queued and stale work, and closes a late Bitmap', async () => {
  const jobs: { signal: AbortSignal; resolve: (value: DecodedTile) => void; x: number }[] = [];
  const read = vi.fn((_src, _level, x, _y, signal) => new Promise<DecodedTile>(resolve => jobs.push({ signal, resolve, x })));
  const scheduler = new TileScheduler(vi.fn(), 4 * 1024 ** 2, 1, read);
  const first = tileRequests(pane(), 1).requests;
  scheduler.update([...first, ...first], [source], new Set());
  expect(jobs).toHaveLength(1);
  expect(jobs[0].x).toBe(0);
  const next = tileRequests(pane(-5120), 1).requests.filter(r => r.priority < 1);
  scheduler.update(next, [source], new Set());
  expect(jobs[0].signal.aborted).toBe(true);
  const obsolete = decoded();
  jobs[0].resolve(obsolete);
  await vi.waitFor(() => expect(jobs).toHaveLength(2));
  expect(jobs[1].x).toBe(10);
  expect(obsolete.bitmap.close).toHaveBeenCalledOnce();
  const accepted = decoded(0, 10);
  jobs[1].resolve(accepted);
  await vi.waitFor(() => expect(scheduler.cache.size).toBe(1));
  scheduler.close();
  expect(accepted.bitmap.close).toHaveBeenCalledOnce();
  expect(scheduler.bytes).toBe(0);
});

it('evicts inactive least-recently-used Bitmaps and invalidates an old source version', async () => {
  const results = [decoded(0, 0), decoded(0, 1), decoded(0, 2)];
  const read = vi.fn().mockImplementation((_src, _level, x) => Promise.resolve(results[x]));
  const scheduler = new TileScheduler(vi.fn(), 2 * 1024 ** 2, 1, read);
  for (let x = 0; x <= 2; x++) {
    scheduler.update(tileRequests(pane(-x * 512), 1).requests.filter(r => r.priority < 1), [source], new Set());
    await vi.waitFor(() => expect(scheduler.cache.has(`${source.key}/0/${x}/0`)).toBe(true));
    if (x === 1) scheduler.touch(scheduler.cache.get(`${source.key}/0/0/0`)!);
  }
  expect(results[1].bitmap.close).toHaveBeenCalledOnce();
  expect(results[0].bitmap.close).not.toHaveBeenCalled();
  scheduler.update([], [{ ...source, key: 'source/v2' }], new Set());
  expect(results[0].bitmap.close).toHaveBeenCalledOnce();
  expect(results[2].bitmap.close).toHaveBeenCalledOnce();
  scheduler.close();
});

it('draws the cached parent under a fading child and stops animating after opaque coverage', () => {
  const renderer = new TileRenderer(vi.fn());
  const cache = renderer.scheduler.cache;
  const parent = { ...decoded(1), key: `${source.key}/1/0/0`, sourceKey: source.key, ready: 0, fade: 0 };
  const child = { ...decoded(), key: `${source.key}/0/0/0`, sourceKey: source.key, ready: 200, fade: 160 };
  cache.set(parent.key, parent);
  cache.set(child.key, child);
  const camera = new Map([['A', pane()]]);
  const draws: [unknown, number][] = [];
  const ctx = { globalAlpha: 1, save() {}, restore() {}, beginPath() {}, rect() {}, clip() {},
    drawImage(this: { globalAlpha: number }, image: unknown) { draws.push([image, this.globalAlpha]); },
  } as unknown as CanvasRenderingContext2D;
  renderer.update(camera, [source], 240, 1, false);
  expect(renderer.draw(ctx, 'A', 240, 1, false)).toBe(true);
  expect(draws).toEqual([[parent.bitmap, 1], [child.bitmap, 1 - .75 ** 3]]);
  draws.length = 0;
  renderer.update(camera, [source], 380, 1, false);
  expect(renderer.draw(ctx, 'A', 380, 1, false)).toBe(false);
  expect(draws).toEqual([[child.bitmap, 1]]);
  renderer.scheduler.close();
});
