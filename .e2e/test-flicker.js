// 交替对比（Flicker Comparison）浏览器回归（2026-11-03）
// ------------------------------------------------------------------
// 跑 frontend/dist + 静态服务，用页内合成 JPEG 摆两张同场景影像（不经后端）：
//   A. 进入：胶囊出现、配对文字 / 节拍 / 播放键齐全，配准通过
//   B. 播放：两张图在**同一块画布**上按节拍轮换，左右亮度互换（真的翻面、不是黑屏）
//   C. 暂停：停在当前这一帧
//   D. 自动暂停：画布拖动（平移）→ 停
//   E. 自动暂停 + 重配：切图 → 停，并以新活动图重新配对
//   F. 退出：回到单屏，原始视口一个数都没变；胶囊消失
//   G. 无可配图：新场景目录里只有一张 → 禁止播放并显示具体原因
// 用法: node test-flicker.js
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchPage } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '..', 'frontend', 'dist');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

let pass = 0;
function assert(cond, msg) {
  if (!cond) throw new Error('断言失败: ' + msg);
  console.log('  ✓ ' + msg);
  pass++;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function startServer() {
  const server = http.createServer((req, res) => {
    let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const m = urlPath.match(/^\/(viewer|chat|queue)\/$/);
    if (m) { res.writeHead(301, { location: '/' + m[1] }); res.end(); return; }
    if (urlPath === '/') urlPath = '/index.html';
    let filePath = path.join(DIST, urlPath);
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(DIST, 'index.html');
    }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(500); res.end(String(err)); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/** 页内合成 400×200 JPEG：`leftBlack` = 左黑右白，否则左白右黑。 */
function makeJpeg(leftBlack) {
  return `(() => {
    const c = document.createElement('canvas');
    c.width = 400; c.height = 200;
    const g = c.getContext('2d');
    g.fillStyle = '${leftBlack ? '#101010' : '#f0f0f0'}'; g.fillRect(0, 0, 200, 200);
    g.fillStyle = '${leftBlack ? '#f0f0f0' : '#101010'}'; g.fillRect(200, 0, 200, 200);
    return c.toDataURL('image/jpeg', 0.95);
  })()`;
}

/** 页内把 dataURL → Blob → openSceneJpg，等遮罩收起、像素就位。 */
async function openDemo(page, url, meta) {
  await page.evaluate(async (u, m) => {
    const blob = await (await fetch(u)).blob();
    await window.__viewer.openSceneJpg(m, blob);
  }, url, meta);
  for (let i = 0; i < 80; i++) {
    const st = await page.evaluate(() => ({
      busy: window.__viewer.overlayVisible(),
      rec: window.__viewer.activeRec(),
    }));
    if (!st.busy && st.rec && st.rec.thumbW > 0 && st.rec.sceneDir === meta.sceneDir) return st.rec;
    await sleep(100);
  }
  throw new Error('场景没装上：' + JSON.stringify(await page.evaluate(() => window.__viewer.activeRec())));
}

/** 画布中心左右各 ±60px 的亮度（400×200 的图按 scale=1 居中）。 */
function sampleCanvas(page) {
  return page.evaluate(() => {
    const c = document.querySelector('.view-canvas');
    const g = c.getContext('2d');
    const cx = Math.floor(c.width / 2), cy = Math.floor(c.height / 2);
    const v = (dx) => {
      const d = g.getImageData(cx + dx, cy, 1, 1).data;
      return (d[0] + d[1] + d[2]) / 3;
    };
    return { left: v(-60), right: v(60), cw: c.width, ch: c.height };
  });
}

async function waitFlip(page, wantAlt, timeoutMs = 4000) {
  const t0 = Date.now();
  for (;;) {
    const st = await page.evaluate(() => window.__viewer.flicker());
    if ((st.index === 1) === wantAlt) return st;
    if (Date.now() - t0 > timeoutMs) throw new Error('等翻面超时：' + JSON.stringify(st));
    await sleep(50);
  }
}

(async () => {
  const server = await startServer();
  const base = 'http://127.0.0.1:' + server.address().port;
  console.log(`[test-flicker] 静态服务 ${base}  (dist=${DIST})`);

  const { browser, page, errors } = await launchPage();
  try {
    // 默认视口 800×600 下左栏 + 侧舱会把舞台挤到 0 宽；与其它浏览器回归同口径。
    await page.setViewport({ width: 1366, height: 900, deviceScaleFactor: 1 });
    await page.goto(base + '/viewer', { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForFunction(() => !!(window.__viewer && window.__viewer.flicker), { timeout: 15000 });
    await page.waitForFunction(() => {
      const c = document.querySelector('.view-canvas');
      return !!c && c.width > 0;
    }, { timeout: 10000 });

    /* ============ A. 摆两张同场景影像 ============ */
    console.log('--- A. 进入交替对比 ---');
    const SCENE = '/DiskArray/E2E/FLICKER';
    const jpgPan = await page.evaluate(makeJpeg(true));      // 左黑右白
    const jpgSr = await page.evaluate(makeJpeg(false));      // 左白右黑
    const panRec = await openDemo(page, jpgPan, {
      name: 'PAN', W: 400, H: 200, sceneId: 'fx-pan',
      lqPath: SCENE, sceneDir: SCENE, stageKind: 'input',
    });
    const srRec = await openDemo(page, jpgSr, {
      name: 'DEMO_SR', W: 400, H: 200, sceneId: 'fx-sr',
      lqPath: SCENE, sceneDir: SCENE, stageKind: 'product', stageSuffix: 'SR',
    });
    assert(srRec.stageKind === 'product' && panRec.stageKind === 'input',
      `两张同场景影像就位（${panRec.name} / ${srRec.name}）`);

    // 进入前的单屏视口：退出后要原样回来
    const viewBefore = await page.evaluate(() => ({ ...window.__viewer.cmpPanes()[0].view }));

    await page.evaluate(() => window.__viewer.setCmpMode('flicker'));
    await page.waitForSelector('[data-e2e="flicker-capsule"]', { timeout: 5000 });
    // 默认起手是「直方图均衡」；两张合成图只有两个灰阶，均衡会把它们压到一起。
    // 这里换成线性拉伸，让「左亮/右亮」成为可判读的翻面证据（与交替逻辑无关）。
    await page.evaluate(() => window.__viewer.setStretch('linear'));
    await sleep(100);
    const st0 = await page.evaluate(() => window.__viewer.flicker());
    assert(st0.ready === true && st0.reason === '',
      `配对通过，可以播放（reason="${st0.reason}"）`);
    assert(st0.pair.a === 'SR' && st0.pair.b === 'PAN',
      `胶囊两端是 SR/PAN（实际 ${st0.pair.a}/${st0.pair.b}）`);
    assert(st0.intervalMs === 1000 && st0.playing === false,
      `默认 1.0 秒、进入时不自动播放（${st0.intervalMs}ms）`);
    assert(!!await page.$('[data-e2e="flicker-interval"]')
      && (await page.$$('[data-e2e="flicker-interval"] option')).length === 5,
      '节拍下拉是五档');

    /* ============ B. 播放：同一画布上真翻面 ============ */
    console.log('--- B. 播放 ==========');
    const s0 = await sampleCanvas(page);
    assert(s0.left > s0.right,
      `第一帧是 SR（左亮右暗：${s0.left.toFixed(0)}/${s0.right.toFixed(0)}）`);
    await page.click('[data-e2e="flicker-play"]');
    let st1 = await waitFlip(page, true);
    assert(st1.playing === true && st1.showingAlt === true,
      '播放中已翻到第二张（PAN）');
    const s1 = await sampleCanvas(page);
    assert(s1.left < s1.right,
      `第二帧是 PAN：同画布左右亮度互换（${s1.left.toFixed(0)}/${s1.right.toFixed(0)}）`);
    assert(s1.cw === s0.cw && s1.ch === s0.ch,
      `翻面没有重建画布（${s1.cw}×${s1.ch} 保持不变）`);
    assert(s1.left > 2 || s1.right > 2, '翻面后不是黑屏');

    /* ============ C. 暂停：停在当前帧 ============ */
    console.log('--- C. 暂停 ==========');
    await page.click('[data-e2e="flicker-pause"]');
    const stPaused = await page.evaluate(() => window.__viewer.flicker());
    await sleep(1400);
    const stStill = await page.evaluate(() => window.__viewer.flicker());
    assert(stPaused.playing === false && stStill.index === stPaused.index,
      `暂停后帧号不动（${stPaused.index} → ${stStill.index}）`);

    /* ============ F1. 退出：原始视口原样恢复（其间没切图、没平移） ============ */
    console.log('--- F1. 退出恢复 ==========');
    await page.evaluate(() => window.__viewer.setCmpMode('off'));
    await sleep(200);
    const cmpOff0 = await page.evaluate(() => window.__viewer.cmpMode());
    const viewAfter0 = await page.evaluate(() => ({ ...window.__viewer.cmpPanes()[0].view }));
    assert(cmpOff0 === 'off' && !(await page.$('[data-e2e="flicker-capsule"]')),
      '退出后回到单屏、胶囊消失');
    assert(Math.abs(viewAfter0.scale - viewBefore.scale) < 1e-9
      && Math.abs(viewAfter0.ox - viewBefore.ox) < 1e-9
      && Math.abs(viewAfter0.oy - viewBefore.oy) < 1e-9,
      `原始视口一个数都没变（scale ${viewBefore.scale} → ${viewAfter0.scale}）`);

    await page.evaluate(() => window.__viewer.setCmpMode('flicker'));
    await page.waitForSelector('[data-e2e="flicker-capsule"]', { timeout: 5000 });
    assert((await page.evaluate(() => window.__viewer.flicker())).ready === true,
      '再次进入仍可播放');

    /* ============ D. 平移自动暂停 ============ */
    console.log('--- D. 平移暂停 ==========');
    await page.evaluate(() => window.__viewer.flickerPlay());
    await waitFlip(page, true);
    const rect = await page.evaluate(() => {
      const r = document.querySelector('.view-canvas').getBoundingClientRect();
      return { cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
    });
    await page.mouse.move(rect.cx, rect.cy);
    await page.mouse.down();
    await page.mouse.move(rect.cx + 80, rect.cy + 40, { steps: 5 });
    await page.mouse.up();
    await sleep(120);
    assert((await page.evaluate(() => window.__viewer.flicker())).playing === false,
      '画布拖动（平移）→ 自动暂停');

    /* ============ E. 切图自动暂停并重配 ============ */
    console.log('--- E. 切图暂停 + 重配 ==========');
    await page.evaluate(() => window.__viewer.flickerPlay());
    await waitFlip(page, true);
    // 交替模式复用了「点选清单」（compareOn 为真时可见）：点 PAN 那一行换活动图，
    // 与用户手动切图的路径一致，不额外开测试专用钩子。
    const switched = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('[data-e2e="cmp-row"]')];
      const row = rows.find((r) => (r.textContent || '').indexOf('PAN') >= 0);
      if (!row) return { ok: false, rows: rows.map((r) => (r.textContent || '').trim()) };
      row.click();
      return { ok: true };
    });
    assert(switched.ok, `点选清单里能点到 PAN（${JSON.stringify(switched.rows || [])}）`);
    await sleep(200);
    const stSwitch = await page.evaluate(() => window.__viewer.flicker());
    assert(stSwitch.playing === false, '切图 → 自动暂停');
    assert(stSwitch.pair.a === 'PAN' && stSwitch.pair.b === 'SR',
      `以新活动图为参考图重配（${stSwitch.pair.a}/${stSwitch.pair.b}）`);

    /* ============ F2. 切过图后退出：保留当前看到的视口 ============ */
    console.log('--- F2. 切图后退出的视口 ==========');
    // 两张缩略图同尺寸，翻到第二张时 display view 与参考图 view 数值相同，
    // 所以「退出后 = 退出前画布上的 view」可以直接比。
    const viewInFlicker = await page.evaluate(() => ({ ...window.__viewer.cmpPanes()[0].view }));
    await page.evaluate(() => window.__viewer.setCmpMode('off'));
    await sleep(200);
    const viewAfter = await page.evaluate(() => ({ ...window.__viewer.cmpPanes()[0].view }));
    assert(await page.evaluate(() => window.__viewer.cmpMode()) === 'off',
      '退出后回到单屏');
    assert(Math.abs(viewAfter.scale - viewInFlicker.scale) < 1e-9
      && Math.abs(viewAfter.ox - viewInFlicker.ox) < 1e-9
      && Math.abs(viewAfter.oy - viewInFlicker.oy) < 1e-9,
      `切图后退出：保留交替中看到的视口（scale ${viewInFlicker.scale}）`);
    assert(await page.evaluate(() => window.__viewer.activeRec().name) === 'PAN',
      '单屏显示的是交替中切过去的那张（PAN）');

    /* ============ G. 没有可配的图 → 禁止播放并说明 ============ */
    console.log('--- G. 无可配图 ==========');
    const OTHER = '/DiskArray/E2E/OTHER';
    await openDemo(page, await page.evaluate(makeJpeg(true)), {
      name: 'OTHER_PAN', W: 400, H: 200, sceneId: 'fx-other',
      lqPath: OTHER, sceneDir: OTHER, stageKind: 'input',
    });
    await page.evaluate(() => window.__viewer.setCmpMode('flicker'));
    await sleep(250);
    const stNo = await page.evaluate(() => window.__viewer.flicker());
    assert(stNo.ready === false && stNo.reason.indexOf('没有可交替') >= 0,
      `无可配图 → 不放行并给原因（${stNo.reason.slice(0, 24)}…）`);
    const disabled = await page.evaluate(() =>
      document.querySelector('[data-e2e="flicker-play"]').disabled);
    assert(disabled === true, '播放键置灰');
    assert(await page.evaluate(() => {
      const el = document.querySelector('[data-e2e="flicker-reason"]');
      return !!el && el.textContent.trim().length > 0;
    }), '原因写在胶囊下方');

    assert(errors.length === 0, `无浏览器错误 (${JSON.stringify(errors.slice(0, 5))})`);
    console.log(`\n[test-flicker] ✅ 全部通过 (${pass} 项断言)`);
  } finally {
    await browser.close();
    server.close();
  }
})().catch((err) => {
  console.error('\n[test-flicker] ❌ 失败:', err.message);
  process.exit(1);
});
