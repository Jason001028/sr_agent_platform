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
  apiResolveScene: vi.fn(), fetchDropSceneJpg: vi.fn(), fetchSceneJpg: vi.fn(), apiSceneSiblings: vi.fn() }));
vi.mock('../../lib/local_preview.js', async original => ({ ...await original<typeof import('../../lib/local_preview.js')>(),
  sourceFingerprint: vi.fn() }));
vi.mock('../../lib/localDecodeWorker', () => ({ decodeLocalFile: vi.fn() }));
import Toolbar from '../../components/Toolbar.vue';
import CompareBar from '../../components/CompareBar.vue';
import { apiResolveScene, fetchDropSceneJpg, fetchSceneJpg, apiSceneSiblings } from '../../lib/api';
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
  app = createApp({ render: () => h('div', [h(Toolbar), h(CompareBar)]) }); app.use(pinia); app.mount(root);
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
