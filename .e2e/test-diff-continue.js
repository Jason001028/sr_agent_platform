// Synthetic browser regression: 「延续到下一景」with two local scenes.
// The built app is served statically; /api/diff/analyze is intercepted and
// answered with a synthetic result, no TIFF/SR/backend involved.
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');
const dist = path.resolve(__dirname, '../frontend/dist');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname.startsWith('/api/')) {
    res.statusCode = 404;
    res.setHeader('Content-Type', 'application/json');
    res.end('{}');
    return;
  }
  let file = path.join(dist, decodeURIComponent(url.pathname));
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
  res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
  res.end(fs.readFileSync(file));
});

let browser;
let analyzeCalls = 0;
let holdAnalysis = false;
let releaseAnalysis;
let heldRequestReady;
(async () => {
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    browser = await launchBrowser();
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.stack));
    await page.setViewport({ width: 1440, height: 1000 });
    await page.setRequestInterception(true);
    page.on('request', async (req) => {
      if (!req.url().includes('/api/diff/analyze')) return req.continue();
      analyzeCalls++;
      try {
        if (holdAnalysis) {
          await new Promise(resolve => { releaseAnalysis = resolve; heldRequestReady?.(); });
        }
        const body = JSON.parse(req.postData());
        const fixture = await page.evaluate(async (body) => {
          const image = new Image();
          image.src = body.image_a.grayPng;
          await image.decode();
          const w = image.width, h = image.height;
          const c = document.createElement('canvas');
          c.width = w; c.height = h;
          const ctx = c.getContext('2d');
          ctx.drawImage(image, 0, 0);
          const differenceMap = c.toDataURL();
          ctx.fillStyle = 'black'; ctx.fillRect(0, 0, w, h);
          const binaryMask = c.toDataURL();
          ctx.fillStyle = 'white'; ctx.fillRect(0, 0, w, h);
          const validityMap = c.toDataURL();
          const meta = (input) => ({ ...input, gridWidth: w, gridHeight: h });
          return {
            operation: 'difference-localization', method: body.method, source: 'preview',
            imageA: meta(body.image_a), imageB: meta(body.image_b),
            analysisGrid: { width: w, height: h, origin: [0, 0], scaleA: [1, 1], scaleB: [1, 1], mode: 'preview' },
            differenceMap, binaryMask, validityMap,
            regions: [{
              id: 'r001', rank: 1, areaPx: 100, relativeArea: .1, bbox: [8, 8, 10, 10],
              centroid: [13, 13], contour: [[8, 8], [18, 8], [18, 18], [8, 18]],
              contourKind: 'exact', holes: 0, meanDiff: .5, maxDiff: .8, rankScore: 1,
            }],
            coverage: { gridPixels: w * h, validPixels: w * h, validRatio: 1 },
            parameters: { ...body, threshold: .12 },
            truncated: false, warnings: [], elapsedMs: 1,
          };
        }, body);
        await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) });
      } catch (e) {
        errors.push(e.stack);
        await req.abort();
      }
    });

    await page.goto('http://127.0.0.1:' + server.address().port + '/viewer');
    await page.waitForSelector('[data-e2e=diff-open]');
    await page.evaluate(async () => {
      const p = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
      window.testStores = { viewer: p._s.get('viewer'), diff: p._s.get('diff') };
      const c = document.createElement('canvas'); c.width = c.height = 256;
      const ctx = c.getContext('2d');
      const gradient = ctx.createLinearGradient(0, 0, 256, 256);
      gradient.addColorStop(0, '#305040'); gradient.addColorStop(1, '#a0b0c0');
      ctx.fillStyle = gradient; ctx.fillRect(0, 0, 256, 256);
      const blob = await new Promise((r) => c.toBlob(r, 'image/jpeg'));
      const names = ['PAN_A.jpg', 'PAN_NOSR_A.jpg', 'PAN_B.jpg', 'PAN_NOSR_B.jpg'];
      testStores.viewer.addFiles(names.map((n) => new File([blob], n)));
    });
    await page.waitForFunction(() => testStores.viewer.recs.length === 4
      && testStores.viewer.recs.every((r) => r.src && r.thumb));
    // 本地 JPG 会顺手试一次盘阵关联；这里的假后端一律 404，会弹「没有关联到
    // 盘阵目录」。等几次请求落定后关掉，避免遮住工具栏上的指针命中。
    await new Promise((r) => setTimeout(r, 800));
    await page.evaluate(() => testStores.viewer.hideModal());

    const ids = await page.evaluate(async () => {
      const v = testStores.viewer, d = testStores.diff;
      // recs 按解码完成顺序入列，和 addFiles 的传入顺序不保证一致，按文件名取。
      const byName = Object.fromEntries(v.recs.map((r) => [r.name, r]));
      const a1 = byName['PAN_A.jpg'], a2 = byName['PAN_NOSR_A.jpg'];
      const b1 = byName['PAN_B.jpg'], b2 = byName['PAN_NOSR_B.jpg'];
      a1.sceneDir = '/synthetic/A'; a2.sceneDir = '/synthetic/A';
      b1.sceneDir = '/synthetic/B'; b2.sceneDir = '/synthetic/B';
      a1.stageKind = 'input'; a1.stageLabel = 'PAN';
      a2.stageKind = 'nosr'; a2.stageLabel = 'NOSR';
      b1.stageKind = 'input'; b1.stageLabel = 'PAN';
      b2.stageKind = 'nosr'; b2.stageLabel = 'NOSR';
      // 「下一景」按卡片入列顺序取，合成场景得排成 A 在前、B 在后。
      const sceneOrder = ['/synthetic/A', '/synthetic/B'];
      v.recs.sort((x, y) => sceneOrder.indexOf(x.sceneDir) - sceneOrder.indexOf(y.sceneDir));
      await v.activate(a1.id);
      d.aId = a1.id; d.bId = a2.id;
      d.display = 'overlay'; d.opacity = .6; d.feather = 'medium';
      return { a1: a1.id, a2: a2.id, b1: b1.id, b2: b2.id };
    });
    assert.equal(await page.evaluate(() => testStores.diff.analyze()), true);
    await page.waitForSelector('[data-e2e=diff-continue]');

    // 位置紧挨「差异定位」；外观沿用鹰眼工具键（28px、8 圆角、半透明）。
    assert.equal(await page.evaluate(() =>
      document.querySelector('[data-e2e=diff-open]').closest('.diff-entry').nextElementSibling
        ?.getAttribute('data-e2e') === 'diff-continue'), true);
    const style = await page.$eval('[data-e2e=diff-continue]', (e) => {
      const s = getComputedStyle(e);
      return { opacity: s.opacity, height: s.height, radius: s.borderRadius, text: e.textContent.trim() };
    });
    assert.equal(style.opacity, '0.45');
    assert.equal(style.height, '28px');
    assert.equal(style.radius, '8px');
    assert.equal(style.text, '延续到下一景');
    await page.screenshot({ path: path.join(__dirname, 'artifacts', 'diff-continue-button.png') });

    // Display-only switches do not remove navigation; temporary modes disable it.
    await page.evaluate(() => { testStores.diff.resultVisible = false; testStores.diff.display = 'regions'; });
    await page.waitForFunction(() => !!document.querySelector('[data-e2e=diff-continue]') && !document.querySelector('[data-e2e=diff-continue]').disabled);
    await page.evaluate(() => testStores.viewer.enterDraw());
    await page.waitForFunction(() => document.querySelector('[data-e2e=diff-continue]')?.disabled);
    assert.match(await page.$eval('[data-e2e=diff-continue]', e => e.title), /绘制/);
    await page.evaluate(() => { testStores.viewer.exitDraw(); testStores.viewer.setCompareMode('split'); });
    await page.waitForFunction(() => document.querySelector('[data-e2e=diff-continue]')?.disabled);
    assert.match(await page.$eval('[data-e2e=diff-continue]', e => e.title), /退出对比/);
    await page.evaluate(() => {
      testStores.viewer.setCompareMode('off'); testStores.diff.display = 'overlay'; testStores.diff.resultVisible = true;
    });
    await page.waitForFunction(() => !document.querySelector('[data-e2e=diff-continue]').disabled);
    assert.equal(analyzeCalls, 1, '临时状态切换不重新分析');
    await page.hover('[data-e2e=diff-continue]');
    await page.waitForFunction(() =>
      getComputedStyle(document.querySelector('[data-e2e=diff-continue]')).opacity === '1');

    const heldRequest = new Promise(resolve => { heldRequestReady = resolve; });
    holdAnalysis = true;
    await page.click('[data-e2e=diff-continue]');
    await heldRequest;
    await page.waitForFunction(() => testStores.diff.busy && testStores.diff.continuing);
    const during = await page.$eval('[data-e2e=diff-continue]', e => ({ disabled: e.disabled, text: e.textContent.trim() }));
    assert.deepEqual(during, { disabled: true, text: '续景中…' });
    assert.equal(await page.evaluate(() => testStores.diff.continueToNextScene()), false);
    assert.equal(analyzeCalls, 2);
    holdAnalysis = false;
    releaseAnalysis();
    await page.waitForFunction((n) =>
      !testStores.diff.busy && testStores.diff.aId === n.b1 && testStores.diff.bId === n.b2, {}, ids);
    for (let i = 0; i < 100 && analyzeCalls < 2; i++) await new Promise((r) => setTimeout(r, 50));
    assert.equal(analyzeCalls, 2, '延续到下一景后直接重新分析一次');
    assert.equal(await page.evaluate((n) => testStores.viewer.activeRec.id === n.b1, ids), true);
    assert.equal(await page.evaluate(() => testStores.diff.display), 'overlay');
    assert.equal(await page.evaluate(() => testStores.diff.opacity), .6);
    assert.equal(await page.evaluate(() => testStores.diff.feather), 'medium');
    // 末景没有下一景：按钮收起。
    await page.waitForFunction(() => !document.querySelector('[data-e2e=diff-continue]'));
    await page.waitForSelector('.viewer-legend');
    await page.screenshot({ path: path.join(__dirname, 'artifacts', 'diff-continue-next.png') });

    assert.deepEqual(errors, []);
    console.log('PASS: 延续到下一景 survives display changes, disables in draw/compare, stays during delayed final-scene analysis, blocks duplicate navigation, preserves roles and parameters');
  } finally {
    if (browser) await browser.close();
    await new Promise((r) => server.close(r));
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
