// maskgen.js 纯算法回归（Node 直测，无需浏览器）
// 覆盖：质心/退化、txt 格式、填充约定（边界含入）、重叠并集、连通合并、洞填充、
// 轮廓+简化、魔棒自适应生长/边缘屏障、buildTiff 产物结构。
// 用法: node test-maskgen.js
const path = require('path');
const MaskGen = require(path.join(__dirname, '..', 'tif_viewer', 'maskgen.js'));
const pako = require(path.join(__dirname, '..', 'tif_viewer', 'vendor', 'pako.min.js'));

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }
function assert(c, msg) { if (!c) throw new Error(msg || '断言失败'); }
function near(a, b, eps) { return Math.abs(a - b) <= (eps || 1e-6); }

// 构造 RGBA（fillFn(x,y) -> [r,g,b]）
function makeRgba(w, h, fillFn) {
  const a = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const o = (y * w + x) * 4; const c = fillFn(x, y);
    a[o] = c[0]; a[o + 1] = c[1]; a[o + 2] = c[2]; a[o + 3] = 255;
  }
  return a;
}
function countOnes(m) { let n = 0; for (let i = 0; i < m.length; i++) if (m[i]) n++; return n; }

const SQ = [[2, 2], [7, 2], [7, 7], [2, 7]];          // 方形 [2,2]..[7,7] 边界含入 6×6

test('polygonCentroid 三角形', () => {
  const c = MaskGen.polygonCentroid([[0, 0], [4, 0], [0, 4]]);
  assert(near(c[0], 4 / 3) && near(c[1], 4 / 3), '期望 (1.333,1.333) 实为 ' + c);
});
test('polygonCentroid 退化→包围盒中心', () => {
  const c = MaskGen.polygonCentroid([[0, 0], [0, 0], [4, 0]]);
  assert(near(c[0], 2) && near(c[1], 0), '期望 (2,0) 实为 ' + c);
});
test('buildMaskTxt 格式（全角头/CRLF/2dp）', () => {
  const txt = MaskGen.buildMaskTxt(10, 10, [[[0, 0], [4, 0], [0, 4]]]);
  const lines = txt.split('\r\n');
  assert(lines[0] === '＃掩膜中心点坐标（X，Y）', '头1: ' + JSON.stringify(lines[0]));
  assert(lines[1] === '＃掩膜编号，X坐标，Y坐标', '头2: ' + JSON.stringify(lines[1]));
  assert(lines[2] === '1,1.33,1.33', '数据行: ' + JSON.stringify(lines[2]));
  assert(lines[3] === '', '应以 \r\n 结尾且无多余行');
});
test('填充约定 方形边界含入 = 36px', () => {
  const m = MaskGen.rasterMask([SQ], 16, 16);
  assert(countOnes(m) === 36, '期望 36 实为 ' + countOnes(m));
  assert(m[2 * 16 + 2] === 1 && m[7 * 16 + 7] === 1, '顶点像素应含入');
  assert(m[1 * 16 + 2] === 0 && m[8 * 16 + 2] === 0, '边界外应排除');
});
test('填充约定 三角形 4+3+2+1 = 10px', () => {
  const tri = [[1, 1], [4, 1], [1, 4]];
  const m = MaskGen.rasterMask([tri], 8, 8);
  assert(countOnes(m) === 10, '期望 10 实为 ' + countOnes(m));
});
test('重叠多边形并集不重复计（逐多边形 even-odd + UNION）', () => {
  const m = MaskGen.rasterMask([[[2, 2], [6, 2], [6, 6], [2, 6]], [[4, 4], [8, 4], [8, 8], [4, 8]]], 12, 12);
  // 25 + 25 - 9(重叠) = 41
  assert(countOnes(m) === 41, '期望 41 实为 ' + countOnes(m));
});
test('mergeConnected 重叠→1 区', () => {
  const out = MaskGen.mergeConnected([[[2, 2], [6, 2], [6, 6], [2, 6]], [[4, 4], [8, 4], [8, 8], [4, 8]]], 12, 12);
  assert(out.length === 1, '期望 1 区 实为 ' + out.length);
});
test('mergeConnected 相接→合并', () => {
  const out = MaskGen.mergeConnected([[[2, 2], [6, 2], [6, 6], [2, 6]], [[6, 2], [10, 2], [10, 6], [6, 6]]], 12, 12);
  assert(out.length === 1, '期望 1 区 实为 ' + out.length);
});
test('mergeConnected 包含→吞噬', () => {
  const out = MaskGen.mergeConnected([[[2, 2], [10, 2], [10, 10], [2, 10]], [[4, 4], [6, 4], [6, 6], [4, 6]]], 14, 14);
  assert(out.length === 1, '期望 1 区 实为 ' + out.length);
});
test('mergeConnected 分离→2 区', () => {
  const out = MaskGen.mergeConnected([[[2, 2], [5, 2], [5, 5], [2, 5]], [[8, 8], [11, 8], [11, 11], [8, 11]]], 14, 14);
  assert(out.length === 2, '期望 2 区 实为 ' + out.length);
});
test('fillRegionHoles 环形洞填充', () => {
  const w = 20, h = 20;
  const mask = new Uint8Array(w * h);
  for (let y = 4; y <= 15; y++) for (let x = 4; x <= 15; x++) mask[y * w + x] = 1;
  for (let y = 8; y <= 11; y++) for (let x = 8; x <= 11; x++) mask[y * w + x] = 0;  // 洞
  const out = MaskGen.fillRegionHoles(mask, w, h);
  assert(out[9 * w + 9] === 1, '洞应被填充');
  assert(out[0] === 0 && out[19 * w + 19] === 0, '背景仍为 0');
});
test('traceContour + simplifyPoly 边界轮廓', () => {
  const w = 20, h = 20;
  const mask = new Uint8Array(w * h);
  for (let y = 5; y <= 9; y++) for (let x = 5; x <= 9; x++) mask[y * w + x] = 1;
  const pts = MaskGen.traceContour(mask, w, h);
  assert(pts.length >= 8, '轮廓点数过少: ' + pts.length);
  assert(pts[0][0] === 5 && pts[0][1] === 5, '起点应为最左上填充像素');
  let top = 0, bottom = 0, left = 0, right = 0;   // 四边都要有边界点
  for (const p of pts) {
    assert(p[0] >= 5 && p[0] <= 9 && p[1] >= 5 && p[1] <= 9, '点越界 ' + p);
    if (p[1] === 5) top++; if (p[1] === 9) bottom++;
    if (p[0] === 5) left++; if (p[0] === 9) right++;
  }
  assert(top > 0 && bottom > 0 && left > 0 && right > 0, '轮廓应覆盖四边');
  const poly = MaskGen.simplifyPoly(pts, 0.5);
  assert(poly.length === 4, '5×5 方块简化后应 4 角点，实为 ' + poly.length);
  let a2 = 0;
  for (let i = 0; i < poly.length; i++) {
    const j = (i + 1) % poly.length;
    a2 += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1];
  }
  assert(Math.abs(a2) > 0, '简化多边形应非退化');
});
test('魔棒 实心色块全选 + 背景零泄漏', () => {
  const w = 150, h = 150;
  const rgba = makeRgba(w, h, (x, y) => (x >= 30 && x < 120 && y >= 30 && y < 120) ? [100, 100, 100] : [200, 200, 200]);
  const sel = MaskGen.floodSelect(w, h, rgba, 75, 75, 20, 64);
  const cnt = countOnes(sel);
  assert(cnt >= 8000, '应选完整块 8100，实为 ' + cnt);
  assert(sel[145 * w + 145] === 0 && sel[0] === 0, '背景泄漏');
});
test('魔棒 纹理自适应区域生长（±15 细纹理整块）', () => {
  const w = 150, h = 150;
  const rgba = makeRgba(w, h, (x, y) => {
    const inPatch = x >= 30 && x < 120 && y >= 30 && y < 120;
    if (!inPatch) return [200, 200, 200];
    return ((((x >> 1) + (y >> 1)) & 1) === 0) ? [85, 85, 85] : [115, 115, 115];
  });
  const sel = MaskGen.floodSelect(w, h, rgba, 75, 75, 20, 64);
  const cnt = countOnes(sel);
  assert(cnt >= 7000, '自适应窗口应覆盖整块纹理区，实为 ' + cnt + '/8100');
  assert(sel[145 * w + 145] === 0, '背景泄漏');
});
test('魔棒 边缘屏障不跨越强边缘', () => {
  const w = 150, h = 150;
  const rgba = makeRgba(w, h, (x, y) => (x < 120) ? [100, 100, 100] : [200, 200, 200]);
  const sel = MaskGen.floodSelect(w, h, rgba, 50, 75, 20, 64);
  assert(sel[75 * w + 130] === 0, '屏障右侧不应选中');
  assert(countOnes(sel) >= 16000, '左侧应基本全选，实为 ' + countOnes(sel));
});
test('魔棒 种子在边界不崩', () => {
  const w = 50, h = 50;
  const rgba = makeRgba(w, h, () => [150, 150, 150]);
  const sel = MaskGen.floodSelect(w, h, rgba, 0, 0, 20, 64);
  assert(sel[0] === 1 && sel.length === w * h, '返回异常');
});
test('buildTiff 产物结构（classic TIFF / Deflate / 0-255）', async () => {
  const tif = await MaskGen.buildTiff(16, 16, [SQ]);
  assert(tif[0] === 0x49 && tif[1] === 0x49 && tif[2] === 42, '应为 II+42 小端 classic TIFF');
  const dv = new DataView(tif.buffer, tif.byteOffset, tif.byteLength);
  const ifd = dv.getUint32(4, true);
  const n = dv.getUint16(ifd, true);
  const tags = {};
  for (let i = 0; i < n; i++) {
    const e = ifd + 2 + i * 12;
    const tag = dv.getUint16(e, true);
    tags[tag] = { type: dv.getUint16(e + 2, true), count: dv.getUint32(e + 4, true), value: dv.getUint32(e + 8, true) };
  }
  assert(tags[256].value === 16 && tags[257].value === 16, '宽高应为 16');
  assert(tags[259].value === 8, '压缩应为 Deflate(8)');
  assert(tags[262].value === 1, 'Photometric 应为 BlackIsZero');
  assert(tags[258].value === 8 && tags[277].value === 1, '8bit 单波段');
  const off = tags[273].value, len = tags[279].value;
  const comp = new Uint8Array(tif.buffer, off, len);
  const raw = pako.inflate(comp);
  assert(raw.length === 256, '解压后应 256 字节');
  let white = 0;
  for (let i = 0; i < raw.length; i++) if (raw[i] === 255) white++;
  assert(white === 36, '255 像素应 36 实为 ' + white);
  assert(raw[0] === 0 && raw[15] === 0, '背景行应为 0');
  assert(raw[2 * 16 + 2] === 255 && raw[7 * 16 + 7] === 255, '方形顶点应为 255');
});

(async () => {
  let pass = 0, fail = 0;
  for (const t of tests) {
    try { await t.fn(); console.log('  [OK]   ' + t.name); pass++; }
    catch (e) { console.log('  [FAIL] ' + t.name + ': ' + (e.message || e)); fail++; }
  }
  console.log('\n结果: ' + pass + '/' + tests.length + ' 通过');
  process.exit(fail ? 1 : 0);
})();
