/**
 * tifDecode.test.ts — tifDecode.ts 像素 golden 测试（自包含 fixtures）
 * ------------------------------------------------------------------
 * 用 gen-fixtures.py 生成的 6 个零依赖 fixture 做逐像素黄金断言，覆盖：
 *   - tiffTags / parseStrips / layoutInfo（classic + BigTIFF）
 *   - sparseSample 稀疏条带采样（端点精确对齐 mapX/mapY，灰度 golden）
 *   - sparseCollect 纯管线（photometric=0 → invert 反相 golden）
 *   - probeImage（GeoTIFF.fromBlob，FileReader shim 见 setup.ts）
 *   - chunkedCollect 分块降采样（scale=1 时逐像素等于原图，RGB golden）
 *   - computeStats / stretchMap / stretchRgba / planExport 纯函数
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  tiffTags, parseStrips, layoutInfo, sparseSample, sparseCollect,
  probeImage, getSamplePlan, chunkedCollect,
  computeStats, stretchMap, stretchRgba, planExport,
  SPARSE_PREVIEW_MAX, type SparseLayoutOk, type ProbeInfo,
} from '../tifDecode.js';
import { needGeo } from '../decode.js';
import { FileSource } from '../source.js';

interface FixtureMeta {
  name: string;
  bytes: number;
  W: number;
  H: number;
  spp: number;
  bits: number;
  photometric: number;
  compression: number;
  rps: number;
  sample_format: number;
  big: boolean;
}

const manifest = JSON.parse(
  readFileSync(new URL('../../../fixtures/manifest.json', import.meta.url), 'utf-8'),
) as FixtureMeta[];

function fixtureBuf(name: string): Uint8Array<ArrayBuffer> {
  // Buffer 在新版 @types/node 中被标注为 Buffer<ArrayBufferLike>，其 buffer 类型不被
  // BlobPart 接受；显式 new Uint8Array(n) 拿到全新 ArrayBuffer 再逐字节拷入
  const raw = readFileSync(new URL(`../../../fixtures/${name}`, import.meta.url));
  const out = new Uint8Array(raw.byteLength);
  out.set(raw);
  return out;
}
function fixtureSource(name: string): FileSource {
  return new FileSource(new Blob([fixtureBuf(name)]), name);
}

/** 稀疏采样目标→源线性最近邻映射（与 tifDecode.sparseSample 同公式，独立复算用） */
function mapXLinear(pw: number, W: number, j: number): number {
  return pw <= 1 ? 0 : Math.round((j * (W - 1)) / (pw - 1));
}

/* ---------------- 布局解析（classic + BigTIFF） ---------------- */
describe('tiffTags / parseStrips（manifest 驱动）', () => {
  for (const m of manifest) {
    it(`${m.name} 标签与条带布局`, async () => {
      const src = fixtureSource(m.name);
      const tags = await tiffTags(src);
      expect(tags[256]).toBe(m.W);
      expect(tags[257]).toBe(m.H);
      expect(tags[258]).toBe(m.bits);
      expect(tags[259]).toBe(m.compression);
      expect(tags[262]).toBe(m.photometric);
      expect(tags[277]).toBe(m.spp);
      expect(tags[278]).toBe(m.rps);
      // 单波段 + 无压缩 + 合法 bpp → 可稀疏解析；否则结构不支持（如 rgb8 spp=3）
      if (m.spp === 1 && m.compression === 1) {
        const sp = await parseStrips(src);
        expect(sp.ok).toBe(true);
        const s = sp as SparseLayoutOk;
        expect(s.W).toBe(m.W);
        expect(s.H).toBe(m.H);
        expect(s.rps).toBe(m.rps);
        expect(s.bpp).toBe(Math.floor(m.bits / 8));
        expect(s.strips).toBe(Math.ceil(m.H / m.rps));
        expect(s.offs.length).toBe(s.strips);
        expect(s.lens.length).toBe(s.strips);
      } else {
        const sp = await parseStrips(src);
        expect(sp.ok).toBe(false);
      }
    });
  }

  it('layoutInfo 摘要（条带 / 压缩 / 瓦片）', () => {
    expect(layoutInfo({ 259: 1, 278: 64 })).toBe('无压缩 · 条带64行');
    expect(layoutInfo({ 259: 8, 278: 16 })).toBe('Deflate · 条带16行');
    expect(layoutInfo({ 259: 1, 322: 256, 323: 256 })).toBe('无压缩 · 瓦片256×256');
    expect(layoutInfo({ 259: 1 })).toBe('无压缩 · 布局未知');
    expect(layoutInfo({})).toBe('压缩? · 布局未知');
  });
});

/* ---------------- 稀疏条带采样 golden ---------------- */
describe('sparseSample（稀疏条带字节切片）', () => {
  it('gray16_grad → pw=64 端点精确对齐、逐像素 = mapX(j)*257', async () => {
    const src = fixtureSource('gray16_grad.tif');   // 256×256 uint16 v=x*257
    const sp = await parseStrips(src) as SparseLayoutOk;
    const pw = 64, ph = 64;
    const s = await sparseSample(src, sp, 64);
    expect(s.sw).toBe(pw);
    expect(s.sh).toBe(ph);
    expect(s.src.length).toBe(pw * ph);
    expect(s.bytesRead).toBeGreaterThan(0);
    expect(s.stripsTouched).toBeGreaterThan(0);
    for (let j = 0; j < pw; j++) {
      const expectV = mapXLinear(pw, sp.W, j) * 257;
      expect(s.src[j]).toBe(expectV);   // 自然值精确（65535/255=257）
    }
    // 抽样行同样逐像素对齐：首列恒 0、末列恒 65535
    for (let i = 0; i < ph; i++) {
      expect(s.src[i * pw + 0]).toBe(0);
      expect(s.src[i * pw + pw - 1]).toBe(255 * 257);
    }
  });

  it('非方形 u16_rows8 → pw=128 逐像素 = j*257', async () => {
    const src = fixtureSource('u16_rows8.tif');      // 128×64 uint16 v=x*257
    const sp = await parseStrips(src) as SparseLayoutOk;
    const s = await sparseSample(src, sp, 128);
    expect(s.sw).toBe(128);
    expect(s.sh).toBe(64);
    for (let j = 0; j < 128; j++) expect(s.src[j]).toBe(j * 257);
  });

  it('f32_grad float32 采样 = mapX(j)（sample_format=3）', async () => {
    const src = fixtureSource('f32_grad.tif');       // 128×128 float v=x
    const sp = await parseStrips(src) as SparseLayoutOk;
    expect(sp.bpp).toBe(4);
    const s = await sparseSample(src, sp, 64);       // pw=64
    expect(s.sw).toBe(64);
    for (let j = 0; j < 64; j++) expect(s.src[j]).toBe(mapXLinear(64, sp.W, j));
  });

  it('默认 targetMax 取 SPARSE_PREVIEW_MAX（8192）', async () => {
    const src = fixtureSource('u16_rows8.tif');
    const sp = await parseStrips(src) as SparseLayoutOk;
    const s = await sparseSample(src, sp);           // 不传 targetMax
    expect(Math.max(s.sw, s.sh)).toBeLessThanOrEqual(SPARSE_PREVIEW_MAX);
    // 128×64 原图长边 < 8192 → 全分辨率
    expect(s.sw).toBe(128);
    expect(s.sh).toBe(64);
  });
});

/* ---------------- 稀疏收集 + 反相 golden ---------------- */
describe('sparseCollect（photometric=0 → invert）', () => {
  it('u16_whitezero 反相后逐像素 = j', async () => {
    const src = fixtureSource('u16_whitezero.tif');  // 256×128 uint16 v=(255-x)*257, photometric=0
    const sp = await parseStrips(src) as SparseLayoutOk;
    const probe: ProbeInfo = {
      W: 256, H: 128, spp: 1, bits: 16, sampleFormat: 1,
      photometric: 0, compression: 1, layout: '', image: undefined as never, tiff: undefined,
    };
    const r = await sparseCollect(src, probe, sp, 256);
    expect(r.nb).toBe(1);
    expect(r.invert).toBe(true);
    expect(r.W).toBe(256);
    expect(r.H).toBe(128);
    // 自然值覆盖全 [0,65535]：v=(255-j)*257 → 线性 (v/257)=255-j → 反相 255-(255-j)=j
    for (let j = 0; j < 256; j++) {
      expect(r.src[j]).toBe((255 - j) * 257);
      const mapped = stretchMap(r.src[j], 0, r.stats, 'linear');
      expect(mapped).toBe(255 - j);
      expect(255 - mapped).toBe(j);   // invert 后 = j
    }
  });
});

/* ---------------- probeImage（geotiff fromBlob） ---------------- */
describe('probeImage', () => {
  it('rgb8 → W/H/spp/bits/sampleFormat/photometric', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('rgb8.tif')]));
    expect(probe.W).toBe(64);
    expect(probe.H).toBe(64);
    expect(probe.spp).toBe(3);
    expect(probe.bits).toBe(8);
    expect(probe.sampleFormat).toBe(1);
    expect(probe.photometric).toBe(2);        // RGB
    expect(probe.compression).toBe(1);
    expect(probe.layout).toContain('条带64行');
  });

  it('u16_whitezero → photometric=0（WhiteIsZero）', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('u16_whitezero.tif')]));
    expect(probe.photometric).toBe(0);
    expect(probe.sampleFormat).toBe(1);
    expect(probe.bits).toBe(16);
  });

  it('f32_grad → sampleFormat=3（浮点）', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('f32_grad.tif')]));
    expect(probe.sampleFormat).toBe(3);
    expect(probe.bits).toBe(32);
  });

  it('bigtiff_strips → BigTIFF 尺寸正确 + probe.big=true', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('bigtiff_strips.tif')]));
    expect(probe.W).toBe(256);
    expect(probe.H).toBe(256);
    expect(probe.spp).toBe(1);
    expect(probe.compression).toBe(1);
    expect(probe.big).toBe(true);
  });

  it('classic TIFF → probe.big=false', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('rgb8.tif')]));
    expect(probe.big).toBe(false);
  });
});

/* ---------------- 解码路由决策（needGeo） ---------------- */
describe('needGeo（解码路由：小 8bit → UTIF，其余 → geotiff）', () => {
  const base: ProbeInfo = {
    W: 64, H: 64, spp: 3, bits: 8, sampleFormat: 1, photometric: 2,
    compression: 1, layout: '', image: undefined as never, tiff: undefined,
  };
  it('小 8bit classic → false（走 UTIF）', () => {
    expect(needGeo({ ...base, big: false })).toBe(false);
  });
  it('小 8bit BigTIFF → true（UTIF 不能解，走 geotiff 分块）', () => {
    expect(needGeo({ ...base, big: true })).toBe(true);
  });
  it('16bit → true', () => {
    expect(needGeo({ ...base, bits: 16, big: false })).toBe(true);
  });
  it('浮点 → true', () => {
    expect(needGeo({ ...base, sampleFormat: 3, big: false })).toBe(true);
  });
});

/* ---------------- 分块降采样 golden ---------------- */
describe('chunkedCollect（geotiff 分块，scale=1 逐像素等于原图）', () => {
  it('rgb8 scale=1 → src = 原像素（r=x,g=y,b=(x+y)&255）', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('rgb8.tif')]));
    const plan = getSamplePlan(probe.image);
    expect(plan.comps).toBe(3);
    expect(plan.invert).toBe(false);
    const W = probe.W, H = probe.H;
    const src = await chunkedCollect(probe.image, W, H, plan, 1, W, H);
    expect(src.length).toBe(W * H * 3);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const o = (y * W + x) * 3;
        expect(src[o]).toBe(x);
        expect(src[o + 1]).toBe(y);
        expect(src[o + 2]).toBe((x + y) & 255);
      }
    }
  });

  it('gray16_grad scale=1 → src = x*257', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('gray16_grad.tif')]));
    const plan = getSamplePlan(probe.image);
    const W = probe.W, H = probe.H;
    const src = await chunkedCollect(probe.image, W, H, plan, 1, W, H);
    for (let x = 0; x < W; x++) expect(src[x]).toBe(x * 257);
  });

  it('bigtiff_strips scale=0.5 → 256 图降采样到 128', async () => {
    const probe = await probeImage(new Blob([fixtureBuf('bigtiff_strips.tif')]));
    const plan = getSamplePlan(probe.image);
    const src = await chunkedCollect(probe.image, 256, 256, plan, 0.5, 128, 128);
    expect(src.length).toBe(128 * 128);
    // 箱式均值：源列 c → 输出列 floor(c*0.5)，故输出列 xx 平均源列 2xx 与 2xx+1
    // v = col&255（行内渐变，y 无影响）→ 期望 = ((2x)&255 + (2x+1)&255)/2 = 2x + 0.5
    for (let y = 0; y < 128; y++) {
      for (let x = 0; x < 128; x++) {
        const o = y * 128 + x;
        const expected = ((2 * x) + (2 * x + 1)) / 2;
        expect(src[o]).toBeCloseTo(expected, 1);
      }
    }
  });
});

/* ---------------- 纯函数：computeStats / stretchMap / stretchRgba / planExport ---------------- */
describe('computeStats', () => {
  it('min/max/total + 2%百分位（含 NaN 跳过）', () => {
    const src = new Float32Array([0, NaN, 25, 50, 75, 100, Infinity]);
    const st = computeStats(src, 7, 1, 1);
    expect(st[0].min).toBe(0);
    expect(st[0].max).toBe(100);
    expect(st[0].total).toBe(5);          // NaN/Inf 不计入
    expect(st[0].p2).toBeGreaterThan(0);  // 0.02 分位在第一样本后
    expect(st[0].p98).toBeLessThan(100);
  });

  it('3 波段各算各', () => {
    const src = new Float32Array([0, 100, 10, 1, 200, 20, 2, 300, 30]);
    const st = computeStats(src, 3, 1, 3);
    expect(st[0].min).toBe(0); expect(st[0].max).toBe(2);
    expect(st[1].min).toBe(100); expect(st[1].max).toBe(300);
    expect(st[2].min).toBe(10); expect(st[2].max).toBe(30);
  });
});

describe('stretchMap（显示拉伸）', () => {
  function stats(min: number, max: number): Parameters<typeof stretchMap>[2] {
    return [{ min, max, p2: min + (max - min) * 0.02, p98: min + (max - min) * 0.98, hist: new Float64Array(1024), cdf: new Float64Array(1024), total: 0 }];
  }

  it('linear / linear2 / sqrt / log', () => {
    const st = stats(0, 100);
    expect(stretchMap(50, 0, st, 'linear')).toBeCloseTo(127.5, 6);
    expect(stretchMap(50, 0, st, 'sqrt')).toBeCloseTo(Math.sqrt(0.5) * 255, 6);
    expect(stretchMap(50, 0, st, 'log')).toBeCloseTo((Math.log(1.5) / Math.LN2) * 255, 6);
    // linear2 用 p2/p98 窗口：50 在 [2,98] 中点为 50 → t=(50-2)/96=0.5 → 127.5
    expect(stretchMap(50, 0, st, 'linear2')).toBeCloseTo((50 - 2) / (98 - 2) * 255, 6);
  });

  it('equal 用 CDF', () => {
    const src = new Float32Array([0, 25, 50, 75, 100]);
    const st = computeStats(src, 5, 1, 1);
    // sc=10.24 → q = 0,256,512,768,1023；t=0.5 → k=511 → cdf[511]=2 → 2/5*255=102
    expect(stretchMap(50, 0, st, 'equal')).toBe(102);
  });

  it('统计缺失兜底 / 常量图退化', () => {
    expect(stretchMap(123, 0, null, 'linear')).toBe(123);
    expect(stretchMap(-5, 0, null, 'linear')).toBe(0);
    expect(stretchMap(NaN, 0, null, 'linear')).toBe(0);
    expect(stretchMap(50, 0, stats(0, 0), 'linear')).toBe(0);      // 全零 → 黑
    expect(stretchMap(50, 0, stats(50, 50), 'linear')).toBe(128);  // 常量 → 中灰
  });
});

describe('stretchRgba', () => {
  it('单波段 + invert 反相', () => {
    const src = new Float32Array([0, 100, 200]);
    const st = [{ min: 0, max: 200, p2: 4, p98: 196, hist: new Float64Array(1024), cdf: new Float64Array(1024), total: 3 }];
    const rgba = stretchRgba(src, 3, 1, 1, st, 'linear', true);
    expect(rgba[0]).toBe(255);       // 0 → 0 → 反相 255
    expect(rgba[4]).toBe(128);       // 100 → 127.5 → 反相 127.5 → Uint8Clamped 半进位 128
    expect(rgba[8]).toBe(0);         // 200 → 255 → 反相 0
    expect(rgba[3]).toBe(255);       // alpha 恒不透明
  });

  it('3 波段各通道映射 + clamp', () => {
    const src = new Float32Array([0, 100, 200, 300, 400, 500]);
    const st = [
      { min: 0, max: 255, p2: 5.1, p98: 249.9, hist: new Float64Array(1024), cdf: new Float64Array(1024), total: 2 },
      { min: 0, max: 300, p2: 6, p98: 294, hist: new Float64Array(1024), cdf: new Float64Array(1024), total: 2 },
      { min: 0, max: 500, p2: 10, p98: 490, hist: new Float64Array(1024), cdf: new Float64Array(1024), total: 2 },
    ];
    const rgba = stretchRgba(src, 2, 1, 3, st, 'linear', false);
    expect(rgba[0]).toBe(0);          // 像素0 R=0
    expect(rgba[1]).toBe(85);         // 像素0 G=100 /300 → 85
    expect(rgba[2]).toBe(102);        // 像素0 B=200 /500 → 102
    expect(rgba[4]).toBe(255);        // 像素1 R=300 /255 → clamp 255
    expect(rgba[5]).toBe(255);        // 像素1 G=400 /300 → clamp 255
    expect(rgba[6]).toBe(255);        // 像素1 B=500 /500 → 255
  });
});

describe('planExport（导出分辨率预算）', () => {
  it('默认 capLong=8192 → 8192 边、不降档', () => {
    const p = planExport(10000, 10000, 1)!;
    expect(p.pw).toBe(8192);
    expect(p.ph).toBe(8192);
    expect(p.longEdge).toBe(8192);
    expect(p.reduced).toBe(false);
    expect(p.px).toBeLessThanOrEqual(268435456);
  });

  it('多波段封顶 MULTI_BAND_LONG=8192', () => {
    const p = planExport(20000, 20000, 3)!;
    expect(p.pw).toBe(8192);
    expect(p.ph).toBe(8192);
  });

  it('capLong 超 16384² 预算 → 自动降档置 reduced', () => {
    const p = planExport(20000, 20000, 1, 20000)!;
    expect(p.reduced).toBe(true);
    expect(p.pw).toBeLessThanOrEqual(16384);
    expect(p.px).toBeLessThanOrEqual(268435456);
  });

  it('极小图 → 原分辨率', () => {
    const p = planExport(64, 48, 3)!;
    expect(p.pw).toBe(64);
    expect(p.ph).toBe(48);
    expect(p.reduced).toBe(false);
  });
});
