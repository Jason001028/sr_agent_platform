/**
 * scene.test.ts — 阶段4 盘阵场景纯函数（读 JPG 同构 rec + 元数据掩码换算）
 * ------------------------------------------------------------------
 * Node 环境（无 DOM）：只测纯函数 —— query 拼装 / RGBA→1band src（线性=恒等）/
 * 元数据 W/H 的 thumbToOrig 换算分支。JPG 加载/画布在浏览器 .e2e 回归覆盖。
 */
import { describe, it, expect } from 'vitest';
import {
  loadSrConfig, joinBase, scenesQuery, scenesListUrl, scenePreviewUrl,
  tmpPreviewUrl,
  sceneImageUrl, graySrcFromRgba, sceneDecodePixels, thumbPolysToOrig,
  todayScenePrefix, sceneResolveUrl,
  isImageSource, startStretch, SCENE_START_STRETCH,
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
    // 临时那条是**另一个端点**，不是同 URL 带参数：落点与生命周期都不同
    expect(tmpPreviewUrl({ apiBase: '', staticBase: '' }, 'a/b+c=='))
      .toBe('/api/scenes/a%2Fb%2Bc%3D%3D/preview-tmp');
    expect(tmpPreviewUrl({ apiBase: 'http://127.0.0.1:8000/', staticBase: '' }, 'x'))
      .toBe('http://127.0.0.1:8000/api/scenes/x/preview-tmp');
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

  it('stats 固定 0..255 → linear 拉伸为恒等（烘焙值原样上屏）', () => {
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

  it('场景数据上 equal 确实改像素 —— 起手值不是恒等映射', () => {
    const vals = [0, 51, 102, 128, 153, 200, 230, 255, 40, 90, 180, 210, 12, 66, 120, 240];
    const rgba = new Uint8Array(vals.length * 4);
    for (let i = 0; i < vals.length; i++) rgba.fill(vals[i], i * 4, i * 4 + 3);
    const d = sceneDecodePixels(rgba, 4, 4);
    const eq = stretchRgba(d.src, 4, 4, 1, d.stats, 'equal', false);
    const moved = vals.some((v, i) => eq[i * 4] !== v);
    expect(moved).toBe(true);
  });
});

describe('startStretch（显示层拉伸的起手值）', () => {
  it('盘阵场景（route=jpg）恒以直方图均衡起手，不看当前全局模式', () => {
    for (const cur of ['linear', 'linear2', 'sqrt', 'log', 'equal'] as const) {
      expect(startStretch('jpg', cur)).toBe('equal');
    }
    expect(SCENE_START_STRETCH).toBe('equal');
  });

  it('其余路径（本地 TIF / 本地 JPG / 未解码）沿用当前模式', () => {
    for (const route of ['utif', 'sparse', 'chunked', 'img', null, undefined]) {
      expect(startStretch(route, 'log')).toBe('log');
    }
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

describe('isImageSource（§4.7 盘阵 JPG 源行）', () => {
  const row = (name: string, rel: string | null): { name: string; rel: string | null } =>
    ({ name, rel });

  it('.jpg/.jpeg 源（含大写）→ true', () => {
    expect(isImageSource(row('a', '2026/07/a.jpg'))).toBe(true);
    expect(isImageSource(row('a', 'a.jpeg'))).toBe(true);
    expect(isImageSource(row('a', 'a.JPG'))).toBe(true);
  });

  it('光栅行 → false', () => {
    expect(isImageSource(row('a', 'a.tif'))).toBe(false);
    expect(isImageSource(row('a', 'a.img'))).toBe(false);
    expect(isImageSource(row('a', 'a.tiff'))).toBe(false);
  });

  it('rel 缺失时退回 name；都没有 → false', () => {
    expect(isImageSource(row('a.jpg', null))).toBe(true);
    expect(isImageSource(row('GF07A03_PMS01_20260722125045', null))).toBe(false);
    expect(isImageSource(row('', null))).toBe(false);
  });
});

describe('手工场景路径（盘阵任意合法场景目录）', () => {
  it('当天前缀：补零，与后端 {y}/{m}/{d} 模板同形', () => {
    expect(todayScenePrefix(new Date(2026, 8, 17)))
      .toBe('W:\\GSHC2IMPS\\PRODUCT\\2026\\09\\17');
    // 一位数的月/日也要补零（10 月之前全是这种）
    expect(todayScenePrefix(new Date(2026, 0, 5)))
      .toBe('W:\\GSHC2IMPS\\PRODUCT\\2026\\01\\05');
  });

  // 文件名 → 场景路径的反推**已整体收到后端**（backend/pathguard.py）：
  // 前端不再自解析日期、不再自拼模板，所以这里没有对应单测 —— 规则与候选
  // 由 backend/tests/test_paths.py 与 test_scene_resolve.py 钉住，前端行为
  // 由 .e2e/test-manual-scene.js 覆盖。

  it('sceneResolveUrl 走 apiBase', () => {
    expect(sceneResolveUrl({ apiBase: '', staticBase: '' }))
      .toBe('/api/scenes/resolve');
    expect(sceneResolveUrl({ apiBase: 'http://127.0.0.1:8000', staticBase: '' }))
      .toBe('http://127.0.0.1:8000/api/scenes/resolve');
  });
});
