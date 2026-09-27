// 一次性诊断：滚轮事件到底有没有打到画布上（probe-app-zoom.js 里手势零次 drawImage 的原因）
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { launchPage } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '..', 'frontend', 'dist');
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function startServer() {
  const server = http.createServer((req, res) => {
    let u = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const m = u.match(/^\/(viewer|chat|queue)\/$/);
    if (m) { res.writeHead(301, { location: '/' + m[1] }); res.end(); return; }
    if (u === '/') u = '/index.html';
    let f = path.join(DIST, u);
    if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(DIST, 'index.html');
    fs.readFile(f, (e, d) => {
      if (e) { res.writeHead(500); res.end(String(e)); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(f)] || 'application/octet-stream' });
      res.end(d);
    });
  });
  return new Promise((r) => server.listen(0, '127.0.0.1', () => r({ url: 'http://127.0.0.1:' + server.address().port, close: () => server.close() })));
}

(async () => {
  const server = await startServer();
  const tmpJpg = path.join(os.tmpdir(), 'probe-big-8192.jpg');
  const { browser, page, errors } = await launchPage({ args: ['--enable-gpu', '--use-angle=d3d11'] });
  try {
    await page.setViewport({ width: 1600, height: 900 });
    await page.goto(server.url + '/viewer');
    await sleep(800);
    const input = await page.$('input[type=file]');
    await input.uploadFile(tmpJpg);
    for (let i = 0; i < 60; i++) {
      const r = await page.evaluate(() => window.__viewer.activeRec());
      if (r && r.status && r.status.indexOf('已读取') === 0) break;
      await sleep(300);
    }
    const geom = await page.evaluate(() => {
      const c = document.querySelector('canvas.view-canvas');
      const r = c.getBoundingClientRect();
      const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
      const el = document.elementFromPoint(cx, cy);
      const rec = window.__viewer.activeRec();
      return {
        rect: { x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1) },
        center: { cx: +cx.toFixed(1), cy: +cy.toFixed(1) },
        hit: el ? (el.tagName + '.' + (el.className || '')) : null,
        canvasBacking: { w: c.width, h: c.height },
        scale: window.__viewer.view ? +(window.__viewer.cmpPanes()[0].view.scale.toFixed(4)) : null,
        thumb: rec ? rec.thumbW + 'x' + rec.thumbH : null,
      };
    });
    console.log('几何:', JSON.stringify(geom));

    // 关掉通知弹窗后再看一次谁在画布中心
    await page.evaluate(() => { const b = document.querySelector('.nm-ok'); if (b) b.click(); });
    await sleep(400);
    console.log('关弹窗后命中:', JSON.stringify(await page.evaluate((c) => {
      const el = document.elementFromPoint(c.cx, c.cy);
      // 逐层列出该点上的所有元素，找出真正吃掉滚轮的那个
      const stack = document.elementsFromPoint(c.cx, c.cy).map((e) => e.tagName + '.' + (e.className || ''));
      return { top: el ? el.tagName + '.' + (el.className || '') : null, stack };
    }, geom.center)));

    // 1) 真实输入：puppeteer 的 mouse.wheel
    await page.mouse.move(geom.center.cx, geom.center.cy);
    await sleep(120);
    const s0 = await page.evaluate(() => window.__viewer.cmpPanes()[0].view.scale);
    await page.mouse.wheel({ deltaY: -120 });
    await sleep(300);
    const s1 = await page.evaluate(() => window.__viewer.cmpPanes()[0].view.scale);

    // 2) 合成事件：页内 dispatchEvent
    const s2 = await page.evaluate((c) => {
      const el = document.querySelector('canvas.view-canvas');
      el.dispatchEvent(new WheelEvent('wheel', {
        deltaY: -120, clientX: c.cx, clientY: c.cy, bubbles: true, cancelable: true,
      }));
      return window.__viewer.cmpPanes()[0].view.scale;
    }, geom.center);

    console.log('scale: 初始', +s0.toFixed(4), '→ mouse.wheel后', +s1.toFixed(4), '→ dispatchEvent后', +s2.toFixed(4));
    console.log('mouse.wheel 生效:', s1 !== s0, ' dispatchEvent 生效:', s2 !== s1);
    console.log('页面错误:', errors.slice(0, 5));
  } catch (e) {
    console.log('诊断失败:', e.message, errors.slice(0, 5));
  } finally {
    await browser.close();
    server.close();
  }
})();
