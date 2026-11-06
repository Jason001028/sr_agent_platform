import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  FLICKER_INTERVALS_MS, DEFAULT_FLICKER_INTERVAL_MS, FLICKER_ASPECT_TOLERANCE,
  parseFlickerInterval, loadFlickerInterval, saveFlickerInterval,
  registrationRefusal, flickerLabel, flickerPairLabel, pickFlickerPartner,
  aspectDeviation,
} from '../flicker.js';
import type { FlickerImageMeta } from '../flicker.js';

function meta(p: Partial<FlickerImageMeta> & { id: number }): FlickerImageMeta {
  return {
    w: 8000, h: 6000, sceneDir: '/DiskArray/GSHC2IMPS/2026/01/01/S001',
    stageKind: 'input', stageLabel: 'PAN', name: 'PAN.tif',
    ...p,
  };
}

describe('节拍', () => {
  it('五档：0.6 / 0.8 / 1.0 / 1.2 / 1.5 秒', () => {
    expect([...FLICKER_INTERVALS_MS]).toEqual([600, 800, 1000, 1200, 1500]);
    expect(DEFAULT_FLICKER_INTERVAL_MS).toBe(1000);
  });

  it('只认五档，脏值回默认', () => {
    for (const ms of FLICKER_INTERVALS_MS) expect(parseFlickerInterval(ms)).toBe(ms);
    for (const v of [0, 599, 601, 2000, 'abc', null, undefined, NaN, {}]) {
      expect(parseFlickerInterval(v)).toBe(DEFAULT_FLICKER_INTERVAL_MS);
    }
    expect(parseFlickerInterval('1200')).toBe(1200);   // localStorage 存的是字符串
  });
});

describe('节拍持久化', () => {
  const store = new Map<string, string>();
  beforeEach(() => {
    store.clear();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
      setItem: (k: string, v: string) => { store.set(k, v); },
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('没存过 → 默认；存过 → 读回', () => {
    expect(loadFlickerInterval()).toBe(DEFAULT_FLICKER_INTERVAL_MS);
    saveFlickerInterval(1500);
    expect(loadFlickerInterval()).toBe(1500);
  });

  it('写入前先规范化：脏值落成默认值，不把脏值写进 localStorage', () => {
    saveFlickerInterval(1234);
    expect(loadFlickerInterval()).toBe(DEFAULT_FLICKER_INTERVAL_MS);
  });

  it('localStorage 抛错时不炸', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
    });
    expect(loadFlickerInterval()).toBe(DEFAULT_FLICKER_INTERVAL_MS);
    expect(() => saveFlickerInterval(800)).not.toThrow();
  });
});

describe('registrationRefusal（能否确认同一地面）', () => {
  it('同场景目录 + 宽高比一致 → 放行', () => {
    expect(registrationRefusal(meta({ id: 1 }), meta({
      id: 2, w: 16000, h: 12000, stageKind: 'product', stageLabel: 'SR',
    }))).toBeNull();
  });

  it('尺寸未知 → 拒绝并说明', () => {
    expect(registrationRefusal(meta({ id: 1, w: 0 }), meta({ id: 2 })))
      .toContain('尺寸未知');
    expect(registrationRefusal(meta({ id: 1, h: 0 }), meta({ id: 2 })))
      .toContain('尺寸未知');
  });

  it('缺少场景目录 → 拒绝（本地单张图不能被自动配）', () => {
    expect(registrationRefusal(meta({ id: 1, sceneDir: null }), meta({ id: 2 })))
      .toContain('缺少共同的盘阵场景目录');
    expect(registrationRefusal(meta({ id: 1, sceneDir: '' }), meta({ id: 2 })))
      .toContain('缺少共同的盘阵场景目录');
  });

  it('不同场景目录 → 拒绝', () => {
    expect(registrationRefusal(meta({ id: 1 }), meta({
      id: 2, sceneDir: '/DiskArray/GSHC2IMPS/2026/01/01/S002',
    }))).toContain('不同的场景目录');
  });

  it('宽高比偏差按比例计算，重采样的取整误差不误伤', () => {
    expect(aspectDeviation(30831, 30948, 15768, 15823)).toBeLessThan(FLICKER_ASPECT_TOLERANCE);
    expect(aspectDeviation(8000, 6000, 8000, 6000)).toBe(0);
    expect(aspectDeviation(0, 6000, 8000, 6000)).toBe(Infinity);
  });

  it('宽高比偏差 ≤1% 放行，>1% 拒绝', () => {
    // 4:3 = 1.3333；1.4% 偏差 → 拒绝
    const dev = 1 + FLICKER_ASPECT_TOLERANCE * 1.4;
    expect(registrationRefusal(meta({ id: 1, w: 8000, h: 6000 }), meta({
      id: 2, w: Math.round(8000 * dev), h: 6000,
    }))).toContain('宽高比不一致');
    // 0.8% 偏差 → 放行
    const small = 1 + FLICKER_ASPECT_TOLERANCE * 0.8;
    expect(registrationRefusal(meta({ id: 1, w: 8000, h: 6000 }), meta({
      id: 2, w: Math.round(8000 * small), h: 6000,
    }))).toBeNull();
  });
});

describe('标签', () => {
  it('优先环节标签，其次文件名去扩展名', () => {
    expect(flickerLabel(meta({ id: 1, stageLabel: 'NOSR' }))).toBe('NOSR');
    expect(flickerLabel(meta({ id: 1, stageLabel: null, name: 'GF07A03.tif' }))).toBe('GF07A03');
    expect(flickerLabel(meta({ id: 1, stageLabel: '  ', name: 'A.JPEG' }))).toBe('A');
  });

  it('配对文字是 A/B', () => {
    expect(flickerPairLabel(
      meta({ id: 1, stageLabel: 'PAN' }),
      meta({ id: 2, stageLabel: 'NOSR' }),
    )).toBe('PAN/NOSR');
  });
});

describe('pickFlickerPartner', () => {
  it('没有场景目录 → 不配（本地图不猜）', () => {
    expect(pickFlickerPartner(meta({ id: 1, sceneDir: null }), [meta({ id: 2 })])).toBeNull();
  });

  it('只看同场景、非自己、非占位', () => {
    const active = meta({ id: 1 });
    const picked = pickFlickerPartner(active, [
      meta({ id: 1, stageKind: 'product', stageLabel: 'SR' }),
      meta({ id: 2, pending: true, stageKind: 'nosr', stageLabel: 'NOSR' }),
      meta({ id: 3, sceneDir: '/other', stageKind: 'nosr', stageLabel: 'NOSR' }),
      meta({ id: 4, stageKind: 'nosr', stageLabel: 'NOSR' }),
    ]);
    expect(picked?.id).toBe(4);
  });

  it('看本体时优先 NOSR，其次产物', () => {
    const active = meta({ id: 1, stageKind: 'input' });
    const picked = pickFlickerPartner(active, [
      meta({ id: 2, stageKind: 'product', stageLabel: 'SR' }),
      meta({ id: 3, stageKind: 'nosr', stageLabel: 'NOSR' }),
    ]);
    expect(picked?.id).toBe(3);
  });

  it('看产物时优先 NOSR；看 NOSR 时优先产物', () => {
    const product = meta({ id: 1, stageKind: 'product', stageLabel: 'SR' });
    expect(pickFlickerPartner(product, [
      meta({ id: 2, stageKind: 'input', stageLabel: 'PAN' }),
      meta({ id: 3, stageKind: 'nosr', stageLabel: 'NOSR' }),
    ])?.id).toBe(3);

    const nosr = meta({ id: 1, stageKind: 'nosr', stageLabel: 'NOSR' });
    expect(pickFlickerPartner(nosr, [
      meta({ id: 2, stageKind: 'input', stageLabel: 'PAN' }),
      meta({ id: 3, stageKind: 'product', stageLabel: 'SR' }),
    ])?.id).toBe(3);
  });

  it('能配准的优先于不能配准的（哪怕环节更互补）', () => {
    const active = meta({ id: 1 });
    const picked = pickFlickerPartner(active, [
      meta({ id: 2, stageKind: 'nosr', stageLabel: 'NOSR', w: 9000 }),   // 宽高比不对
      meta({ id: 3, stageKind: 'product', stageLabel: 'SR' }),           // 同比例
    ]);
    expect(picked?.id).toBe(3);
  });

  it('同分时 id 小的先选（稳定）', () => {
    const picked = pickFlickerPartner(meta({ id: 9 }), [
      meta({ id: 7, stageKind: 'nosr', stageLabel: 'NOSR' }),
      meta({ id: 5, stageKind: 'nosr', stageLabel: 'NOSR' }),
    ]);
    expect(picked?.id).toBe(5);
  });
});
