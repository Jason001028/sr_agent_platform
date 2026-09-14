// 环境绿灯冒烟：file:// 打开真实 tif-viewer.html + window.__viewer 钩子存在 + 零页面错误
// 用法: node test-bootstrap.js    （环境就绪判据）
const { launchPage } = require('./launchBrowser');
const { openViewer } = require('./lib/capture');

async function main() {
  const { browser, page, errors } = await launchPage();
  try {
    await openViewer(page);
    const info = await page.evaluate(() => ({
      title: document.title,
      hasViewer: !!window.__viewer,
      fileInput: !!document.getElementById('fileInput'),
      canvases: document.querySelectorAll('canvas').length,
    }));
    if (!info.hasViewer || !info.fileInput) throw new Error('viewer 钩子/输入缺失');
    console.log('bootstrap OK:', info.title, '| canvas x' + info.canvases, '| __viewer=true');
  } finally {
    await browser.close();
  }
  if (errors.length) { console.log('页面错误:'); errors.forEach((e) => console.log('  ' + e)); process.exit(1); }
}
main().catch((e) => { console.error('bootstrap FAIL:', e.message); process.exit(1); });
