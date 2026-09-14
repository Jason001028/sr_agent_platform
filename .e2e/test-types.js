// 数据类型矩阵回归（对齐 gui-experience §5）
// 断言基于 2026-09-01 金标准采集（当前正确实现的行为快照），容差宽松。
// 用法: node test-types.js
const { launchPage } = require('./launchBrowser');
const { openViewer, uploadAndWait, sampleThumb } = require('./lib/capture');
const path = require('path');

const T = (p) => path.join(__dirname, '..', 'test-tifs', p);

// file, W, H: 原图像素尺寸；utif: true=走 UTIF 路径（状态"解码耗时"），false=geotiff（"读取耗时"）
// gradient LR: meanR-meanL 强；invert: meanL-meanR 强（反色）；flat: |L-R| 小（平坦）
const CASES = [
  { file: 'rgb8.tif', name: 'rgb8 8bit RGB', utif: true, W: 1024, H: 1024, gradient: true, meanLo: 100, meanHi: 160 },
  { file: 'gdal_byte.tif', name: 'gdal_byte', utif: true, W: 20, H: 20, meanLo: 115, meanHi: 140 },
  { file: 'gdal_float32.tif', name: 'gdal_float32', utif: false, W: 20, H: 20, meanLo: 55, meanHi: 95 },
  { file: 'gray16.tif', name: 'gray16 拉伸', utif: false, W: 512, H: 512, gradient: true, meanLo: 90, meanHi: 170 },
  { file: 'deflate_tile128.tif', name: 'deflate_tile128', utif: true, W: 512, H: 512, flat: 30, meanLo: 110, meanHi: 145 },
  { file: 'types/u16_rgb.tif', name: 'u16_rgb 不透明', utif: false, W: 256, H: 256, flat: 5, meanLo: 110, meanHi: 145 },
  { file: 'types/u16_whitelzero.tif', name: 'u16_whitelzero 反色', utif: false, W: 128, H: 128, invert: true },
  { file: 'types/u32_gray.tif', name: 'u32_gray 拉伸', utif: false, W: 256, H: 256, gradient: true, meanLo: 90, meanHi: 170 },
  { file: 'types/f32_gray.tif', name: 'f32_gray', utif: false, W: 256, H: 256, gradient: true, meanLo: 90, meanHi: 170 },
  { file: 'types/f32_reflect.tif', name: 'f32_reflect', utif: false, W: 256, H: 256, gradient: true, meanLo: 90, meanHi: 170 },
  { file: 'types/f32_rgb.tif', name: 'f32_rgb', utif: false, W: 256, H: 256, flat: 5, meanLo: 110, meanHi: 145 },
  { file: 'types/f32_skew.tif', name: 'f32_skew', utif: false, W: 512, H: 512, gradient: true, meanLo: 60, meanHi: 120, blackPctMax: 15 },  // 偏斜分布左端低值拉伸后本含 ~7.8% 黑
  { file: 'types/gf_like.tif', name: 'gf_like GF模拟', utif: false, W: 2048, H: 1024, gradient: true, meanLo: 90, meanHi: 170 },
];

async function main() {
  const { browser, page, errors } = await launchPage();
  let pass = 0, fail = 0;
  try {
    for (const c of CASES) {
      await openViewer(page);
      let rec, s;
      try {
        rec = await uploadAndWait(page, T(c.file), 30000);
        s = await sampleThumb(page, 256);
      } catch (e) {
        console.log('  [FAIL] ' + c.name + '（' + c.file + '）: ' + e.message);
        fail++; continue;
      }
      const probs = [];
      if (rec.W !== c.W || rec.H !== c.H) probs.push('尺寸 ' + rec.W + 'x' + rec.H + ' ≠ ' + c.W + 'x' + c.H);
      const isUtif = rec.status.indexOf('解码耗时') >= 0;
      if (isUtif !== c.utif) probs.push('路径异常: 状态 "' + rec.status + '"');
      if (c.gradient && (s.meanR - s.meanL) < 80) probs.push('渐变方向缺失 L=' + s.meanL.toFixed(1) + ' R=' + s.meanR.toFixed(1));
      if (c.invert && (s.meanL - s.meanR) < 80) probs.push('反色缺失 L=' + s.meanL.toFixed(1) + ' R=' + s.meanR.toFixed(1));
      if (typeof c.flat === 'number' && Math.abs(s.meanL - s.meanR) > c.flat) probs.push('应平坦 L-R=' + (s.meanL - s.meanR).toFixed(1));
      if (s.mean < c.meanLo || s.mean > c.meanHi) probs.push('均值越界 ' + s.mean.toFixed(1));
      const bMax = c.blackPctMax || 5, wMax = c.whitePctMax || 5;
      if (s.blackPct > bMax) probs.push('黑像素过多 ' + s.blackPct.toFixed(2) + '%');
      if (s.whitePct > wMax) probs.push('白像素过多 ' + s.whitePct.toFixed(2) + '%');
      if (probs.length) {
        console.log('  [FAIL] ' + c.name + '（' + c.file + '）: ' + probs.join('; '));
        fail++;
      } else {
        console.log('  [OK]   ' + c.name + '（' + c.file + '） ' + rec.status);
        pass++;
      }
    }
  } finally {
    await browser.close();
  }
  console.log('\n结果: ' + pass + '/' + CASES.length + ' 通过');
  if (errors.length) { console.log('页面错误:'); errors.slice(0, 5).forEach((e) => console.log('  ' + e)); }
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });
