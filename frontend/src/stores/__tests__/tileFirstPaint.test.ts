// @vitest-environment jsdom
import { createPinia } from 'pinia';
import { createApp, markRaw, nextTick } from 'vue';
import { afterEach, expect, it, vi } from 'vitest';
import TifCanvas from '../../components/TifCanvas.vue';
import { browserKit } from '../../lib/browserKit';
import { loadTile, type DecodedTile, type TileSource } from '../../lib/tileSource';
import { useViewerStore, type ViewerRec } from '../viewer';

vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../components/MiniNavigator.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/ViewerBackdrop.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/ViewerHistoryNavigation.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/AgentAnnotationOverlay.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/DiffOverlay.vue', () => ({ default: { render: () => null } }));
vi.mock('../../lib/tileSource', async importOriginal => ({
  ...await importOriginal<typeof import('../../lib/tileSource')>(), loadTile: vi.fn(),
}));

let app: ReturnType<typeof createApp> | undefined;
let root: HTMLElement | undefined;

afterEach(() => {
  app?.unmount();
  root?.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.mocked(loadTile).mockReset();
});

function mountCanvas(width = 1024) {
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  vi.stubGlobal('matchMedia', undefined);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  const drawImage = vi.fn<(image: CanvasImageSource) => number>();
  const ctx = new Proxy({ drawImage, globalAlpha: 1 }, {
    get: (target, key) => Reflect.get(target, key) ?? (() => {}),
  });
  drawImage.mockImplementation(() => ctx.globalAlpha);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 1024, 512));
  vi.spyOn(performance, 'now').mockReturnValue(100);
  const source = markRaw<TileSource>({
    key: 'asset/rev',
    asset: { id: 'asset', name: 'scene.tif', W: width, H: 512, size: 1, uploaded: false,
      serverPath: null, sourceRev: 'rev', fingerprint: { name: 'scene.tif', size: 1, samples: ['', '', ''] } },
    info: { width, height: 512, tileSize: 512, maxLevel: 10, displayProfiles: true, overviews: [],
      band: 1, band_count: 1, dtype: 'uint16', nodata: null, layout: 'strip', compression: 'none',
      block_size: [width, 1], sourceVersion: 'rev', rasterVersion: 'raster', stretchVersion: 'tone' },
  });
  const thumb = markRaw(browserKit.createCanvas(width, 512));
  const pinia = createPinia();
  const store = useViewerStore(pinia);
  const rec: ViewerRec = {
    id: 1, name: 'scene', W: width, H: 512, route: 'jpg', paintedMode: 'equal', thumb, tileSource: source,
    file: markRaw(new File(['x'], 'scene.jpg')), size: 1, probe: null, src: null, srcw: width, srch: 512,
    nbands: 1, invert: false, stats: null, sceneId: 'scene', lqPath: null, token: 0, layout: '', status: '',
    statusCls: '', maskRois: null,
  };
  store.recs.push(rec);
  store.activeId = 1;
  root = document.createElement('div');
  document.body.append(root);
  app = createApp(TifCanvas);
  app.use(pinia).mount(root);
  store.setCanvasSize(1024, 512);
  store.view = { scale: 1024 / width, ox: 0, oy: 0 };
  const render = async (now: number) => {
    await nextTick();
    drawImage.mockClear();
    const ready = [...frames.values()];
    frames.clear();
    for (const callback of ready) callback(now);
    return drawImage.mock.calls.flatMap(([image], index) => drawImage.mock.results[index].value > 0 ? [image] : []);
  };
  return { store, thumb, render };
}

function delayedTiles() {
  const requests = new Map<string, { resolve: (result: DecodedTile) => void; reject: (error: Error) => void }>();
  vi.mocked(loadTile).mockImplementation((_source, level, x) => new Promise((resolve, reject) => {
    requests.set(`${level}/${x}`, { resolve, reject });
  }));
  const release = async (level: number, x: number) => {
    const step = 2 ** level;
    const span = 512 * step;
    const sourceHeight = Math.min(512, span);
    const bitmap = { width: 512, height: sourceHeight / step, close() {} } as ImageBitmap;
    requests.get(`${level}/${x}`)!.resolve({ bitmap, tile: {
      level, x: x * span, y: 0, width: bitmap.width, height: bitmap.height,
      sourceWidth: span, sourceHeight,
    } as DecodedTile['tile'] });
    await Promise.resolve();
    return bitmap;
  };
  return { requests, release };
}

it.each(['target', 'overview'])('shows the whole JPEG until an opaque %s layer can replace it', async layer => {
  const tiles = delayedTiles();
  const { thumb, render } = mountCanvas();
  expect(await render(100)).toEqual([thumb]);

  const left = await tiles.release(0, 0);
  expect(await render(150)).toEqual([thumb]);
  if (layer === 'overview') {
    const overview = await tiles.release(1, 0);
    expect(await render(160)).toEqual([overview, left]);
  } else {
    const right = await tiles.release(0, 1);
    expect(await render(160)).toEqual([thumb]);
    expect(await render(270)).toEqual([left, right]);
  }
});

it('draws local detail after zoom while a neighboring tile and the full-image tile are pending', async () => {
  const tiles = delayedTiles();
  const { store, thumb, render } = mountCanvas(2048);
  expect(await render(100)).toEqual([thumb]);
  const coarseLeft = await tiles.release(1, 0);
  const coarseRight = await tiles.release(1, 1);
  expect(await render(270)).toEqual([coarseLeft, coarseRight]);

  store.onWheel(0, 0, 2);
  expect(await render(290)).toEqual([thumb, coarseLeft]);
  expect(tiles.requests.has('0/1')).toBe(true);
  expect(tiles.requests.has('2/0')).toBe(true);
  const detailLeft = await tiles.release(0, 0);
  expect(await render(310)).toEqual([thumb, coarseLeft, detailLeft]);
});

it('retains the default JPEG after repeated tile failures and excludes it for custom and binary displays', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  const tiles = delayedTiles();
  const { store, thumb, render } = mountCanvas();
  expect(await render(100)).toEqual([thumb]);
  const failTiles = () => {
    for (const request of tiles.requests.values()) request.reject(new Error('tile unavailable'));
  };
  failTiles();
  await vi.advanceTimersByTimeAsync(700);
  expect(loadTile).toHaveBeenCalledTimes(6);
  failTiles();
  await vi.advanceTimersByTimeAsync(700);
  expect(loadTile).toHaveBeenCalledTimes(6);
  store.renderTick++;
  expect(await render(1500)).toEqual([thumb]);

  for (const profile of ['window:1:7', 'binary']) {
    store.recs[0].tileDisplay = profile;
    store.renderTick++;
    expect(await render(1516)).toEqual([]);
  }
});
