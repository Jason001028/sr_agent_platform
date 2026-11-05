// Production-browser regression using synthetic JPEGs; never runs SR/GPU tasks.
// Run after frontend build: node .e2e/test-image-refresh.js
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');
const dist = path.resolve(__dirname, '../frontend/dist');
const streams = new Set();
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/queue/events')) {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.write(': connected\n\n');
    streams.add(res); req.on('close', () => streams.delete(res)); return;
  }
  if (req.url.startsWith('/api/')) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(req.url.startsWith('/api/queue') ? { tasks: [] } : {})); return;
  }
  const relative = decodeURIComponent(req.url.split('?')[0]);
  const target = path.resolve(dist, '.' + relative);
  if (!target.startsWith(dist + path.sep) && target !== dist) { res.writeHead(403); res.end(); return; }
  const file = fs.existsSync(target) && fs.statSync(target).isFile() ? target : path.join(dist, 'index.html');
  const mime = { '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.html': 'text/html' };
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
let browser;
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await launchBrowser(); const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/viewer`, { waitUntil: 'networkidle2' });
  await page.waitForFunction(() => document.querySelector('#app').__vue_app__?.config.globalProperties.$pinia._s.has('viewer'));
  const fixture = await page.evaluate(async () => {
    const stores = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
    const viewer = stores.get('viewer'), refresh = stores.get('imageRefresh');
    const cv = document.createElement('canvas'); cv.width = 320; cv.height = 240;
    const ctx = cv.getContext('2d'); const gradient = ctx.createLinearGradient(0, 0, 320, 240);
    gradient.addColorStop(0, '#23343a'); gradient.addColorStop(1, '#a8c6b8'); ctx.fillStyle = gradient; ctx.fillRect(0, 0, 320, 240);
    const blob = await new Promise(resolve => cv.toBlob(resolve, 'image/jpeg'));
    const meta = { name: 'PAN.tif', sceneId: 'original', sceneDir: '/synthetic/sequence', lqPath: '/synthetic/sequence', W: 1280, H: 960, srcRev: 'aa-1-1' };
    await viewer.openSceneJpg(meta, blob);
    const originalId = viewer.activeId;
    const result = { task_id: 1, state: 'ready', note: '新预览就绪', group: meta.sceneDir, kind: 'product', finished_at: 1,
      version: 'output|bb-2-2', row: { id: 'output', name: 'PAN_SR.tif', W: 1280, H: 960, srcRev: 'bb-2-2', hasPreview: true,
        previewDiv: viewer.previewDiv, jpgUrl: null, size_bytes: 2000000, lq_path: meta.sceneDir } };
    refresh.accept(result);
    refresh.accept({ ...result, task_id: 2, version: 'output2|cc-3-3', row: { ...result.row, id: 'output2', name: 'PAN_SR_2.tif', srcRev: 'cc-3-3' } });
    viewer.view.scale = 1.4; viewer.view.ox = -35; viewer.view.oy = -20;
    return { result, originalId, jpeg: Array.from(new Uint8Array(await blob.arrayBuffer())) };
  });
  const stamp = Buffer.from(`srprev:v4:div${fixture.result.row.previewDiv}+equal:q90;src=bb-2-2`);
  const segment = Buffer.alloc(4); segment[0] = 255; segment[1] = 254; segment.writeUInt16BE(stamp.length + 2, 2);
  const jpeg = Buffer.from(fixture.jpeg);
  const stamped = Buffer.concat([jpeg.subarray(0, 2), segment, stamp, jpeg.subarray(2)]);
  let badPreview = true;
  await page.setRequestInterception(true);
  page.on('request', req => {
    if (/\/api\/queue\/1\/image-refresh\/preview/.test(req.url())) {
      req.respond({ status: 200, contentType: 'image/jpeg', body: badPreview ? Buffer.from('corrupt') : stamped });
    } else if (/\/api\/queue\/1\/image-refresh(?:\?|$)/.test(req.url())) {
      req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture.result) });
    } else req.continue();
  });
  await page.waitForFunction(() => document.querySelectorAll('[data-e2e="image-refresh-badge"]').length === 2);
  await page.waitForFunction(() => document.querySelectorAll('.sr-refresh-card--sweep').length === 1);
  assert.equal(await page.$eval('.refresh-group-count', el => el.textContent.trim()), '2 张新图');
  assert.equal(await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').activeId), fixture.originalId);
  const outputId = await page.evaluate(() => Object.values(document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('imageRefresh').entries).find(e => e.result.task_id === 1).recId);
  const card = `[data-rec-id="${outputId}"]`;
  assert.equal(await page.$eval(card, el => getComputedStyle(el).transform), 'none');
  assert.equal(await page.$eval(`${card} .ft`, el => el.getBoundingClientRect().width), 96);
  await page.waitForFunction(() => document.querySelectorAll('.sr-refresh-card--enter').length === 0);
  const artifacts = path.join(__dirname, 'artifacts'); fs.mkdirSync(artifacts, { recursive: true });
  await page.screenshot({ path: path.join(artifacts, 'image-refresh-unread.png') });
  await page.click(`${card} .refresh-open`);
  await page.waitForSelector(`${card} .refresh-error`);
  assert.equal(await page.$eval('.refresh-group-count', el => el.textContent.trim()), '2 张新图');
  badPreview = false;
  await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
  await page.waitForFunction(() => document.querySelectorAll('.sr-refresh-card--sweep').length === 0);
  assert.ok(await page.$eval(`${card} .sr-refresh-card__badge-icon`, el => el.src.endsWith('-static.svg')));
  await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'no-preference' }]);
  await page.evaluate(() => {
    const viewer = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
    viewer.setPaneView('A', { scale: 1.4, ox: -35, oy: -20 });
  });
  await page.click(`${card} .refresh-open`);
  await page.waitForFunction(id => {
    const stores = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
    return stores.get('viewer').activeId === id && stores.get('imageRefresh').entries[id]?.unread === false;
  }, {}, outputId);
  await page.waitForFunction(selector => !document.querySelector(selector).classList.contains('sr-refresh-card--unread'), {}, card);
  assert.equal(await page.$eval('.refresh-group-count', el => el.textContent.trim()), '1 张新图');
  const context = await page.evaluate(id => {
    const viewer = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
    return { version: viewer.recs.find(r => r.id === id).srcRev, x: viewer.view.ox, y: viewer.view.oy, scale: viewer.view.scale };
  }, outputId);
  assert.deepEqual(context, { version: 'bb-2-2', x: -35, y: -20, scale: 1.4 });
  // A read receipt may survive while workspace records still refer to older pixels.
  await page.evaluate(id => {
    const viewer = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
    viewer.recs.find(r => r.id === id).srcRev = 'bb-1-1';
  }, outputId);
  await page.click(`${card} .fname`);
  await page.waitForFunction(id => {
    const viewer = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
    return viewer.recs.find(r => r.id === id).srcRev === 'bb-2-2';
  }, {}, outputId);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(artifacts, 'image-refresh-read.png') });
  console.log('Image Refresh browser checks passed: unread, group counts, corrupt JPEG retry, reduced motion, atomic display, camera preservation, read-version reopening.');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close(); for (const stream of streams) stream.end(); server.close();
});
