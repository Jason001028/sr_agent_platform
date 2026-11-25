// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createApp, h } from 'vue';
import type { App } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
vi.mock('../../router/index.js', () => ({ default: {} }));
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('../../components/CoordinateInput.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/PreviewScaleControl.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/AutoTourButton.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/DiffLocateButton.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/DiffContinueButton.vue', () => ({ default: { render: () => null } }));
vi.mock('../../components/SRSubmitFeedback.vue', () => ({ default: { render: () => null } }));
vi.mock('../../lib/api.js', async original => ({ ...await original<typeof import('../../lib/api.js')>(),
  apiResolveScene: vi.fn(), apiSceneTiffFiles: vi.fn(), fetchDropSceneJpg: vi.fn(), fetchSceneJpg: vi.fn(), apiSceneSiblings: vi.fn() }));
vi.mock('../../lib/local_preview.js', async original => ({ ...await original<typeof import('../../lib/local_preview.js')>(),
  sourceFingerprint: vi.fn() }));
vi.mock('../../lib/localDecodeWorker', () => ({ decodeLocalFile: vi.fn() }));
import Toolbar from '../../components/Toolbar.vue';
import CompareBar from '../../components/CompareBar.vue';
import FileList from '../../components/FileList.vue';
import { apiResolveScene, apiSceneTiffFiles, fetchDropSceneJpg, fetchSceneJpg, apiSceneSiblings } from '../../lib/api';
import type { SceneResolveResult, SceneSiblings } from '../../lib/api';
import { sourceFingerprint } from '../../lib/local_preview';
import { decodeLocalFile } from '../../lib/localDecodeWorker';
import { browserKit } from '../../lib/browserKit';
import { useViewerStore } from '../viewer';

let app: App, root: HTMLDivElement;
const dir = '/DiskArray/selected-scene';
beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks();
  const pinia = createPinia(); setActivePinia(pinia);
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 2, height: 2, close() {} }));
  vi.stubGlobal('ImageData', class { constructor(public data: unknown, public width: number, public height: number) {} });
  vi.spyOn(browserKit, 'createCanvas').mockImplementation((width, height) => ({ width, height,
    getContext: () => ({ putImageData() {}, drawImage() {}, getImageData: () => ({ data: new Uint8ClampedArray(width * height * 4).fill(100) }) }) } as never));
  vi.mocked(sourceFingerprint).mockResolvedValue({ name: 'selected.tif', size: 3, samples: ['a','b','c'] });
  vi.mocked(apiResolveScene).mockResolvedValue({ row: { id: 'selected', name: 'selected', W: 100, H: 100 },
    resolved: { dir, sr_capable: true, preview_only: false, kind: 'input', mask_path: dir + '/mask.tif' } } as SceneResolveResult);
  vi.mocked(fetchDropSceneJpg).mockResolvedValue(new Blob(['jpg']));
  vi.mocked(fetchSceneJpg).mockResolvedValue(new Blob(['sibling-jpg']));
  vi.mocked(apiSceneSiblings).mockImplementation(async (_cfg, sid, suffix) => ({ sceneId: sid, lqPath: dir,
    suffix: suffix ?? 'ori', suffixFrom: 'query', div: 4, productCandidates: [], nosrCandidates: [],
    items: [ { kind: 'input' as const, id: 'selected', name: 'selected.tif' },
      { kind: 'product' as const, id: suffix === 'old' ? 'old' : 'ori', name: suffix === 'old' ? 'selected_old.tif' : 'selected_ori.tif' },
      { kind: 'nosr' as const, id: 'nosr', name: 'selected_NOSR.tif' } ].map(it => ({ ...it, exists: true, W: 100, H: 100,
        hasPreview: false, previewDiv: null, jpgUrl: null, rel: null, sizeBytes: 3, mtime: null })) } satisfies SceneSiblings));
  root = document.createElement('div'); document.body.append(root);
  app = createApp({ render: () => h('div', [h(Toolbar), h(CompareBar), h(FileList)]) }); app.use(pinia); app.mount(root);
});
afterEach(() => { useViewerStore().stopImageLoad(); app.unmount(); root.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function selectTiff() {
  const input = root.querySelector<HTMLInputElement>('input[type=file]')!;
  const file = new File(['raw'], 'selected.tif');
  Object.defineProperty(input, 'files', { configurable: true, value: [file] });
  input.dispatchEvent(new Event('change', { bubbles: true }));
  await vi.waitFor(() => expect(useViewerStore().imageLoad.active).toBe(false));
  await vi.waitFor(() => expect(useViewerStore().recs[0]?.route).toBe('jpg'));
  expect(input.value).toBe('');
  return useViewerStore();
}

it('selects a TIFF through the real toolbar, verifies it, and uses server downsampled JPG without local decoding', async () => {
  const store = await selectTiff();
  expect(apiResolveScene).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ name: 'selected.tif', size_bytes: 3,
    source_fingerprint: expect.objectContaining({ samples: ['a','b','c'] }) }), expect.anything());
  expect(fetchDropSceneJpg).toHaveBeenCalledWith(expect.anything(), 'selected', store.previewDiv, expect.any(Function), expect.any(AbortSignal));
  expect(decodeLocalFile).not.toHaveBeenCalled();
  expect(store.activeRec).toMatchObject({ sceneId: 'selected', sceneDir: dir, lqPath: dir, serverSourceVerified: true });
  await vi.waitFor(() => expect(fetchSceneJpg).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ id: 'nosr' }), store.previewDiv));
});

it.each(['nosr', 'product', 'old'] as const)('opens the selected TIFF companion %s using the verified scene and server JPG', async kind => {
  const store = await selectTiff();
  expect(await store.openSceneSibling(kind)).toBe(true);
  expect(apiSceneSiblings).toHaveBeenLastCalledWith(expect.anything(), 'selected', kind === 'old' ? 'old' : undefined);
  expect(store.activeRec?.sceneId).toBe(kind === 'product' ? 'ori' : kind);
  expect(store.activeRec?.route).toBe('jpg');
  expect(store.activeRec?.sceneDir).toBe(dir);
  expect(decodeLocalFile).not.toHaveBeenCalled();
});

it('offers native tiles for a small TIFF opened through the same-scene shortcut', async () => {
  const store = await selectTiff();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/assets')) {
      return Response.json({ id: 'asset-nosr', name: 'selected_NOSR.tif', W: 100, H: 100,
        sourceRev: 'nosr-rev', uploaded: false });
    }
    if (url.includes('/assets/asset-nosr/tiles?')) {
      return Response.json({ width: 100, height: 100, tileSize: 512, band_count: 1,
        sourceVersion: 'nosr-rev', rasterVersion: 'raster-nosr', displayProfiles: true });
    }
    if (url.includes('/assets/asset-nosr/preview?')) {
      return new Response('jpg', { headers: { 'X-Preview-Width': '2', 'X-Preview-Height': '2' } });
    }
    return new Response(null, { status: 404 });
  }));
  expect(await store.openSceneSibling('nosr')).toBe(true);
  expect(store.activeRec?.tileSource?.asset.id).toBe('asset-nosr');
  await vi.waitFor(() => expect(root.querySelector('[data-e2e="native-zoom"]')).toBeTruthy());
});

it('keeps the PAN tier while smaller NOSR opens at half size and follows fine PAN tiers at full size',async()=>{
  const store=await selectTiff();store.setPreviewDiv(4);
  await vi.waitFor(()=>expect(store.imageLoad.active).toBe(false));
  const pan=store.activeRec!;
  const response=await apiSceneSiblings({apiBase:'',staticBase:''},'selected') as SceneSiblings;
  vi.mocked(apiSceneSiblings).mockResolvedValue({...response,items:response.items.map(item=>item.kind==='nosr'?{...item,W:50,H:50}:item)});
  expect(await store.openSceneSibling('nosr')).toBe(true);
  const nosr=store.activeRec!;
  expect(fetchSceneJpg).toHaveBeenLastCalledWith(expect.anything(),expect.objectContaining({id:'nosr'}),2,expect.any(Function));
  expect(store.previewDiv).toBe(4);expect(nosr.detailDiv).toBe(2);
  store.setPreviewDiv(2);await vi.waitFor(()=>expect(nosr.detailDiv).toBe(1));
  expect(fetchDropSceneJpg).toHaveBeenLastCalledWith(expect.anything(),'nosr',1,undefined,expect.any(AbortSignal));
  await store.activate(pan.id);expect(pan.detailDiv).toBe(2);
  await store.activate(nosr.id);expect(nosr.detailDiv).toBe(1);
  const count=vi.mocked(fetchDropSceneJpg).mock.calls.filter(call=>call[1]==='nosr').length;
  store.setPreviewDiv(1);await vi.waitFor(()=>expect(store.imageLoad.active).toBe(false));
  expect(nosr.detailDiv).toBe(1);
  expect(vi.mocked(fetchDropSceneJpg).mock.calls.filter(call=>call[1]==='nosr')).toHaveLength(count);
  store.setPreviewDiv(8);await vi.waitFor(()=>expect(nosr.detailDiv).toBe(2));
  expect(store.previewDiv).toBe(8);
});

it('merges metadata only, then loads a companion once on repeated clicks', async () => {
  const verified = await apiResolveScene({ apiBase: '', staticBase: '' }, { name: 'selected.tif' });
  vi.mocked(apiResolveScene).mockResolvedValue({ ...verified, discovery: 'grant' });
  vi.mocked(apiSceneTiffFiles).mockResolvedValue([
    { id: 'selected', name: 'selected.tif', size_bytes: 3, srcRev: 'main-rev',
      kind: 'input', suffix: '', mtime_ms: 1, readOnly: true, viewerRole: 'stage' },
    ...['PAN_ori.tif', 'other.TIFF', 'third.tiff', 'fourth.tif', 'fifth.tif'].map((name, index) => ({
      id: 'companion-' + index, name, size_bytes: 3, srcRev: 'rev-' + index,
      kind: null, suffix: '', mtime_ms: 1, readOnly: true as const,
      viewerRole: index === 0 ? 'reference' as const : 'other' as const,
    })),
  ]);
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/assets') && JSON.parse(String(init?.body)).sceneId === 'companion-0') {
      return Response.json({ id: 'asset-ref', name: 'PAN_ori.tif', W: 100, H: 100,
        sourceRev: 'rev-0', uploaded: false });
    }
    if (url.includes('/assets/asset-ref/preview?')) {
      return new Response('jpg', { headers: { 'X-Preview-Width': '2', 'X-Preview-Height': '2' } });
    }
    if (url.endsWith('/assets') && JSON.parse(String(init?.body)).sceneId === 'companion-1') {
      return Response.json({ id: 'asset-tiff', name: 'other.TIFF', W: 100, H: 100,
        sourceRev: 'rev-1', uploaded: false });
    }
    if (url.includes('/assets/asset-tiff/tiles?')) {
      return Response.json({ width: 100, height: 100, tileSize: 512, band_count: 1,
        sourceVersion: 'rev-1', rasterVersion: 'raster-tiff', displayProfiles: true });
    }
    if (url.includes('/assets/asset-tiff/preview?')) {
      return new Response('jpg', { headers: { 'X-Preview-Width': '2', 'X-Preview-Height': '2' } });
    }
    return new Response(null, { status: 404 });
  });
  vi.stubGlobal('fetch', fetcher);
  const store = await selectTiff();
  await vi.waitFor(() => expect(store.recs).toHaveLength(6));
  const cards = store.recs.filter(rec => rec.discoveredTiff);
  const cardElement = (id: number) => root.querySelector(`[data-rec-id="${id}"]`);
  expect(cards).toHaveLength(5);
  expect(cardElement(cards[0].id)?.getAttribute('title')).toContain('打开后检查高清图块');
  expect(cards.every(rec => !rec.thumb && !rec.W && !rec.lqPath)).toBe(true);
  expect(fetchSceneJpg).not.toHaveBeenCalled();
  expect(fetcher.mock.calls).toHaveLength(1);
  await Promise.all([store.activate(cards[0].id), store.activate(cards[0].id)]);
  expect(store.activeRec).toMatchObject({ sceneId: 'companion-0', W: 100, H: 100,
    previewOnly: true, lqPath: null, viewerRole: 'reference' });
  expect(cards[0].thumb).toBeTruthy();
  expect(cards.slice(1).every(rec => !rec.thumb)).toBe(true);
  expect(fetcher.mock.calls.filter(([url]) => url.includes('/assets/asset-ref/preview?'))).toHaveLength(1);
  expect(cards[0].tileSource).toBeNull();
  expect(cardElement(cards[0].id)?.getAttribute('title')).toContain('仅整图预览');
  expect(cardElement(cards[0].id)?.querySelector('[data-e2e="file-preview-status"]')?.textContent).toBe('仅整图预览');
  await store.activate(cards[1].id);
  expect(store.activeRec?.tileSource?.asset.id).toBe('asset-tiff');
  await vi.waitFor(() => expect(root.querySelector('[data-e2e="native-zoom"]')).toBeTruthy());
  expect(cardElement(cards[1].id)?.getAttribute('title')).toContain('支持按需高清');
  expect(cardElement(cards[1].id)?.querySelector('[data-e2e="file-preview-status"]')?.textContent).toBe('支持按需高清');
  store.addFiles(new File(['raw'], 'selected.tif'));
  await vi.waitFor(() => expect(store.imageLoad.active).toBe(false));
  expect(store.recs).toHaveLength(6);
  expect(store.recs.map(rec => rec.sceneId).filter(id => id === 'selected')).toHaveLength(1);
});

it('discards a discovery response after the workspace is cleared', async () => {
  const verified = await apiResolveScene({ apiBase: '', staticBase: '' }, { name: 'selected.tif' });
  vi.mocked(apiResolveScene).mockResolvedValue({ ...verified, discovery: 'grant' });
  let respond!: (files: Awaited<ReturnType<typeof apiSceneTiffFiles>>) => void;
  vi.mocked(apiSceneTiffFiles).mockImplementationOnce(() => new Promise(resolve => { respond = resolve; }));
  const store = await selectTiff();
  await vi.waitFor(() => expect(respond).toBeTypeOf('function'));
  store.clearRecs();
  respond([{ id: 'stale', name: 'PAN_ori.tif', size_bytes: 3, srcRev: 'rev-0',
    kind: null, suffix: '', mtime_ms: 1, readOnly: true, viewerRole: 'reference' }]);
  await Promise.resolve();
  expect(store.recs).toHaveLength(0);
});
