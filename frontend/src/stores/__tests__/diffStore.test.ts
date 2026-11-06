/**
 * stores/diff.ts 行为测试 —— 覆盖复审里已证实的交互缺陷：
 *
 * 1. 分析期间改参数**或换像素（srcRev/token）** → 旧响应必须丢弃；
 * 2. 结果与显示图层在解码完成后一次性发布，解码期间失效则什么都不装；
 * 3. 「加入掩码 / 定位」必须绑定**产生结果**的影像与像素代次，不能跟着后来换掉的配对走；
 * 4. 羽化切换要立即重建显示层。
 *
 * 图形层用最小 Canvas/Image 假实现，只保留数据通路，不测像素观感。
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { nextTick, reactive } from 'vue';
import { createPinia, setActivePinia } from 'pinia';

const mocks = vi.hoisted(() => ({ viewer: null as any, api: vi.fn(), siblings: vi.fn() }));

vi.mock('../viewer', () => ({ useViewerStore: () => mocks.viewer }));
vi.mock('../../lib/api.js', () => ({
  apiDiffAnalyze: (...args: unknown[]) => mocks.api(...args),
  apiSceneSiblings: (...args: unknown[]) => mocks.siblings(...args),
}));
vi.mock('../../lib/scene.js', () => ({ loadSrConfig: () => ({}) }));

import { useDiffStore } from '../diff';
import type { DiffResult } from '../../lib/diffLocate';

class FakeCtx {
  data: Uint8ClampedArray | null = null;
  globalCompositeOperation = 'source-over';
  globalAlpha = 1;
  fillStyle = '';
  imageSmoothingEnabled = true;
  filter = 'none';
  putImageData(img: { data: Uint8ClampedArray }) {
    this.data = new Uint8ClampedArray(img.data);
  }
  getImageData(_x: number, _y: number, w: number, h: number) {
    const size = w * h * 4;
    return {
      data: this.data && this.data.length === size
        ? this.data : new Uint8ClampedArray(size),
      width: w,
      height: h,
    };
  }
  drawImage(src: any) {
    const data = src?.ctx?.data ?? src?.data;
    if (data) this.data = new Uint8ClampedArray(data);
  }
  fillRect() {
    if (this.globalCompositeOperation === 'source-in' && this.data) {
      for (let i = 0; i < this.data.length; i += 4) {
        if (this.data[i + 3] > 0) {
          this.data[i] = 224; this.data[i + 1] = 86; this.data[i + 2] = 50;
        }
      }
    }
  }
}

class FakeCanvas {
  width = 1;
  height = 1;
  ctx = new FakeCtx();
  getContext() { return this.ctx; }
  toDataURL() { return 'data:image/png;base64,AAAA'; }
}

class FakeImage {
  width = 2;
  height = 2;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  set src(_v: string) {
    if (holdImages) imageQueue.push(this);
    else queueMicrotask(() => this.onload?.());
  }
}

/** 结果图层解码的阶段控制：需要模拟「解码期间改参数」时先挂起。 */
let holdImages = false;
const imageQueue: FakeImage[] = [];
function flushImages(): void {
  holdImages = false;
  for (const img of imageQueue.splice(0)) img.onload?.();
}

function rec(id: number, name: string) {
  const w = 32;
  const h = 32;
  const src = new Float32Array(w * h);
  for (let i = 0; i < src.length; i++) src[i] = ((i + id * 7) % 29) / 29;
  return {
    id, name, src, srcw: w, srch: h, nbands: 1, W: w, H: h,
    thumb: { width: 16, height: 16 }, pending: false, sceneId: 's' + id,
    srcRev: 'v1', tourIdentity: {id,sequence:'fixture',role:id===1?'pan':'nosr',width:w,height:h,sourceRevision:'v1'},
  } as any;
}

/** 场景卡：给 rec 补上场景目录与环节（延续到下一景按这两项分组、按角色配对）。 */
function sceneRec(
  id: number, name: string, sceneDir: string,
  stageKind?: 'input' | 'product' | 'nosr', stageLabel = '',
) {
  const r = rec(id, name);
  r.sceneDir = sceneDir;
  r.stageKind = stageKind;
  r.stageLabel = stageLabel;
  return r;
}

function fakeResult(overrides: Partial<DiffResult> = {}): DiffResult {
  return {
    operation: 'difference-localization',
    method: 'absdiff',
    source: 'preview',
    imageA: {
      id: 1, name: 'PAN', sourceWidth: 32, sourceHeight: 32,
      thumbWidth: 16, thumbHeight: 16, gridWidth: 32, gridHeight: 32,
    },
    imageB: {
      id: 2, name: 'PAN_old', sourceWidth: 32, sourceHeight: 32,
      thumbWidth: 16, thumbHeight: 16, gridWidth: 32, gridHeight: 32,
    },
    analysisGrid: {
      width: 32, height: 32, origin: [0, 0], scaleA: [1, 1], scaleB: [1, 1],
      mode: 'preview',
    },
    differenceMap: 'data:image/png;base64,AAAA',
    binaryMask: 'data:image/png;base64,AAAA',
    regions: [{
      id: 'r001', rank: 1, areaPx: 100, relativeArea: 0.1,
      bbox: [4, 4, 10, 10], centroid: [9, 9], meanDiff: 0.5, maxDiff: 0.8,
      rankScore: 5, contour: [[4, 4], [14, 4], [14, 14], [4, 14]],
      contourKind: 'exact', holes: 0,
    }],
    truncated: false,
    coverage: { validPixels: 1024, gridPixels: 1024, validRatio: 1 },
    parameters: {
      method: 'absdiff', sensitivity: 'standard', threshold: 0.12, minAreaPx: 64,
      blur: true, openKernel: 3, closeKernel: 5, ssimWindow: 7,
      ssimGaussian: false, backend: 'scipy',
    },
    warnings: [],
    elapsedMs: 10,
    ...overrides,
  } as DiffResult;
}

function makeViewer() {
  const a = rec(1, 'PAN');
  const b = rec(2, 'PAN_old');
  const c = rec(3, 'OTHER');
  const state: any = reactive({
    recs: [a, b, c],
    activeRec: a,
    panes: [{ side: 'A', rec: a, rect: { x: 0, y: 0, w: 800, h: 600 }, view: { scale: 1, ox: 0, oy: 0 }, active: true }],
    split: false,
    paneB: null,
    compareMode: 'none',
    compareOn: false,
    drawMode: false,
    renderTick: 0,
    focusPaneRect: vi.fn(() => true),
    addMaskPolygon: vi.fn(() => true),
    openComparePair: vi.fn(() => true),
    ensureSceneImage: vi.fn(() => null),
    ensureCardPixels: vi.fn(async () => true),
    activate: vi.fn(async (id: number) => {
      state.activeRec = state.recs.find((r: any) => r.id === id);
      state.panes = [{ side: 'A', rec: state.activeRec, rect: { x: 0, y: 0, w: 800, h: 600 }, view: { scale: 1, ox: 0, oy: 0 }, active: true }];
    }),
  });
  return state;
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.stubGlobal('document', { createElement: () => new FakeCanvas() });
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('ImageData', class {
    data: Uint8ClampedArray;
    width: number;
    height: number;
    constructor(data: Uint8ClampedArray, width: number, height: number) {
      this.data = data; this.width = width; this.height = height;
    }
  });
  mocks.viewer = makeViewer();
  mocks.api.mockReset();
  mocks.siblings.mockReset();
  mocks.siblings.mockResolvedValue({ items: [], tourItems: [] });
  holdImages = false;
  imageQueue.length = 0;
});
afterEach(() => {
  holdImages = false;
  imageQueue.length = 0;
  vi.unstubAllGlobals();
});

describe('diff store — 请求与结果身份', () => {
  it('analyzes proportional PAN/NOSR at different resolutions without tour certificates', async () => {
    const viewer = mocks.viewer;
    viewer.recs[0].tourIdentity = undefined;
    viewer.recs[1].tourIdentity = undefined;
    viewer.recs[1].W *= 4; viewer.recs[1].H *= 4;
    const diff = useDiffStore();
    diff.aId = viewer.recs[0].id; diff.bId = viewer.recs[1].id;
    mocks.api.mockResolvedValue(fakeResult());
    expect(diff.precisionReason).toBeNull();
    expect(await diff.analyze()).toBe(true);
    expect(mocks.api).toHaveBeenCalledOnce();
    expect(diff.result?.warnings.join(' ')).toContain('分辨率不同');
  });

  it('discards a response for parameters changed while the request was in flight', async () => {
    let resolve!: (value: DiffResult) => void;
    mocks.api.mockReturnValue(new Promise<DiffResult>((r) => { resolve = r; }));
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    const pending = diff.analyze();
    expect(diff.busy).toBe(true);
    diff.method = 'ssim';                 // 请求期间改方法
    resolve(fakeResult());
    expect(await pending).toBe(false);
    expect(diff.result).toBeNull();
    expect(diff.notice).toContain('丢弃');
  });

  it('applies a response when the signature did not change', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    expect(await diff.analyze()).toBe(true);
    expect(diff.result).not.toBeNull();
    expect(diff.stale).toBe(false);
  });

  it('discards a response when the pair pixels were reloaded in flight', async () => {
    let resolve!: (value: DiffResult) => void;
    mocks.api.mockReturnValue(new Promise<DiffResult>((r) => { resolve = r; }));
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    const pending = diff.analyze();
    const a = mocks.viewer.recs[0];
    a.src = new Float32Array(a.src.length);   // 同一记录 ID，换了像素
    a.srcRev = 'rev-2';
    a.token = 7;
    resolve(fakeResult());
    expect(await pending).toBe(false);
    expect(diff.result).toBeNull();
    expect(diff.notice).toContain('丢弃');
  });

  it('publishes result and layers only after decoding, and drops on param change', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    holdImages = true;
    const pending = diff.analyze();
    await Promise.resolve();
    await Promise.resolve();
    expect(imageQueue.length).toBe(2);
    // 解码未完成 → 结果与图层都还没发布。
    expect(diff.result).toBeNull();
    expect(diff.heatLayer).toBeNull();
    diff.advanced = { ...diff.advanced, threshold: 0.2 };
    flushImages();
    expect(await pending).toBe(false);
    expect(diff.result).toBeNull();
    expect(diff.heatLayer).toBeNull();
    expect(diff.notice).toContain('解码');
  });

  it('invalidates a published result when the source pixels change', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    expect(await diff.analyze()).toBe(true);
    expect(diff.stale).toBe(false);
    const a = mocks.viewer.recs[0];
    a.src = new Float32Array(a.src.length);
    a.srcRev = 'rev-2';
    expect(diff.stale).toBe(true);
    expect(diff.addRegionToMask('r001')).toBe(false);
    expect(mocks.viewer.addMaskPolygon).not.toHaveBeenCalled();
  });

  it('rebuilds the display mask when feather changes', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    await diff.analyze();
    const before = diff.maskLayer;
    diff.feather = 'medium';
    await nextTick();
    expect(diff.maskLayer).not.toBe(before);
  });
});

describe('diff store — 候选绑定结果影像', () => {
  it('adds the contour to the result image, not the current pair', () => {
    const diff = useDiffStore();
    diff.result = fakeResult();
    // 模拟结果签名与当前参数一致、但当前配对已被改成 3/4 的错位场景。
    diff.aId = 3;
    diff.resultKey = diff.currentKey();
    expect(diff.addRegionToMask('r001')).toBe(true);
    expect(mocks.viewer.addMaskPolygon).toHaveBeenCalledTimes(1);
    expect(mocks.viewer.addMaskPolygon.mock.calls[0][0]).toBe(1);
  });

  it('refuses stale results, bbox fallbacks and regions with holes', () => {
    const diff = useDiffStore();
    diff.result = fakeResult();
    diff.aId = 3;
    // 未重新签 resultKey → stale
    expect(diff.addRegionToMask('r001')).toBe(false);
    expect(mocks.viewer.addMaskPolygon).not.toHaveBeenCalled();

    diff.resultKey = diff.currentKey();
    diff.result = fakeResult({
      regions: [{
        id: 'r001', rank: 1, areaPx: 100, relativeArea: 0.1,
        bbox: [4, 4, 10, 10], centroid: [9, 9], meanDiff: 0.5, maxDiff: 0.8,
        rankScore: 5, contour: [[4, 4], [14, 4], [14, 14], [4, 14]],
        contourKind: 'bbox', holes: 0,
      }],
    });
    expect(diff.addRegionToMask('r001')).toBe(false);

    diff.result = fakeResult({
      regions: [{
        id: 'r001', rank: 1, areaPx: 100, relativeArea: 0.1,
        bbox: [4, 4, 10, 10], centroid: [9, 9], meanDiff: 0.5, maxDiff: 0.8,
        rankScore: 5, contour: [[4, 4], [14, 4], [14, 14], [4, 14]],
        contourKind: 'outer', holes: 1,
      }],
    });
    expect(diff.addRegionToMask('r001')).toBe(false);

    diff.result = fakeResult({
      regions: [{
        id: 'r001', rank: 1, areaPx: 100, relativeArea: 0.1,
        bbox: [4, 4, 10, 10], centroid: [9, 9], meanDiff: 0.5, maxDiff: 0.8,
        rankScore: 5, contour: [[4, 4], [14, 4], [14, 14], [4, 14]],
        contourKind: 'approx', holes: 0,
      }],
    });
    expect(diff.addRegionToMask('r001')).toBe(false);
    expect(mocks.viewer.addMaskPolygon).not.toHaveBeenCalled();
  });

  it('locates on a pane showing the result image, never on an unrelated one', async () => {
    const diff = useDiffStore();
    diff.result = fakeResult();
    mocks.viewer.activeRec = mocks.viewer.recs[2];     // 当前显示无关影像 C
    expect(await diff.locateRegion('r001')).toBe(true);
    expect(mocks.viewer.focusPaneRect).toHaveBeenCalledWith('A', expect.any(Array));
    // 定位的是结果里的 A，不是当前活动影像 C。
    expect(mocks.viewer.activeRec.id).toBe(3);
  });

  it('activates the result image when no pane shows it', async () => {
    const diff = useDiffStore();
    diff.result = fakeResult();
    mocks.viewer.panes = [{ side: 'A', rec: mocks.viewer.recs[2], rect: { x: 0, y: 0, w: 800, h: 600 }, view: { scale: 1, ox: 0, oy: 0 }, active: true }];
    mocks.viewer.activeRec = mocks.viewer.recs[2];
    expect(await diff.locateRegion('r001')).toBe(true);
    expect(mocks.viewer.activate).toHaveBeenCalledWith(1);
    expect(mocks.viewer.focusPaneRect).toHaveBeenCalledWith('A', expect.any(Array));
  });
});


describe('diff visualization controls', () => {
  it('hides and restores cached layers and selection without changing camera, parameters or results', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1; diff.bId = 2;
    await diff.analyze();
    diff.display = 'heat';
    diff.selectRegion('r001');
    const result = diff.result, heat = diff.heatLayer, regions = diff.regions;
    const key = diff.currentKey();
    const camera = { ...mocks.viewer.panes[0].view };
    diff.openPanel(); diff.closePanel();
    expect(diff.resultVisible).toBe(true);
    expect(diff.layerFor(mocks.viewer.recs[0])?.heat).toBe(heat);
    diff.resultVisible = false;
    await nextTick();
    expect(diff.layerFor(mocks.viewer.recs[0])).toBeNull();
    expect(diff.showHeat).toBe(false);
    expect(diff.showMarkers).toBe(false);
    expect(diff.result).toBe(result);
    expect(diff.regions).toBe(regions);
    expect(diff.selectedId).toBe('r001');
    diff.resultVisible = true;
    await nextTick();
    expect(diff.layerFor(mocks.viewer.recs[0])?.heat).toBe(heat);
    expect(diff.showMarkers).toBe(true);
    expect(mocks.viewer.panes[0].view).toEqual(camera);
    expect(diff.currentKey()).toBe(key);
    expect(mocks.api).toHaveBeenCalledTimes(1);
    diff.clearResults();
    expect(diff.result).toBeNull();
    expect(diff.heatLayer).toBeNull();
    expect(diff.regions).toEqual([]);
  });

  it('all modes, alpha and feather change locally; layers follow result identities and ROI coverage', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1; diff.bId = 2;
    await diff.analyze();
    const result = diff.result!, heat = diff.heatLayer, key = diff.currentKey();
    result.analysisGrid = { ...result.analysisGrid, width: 8, height: 8, fullWidth: 32, fullHeight: 32, scopeX: 8, scopeY: 16 };
    for (const mode of ['regions', 'heat', 'overlay'] as const) {
      diff.display = mode;
      diff.opacity = 0.7;
      diff.feather = 'medium';
      await nextTick();
      expect(diff.showHeat).toBe(mode !== 'regions');
      expect(diff.showMarkers).toBe(true);
      expect(diff.layerFor(mocks.viewer.recs[0])?.coverage).toEqual([4, 8, 4, 4]);
      expect(diff.layerFor(mocks.viewer.recs[1])?.heat).toBe(heat);
      expect(diff.layerFor(mocks.viewer.recs[2])).toBeNull();
      expect(diff.result).toBe(result);
      expect(diff.currentKey()).toBe(key);
      expect(diff.stale).toBe(false);
    }
    expect(mocks.api).toHaveBeenCalledTimes(1);
  });
});

describe('diff store — 同景收口', () => {
  it('lists only images from the scene selected on the left', () => {
    const v = mocks.viewer;
    v.recs[0].sceneDir = '/scenes/20260101'; v.recs[1].sceneDir = '/scenes/20260101';
    v.recs[2].sceneDir = '/scenes/20260102';
    const diff = useDiffStore();
    expect(diff.pairOptions.map((o) => o.id)).toEqual([1, 2]);
    v.activeRec = v.recs[2];
    expect(diff.pairOptions.map((o) => o.id)).toEqual([3]);
  });

  it('adds unopened same-scene images to the dropdown when the panel opens', async () => {
    const v = mocks.viewer;
    v.recs.forEach((r: any) => { r.sceneDir = '/scenes/20260101'; });
    mocks.siblings.mockResolvedValue({
      lqPath: '/scenes/20260101', tourItems: [],
      items: [{ kind: 'nosr', id: 's9', name: 'PAN_NOSR.tif', exists: true, W: 32, H: 32, srcRev: 'v9' }],
    });
    v.ensureSceneImage.mockImplementation((item: any) => {
      v.recs.push({
        id: 9, name: item.name, W: item.W, H: item.H, sceneId: item.id,
        sceneDir: '/scenes/20260101', srcRev: 'v9', pending: false, card: {},
      });
      return 9;
    });
    const diff = useDiffStore();
    diff.openPanel();
    await vi.waitFor(() => expect(diff.pairOptions.map((o) => o.id)).toContain(9));
    expect(mocks.siblings).toHaveBeenCalledWith({}, 's1', undefined, true, ['s1', 's2', 's3']);
  });

  it('fetches pixels for an unopened same-scene image before analyzing', async () => {
    const v = mocks.viewer;
    v.recs.forEach((r: any) => { r.sceneDir = '/scenes/20260101'; });
    v.recs.push({
      id: 9, name: 'PAN_ori.tif', W: 32, H: 32, sceneId: 's9',
      sceneDir: '/scenes/20260101', srcRev: 'v9', pending: false,
      thumb: null, src: null, card: {},
    });
    v.ensureCardPixels.mockImplementation(async (id: number) => {
      const r = v.recs.find((x: any) => x.id === id);
      if (!r) return false;
      if (!r.thumb) {
        r.thumb = { width: 16, height: 16 };
        r.src = new Float32Array(32 * 32);
        r.srcw = 32; r.srch = 32; r.nbands = 1;
      }
      return true;
    });
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1; diff.bId = 9;
    expect(await diff.analyze()).toBe(true);
    expect(v.ensureCardPixels).toHaveBeenCalledWith(9);
  });
});

describe('diff store — 延续到下一景', () => {
  function twoSceneViewer() {
    const v = mocks.viewer;
    const pan1 = sceneRec(1, 'PAN.tif', '/scenes/A', 'input', 'PAN');
    const nosr1 = sceneRec(2, 'PAN_NOSR.tif', '/scenes/A', 'nosr', 'NOSR');
    const pan2 = sceneRec(11, 'PAN.tif', '/scenes/B', 'input', 'PAN');
    const sr2 = sceneRec(12, 'PAN_SR.tif', '/scenes/B', 'product', 'SR');
    const nosr2 = sceneRec(13, 'PAN_NOSR.tif', '/scenes/B', 'nosr', 'NOSR');
    v.recs = [pan1, nosr1, pan2, sr2, nosr2];
    v.activeRec = pan1;
    v.panes = [{ side: 'A', rec: pan1, rect: { x: 0, y: 0, w: 800, h: 600 }, view: { scale: 1, ox: 0, oy: 0 }, active: true }];
    return { v, pan1, nosr1, pan2, sr2, nosr2 };
  }

  it('只在本景有热力结果、单图显示且还有下一景时出现', async () => {
    const { v, pan1, nosr1 } = twoSceneViewer();
    const diff = useDiffStore();
    diff.aId = pan1.id; diff.bId = nosr1.id;
    mocks.api.mockResolvedValue(fakeResult());

    expect(diff.canContinueScene).toBe(false);       // 还没有结果
    await diff.analyze();
    diff.display = 'regions';
    expect(diff.canContinueScene).toBe(false);       // 原图标记不是热力图
    diff.display = 'overlay';
    expect(diff.canContinueScene).toBe(true);

    v.compareOn = true;
    expect(diff.canContinueScene).toBe(false);       // 对比上下文不算单景图
    v.compareOn = false;

    v.activeRec = v.recs[4];                         // 末景没有下一景
    expect(diff.canContinueScene).toBe(false);
  });

  it('按当前 A/B 角色落到下一景，并直接重新分析', async () => {
    const { v, pan1, nosr1, nosr2, pan2 } = twoSceneViewer();
    const diff = useDiffStore();
    // 当前 A/B 故意反着填：A=NOSR、B=PAN，下一景也必须维持这个角色。
    diff.aId = nosr1.id; diff.bId = pan1.id;
    mocks.api.mockResolvedValue(fakeResult());
    await diff.analyze();
    diff.display = 'overlay';

    expect(diff.canContinueScene).toBe(true);
    expect(await diff.continueToNextScene()).toBe(true);
    // 活动景 watcher 的 ensurePair 会推荐 PAN/NOSR；这里必须保住 NOSR/PAN 的角色顺序。
    expect(v.activate).toHaveBeenCalledWith(nosr2.id);
    expect(diff.aId).toBe(nosr2.id);
    expect(diff.bId).toBe(pan2.id);
    expect(mocks.api).toHaveBeenCalledTimes(2);
  });

  it('下一景只开了本体时，向只读同景候选补齐缺的角色', async () => {
    const v = mocks.viewer;
    const pan1 = sceneRec(1, 'PAN.tif', '/scenes/A', 'input', 'PAN');
    const nosr1 = sceneRec(2, 'PAN_NOSR.tif', '/scenes/A', 'nosr', 'NOSR');
    const pan2 = sceneRec(11, 'PAN.tif', '/scenes/B', 'input', 'PAN');
    v.recs = [pan1, nosr1, pan2];
    v.activeRec = pan1;
    v.panes = [{ side: 'A', rec: pan1, rect: { x: 0, y: 0, w: 800, h: 600 }, view: { scale: 1, ox: 0, oy: 0 }, active: true }];
    mocks.siblings.mockResolvedValue({
      lqPath: '/scenes/B', tourItems: [],
      items: [{ kind: 'nosr', id: 's99', name: 'PAN_NOSR.tif', exists: true, W: 32, H: 32, srcRev: 'v9' }],
    });
    v.ensureSceneImage.mockImplementation((item: any, res: any) => {
      v.recs.push(sceneRec(99, item.name, res.lqPath, item.kind, 'NOSR'));
      return 99;
    });
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = pan1.id; diff.bId = nosr1.id;
    await diff.analyze();
    diff.display = 'heat';

    expect(await diff.continueToNextScene()).toBe(true);
    expect(mocks.siblings).toHaveBeenCalledWith({}, 's11', undefined, true, ['s11']);
    expect(v.activate).toHaveBeenCalledWith(pan2.id);
    expect(diff.aId).toBe(pan2.id);
    expect(diff.bId).toBe(99);
  });

  it('没有下一景或下一景凑不出兼容配对时保持原状并给出原因', async () => {
    const { v, pan1, nosr1 } = twoSceneViewer();
    const diff = useDiffStore();
    diff.aId = pan1.id; diff.bId = nosr1.id;
    mocks.api.mockResolvedValue(fakeResult());
    await diff.analyze();
    diff.display = 'overlay';

    // 下一景只剩一张图：角色补不齐、也构不成一对。
    v.recs = v.recs.filter((r: any) => r.id !== 13 && r.id !== 12);
    const calls = mocks.api.mock.calls.length;
    expect(await diff.continueToNextScene()).toBe(false);
    expect(diff.error).toContain('下一景');
    expect(mocks.api.mock.calls.length).toBe(calls);

    // 末景：没有下一景。
    v.activeRec = v.recs.find((r: any) => r.id === 11);
    expect(diff.canContinueScene).toBe(false);
    expect(await diff.continueToNextScene()).toBe(false);
    expect(diff.error).toContain('没有下一景');
  });
});

describe('diff store — 亮度归因权重', () => {
  const tone = (weight: number) => ({
    meanA: 150, meanB: 155, delta: 5, mid: 1, dark: 0, flat: 1,
    weight, norm: weight >= 1.8 ? 1 : 0, pixels: 100,
  });

  it('默认关闭时保持后端顺序，开启后按复核权重排序', () => {
    const res = fakeResult();
    res.regions = [
      { ...res.regions[0], id: 'r001', rank: 1, rankScore: 100, tone: tone(0.3) },
      { ...res.regions[0], id: 'r002', rank: 2, rankScore: 80, tone: tone(1.8) },
    ];
    const diff = useDiffStore();
    diff.result = res;

    expect(diff.toneWeight).toBe(false);
    expect(diff.orderedRegions.map((r) => r.id)).toEqual(['r001', 'r002']);
    expect(diff.viewerRegions.map((r) => r.id)).toEqual(['r001', 'r002']);

    diff.toneWeight = true;
    // 100×0.3 = 30 < 80×1.8 = 144
    expect(diff.orderedRegions.map((r) => r.id)).toEqual(['r002', 'r001']);
    expect(diff.viewerRegions.map((r) => r.id)).toEqual(['r002', 'r001']);
    expect(diff.regions.map((r) => r.id)).toEqual(['r001', 'r002']); // 原始顺序不变
  });

  it('开启逐像素加权会重建热力图层，关闭后重建未加权图层', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1; diff.bId = 2;
    expect(await diff.analyze()).toBe(true);
    const off = diff.heatLayer;
    expect(off).not.toBeNull();

    diff.toneWeight = true;
    await nextTick();
    const on = diff.heatLayer;
    expect(on).not.toBeNull();
    expect(on).not.toBe(off);

    diff.toneWeight = false;
    await nextTick();
    expect(diff.heatLayer).not.toBeNull();
    expect(diff.heatLayer).not.toBe(on);
    // 只重建显示层，不重新请求分析。
    expect(mocks.api).toHaveBeenCalledTimes(1);
  });

  it('分析结果会附带连续归因统计', async () => {
    mocks.api.mockResolvedValue(fakeResult());
    const diff = useDiffStore();
    diff.aId = 1;
    diff.bId = 2;
    expect(await diff.analyze()).toBe(true);
    const toneStats = diff.result?.regions[0].tone;
    expect(toneStats).toBeDefined();
    expect(toneStats!.weight).toBeGreaterThanOrEqual(0.3);
    expect(toneStats!.weight).toBeLessThanOrEqual(1.8);
    expect(toneStats!.norm).toBeGreaterThanOrEqual(0);
    expect(toneStats!.norm).toBeLessThanOrEqual(1);
  });
});
