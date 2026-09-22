/**
 * scene.test.ts — 阶段4 盘阵场景纯函数（读 JPG 同构 rec + 元数据掩码换算）
 * ------------------------------------------------------------------
 * Node 环境（无 DOM）：只测纯函数 —— query 拼装 / RGBA→1band src（线性=恒等）/
 * 元数据 W/H 的 thumbToOrig 换算分支。JPG 加载/画布在浏览器 .e2e 回归覆盖。
 */
import { describe, it, expect, vi } from 'vitest';
import {
  loadSrConfig, joinBase, scenesQuery, scenesListUrl, scenePreviewUrl,
  dropPreviewUrl,
  sceneImageUrl, graySrcFromRgba, sceneDecodePixels, thumbPolysToOrig,
  todayScenePrefix, sceneResolveUrl,
  isImageSource, startStretch, SCENE_START_STRETCH,
  isBakedPreviewUrl, previewNeedsBake, previewDivLabel, loadPreviewDiv,
  savePreviewDiv, SCENE_PREVIEW_DIVS, DEFAULT_PREVIEW_DIV,
  rasterPreviewWins, previewCacheKey, sceneAnchors, ANCHOR_MAX,
} from '../scene.js';
import type { SceneRow, RasterPreview } from '../scene.js';
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
    expect(scenePreviewUrl({ apiBase: '', staticBase: '' }, 'a/b+c==', 4))
      .toBe('/api/scenes/a%2Fb%2Bc%3D%3D/preview?div=4');
    // 拖入那条是**另一个端点**，不是同 URL 带参数：落点与生命周期都不同
    expect(dropPreviewUrl({ apiBase: '', staticBase: '' }, 'a/b+c==', 8))
      .toBe('/api/scenes/a%2Fb%2Bc%3D%3D/preview-drop?div=8');
    expect(dropPreviewUrl({ apiBase: 'http://127.0.0.1:8000/', staticBase: '' }, 'x'))
      .toBe(`http://127.0.0.1:8000/api/scenes/x/preview-drop`
        + `?div=${DEFAULT_PREVIEW_DIV}`);
    expect(sceneImageUrl({ apiBase: '', staticBase: '' }, '/disk-array/x/y.jpg'))
      .toBe('/disk-array/x/y.jpg');
    expect(sceneImageUrl({ apiBase: '', staticBase: 'http://static:9000' }, '/disk-array/a b.jpg'))
      .toBe('http://static:9000/disk-array/a b.jpg');
  });
});

describe('预览档位（全局下采样）', () => {
  const CFG = { apiBase: '', staticBase: 'http://static:9000' };

  it('静态 URL 只对**烤出来的**预览拼 ?div=（源本身就是 JPG 的行不拼）', () => {
    expect(sceneImageUrl(CFG, '/disk-array/a/b_preview.jpg', 8))
      .toBe('http://static:9000/disk-array/a/b_preview.jpg?div=8');
    // 源即显示件：档位对它无意义，拼了反而打红逐字断言的 e2e
    expect(sceneImageUrl(CFG, '/disk-array/a/b.jpg', 8))
      .toBe('http://static:9000/disk-array/a/b.jpg');
    expect(sceneImageUrl(CFG, '/disk-array/a/b_preview.jpg'))
      .toBe('http://static:9000/disk-array/a/b_preview.jpg');
    expect(sceneImageUrl(CFG, null, 8)).toBe('');
  });

  it('isBakedPreviewUrl 认的是产物名，不是「后缀是 jpg」', () => {
    expect(isBakedPreviewUrl('/disk-array/a/b_preview.jpg')).toBe(true);
    expect(isBakedPreviewUrl('/disk-array/a/b_preview.jpeg')).toBe(true);
    expect(isBakedPreviewUrl('/disk-array/a/b.jpg')).toBe(false);
    // 改名（2026-09-22）前的点号那份：两代都认 —— 静态 URL 是后端给的，
    // 两边版本错开一档时必须认得出，认不出换档位后最长一小时看到旧图
    expect(isBakedPreviewUrl('/disk-array/a/b.preview.jpg')).toBe(true);
    expect(isBakedPreviewUrl('/disk-array/a/bjpeg.jpg')).toBe(false);
    expect(isBakedPreviewUrl('/disk-array/a/preview.jpg')).toBe(false);
    expect(isBakedPreviewUrl(null)).toBe(false);
  });

  const row = (over: Partial<SceneRow>): SceneRow => ({
    id: 'x', name: 'n', satellite: null, sensor: null, date: null,
    size_bytes: 0, fake: false, W: 1, H: 1, rel: null,
    jpgUrl: null, hasPreview: false, lq_path: null, ...over,
  });

  it('previewNeedsBake：四条分支', () => {
    // 库外（无静态 URL）→ 打 /preview，会烤
    expect(previewNeedsBake(row({ jpgUrl: null }), 4)).toBe(true);
    // 缓存不在 → 烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a_preview.jpg',
      hasPreview: false }), 4)).toBe(true);
    // 在，但档位不符（含旧格式戳 null）→ 烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a_preview.jpg', hasPreview: true,
      previewDiv: 8 }), 4)).toBe(true);
    expect(previewNeedsBake(row({ jpgUrl: '/d/a_preview.jpg', hasPreview: true,
      previewDiv: null }), 4)).toBe(true);
    // 在且档位对得上 → 不烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a_preview.jpg', hasPreview: true,
      previewDiv: 4 }), 4)).toBe(false);
    // 源即显示件 → 永不烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a.jpg', hasPreview: true,
      previewDiv: null }), 4)).toBe(false);
  });

  it('档位表与后端 PREVIEW_DIVISORS 同序同值，默认 ÷4', () => {
    expect([...SCENE_PREVIEW_DIVS]).toEqual([2, 4, 8, 16, 32]);
    expect(DEFAULT_PREVIEW_DIV).toBe(4);
    expect(previewDivLabel(16)).toBe('1/16');
  });

  it('loadPreviewDiv / savePreviewDiv：读回存的值，脏值退回默认', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
      setItem: (k: string, v: string) => { store.set(k, v); },
    });
    expect(loadPreviewDiv()).toBe(DEFAULT_PREVIEW_DIV);   // 没存过
    savePreviewDiv(16);
    expect(loadPreviewDiv()).toBe(16);
    store.set('sr.previewDiv', '3');                      // 不是合法档位
    expect(loadPreviewDiv()).toBe(DEFAULT_PREVIEW_DIV);
    store.set('sr.previewDiv', 'abc');
    expect(loadPreviewDiv()).toBe(DEFAULT_PREVIEW_DIV);
    vi.unstubAllGlobals();
  });

  it('localStorage 不可用（opaque origin / 隐私模式）不抛，一律回默认档', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('SecurityError'); },
      setItem: () => { throw new Error('SecurityError'); },
    });
    expect(loadPreviewDiv()).toBe(DEFAULT_PREVIEW_DIV);
    expect(() => savePreviewDiv(8)).not.toThrow();
    vi.unstubAllGlobals();
  });
});

describe('显示源比较规则：谁清晰用谁（rasterPreviewWins）', () => {
  const rp = (over: Partial<RasterPreview>): RasterPreview => ({
    id: 'raster-id', name: 'PAN.tif', rel: 'a/PAN.tif',
    jpgUrl: '/disk-array/a/PAN_preview.jpg',
    rasterW: 24000, rasterH: 24000, jpgW: 8192, jpgH: 8192,
    hasPreview: false, previewDiv: null, ...over,
  });

  it('判据就是 round(长边/div) > 显示件长边（与后端 preview_max_edge 同式）', () => {
    // 真机量级：24000 源 + 8192 显示件。÷2 烤出 12000 → 赢
    expect(rasterPreviewWins(rp({}), 2)).toBe(true);
    // ÷4 烤出 6000 → 输。**默认档位下这条规则基本不触发**，这正是要如实告诉用户的
    expect(rasterPreviewWins(rp({}), 4)).toBe(false);
    expect(rasterPreviewWins(rp({}), 8)).toBe(false);
  });

  it('相等不换（严格大于）：换过去要付一次烘焙，换来一样清晰就没理由动', () => {
    // 1600 源 ÷4 = 400，显示件长边正好 400 → 平手，保持现状
    expect(rasterPreviewWins(rp({ rasterW: 1600, rasterH: 800,
      jpgW: 400, jpgH: 200 }), 4)).toBe(false);
    // 显示件长边 399 → 400 > 399，差一像素也换：判据是算出来的数，不是「差不多」
    expect(rasterPreviewWins(rp({ rasterW: 1600, rasterH: 800,
      jpgW: 399, jpgH: 200 }), 4)).toBe(true);
    // 除数是**四舍五入**不是向上取整：round(1601/4)=400，仍与显示件平手
    expect(rasterPreviewWins(rp({ rasterW: 1601, rasterH: 800,
      jpgW: 400, jpgH: 200 }), 4)).toBe(false);
    expect(rasterPreviewWins(rp({ rasterW: 1602, rasterH: 800,
      jpgW: 400, jpgH: 200 }), 4)).toBe(true);   // round(1602/4)=401
  });

  it('两侧都取长边比（不是宽比宽）', () => {
    // 栅格长边 640 ÷4 = 160；显示件长边 600 → 输，尽管栅格在题目里比显示件「高」
    expect(rasterPreviewWins(rp({ rasterW: 320, rasterH: 640,
      jpgW: 600, jpgH: 10 }), 4)).toBe(false);
    // 反过来：栅格是横长条，长边 1600 ÷4 = 400 > 显示件长边 399 → 赢
    expect(rasterPreviewWins(rp({ rasterW: 1600, rasterH: 100,
      jpgW: 399, jpgH: 20 }), 4)).toBe(true);
  });

  it('保守兜底一律 false（继续显示那张 jpg，绝不因为找不到更好的把图弄没了）', () => {
    expect(rasterPreviewWins(null, 4)).toBe(false);
    expect(rasterPreviewWins(undefined, 4)).toBe(false);
    expect(rasterPreviewWins(rp({ jpgW: 0 }), 4)).toBe(false);
    expect(rasterPreviewWins(rp({ jpgH: 0 }), 4)).toBe(false);
    expect(rasterPreviewWins(rp({ rasterW: 0 }), 2)).toBe(false);
    // div 不是合法档位 → 不认（否则「除以 3」这种前端到处没实现的档位会算出个假答案）
    expect(rasterPreviewWins(rp({}), 3)).toBe(false);
    expect(rasterPreviewWins(rp({ rasterW: 24000, jpgW: 1 }), 0)).toBe(false);
  });

  const row = (over: Partial<SceneRow>): SceneRow => ({
    id: 'x', name: 'n', satellite: null, sensor: null, date: null,
    size_bytes: 0, fake: false, W: 1, H: 1, rel: null,
    jpgUrl: null, hasPreview: false, lq_path: null, ...over,
  });

  it('previewNeedsBake 的栅格分支：按**栅格那份预览**判，不看源 jpg 的三个字段', () => {
    const win = rp({ rasterW: 24000, rasterH: 24000, jpgW: 8192, jpgH: 8192 });
    // 栅格赢且栅格那份预览还没烤过 → 要烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a.jpg', hasPreview: true,
      previewDiv: null, rasterPreview: win }), 2)).toBe(true);
    // 烤过但不是这一档 → 要烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a.jpg', hasPreview: true,
      previewDiv: null, rasterPreview: { ...win, hasPreview: true, previewDiv: 4 } }),
      2)).toBe(true);
    // 烤过且档位对得上 → 不烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a.jpg', hasPreview: true,
      previewDiv: null, rasterPreview: { ...win, hasPreview: true, previewDiv: 2 } }),
      2)).toBe(false);
    // 栅格**输**（÷4）→ 落回源 jpg 那套：源即显示件，永不烤
    expect(previewNeedsBake(row({ jpgUrl: '/d/a.jpg', hasPreview: true,
      previewDiv: null, rasterPreview: win }), 4)).toBe(false);
  });

  it('不给 rasterPreview（旧后端 / 无同名栅格）→ 行为与今天逐字节一致', () => {
    expect(previewNeedsBake(row({ jpgUrl: '/d/a.jpg', hasPreview: true }), 4)).toBe(false);
    expect(previewNeedsBake(row({ jpgUrl: null }), 4)).toBe(true);
  });

  it('sceneImageUrl 对 rasterPreview.jpgUrl 自动附 ?div=（它就是 _preview.jpg）', () => {
    const CFG = { apiBase: '', staticBase: 'http://static:9000' };
    expect(sceneImageUrl(CFG, rp({}).jpgUrl, 8))
      .toBe('http://static:9000/disk-array/a/PAN_preview.jpg?div=8');
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
    expect(sceneImageUrl(cfg, '/disk-array/GF07A03_PMS01_20260722125045_preview.jpg'))
      .toBe('/disk-array/GF07A03_PMS01_20260722125045_preview.jpg');
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

describe('previewCacheKey（预览 blob 的缓存键）', () => {
  it('同一条行 + 同档位 → 稳定（同参数两次调用逐字相同）', () => {
    const row = { id: 'abc123' };
    expect(previewCacheKey(row, 4)).toBe(previewCacheKey({ id: 'abc123' }, 4));
    expect(previewCacheKey(row, 4)).toBe('abc123|4|jpg');
  });

  it('档位进键：同一张图的不同档位互不覆盖', () => {
    const row = { id: 'abc123' };
    expect(previewCacheKey(row, 4)).not.toBe(previewCacheKey(row, 8));
  });

  it('不同的行互不覆盖', () => {
    expect(previewCacheKey({ id: 'a' }, 4)).not.toBe(previewCacheKey({ id: 'b' }, 4));
  });

  it('栅格那份与源 jpg 那份是两个键（栅格赢的档位端上来的不是同一张图）', () => {
    const row = { id: 'abc123' };
    const rp = { name: 'PAN.tif' };
    expect(previewCacheKey(row, 4, rp)).toBe('abc123|4|ras:PAN.tif');
    expect(previewCacheKey(row, 4, rp)).not.toBe(previewCacheKey(row, 4));
    // 同名栅格换了张图（不同 name）也要分开
    expect(previewCacheKey(row, 4, { name: 'PAN.tif' }))
      .not.toBe(previewCacheKey(row, 4, { name: 'GF07A03.tif' }));
  });

  it('raster 传 null / undefined 等同于「源 jpg 那份」', () => {
    const row = { id: 'abc123' };
    expect(previewCacheKey(row, 4, null)).toBe(previewCacheKey(row, 4));
    expect(previewCacheKey(row, 4, undefined)).toBe(previewCacheKey(row, 4));
  });

  it('键里没有裸的下划线/空格歧义：行 id 与栅格名之间不会撞车', () => {
    // 'a|4|jpg' 与栅格名恰好叫 'jpg' 的情形：前缀不同，撞不上
    expect(previewCacheKey({ id: 'a' }, 4, { name: 'jpg' }))
      .not.toBe(previewCacheKey({ id: 'a' }, 4));
  });
});

describe('sceneAnchors —— 拖 jpg 时递给后端的锚定目录', () => {
  it('按给进来的顺序保留（顺序就是优先级，后端取第一个成立的）', () => {
    expect(sceneAnchors(['/d/近', '/d/远'])).toEqual(['/d/近', '/d/远']);
  });

  it('空值丢掉、重复只留一份', () => {
    expect(sceneAnchors([null, '/d/a', undefined, '', '/d/a', '/d/b']))
      .toEqual(['/d/a', '/d/b']);
  });

  it('最多 ANCHOR_MAX 个 —— 它是提示不是断言，不该被拿来灌请求', () => {
    const many = ['/d/1', '/d/2', '/d/3', '/d/4', '/d/5'];
    expect(sceneAnchors(many)).toHaveLength(ANCHOR_MAX);
    expect(sceneAnchors(many)[0]).toBe('/d/1');
  });

  it('一个都没有 → 空数组（后端照旧按名字反推）', () => {
    expect(sceneAnchors([null, undefined, ''])).toEqual([]);
  });
});
