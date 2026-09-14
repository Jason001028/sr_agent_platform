// 采集金标准：真实浏览器跑一遍各测试图，输出画布统计（供 test-*.js 断言基线）
// 用法: node capture-golden.js
const { launchPage } = require('./launchBrowser');
const { openViewer, uploadAndWait, sampleThumb } = require('./lib/capture');
const path = require('path');

const T = (p) => path.join(__dirname, '..', 'test-tifs', p);
const IMAGES = [
  'rgb8.tif', 'gdal_byte.tif', 'gdal_float32.tif', 'gray16.tif', 'deflate_tile128.tif',
  'types/u16_rgb.tif', 'types/u16_whitelzero.tif', 'types/u32_gray.tif',
  'types/f32_gray.tif', 'types/f32_reflect.tif', 'types/f32_rgb.tif', 'types/f32_skew.tif',
  'types/gf_like.tif',
  'sparse/big_u16.tif',
];

async function main() {
  const { browser, page, errors } = await launchPage();
  try {
    for (const rel of IMAGES) {
      await openViewer(page);          // 每图前重开页面，隔离 recs 状态
      const rec = await uploadAndWait(page, T(rel), 120000);
      const s = await sampleThumb(page, 256);
      console.log(JSON.stringify({ file: rel, ...rec, ...s }));
    }
  } finally {
    await browser.close();
  }
  if (errors.length) { console.log('页面错误:'); errors.slice(0, 5).forEach((e) => console.log('  ' + e)); process.exit(1); }
}
main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });
