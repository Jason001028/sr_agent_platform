// Phase 2 验收：Vue3 构建产物 dist 的浏览器运行时验证（.e2e 本地资产，gitignore 不入库）
// 验证点：
//   1. 静态服务打开 dist 首页 → 应用挂载、无外部网络请求、无 console/page 错误
//   2. 路由 /viewer 可达（SPA fallback 生效）
//   3. 上传 fixture → probe→chunked→拉伸→canvas 真实渲染（取像素断言非全黑且有渐变）
//   4. 切换拉伸模式 → 重绘（像素变化）
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchPage } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '..', 'frontend', 'dist');
const FIXTURE = path.resolve(__dirname, '..', 'frontend', 'fixtures', 'u16_whitezero.tif');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

function startServer() {
  const server = http.createServer((req, res) => {
    let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    // 与 deploy/nginx.conf 的尾斜杠规整一致：/viewer/ → 301 /viewer（base:'./' 下尾斜杠会把
    // ./assets 解析成 /viewer/assets → 404）
    const m = urlPath.match(/^\/(viewer|chat|queue)\/$/);
    if (m) { res.writeHead(301, { location: '/' + m[1] }); res.end(); return; }
    if (urlPath === '/') urlPath = '/index.html';
    let filePath = path.join(DIST, urlPath);
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(DIST, 'index.html'); // SPA fallback（history 路由 /viewer 等）
    }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(500); res.end(String(err)); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function assert(cond, msg) {
  if (!cond) throw new Error('断言失败: ' + msg);
  console.log('  ✓ ' + msg);
}

async function main() {
  const server = await startServer();
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  console.log(`[phase2-check] 静态服务 ${base}  (dist=${DIST})`);

  const { browser, page, errors } = await launchPage();
  const externalRequests = [];
  page.on('request', (req) => {
    const u = req.url();
    if (!u.startsWith(base) && !u.startsWith('data:')) externalRequests.push(u);
  });

  try {
    // 1. 首页挂载 + 无外部请求
    console.log('--- 1. 首页加载 ---');
    await page.goto(base + '/', { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForSelector('.app-nav', { timeout: 10000 });
    const title = await page.title();
    assert(title.includes('sr_agent_platform'), `页面标题=${title}`);
    const brand = await page.$eval('.brand', (el) => el.textContent);
    assert(brand.includes('sr_agent_platform'), `brand=${brand}`);
    assert(externalRequests.length === 0, `无外部网络请求 (external=${JSON.stringify(externalRequests)})`);

    // 2. 路由：/viewer 直接可达（SPA fallback）
    console.log('--- 2. /viewer 路由 ---');
    await page.goto(base + '/viewer', { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForSelector('button', { timeout: 10000 });
    const btnText = await page.$eval('button', (el) => el.textContent);
    assert(btnText.includes('选择 TIF'), `查看器按钮存在 (${btnText})`);

    // 3. 上传 fixture → 解码 → canvas 渲染
    console.log('--- 3. 解码渲染 ---');
    const input = await page.$('input[type=file]');
    assert(!!input, '文件 input 存在');
    await input.uploadFile(FIXTURE);
    await page.waitForSelector('canvas.tif-canvas[width]', { timeout: 20000 });
    await page.waitForFunction(() => {
      const el = document.querySelector('.rec-bar');
      return el && el.textContent.includes('256×128');
    }, { timeout: 20000 });
    const stats1 = await page.evaluate(() => {
      const c = document.querySelector('canvas.tif-canvas');
      const ctx = c.getContext('2d');
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let min = 255, max = 0, sum = 0, nz = 0;
      const step = Math.max(1, (d.length / 4 / 5000) | 0);
      for (let i = 0; i < d.length; i += 4 * step) {
        const v = d[i];
        if (v < min) min = v; if (v > max) max = v;
        sum += v; if (v > 0) nz++;
      }
      return { w: c.width, h: c.height, min, max, mean: sum / nz, nonBlack: nz > 0 };
    });
    assert(stats1.w > 0 && stats1.h > 0, `canvas 尺寸 ${stats1.w}×${stats1.h}`);
    assert(stats1.nonBlack && stats1.max > stats1.min, `像素有内容且有渐变 (min=${stats1.min} max=${stats1.max} mean=${stats1.mean.toFixed(1)})`);

    // 4. 切换拉伸 → 重绘
    console.log('--- 4. 拉伸切换 ---');
    await page.select('select', 'sqrt');
    await new Promise((r) => setTimeout(r, 300));
    const stats2 = await page.evaluate(() => {
      const c = document.querySelector('canvas.tif-canvas');
      const ctx = c.getContext('2d');
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let sum = 0, n = 0;
      const step = Math.max(1, (d.length / 4 / 5000) | 0);
      for (let i = 0; i < d.length; i += 4 * step) { sum += d[i]; n++; }
      return { mean: sum / n };
    });
    assert(Math.abs(stats2.mean - stats1.mean) > 0.5, `拉伸切换重绘生效 (mean ${stats1.mean.toFixed(1)} → ${stats2.mean.toFixed(1)})`);

    // 5. 尾斜杠路由 /viewer/ → 301 规整 → 应用仍正常（对齐 nginx.conf）
    console.log('--- 5. 尾斜杠路由 ---');
    const resp = await page.goto(base + '/viewer/', { waitUntil: 'networkidle0', timeout: 30000 });
    assert(resp && resp.status() === 200 && page.url().endsWith('/viewer'), `301 规整到 /viewer (最终 URL=${page.url()})`);
    await page.waitForSelector('.app-nav', { timeout: 10000 });
    assert(externalRequests.length === 0, `尾斜杠路径无外部请求 (external=${JSON.stringify(externalRequests)})`);

    // 汇总
    assert(errors.length === 0, `无浏览器错误 (${JSON.stringify(errors.slice(0, 5))})`);
    console.log('\n[phase2-check] ✅ 全部通过');
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((err) => {
  console.error('\n[phase2-check] ❌ 失败:', err.message);
  process.exit(1);
});
