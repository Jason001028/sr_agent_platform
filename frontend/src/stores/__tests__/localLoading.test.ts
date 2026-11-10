// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('../../lib/api.js', async original => ({ ...await original<typeof import('../../lib/api.js')>(),
  apiResolveScene: vi.fn(), fetchDropSceneJpg: vi.fn(), fetchSceneJpg: vi.fn(), apiSceneSiblings: vi.fn().mockResolvedValue({ items: [] }) }));
vi.mock('../../lib/local_preview.js', async original => ({ ...await original<typeof import('../../lib/local_preview.js')>(),
  sourceFingerprint: vi.fn().mockResolvedValue({ name: 'a.tif', size: 3, samples: ['','',''] }) }));
vi.mock('../../lib/localDecodeWorker', () => ({ decodeLocalFile: vi.fn() }));
import { apiResolveScene, fetchDropSceneJpg } from '../../lib/api';
import type { SceneResolveResult } from '../../lib/api';
import { decodeLocalFile } from '../../lib/localDecodeWorker';
import { browserKit } from '../../lib/browserKit';
import { sourceFingerprint } from '../../lib/local_preview';
import { useViewerStore } from '../viewer';

const resolved = () => ({ source: 'manual', row: { id: 'source-1', W: 2, H: 2, srcRev: 'rev-1' },
  resolved: { dir: '/DiskArray/scene', input: '/DiskArray/scene/a.tif', mask_path: '/DiskArray/scene/a_mask.tif',
    sr_capable: true, preview_only: false, kind: 'input' } } as SceneResolveResult);
const decoded = () => ({ W: 2, H: 2, src: new Float32Array([1,2,3,4]), srcw: 2, srch: 2, nbands: 1,
  invert: false, stats: null, route: 'sparse' as const, ms: 1, rgba: new Uint8ClampedArray(16).fill(100),
  jpeg: new Blob(['local-jpeg'], { type: 'image/jpeg' }), paintedMode: 'equal' as const, layout: 'uncompressed strips' });
beforeEach(() => {
  setActivePinia(createPinia()); vi.clearAllMocks(); localStorage.clear();
  vi.stubGlobal('Worker', class {}); vi.stubGlobal('OffscreenCanvas', class {});
  vi.stubGlobal('ImageData', class { constructor(public data: unknown, public width: number, public height: number) {} });
  vi.spyOn(browserKit, 'createCanvas').mockImplementation((width, height) => ({ width, height,
    getContext: () => ({ putImageData() {}, drawImage() {}, getImageData: () => ({ data: new Uint8ClampedArray(width * height * 4) }) }) } as never));
  vi.mocked(apiResolveScene).mockResolvedValue(resolved());
  vi.mocked(sourceFingerprint).mockResolvedValue({ name: 'a.tif', size: 3, samples: ['','',''] });
  vi.mocked(fetchDropSceneJpg).mockRejectedValue(new Error('large server JPG decode unavailable'));
  vi.mocked(decodeLocalFile).mockResolvedValue(decoded());
});
afterEach(() => { useViewerStore().stopImageLoad(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('keeps verified scene identity when server preview fails and publishes a local JPEG', async () => {
  const store = useViewerStore(); store.addFiles(new File(['raw'], 'a.tif'));
  await vi.waitFor(() => expect(store.recs[0]?.src).toBeTruthy());
  const rec = store.recs[0];
  expect(rec.serverSourceVerified).toBe(true); expect(rec.sceneId).toBe('source-1');
  expect(rec.lqPath).toBe('/DiskArray/scene'); expect(rec.serverMaskPath).toContain('a_mask.tif');
  expect(rec.route).toBe('sparse'); expect(rec.localPreviewJpeg?.type).toBe('image/jpeg');
  await vi.waitFor(() => expect(store.imageLoad.active).toBe(false));
});
it('never labels an unverified local TIFF as server-associated', async () => {
  vi.mocked(apiResolveScene).mockRejectedValue(new Error('not found'));
  const store = useViewerStore(); store.addFiles(new File(['raw'], 'a.tif'));
  await vi.waitFor(() => expect(store.recs[0]?.src).toBeTruthy());
  expect(store.recs[0].sceneId).toBeNull(); expect(store.recs[0].lqPath).toBeNull();
  expect(store.recs[0].serverSourceVerified).not.toBe(true);
  expect(store.recs[0].localPreviewJpeg).toBeTruthy();
});
it('keeps the server JPG path for a normal 38-megapixel preview instead of forcing X-drive reads', async () => {
  const res = resolved(); res.row.W=24737; res.row.H=24727;
  vi.mocked(apiResolveScene).mockResolvedValue(res);
  vi.mocked(fetchDropSceneJpg).mockResolvedValue(new Blob(['jpg']));
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width:2,height:2,close(){} }));
  const store=useViewerStore();store.setPreviewDiv(4);store.addFiles(new File(['raw'],'a.tif'));
  await vi.waitFor(()=>expect(store.recs[0]?.route).toBe('jpg'));
  expect(fetchDropSceneJpg).toHaveBeenCalled();expect(decodeLocalFile).not.toHaveBeenCalled();
});
it('skips an oversized server JPG and keeps progressive display separate from DN until pixels finish', async () => {
  const res = resolved(); res.row.W = 49474; res.row.H = 49454;
  vi.mocked(apiResolveScene).mockResolvedValue(res);
  let finish!: () => void;
  vi.mocked(decodeLocalFile).mockImplementation(async (_file, _div, _mode, control, _progress, display) => {
    display!.preview({ kind: 'overview', W: 49474, H: 49454, width: 2, height: 2, canvasWidth: 4, canvasHeight: 4,
      rows: 2, y: 0, rgba: new Uint8ClampedArray(16) });
    await control.wait(new Promise<void>(resolve => { finish = resolve; }));
    const result = { ...decoded(), W: 49474, H: 49454 };
    display!.pixels(result);
    await control.checkpoint('生成本地 JPG');
    return result;
  });
  const store = useViewerStore(); store.setPreviewDiv(2); store.addFiles(new File(['raw'], 'a.tif'));
  await vi.waitFor(() => expect(store.recs[0]?.progressivePreview).toBeTruthy());
  expect(fetchDropSceneJpg).not.toHaveBeenCalled();
  expect(store.recs[0].src).toBeNull(); expect(store.recs[0].sceneId).toBe('source-1');
  finish(); await vi.waitFor(() => expect(store.imageLoad.active).toBe(false));
  expect(store.recs[0].progressivePreview).toBeUndefined(); expect(store.recs[0].src).toBeTruthy();
});
it('pauses association completion and Stop does not launch a fallback decoder', async () => {
  let finish!: (value: SceneResolveResult) => void;
  vi.mocked(apiResolveScene).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const store = useViewerStore(); store.addFiles(new File(['raw'], 'a.tif'));
  await vi.waitFor(() => expect(apiResolveScene, JSON.stringify({ error: store.error, load: store.imageLoad, records: store.recs.map(r => ({ status: r.status, note: r.linkNote })) })).toHaveBeenCalled());
  store.pauseImageLoad(); finish(resolved());
  await vi.waitFor(() => expect(store.imageLoad.state).toBe('paused'));
  expect(fetchDropSceneJpg).not.toHaveBeenCalled(); expect(decodeLocalFile).not.toHaveBeenCalled();
  store.stopImageLoad();
  await vi.waitFor(() => expect(store.imageLoad.active).toBe(false));
  expect(decodeLocalFile).not.toHaveBeenCalled(); expect(store.recs[0].sceneId).toBeNull();
  expect(store.error).not.toContain('停止');
});
it('a stopped load cannot hide the next load or overwrite its pixels', async () => {
  vi.mocked(apiResolveScene).mockRejectedValue(new Error('local only'));
  let finish!: (value: ReturnType<typeof decoded>) => void;
  vi.mocked(decodeLocalFile).mockImplementation((_file, _div, _mode, control) =>
    control.wait(new Promise(resolve => { finish = resolve; }), 'sampling'));
  const store = useViewerStore(); store.addFiles(new File(['first'], 'first.tif'));
  await vi.waitFor(() => expect(decodeLocalFile).toHaveBeenCalledTimes(1));
  const firstFinish = finish;
  store.stopImageLoad(); store.addFiles(new File(['second'], 'second.tif'));
  await vi.waitFor(() => expect(decodeLocalFile).toHaveBeenCalledTimes(2));
  expect(store.imageLoad.recId).toBe(store.recs[1].id); expect(store.busy).toBe(true);
  firstFinish(decoded());
  await Promise.resolve(); expect(store.recs[0].src).toBeNull(); expect(store.busy).toBe(true);
  finish(decoded());
  await vi.waitFor(() => expect(store.imageLoad.active).toBe(false));
  expect(store.recs[1].src?.[0]).toBe(1); expect(store.recs[0].src).toBeNull();
});
