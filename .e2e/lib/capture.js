// 浏览器回归公共工具：打开 tif-viewer → 上传测试图 → 采样缩略图画布统计
// 页面状态读取约定（gui-experience §3.7）：input.value 会被清空，必须从 window.recs[].file 拿文件。
const path = require('path');

const VIEWER = 'file:///' + path.join(__dirname, '..', '..', 'tif_viewer', 'tif-viewer.html').replace(/\\/g, '/');

async function openViewer(page) {
  await page.goto(VIEWER, { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction(() => !!window.__viewer && !!document.getElementById('fileInput'), { timeout: 15000 });
}

async function uploadAndWait(page, filePath, timeoutMs) {
  const input = await page.$('#fileInput');
  if (!input) throw new Error('未找到 #fileInput');
  await input.uploadFile(filePath);
  const t0 = Date.now();
  for (;;) {
    const rec = await page.evaluate(() => {
      const r = window.recs && window.recs[0];
      if (!r) return null;
      return { status: r.status || '', W: r.W, H: r.H, thumbW: r.thumb ? r.thumb.width : 0, thumbH: r.thumb ? r.thumb.height : 0 };
    });
    if (rec && rec.status) {
      if (rec.status.indexOf('完成') === 0) return rec;
      if (rec.status.indexOf('解码失败') === 0) throw new Error('解码失败: ' + rec.status);
    }
    if (Date.now() - t0 > timeoutMs) throw new Error('超时等待解码，最近状态: ' + (rec && rec.status));
    await new Promise((r) => setTimeout(r, 250));
  }
}

// 在页面内把 thumb 画到 size×size 临时画布再读像素（8192² 大图也便宜），返回统计
function sampleThumb(page, size) {
  return page.evaluate((size) => {
    const rec = window.recs[0];
    if (!rec || !rec.thumb) throw new Error('无 rec.thumb');
    const tw = rec.thumb.width, th = rec.thumb.height;
    const s = size || 256;
    const c = document.createElement('canvas');
    c.width = s; c.height = s;
    const g = c.getContext('2d');
    g.imageSmoothingEnabled = true;
    g.drawImage(rec.thumb, 0, 0, s, s);
    const d = g.getImageData(0, 0, s, s).data;
    let sum = 0, sumL = 0, sumR = 0, n = 0, black = 0, white = 0;
    const split = Math.floor(s * 0.2);
    for (let y = 0; y < s; y++) {
      for (let x = 0; x < s; x++) {
        const i = (y * s + x) * 4;
        const v = (d[i] + d[i + 1] + d[i + 2]) / 3;
        sum += v; n++;
        if (x < split) sumL += v;
        if (x >= s - split) sumR += v;
        if (v < 2) black++;
        if (v > 252) white++;
      }
    }
    return {
      tw, th,
      mean: sum / n,
      meanL: sumL / (split * s),
      meanR: sumR / (split * s),
      blackPct: (black / n) * 100,
      whitePct: (white / n) * 100,
    };
  }, size);
}

module.exports = { openViewer, uploadAndWait, sampleThumb, VIEWER };
