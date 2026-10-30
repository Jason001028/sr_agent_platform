// Real Chromium + Vue store + FileThumb; HTTP image fixtures, no production disk/SR.
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { pathToFileURL } = require('url');
const assert = require('assert/strict');
const { launchPage } = require('./launchBrowser');
const root = path.resolve(__dirname, '..');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  // Production bundling is required: local UMD vendor libraries have no dev ESM default.
  const { build } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/vite/dist/node/index.js')).href);
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sr-batch-e2e-'));
  await build({ root: path.join(root, 'frontend'), configFile: path.join(root, 'frontend/vite.config.ts'),
    base: '/', build: { outDir, rollupOptions: { input: path.join(root, 'frontend/tests/batch-preview.html') } } });
  const server = http.createServer((req, res) => {
    const file = path.resolve(outDir, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(outDir + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return; }
    const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type }); fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    const session = await launchPage(); browser = session.browser;
    const page = session.page;
    page.on('pageerror', error => console.error('browser error:', error.message));
    page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
    await page.setViewport({ width: 1200, height: 900 });
    await page.goto(origin + '/tests/batch-preview.html');
    await page.waitForFunction(() => !!window.batchTest);
    // Generate fixtures once; detailed decoding uses the real browser JPEG pipeline.
    const fixtures = await page.evaluate(() => [64, 4096].map(size => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = '#537b9c'; ctx.fillRect(0, 0, size, size);
      return canvas.toDataURL('image/jpeg').split(',')[1];
    }));
    const requests = [];
    await page.setRequestInterception(true);
    page.on('request', async req => {
      const url = req.url();
      if (url.includes('/disk-array/')) {
        requests.push(url);
        const thumb = url.includes('_thumb.jpg');
        await req.respond({ status: 200, contentType: 'image/jpeg', body: Buffer.from(fixtures[thumb ? 0 : 1], 'base64') });
      } else if (url.includes('/api/')) {
        requests.push(url);
        await req.respond({ status: 200, contentType: 'application/json', body: '{}' });
      } else await req.continue();
    });
    for (const count of [10, 50, 100]) {
      requests.length = 0;
      const result = await page.evaluate(async count => {
        const { store, nextTick } = window.batchTest;
        store.clearRecs();
        const started = performance.now();
        for (let i = 0; i < count; i++) {
          const id = store.insertSceneCard({ sceneId: String(i), name: String(i), W: 8192, H: 8192 },
            { id: String(i), thumbnail_url: `/disk-array/${count}/${i}_thumb.jpg`, jpgUrl: `/disk-array/${i}.jpg`, hasPreview: true });
          await store.bakeCardPixels(id);
        }
        await nextTick();
        return { count, enqueueMs: performance.now() - started,
          detailCanvases: document.querySelectorAll('.ft canvas').length,
          resident: store.recs.filter(rec => rec.src || rec.thumb).length };
      }, count);
      await page.waitForFunction(() => [...document.querySelectorAll('.ft img')].some(img => img.naturalWidth > 0));
      assert.equal(result.detailCanvases, 0); assert.equal(result.resident, 0);
      assert(requests.every(url => url.includes('_thumb.jpg')), JSON.stringify(requests));
      console.log(JSON.stringify({ ...result, observedThumbnailRequests: requests.length, detailedRequests: 0 }));
    }
    // Real 4096² canvas + Float32 pixels, exercising the existing budget watcher.
    const recovery = await page.evaluate(async () => {
      const { store, nextTick } = window.batchTest;
      store.setPreviewMemoryMB(256);
      const ids = store.recs.slice(0, 3).map(rec => rec.id);
      for (const id of ids) await store.activate(id);
      await nextTick();
      const evicted = !store.recs.find(rec => rec.id === ids[0]).thumb;
      await store.activate(ids[0]);
      const rec = store.recs.find(rec => rec.id === ids[0]);
      return { evicted, restored: !!rec.thumb && !!rec.src, sceneId: rec.sceneId, fileBytes: rec.file.size };
    });
    assert.equal(recovery.evicted, true); assert.equal(recovery.restored, true);
    assert.equal(recovery.sceneId, '0'); assert.equal(recovery.fileBytes, 0);
    console.log('browser recovery', JSON.stringify(recovery));
    const consumers = await page.evaluate(async () => {
      const { store, nextTick } = window.batchTest;
      const ids = store.recs.slice(0, 3).map(rec => rec.id);
      const activeBefore = store.activeId;
      const screenshotTargetWasEvicted = !store.recs.find(rec => rec.id === ids[1]).thumb;
      const shot = await store.captureForChat(ids[1]);
      const screenshotKeptActive = store.activeId === activeBefore;
      store.setCmpPrefetch(false);
      store.setCompareMode('split');
      await store.activate(ids[0], 'A');
      await store.activate(ids[2], 'B');
      await nextTick();
      const rightTargetWasEvicted = !store.recs.find(rec => rec.id === ids[1]).thumb;
      await store.activate(ids[1], 'B');
      return { screenshotTargetWasEvicted, screenshot: !!shot?.dataUrl, screenshotKeptActive,
        rightTargetWasEvicted, leftId: store.paneA, rightId: store.paneB,
        expectedLeft: ids[0], expectedRight: ids[1],
        bothResident: !!store.recForSide('A')?.thumb && !!store.recForSide('B')?.thumb };
    });
    assert.equal(consumers.screenshotTargetWasEvicted, true);
    assert.equal(consumers.screenshot, true); assert.equal(consumers.screenshotKeptActive, true);
    assert.equal(consumers.rightTargetWasEvicted, true); assert.equal(consumers.bothResident, true);
    assert.equal(consumers.leftId, consumers.expectedLeft); assert.equal(consumers.rightId, consumers.expectedRight);
    console.log('browser consumers', JSON.stringify(consumers));
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
