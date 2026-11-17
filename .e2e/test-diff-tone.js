// Synthetic browser regression: 差异候选「亮度归因权重」开关。
// /api/diff/analyze 被拦截并返回两个合成区域：暗部与中间调各一。
// 断言默认关闭时保持后端顺序，打开后中间调平坦区域被提前，且候选行出现权重条。
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
      try {
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
          // 差异强度取中间值：加权后中间调会升到接近满色，暗部保持 1×，
          // 这样开关前后的热力图颜色有可见差别。
          ctx.fillStyle = '#808080'; ctx.fillRect(0, 0, w, h);
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
            regions: [
              {
                id: 'r001', rank: 1, areaPx: 4096, relativeArea: .0625,
                bbox: [16, 16, 64, 64], centroid: [48, 48],
                contour: [[16, 16], [80, 16], [80, 80], [16, 80]],
                contourKind: 'exact', holes: 0, meanDiff: .5, maxDiff: .8, rankScore: 100,
              },
              {
                id: 'r002', rank: 2, areaPx: 4096, relativeArea: .0625,
                bbox: [160, 16, 64, 64], centroid: [192, 48],
                contour: [[160, 16], [224, 16], [224, 80], [160, 80]],
                contourKind: 'exact', holes: 0, meanDiff: .5, maxDiff: .8, rankScore: 80,
              },
            ],
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
      ctx.fillStyle = '#303030'; ctx.fillRect(0, 0, 128, 256);      // 暗部
      ctx.fillStyle = '#969696'; ctx.fillRect(128, 0, 128, 256);    // 中间调
      const blob = await new Promise((r) => c.toBlob(r, 'image/jpeg', .95));
      testStores.viewer.addFiles([
        new File([blob], 'PAN_T.jpg'), new File([blob], 'PAN_NOSR_T.jpg'),
      ]);
    });
    await page.waitForFunction(() => testStores.viewer.recs.length === 2
      && testStores.viewer.recs.every((r) => r.src && r.thumb));
    await new Promise((r) => setTimeout(r, 800));
    await page.evaluate(() => testStores.viewer.hideModal());

    const ids = await page.evaluate(async () => {
      const v = testStores.viewer;
      const byName = Object.fromEntries(v.recs.map((r) => [r.name, r]));
      const a = byName['PAN_T.jpg'], b = byName['PAN_NOSR_T.jpg'];
      await v.activate(a.id);
      window.testStores.diff.aId = a.id;
      window.testStores.diff.bId = b.id;
      return { a: a.id, b: b.id };
    });
    assert.equal(await page.evaluate(() => testStores.diff.analyze()), true);
    await page.waitForFunction(() => testStores.diff.result?.regions.length === 2);

    const heatSample = () => page.evaluate(() => {
      const c = testStores.diff.heatLayer;
      const g = document.createElement('canvas');
      g.width = c.width; g.height = c.height;
      const gc = g.getContext('2d');
      gc.drawImage(c, 0, 0);
      const px = (x, y) => {
        const d = gc.getImageData(x, y, 1, 1).data;
        return [d[0], d[1], d[2], d[3]];
      };
      return { mid: px(200, 128), dark: px(60, 128) };
    });
    const heatOn = await heatSample();

    const on = await page.evaluate(() => ({
      toneWeight: testStores.diff.toneWeight,
      order: testStores.diff.orderedRegions.map((r) => r.id),
      tones: testStores.diff.regions.map((r) => r.tone && {
        id: r.id, mid: r.tone.mid, dark: r.tone.dark, weight: r.tone.weight,
      }),
    }));
    assert.equal(on.toneWeight, true);
    assert.deepEqual(on.order, ['r002', 'r001']);
    assert.ok(on.tones[0] && on.tones[0].dark > .9, JSON.stringify(on.tones));
    assert.ok(on.tones[1] && on.tones[1].mid > .9, JSON.stringify(on.tones));
    assert.ok(on.tones[1].weight > on.tones[0].weight, JSON.stringify(on.tones));

    await page.click('[data-e2e=diff-open]');
    await page.waitForSelector('[data-e2e=diff-tone-weight]');
    const onRows = await page.evaluate(() => ({
      rows: [...document.querySelectorAll('.dc-row')].map((el) => el.textContent.replace(/\s+/g, ' ').trim()),
      bars: document.querySelectorAll('.dc-tone-fill').length,
    }));
    assert.equal(onRows.bars, 2);
    assert.ok(onRows.rows[0].includes('中间调'), onRows.rows[0]);

    // Turning the default-on weight off must restore the backend order and the
    // unweighted heat layer.
    await page.click('[data-e2e=diff-tone-weight]');
    await page.waitForFunction(() => testStores.diff.toneWeight === false
      && testStores.diff.orderedRegions[0]?.id === 'r001');
    await page.evaluate(() => { testStores.diff.display = 'heat'; });
    await page.waitForSelector('[data-e2e=diff-legend-tone]', { state: 'detached' }).catch(() => {});
    const heatOff = await heatSample();
    assert.notDeepEqual(heatOn.mid, heatOff.mid);
    assert.deepEqual(heatOn.dark, heatOff.dark);
    const off = await page.evaluate(() => ({
      order: testStores.diff.orderedRegions.map((r) => r.id),
      bars: document.querySelectorAll('.dc-tone-fill').length,
    }));
    assert.deepEqual(off.order, ['r001', 'r002']);
    assert.equal(off.bars, 0);

    await page.click('[data-e2e=diff-tone-weight]');
    await page.waitForFunction(() => testStores.diff.toneWeight === true
      && testStores.diff.orderedRegions[0]?.id === 'r002');
    await page.waitForSelector('[data-e2e=diff-legend-tone]');
    assert.equal(heatOn.mid[3], 255);
    assert.ok(await page.$('[data-e2e=diff-legend-tone]'), 'legend must explain weighted intensity');

    await page.screenshot({ path: path.resolve(__dirname, 'artifacts/diff-tone-weight.png'), fullPage: false });
    assert.deepEqual(errors, []);
    console.log('diff tone weight ok', JSON.stringify({ on, off, heatOff, heatOn }));
  } finally {
    await browser?.close();
    server.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
