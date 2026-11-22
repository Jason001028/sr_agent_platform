// @vitest-environment jsdom
import { createApp, nextTick, reactive, type App } from 'vue';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Overlay from '../MssWaterOverlay.vue';

const record = { id: 1, token: 1, sceneId: 'pan', sceneDir: '/pan', stageKind: 'input',
  route: 'jpg', srcRev: 'pan-r1', W: 8, H: 4, thumb: { width: 4, height: 2 } };
const viewer = reactive({ activeRec: { ...record }, overlayMasterVisible: true, drawMode: false,
  panes: [{ rec: { ...record }, rect: { x: 12, y: 0, w: 50, h: 40 }, view: { ox: 3, oy: 4, scale: 2 }, side: 'left' }] });
vi.mock('../../stores/viewer', () => ({ useViewerStore: () => viewer }));

const doc = { status: 'ready', version: 'mss-ndwi.v3', usage: 'viewer_preview_only', id: 'revision', profile: { threshold: 0.3 },
  sources: { pan: { preview_revision: 'pan-r1' } },
  alignment: { status: 'aligned', method: 'exact_embedded_geotiff_footprint', width: 8, height: 4 } };
let element: HTMLElement;
let app: App;

beforeEach(() => {
  viewer.activeRec = { ...record };
  viewer.overlayMasterVisible = true;
  element = document.createElement('div');
  app = createApp(Overlay);
  app.mount(element);
});

afterEach(() => {
  app.unmount();
  vi.unstubAllGlobals();
});

async function analyze(result: object) {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => result }));
  vi.stubGlobal('fetch', fetchMock);
  element.querySelector('button')!.click();
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(fetchMock).toHaveBeenCalledWith('/api/scenes/pan/mss-water', expect.objectContaining({ method: 'POST' }));
}

it('projects the overlay within its pane and honors both visibility controls', async () => {
  await analyze(doc);
  expect(element.querySelector<HTMLImageElement>('img')?.style.left).toBe('3px');
  expect(element.querySelector<HTMLImageElement>('img')?.style.width).toBe('8px');
  expect(element.querySelector('img')?.getAttribute('src')).toBe('/api/scenes/pan/mss-water/revision/overlay.png');
  element.querySelector('button')!.click();
  await nextTick();
  expect(element.querySelector('img')).toBeNull();
  element.querySelector('button')!.click();
  await nextTick();
  expect(element.querySelector('img')).not.toBeNull();
  viewer.overlayMasterVisible = false;
  await nextTick();
  expect(element.querySelector('img')).toBeNull();
});

it('offers native-grid downloads without requesting an unaligned overlay', async () => {
  await analyze({ ...doc, alignment: { status: 'unaligned', detail: '未完成空间对齐' } });
  expect(element.textContent).toContain('未完成空间对齐');
  expect(element.querySelector('.water-pane img')).toBeNull();
  expect(element.querySelector('.water-native img')?.getAttribute('src')).toMatch(/\/mss-preview\.png$/);
  expect(element.querySelector('a[href$="/valid.tif"]')).not.toBeNull();
  viewer.activeRec.token += 1;
  await nextTick();
  expect(element.querySelector('a')).toBeNull();
});

it('offers a retry when the native-grid asset expires after analysis', async () => {
  const result = { ...doc, alignment: { status: 'unaligned', detail: '未完成空间对齐' } };
  await analyze(result);
  element.querySelector('.water-native img')!.dispatchEvent(new Event('error'));
  await nextTick();
  expect(element.querySelector('.water-native')).toBeNull();
  expect(element.querySelector('[role="status"]')?.textContent).toContain('请重试');
  await analyze(result);
  expect(element.querySelector('.water-native img')).not.toBeNull();
});
