/**
 * scene.test.ts — 阶段4 盘阵场景纯函数（读 JPG 同构 rec + 元数据掩码换算）
 * ------------------------------------------------------------------
 * Node 环境（无 DOM）：只测纯函数 —— query 拼装 / RGBA→1band src（线性=恒等）/
 * 元数据 W/H 的 thumbToOrig 换算分支。JPG 加载/画布在浏览器 .e2e 回归覆盖。
 */
import { describe, it, expect } from 'vitest';
import {
  loadSrConfig, joinBase, scenesQuery, scenesListUrl, scenePreviewUrl,
  sceneImageUrl, graySrcFromRgba, sceneDecodePixels, thumbPolysToOrig,
} from '../scene.js';
import { stretchRgba } from '../tifDecode.js';
import { thumbToOrig } from '../viewMath.js';

describe('运行期配置', () => {
  it('无 window / 未注入 → 同源默认', () => {
    const c = loadSrConfig();
    expect(c).toEqual({ apiBase: '', staticBase: '' });
  });

  it('joinBase 去重斜杠', () => {
    expect(joinBase('', '/api/scenes')).toBe('/api/scenes');
    expect(joinBase('http://127.0.0.1:8000', '/api/scenes')).toBe('http://127.0.0.1:8000/api/scenes');
    expect(joinBase('http://127.0.0.1:8000/', '/api/scenes')).toBe('http://127.0.0.1:8000/api/scenes');
  });
});

describe('scenesQuery / 列表 URL', () => {
  it('空参数不拼', () => {
    expect(scenesQuery({})).toBe('');
    expect(scenesQuery({ query: '  ' })).toBe('');
  });

  it('镜像后端参数名 + 编码', () => {
    const q = scenesQuery({
      query: 'GF07', satellite: 'GF07A03', sensor: 'PMS01',
      dateFrom: '2026-07-22', dateTo: '2026-08-01', limit: 20,
    });
    expect(q).toContain('satellite=GF07A03');
    expect(q).toContain('sensor=PMS01');
    expect(q).toContain('dateFrom=2026-07-22');
    expect(q).toContain('query=GF07');
    expect(q).toContain('limit=20');
  });

  it('id 含特殊字符安全进 URL', () => {
    expect(scenePreviewUrl({ apiBase: '', staticBase: '' }, 'a/b+c=='))
      .toBe('/api/scenes/a%2Fb%2Bc%3D%3D/preview');
    expect(sceneImageUrl({ apiBase: '', staticBase: '' }, '/disk-array/x/y.jpg'))
      .toBe('/disk-array/x/y.jpg');
    expect(sceneImageUrl({ apiBase: '', staticBase: 'http://static:9000' }, '/disk-array/a b.jpg'))
      .toBe('http://static:9000/disk-array/a b.jpg');
  });
});

describe('graySrcFromRgba / sceneDecodePixels（route=JPG 同构数据）', () => {
  it('抽取 R 通道为单波段自然值', () => {
    const rgba = new Uint8Array([10, 20, 30, 255, 200, 0, 0, 255]);
    const g = graySrcFromRgba(rgba, 2);
    expect(Array.from(g)).toEqual([10, 200]);
  });

  it('stats 固定 0..255 → linear 拉伸为恒等（已烘焙，不二次拉伸）', () => {
    // 灰度 16 像素，值故意非单调铺满：模拟已 2% 拉伸的浅/中/深灰
    const vals = [0, 51, 102, 128, 153, 200, 230, 255, 40, 90, 180, 210, 12, 66, 120, 240];
    const rgba = new Uint8Array(vals.length * 4);
    for (let i = 0; i < vals.length; i++) {
      rgba[i * 4] = vals[i]; rgba[i * 4 + 1] = vals[i]; rgba[i * 4 + 2] = vals[i]; rgba[i * 4 + 3] = 255;
    }
    const d = sceneDecodePixels(rgba, 4, 4);
    expect(d.nbands).toBe(1);
    expect(d.invert).toBe(false);
    expect(d.stats).not.toBeNull();
    const stats = d.stats!;
    expect(stats[0].min).toBe(0);
    expect(stats[0].max).toBe(255);

    const out = stretchRgba(d.src, 4, 4, 1, d.stats, 'linear', false);
    for (let i = 0; i < vals.length; i++) {
      const v = vals[i];
      expect(out[i * 4]).toBe(v);       // linear + [0,255] → 原样
      expect(out[i * 4 + 1]).toBe(v);
      expect(out[i * 4 + 2]).toBe(v);
      expect(out[i * 4 + 3]).toBe(255);
    }
  });

  it('route=JPG rec 同构：src/srcw/srch/nbands/invert 与本地稀疏 rec 对齐', () => {
    const rgba = new Uint8Array(4 * 4 * 4).fill(77);   // 4×4 灰 77
    for (let i = 0; i < 16; i++) rgba[i * 4 + 3] = 255;
    const d = sceneDecodePixels(rgba, 4, 4);
    expect(d.tw).toBe(4);
    expect(d.th).toBe(4);
    expect(d.src.length).toBe(16);
    expect(d.src[0]).toBe(77);
  });
});

describe('掩码换算（元数据 W/H 分支）', () => {
  it('缩略图多边形 → 全分辨率：与直接 thumbToOrig 一致', () => {
    const W = 12000, H = 6000, tw = 819, th = 410;   // 元数据远大于缩略图
    const poly: [number, number][] = [[0, 0], [818, 0], [818, 409], [0, 409]];
    const out = thumbPolysToOrig([poly], W, H, tw, th)[0];
    expect(out.length).toBe(4);
    out.forEach((pt, i) => {
      const expectPt = thumbToOrig(poly[i][0], poly[i][1], W, H, tw, th);
      expect(pt).toEqual(expectPt);
    });
    // 端点对齐：缩略图右下 → 原图右下，而非 clamp 在端点前
    expect(out[2]).toEqual([W - 1, H - 1]);
    // 中点（端点位对齐的 round 映射，非几何中心）
    const center = thumbPolysToOrig([[[50, 25]]], W, H, tw, th)[0][0];
    expect(center).toEqual([733, 367]);
  });
});

describe('列表 URL 样例', () => {
  it('静态同源默认 + 后端相对 jpgUrl', () => {
    const cfg = { apiBase: '', staticBase: '' };
    expect(scenesListUrl(cfg, { satellite: 'GF07A03', limit: 20 }))
      .toBe('/api/scenes?satellite=GF07A03&limit=20');
    expect(sceneImageUrl(cfg, '/disk-array/GF07A03_PMS01_20260722125045.preview.jpg'))
      .toBe('/disk-array/GF07A03_PMS01_20260722125045.preview.jpg');
  });
});
