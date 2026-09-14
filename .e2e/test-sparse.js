// 稀疏条带预览回归（对齐 gui-experience §3.9）
// big_u16.tif（8192² 16bit 无压缩·条带1行·134MB）：应命中稀疏路径（状态含"稀疏条带预览"、
// 缩略图 8192²、渐变正确、不崩）；strips1_def.tif（Deflate 压缩条带）：应回退分块正常解码（Deflate 回退）。
// 用法: node test-sparse.js
const { launchPage } = require('./launchBrowser');
const { openViewer, uploadAndWait, sampleThumb } = require('./lib/capture');
const path = require('path');

const T = (p) => path.join(__dirname, '..', 'test-tifs', p);

async function main() {
  const { browser, page, errors } = await launchPage();
  let fail = 0;

  try {
    // —— big_u16：稀疏条带预览 ——
    await openViewer(page);
    let rec = await uploadAndWait(page, T('sparse/big_u16.tif'), 120000);
    const s = await sampleThumb(page, 256);
    const probs = [];
    if (rec.status.indexOf('稀疏条带预览') < 0) probs.push('未命中稀疏路径: ' + rec.status);
    if (rec.thumbW !== 8192 || rec.thumbH !== 8192) probs.push('缩略图应 8192² 实为 ' + rec.thumbW + 'x' + rec.thumbH);
    if (rec.W !== 8192 || rec.H !== 8192) probs.push('原图尺寸 ' + rec.W + 'x' + rec.H);
    if ((s.meanR - s.meanL) < 80) probs.push('渐变缺失 L=' + s.meanL.toFixed(1) + ' R=' + s.meanR.toFixed(1));
    if (s.blackPct > 5) probs.push('黑像素过多 ' + s.blackPct.toFixed(2) + '%');
    if (s.whitePct > 5) probs.push('白像素过多 ' + s.whitePct.toFixed(2) + '%');
    if (probs.length) { console.log('  [FAIL] sparse/big_u16: ' + probs.join('; ')); fail++; }
    else { console.log('  [OK]   sparse/big_u16: ' + rec.status); }

    // —— strips1_def：Deflate 压缩 → 回退分块正常解码 ——
    await openViewer(page);
    rec = await uploadAndWait(page, T('sparse/strips1_def.tif'), 60000);
    const probs2 = [];
    if (rec.status.indexOf('完成') !== 0) probs2.push('解码失败: ' + rec.status);
    if (rec.status.indexOf('稀疏条带预览') >= 0) probs2.push('压缩条带不应走稀疏: ' + rec.status);
    if (rec.thumbW > 2048) probs2.push('分块路径缩略图应 ≤2048 实为 ' + rec.thumbW);
    if (probs2.length) { console.log('  [FAIL] sparse/strips1_def: ' + probs2.join('; ')); fail++; }
    else { console.log('  [OK]   sparse/strips1_def: ' + rec.status); }
  } finally {
    await browser.close();
  }

  console.log('\n结果: ' + (fail ? '有失败' : '全部通过'));
  if (errors.length) { console.log('页面错误:'); errors.slice(0, 5).forEach((e) => console.log('  ' + e)); }
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });
