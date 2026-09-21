// 一次性探查（非回归）：真实应用里「大图刚开始缩放卡一下」的帧时分解。
// 与 probe-zoom-jank.js 的区别：那个只压 drawImage 原语（结论：原语不是瓶颈），
// 这个把 frontend/dist 整个跑起来，把 Vue 刷新 / 第二张画布 / 云叠图全算进去。
//   node probe-app-zoom.js [--size=8192] [--steps=30] [--gap=25]
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { launchPage } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '..', 'frontend', 'dist');
const arg = (k, d) => {
  const m = process.argv.find((a) => a.startsWith('--' + k + '='));
  return m ? +m.split('=')[1] : d;
};
const N = arg('size', 8192);
const STEPS = arg('steps', 30);
const GAP = arg('gap', 25);
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function startServer() {
  const server = http.createServer((req, res) => {
    let u = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    // 与 test-vue-viewer.js 同款：/viewer/ 要 301 到 /viewer，否则 dist 里的**相对**
    // 资源路径会解析成 /viewer/assets/… 落空 → 回退 index.html → 模块 MIME 报错
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

/* 装载前注入：把 canvas2d 的三个热点方法计时，并记 rAF 帧间隔。
   注意 drawImage 是**延迟**执行的 —— 记到的时间只是「提交」，真正的光栅化体现在
   帧间隔上。两者一起看：提交变贵 = 主线程活儿；提交不变但帧间隔变长 = 光栅/合成。 */
function instrument() {
  // 次数是精确的，耗时在 toFixed(2) 下会被抹成 0（drawImage 的**提交**只要几十微秒），
  // 所以两者都记：次数用来看「这一帧到底画没画」，耗时用来看主线程里真正贵的是什么。
  const P = { draw: 0, clear: 0, read: 0, drawN: 0, clearN: 0, readN: 0, frames: [], last: 0, n: 0 };
  window.__p = P;
  const proto = CanvasRenderingContext2D.prototype;
  const wrap = (name, key, keyN) => {
    const orig = proto[name];
    proto[name] = function () {
      const t = performance.now();
      const r = orig.apply(this, arguments);
      P[key] += performance.now() - t;
      P[keyN]++;
      return r;
    };
  };
  wrap('drawImage', 'draw', 'drawN');
  wrap('clearRect', 'clear', 'clearN');
  wrap('getImageData', 'read', 'readN');
  (function loop(ts) {
    if (P.last) {
      P.frames.push({
        dt: +(ts - P.last).toFixed(2),
        draw: +P.draw.toFixed(3), clear: +P.clear.toFixed(3), read: +P.read.toFixed(3),
        n: P.drawN + '/' + P.clearN + '/' + P.readN,
      });
      P.draw = 0; P.clear = 0; P.read = 0;
      P.drawN = 0; P.clearN = 0; P.readN = 0;
    }
    P.last = ts; P.n++;
    if (P.n < 4000) requestAnimationFrame(loop);
  })(0);
}

/* 页内造一张 N×N 的 JPEG 并回传 base64（避免依赖本机 Python/Pillow） */
function makeJpegBase64(n) {
  return (async () => {
    const c = document.createElement('canvas');
    c.width = n; c.height = n;
    const g = c.getContext('2d');
    const grad = g.createLinearGradient(0, 0, n, n);
    grad.addColorStop(0, '#0d1b2a'); grad.addColorStop(0.5, '#778da9'); grad.addColorStop(1, '#e0e1dd');
    g.fillStyle = grad; g.fillRect(0, 0, n, n);
    // 高频细节：避免 JPEG 把整张压成纯色后进入「常量图」捷径
    for (let i = 0; i < 30000; i++) {
      const x = (i * 7919) % n, y = (i * 104729) % n;
      g.fillStyle = 'rgba(' + ((i * 37) % 255) + ',' + ((i * 91) % 255) + ',' + ((i * 13) % 255) + ',0.6)';
      g.fillRect(x, y, 3 + (i % 9), 3 + (i % 7));
    }
    const blob = await new Promise((r) => c.toBlob(r, 'image/jpeg', 0.85));
    const buf = new Uint8Array(await blob.arrayBuffer());
    let s = '';
    for (let i = 0; i < buf.length; i += 32768) {
      s += String.fromCharCode.apply(null, buf.subarray(i, i + 32768));
    }
    return { b64: btoa(s), bytes: buf.length };
  })();
}

(async () => {
  const server = await startServer();
  const tmpJpg = path.join(os.tmpdir(), 'probe-big-' + N + '.jpg');
  const { browser, page, errors } = await launchPage({ args: ['--enable-gpu', '--use-angle=d3d11'] });
  const out = { src: N + 'x' + N, steps: STEPS, gapMs: GAP };
  try {
    await page.setViewport({ width: 1600, height: 900 });
    await page.evaluateOnNewDocument(instrument);
    await page.goto(server.url + '/viewer');
    await sleep(800);

    // 造图（页内）→ 落盘 → 走应用的 input[type=file] 上传
    if (!fs.existsSync(tmpJpg)) {
      const jpg = await page.evaluate(makeJpegBase64, N);
      fs.writeFileSync(tmpJpg, Buffer.from(jpg.b64, 'base64'));
      out.jpegMB = +(jpg.bytes / 1048576).toFixed(1);
    } else {
      out.jpegMB = +(fs.statSync(tmpJpg).size / 1048576).toFixed(1);
    }
    const t0 = Date.now();
    const input = await page.$('input[type=file]');
    await input.uploadFile(tmpJpg);
    let rec = null;
    let statusMs = 0;
    const maskTitles = [];
    for (;;) {
      const st = await page.evaluate(() => {
        // 遮罩文案从 DOM 读：`window.__viewer` 是**显式装配**的钩子对象，store 里那个
        // overlay 并不在它上面（读它只会得到 undefined，然后误判成「遮罩没文案」）。
        const t = document.querySelector('.decode-mask .mask-title');
        return {
          rec: window.__viewer.activeRec(),
          masked: !!(window.__viewer.overlayVisible && window.__viewer.overlayVisible()),
          title: t ? t.textContent : null,
        };
      });
      rec = st.rec;
      if (st.masked && st.title && maskTitles.indexOf(st.title) < 0) maskTitles.push(st.title);
      // 本地 JPG 走 route='img'，成功态是「已读取」而不是「完成」
      const ok = rec && rec.status &&
        (rec.status.indexOf('完成') === 0 || rec.status.indexOf('已读取') === 0);
      if (ok && !statusMs) statusMs = Date.now();
      // **状态就绪 ≠ 画面可交互**：装载收尾的缩放预热还在遮罩底下跑，遮罩盖着画布时
      // 真实滚轮会被它按命中测试吃掉 → 不等它收起，手势段测的是遮罩不是画布。
      if (ok && !st.masked) break;
      if (rec && rec.status && rec.status.indexOf('失败') === 0) throw new Error(rec.status);
      if (Date.now() - t0 > 120000) throw new Error('等待解码超时: ' + (rec && rec.status));
      await sleep(100);
    }
    out.loadMs = Date.now() - t0;
    out.maskTitles = maskTitles;                 // 应含「正在预热缩放…」
    out.warmMs = Date.now() - statusMs;          // 状态写好 → 遮罩收起 = 上屏+预热耗时
    out.thumb = rec.thumbW + 'x' + rec.thumbH;
    out.warmed = rec.warmed;                     // 预热真的落账了？（不是「跑过但没记」）
    // 预热必须把视图原样还回来：这里与后面手势段的起始 scale 应当吻合
    out.scaleAfterLoad = await page.evaluate(() => window.__viewer.cmpPanes()[0].view.scale);

    // 通知弹窗盖在画布正中：真实滚轮会被它吃掉（elementFromPoint = BUTTON.nm-ok），
    // 而合成 dispatchEvent 绕过命中测试 —— 不关掉就会得出「手势零次 drawImage」的假结论。
    const closed = await page.evaluate(() => {
      const b = document.querySelector('.nm-ok');
      if (!b) return false;
      b.click();
      return true;
    });
    out.noticeModalClosed = closed;
    await sleep(300);
    // 关完弹窗再确认一次画布中心真的是画布（不是又被谁盖住了）
    out.hitAtCenter = await page.evaluate(() => {
      const c = document.querySelector('canvas.view-canvas');
      const r = c.getBoundingClientRect();
      const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return el ? el.tagName + '.' + (el.className || '') : null;
    });

    // 手势：鼠标停在画布中心后连续滚轮
    const rect = await page.evaluate(() => {
      const c = document.querySelector('canvas.view-canvas');
      const r = c.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    await page.mouse.move(rect.x, rect.y);
    await sleep(600);                                  // 静置：让缓存该失效的都失效
    await page.evaluate(() => { window.__p.frames.length = 0; });
    out.scaleBefore = await page.evaluate(() => window.__viewer.cmpPanes()[0].view.scale);
    for (let i = 0; i < STEPS; i++) {
      await page.mouse.wheel({ deltaY: -120 });
      await sleep(GAP);
    }
    await sleep(400);
    out.scaleAfter = await page.evaluate(() => window.__viewer.cmpPanes()[0].view.scale);
    const P = await page.evaluate(() => window.__p.frames);
    out.frames = P.map((f) => f.dt);
    out.first8 = P.slice(0, 8).map((f) => ({ dt: f.dt, ms: f.draw, clearMs: f.clear, calls: f.n }));
    const sorted = out.frames.slice().sort((a, b) => a - b);
    out.medianFrame = sorted[sorted.length >> 1];
    out.maxFrame = Math.max.apply(null, out.frames);
    // 整段手势的汇总：画了没有、主线程花了多少、有没有一帧掉出去
    const busy = P.filter((f) => f.n !== '0/0/0');
    out.summary = {
      framesRecorded: P.length,
      framesWithDraw: busy.length,
      maxDrawMs: Math.max.apply(null, P.map((f) => f.draw)),
      maxClearMs: Math.max.apply(null, P.map((f) => f.clear)),
      maxReadMs: Math.max.apply(null, P.map((f) => f.read)),
      callsFirstFrame: P.length ? P[0].n : null,
      callsPerFrame: busy.length ? busy[busy.length - 1].n : null,
      framesOver20ms: P.filter((f) => f.dt > 20).length,
    };
    // 云叠/ROI 是否参与（若用户开了云量红叠，这里会是 true）
    out.cloudShow = await page.evaluate(() => !!document.querySelector('.rt') && window.__viewer.cloudShow?.());
    out.errors = errors;
    console.log(JSON.stringify(out, null, 2));
  } catch (e) {
    console.log('探查失败:', e.message);
    console.log(JSON.stringify(out, null, 2));
    console.log('页面错误:', errors.slice(0, 5));
  } finally {
    await browser.close();
    server.close();
  }
})();
