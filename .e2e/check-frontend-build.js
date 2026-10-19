// Phase 2 验收：Vue3 构建产物 dist 的浏览器运行时验证（.e2e 本地资产，gitignore 不入库）
// 验证点：
//   1. 静态服务打开 dist 首页 → 应用挂载、无外部网络请求、无 console/page 错误
//   2. 路由 /viewer 可达（SPA fallback 生效）
//   3. 上传 fixture → probe→chunked→拉伸→canvas 真实渲染（取像素断言非全黑且有渐变）
//   4. 切换拉伸模式 → 重绘（像素变化）
//   5. 顶栏品牌区：平台名 + logo 预留区（部署态没有 logo 文件，退占位小方块）、只有名字变大
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchPage } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '..', 'frontend', 'dist');
const FIXTURE = path.resolve(__dirname, '..', 'frontend', 'fixtures', 'u16_whitezero.tif');
// 故意写死字面量（不从 src/lib/brand.ts import）：测试要独立地钉住这个名字，
// 跟着常量走的话，常量改错就一起错了。
const APP_NAME = '长光卫星-修图智能体平台';
// 顶栏 logo 的高度带（style.css 的 .brand-mark）—— 预留区的判据
const MARK_H = 28;
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
    assert(title.includes(APP_NAME), `页面标题=${title}`);
    const brand = await page.$eval('.brand-name', (el) => el.textContent.trim());
    assert(brand === APP_NAME, `平台名=${brand}`);

    // 5. 品牌区：logo 素材不在仓库里（正式素材在内网机上），所以**部署态就只有「没有 logo」这一种**。
    // 等它定形再断言：img 拿到的东西解不出来时由 @error 自摘（本文件的静态服务对不存在的文件
    // 回退 index.html 而非 404，img 一样报 error，与 nginx 的 404 同路），摘掉后才轮到占位小方块。
    await page.waitForFunction(
      () => {
        const el = document.querySelector('.brand-mark');
        if (!el) return false;
        const img = el.querySelector('img');
        // 无 img（已摘掉）或 img 已解码出像素（真的放了 logo）才算定形 —— 都排除掉「正在加载」
        return !img || img.naturalWidth > 0;
      },
      { timeout: 5000 },
    );
    const mark = await page.$eval('.brand-mark', (el) => {
      const r = el.getBoundingClientRect();
      return {
        w: r.width,
        h: r.height,
        imgs: el.querySelectorAll('img').length,
        dot: getComputedStyle(el, '::before').width,
      };
    });
    assert(mark.imgs === 0, `没有 logo 文件时不留破图（img=${mark.imgs}）`);
    assert(mark.dot === '10px', `占位小方块顶上（::before width=${mark.dot}）`);
    assert(mark.w === MARK_H && mark.h === MARK_H,
      `logo 预留区占住 ${MARK_H}×${MARK_H}（实测 ${mark.w}×${mark.h}）— 放了 logo 也不挤名字`);

    // 平台名比四个导航项大一号，且**只有**名字变
    const font = await page.evaluate(() => ({
      brand: getComputedStyle(document.querySelector('.brand-name')).fontSize,
      nav: Array.from(document.querySelectorAll('.nav-links a'))
        .map((a) => getComputedStyle(a).fontSize),
    }));
    assert(font.brand === '18px', `平台名字号=${font.brand}`);
    assert(font.nav.length === 4 && font.nav.every((s) => s === '14px'),
      `四个导航项仍是 14px（${font.nav.join('/')}）`);

    assert(externalRequests.length === 0, `无外部网络请求 (external=${JSON.stringify(externalRequests)})`);

    // 2. 路由：/viewer 直接可达（SPA fallback）
    console.log('--- 2. /viewer 路由 ---');
    await page.goto(base + '/viewer', { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForSelector('button', { timeout: 10000 });
    const btnText = await page.$eval('button', (el) => el.textContent);
    // 只认「有选择入口」这件事：这个按钮从 ba8e61d 起叫「选择影像…」（含拖入），
    // 本文件是 Phase 2 的验收脚本，没人随改动重跑，于是钉着更早的「选择 TIF」空跑至今。
    assert(btnText.includes('选择'), `查看器按钮存在 (${btnText})`);

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
