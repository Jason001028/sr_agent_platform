import { expect, it, vi } from 'vitest';
import { TileScheduler, tileRequests } from '../tileScheduler';
import { TileRenderer, tileAxis } from '../tileRenderer';
import { bootstrapTileLevel, loadTile, openTileSource, overviewDiv, withTileDisplay } from '../tileSource';
import type { DecodedTile, TileSource } from '../tileSource';
import { nativePreviewScale, rasterTransform, tileLevel } from '../viewMath';
import { overviewResolution, tileResolution } from '../viewResolution';

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

it('cancels the previous display configuration at the same L0 position', async () => {
  const viewSource = { ...source, info: { ...source.info, displayProfiles: true } };
  const configured = withTileDisplay(viewSource, 'window:1.00000000000e+0:7.00000000000e+0');
  const calls: { signal: AbortSignal; resolve: (tile: DecodedTile) => void }[] = [];
  const scheduler = new TileScheduler(vi.fn(), 8 * 1024 ** 2, 1, (_src, _l, _x, _y, signal) =>
    new Promise(resolve => calls.push({ signal, resolve })));
  const frame = (tileSource: TileSource) => {
    const requests = tileRequests({ ...pane(), source: tileSource }, 1).requests;
    scheduler.update(requests, [tileSource], new Set());
  };
  frame(viewSource);
  frame(configured);
  expect(calls[0].signal.aborted).toBe(true);
  const obsolete = decoded();
  calls[0].resolve(obsolete);
  await vi.waitFor(() => expect(calls).toHaveLength(2));
  expect(obsolete.bitmap.close).toHaveBeenCalledOnce();
  calls[1].resolve(decoded());
  await vi.waitFor(() => expect(scheduler.cache.has(`${configured.key}/0/0/0`)).toBe(true));
  expect(scheduler.cache.has(`${viewSource.key}/0/0/0`)).toBe(false);
  scheduler.close();
});

it('rejects stale display metadata before allocating a bitmap', async () => {
  const configured = withTileDisplay({ ...source, info: {
    ...source.info, displayProfiles: true, sourceVersion: 'v1', stretchVersion: 'tone-v2', band: 1,
  } }, 'linear2');
  const header = { ...decoded().tile, sourceVersion: 'v1', rasterVersion: 'v1',
    stretchVersion: 'tone-v2', band: 1, requestedSampling: 'nearest', resampling: 'nearest', displayKey: 'equal' };
  const decode = vi.fn();
  vi.stubGlobal('createImageBitmap', decode);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('png', {
    headers: { 'X-SR-Tile': JSON.stringify(header) },
  })));
  try {
    await expect(loadTile(configured, 0, 0, 0, new AbortController().signal)).rejects.toThrow('geometry changed');
    expect(decode).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllGlobals();
  }
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
  expect(renderer.presentation('A')).toMatchObject({ targetLOD: 0, readyLOD: null, drawn: 0, visible: 1 });
  expect(renderer.draw(ctx, 'A', 240, 1, false)).toBe(true);
  expect(renderer.presentation('A')?.readyLOD).toBeNull();
  expect(draws).toEqual([[parent.bitmap, 1], [child.bitmap, 1 - .75 ** 3]]);
  draws.length = 0;
  renderer.update(camera, [source], 380, 1, false);
  expect(renderer.draw(ctx, 'A', 380, 1, false)).toBe(false);
  expect(renderer.presentation('A')).toMatchObject({ readyLOD: 0, drawn: 1 });
  expect(draws).toEqual([[child.bitmap, 1]]);
  renderer.update(new Map([['A', pane(-512)], ['B', pane(600)]]), [source], 400, 1, true);
  renderer.draw(ctx, 'A', 400, 1, true);
  renderer.draw(ctx, 'B', 400, 1, true);
  expect(renderer.presentation('A')).toMatchObject({ readyLOD: null, drawn: 0, visible: 1 });
  expect(renderer.presentation('B')).toMatchObject({ readyLOD: null, drawn: 0, visible: 0 });
  renderer.scheduler.close();
});

it('keeps Overview dimensions independent of displayed LOD and physical screen scale', () => {
  expect(overviewResolution(49475, 49454, 16387, 16380)).toBe('16387×16380 · 约 1/3.02');
  expect(tileResolution({ x: 1, y: 1 }, 1, false, 0, null, 4)).toBe('原生 1:1 · 高清加载中');
  expect(tileResolution({ x: .5, y: .5 }, 2, false, 0, 0, 4)).toBe('原生 1:1 · 已就绪');
  expect(tileResolution({ x: .5, y: .5 }, 3, false, 0, 0, 4)).toBe('视图 1.5:1 · L0');
  expect(tileResolution({ x: .25, y: .25 }, 2, false, 1, 1, 4)).toBe('视图 1:2 · L1');
  expect(tileResolution({ x: .125, y: .125 }, 2, false, 2, 2, 4)).toBe('视图 1:4 · L2');
});

it('counts the short edge tile without requiring coverage beyond the image', () => {
  const renderer = new TileRenderer(vi.fn());
  const edge = decoded(0, 96, 96);
  Object.assign(edge.tile, { sourceWidth: 323, sourceHeight: 302, width: 323, height: 302 });
  const key = `${source.key}/0/96/96`;
  renderer.scheduler.cache.set(key, {
    ...edge, bitmap: bitmap(323, 302), key, sourceKey: source.key, ready: 0, fade: 0,
  });
  const drawImage = vi.fn();
  const ctx = { save() {}, restore() {}, beginPath() {}, rect() {}, clip() {}, drawImage } as unknown as CanvasRenderingContext2D;
  renderer.update(new Map([['A', pane(-49152, -49152)]]), [source], 1, 1, true);
  renderer.draw(ctx, 'A', 1, 1, true);
  expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 323, 302, 32, 0, 323, 302);
  expect(renderer.presentation('A')).toMatchObject({ visible: 1, drawn: 1, readyLOD: 0 });
  renderer.scheduler.close();
});
