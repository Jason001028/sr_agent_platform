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

const mocks = vi.hoisted(() => ({ viewer: null as any, api: vi.fn() }));

vi.mock('../viewer', () => ({ useViewerStore: () => mocks.viewer }));
vi.mock('../../lib/api.js', () => ({
  apiDiffAnalyze: (...args: unknown[]) => mocks.api(...args),
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
  } as any;
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
    drawMode: false,
    renderTick: 0,
    focusPaneRect: vi.fn(() => true),
    addMaskPolygon: vi.fn(() => true),
    openComparePair: vi.fn(() => true),
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
  holdImages = false;
  imageQueue.length = 0;
});
afterEach(() => {
  holdImages = false;
  imageQueue.length = 0;
  vi.unstubAllGlobals();
});

describe('diff store — 请求与结果身份', () => {
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
