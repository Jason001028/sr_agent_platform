// Synthetic browser regression: built app + intercepted read-only analysis, no TIFF reloads.
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');
const dist = path.resolve(__dirname, '../frontend/dist');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/scenes/resolve') {
    let raw=''; for await(const chunk of req) raw+=chunk;
    const input=JSON.parse(raw);
    res.setHeader('Content-Type','application/json');
    res.end(JSON.stringify({row:{id:input.name,name:input.name,W:256,H:256,rasterPreview:null},resolved:{dir:'/synthetic/same-scene',sr_capable:false,kind:'input'}}));return;
  }
  if (url.pathname === '/api/queue') { res.setHeader('Content-Type','application/json');res.end(JSON.stringify({tasks:[]}));return; }
  if (url.pathname.startsWith('/api/')) { res.statusCode = 404; res.setHeader('Content-Type', 'application/json'); res.end('{}'); return; }
  let file = path.join(dist, decodeURIComponent(url.pathname));
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
  res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
  res.end(fs.readFileSync(file));
});
let browser, calls = 0;
(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  try {
    browser = await launchBrowser();
    const page = await browser.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.stack));
    await page.setViewport({ width: 1440, height: 1000 });
    await page.setRequestInterception(true);
    page.on('request', async req => {
      if (!req.url().includes('/api/diff/analyze')) return req.continue();
      calls++;
      try {
        const body = JSON.parse(req.postData());
        const fixture = await page.evaluate(async body => {
          const image = new Image(); image.src = body.image_a.grayPng; await image.decode();
          const w = image.width, h = image.height;
          const c = document.createElement('canvas'); c.width = w; c.height = h;
          const ctx = c.getContext('2d'), pixels = ctx.createImageData(w, h);
          for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4, v = Math.round(x / (w - 1) * 255);
            pixels.data.set([v, v, v, 255], i);
          }
          ctx.putImageData(pixels, 0, 0); const differenceMap = c.toDataURL();
          ctx.fillStyle = 'black'; ctx.fillRect(0, 0, w, h); const binaryMask = c.toDataURL();
          ctx.fillStyle = 'white'; ctx.fillRect(0, 0, w, h);
          ctx.fillStyle = 'black'; ctx.fillRect(w / 2 - 3, h / 2 - 3, 6, 6);
          const meta = input => ({ ...input, gridWidth: w, gridHeight: h });
          return { operation: 'difference-localization', method: body.method, source: 'preview',
            imageA: meta(body.image_a), imageB: meta(body.image_b),
            analysisGrid: { width: w, height: h, origin: [0, 0], scaleA: [1, 1], scaleB: [1, 1], mode: 'preview' },
            differenceMap, binaryMask, validityMap: c.toDataURL(),
            regions: [{ id: 'r001', rank: 1, areaPx: 100, relativeArea: .1, bbox: [8, 8, 10, 10], centroid: [13, 13],
              contour: [[8,8],[18,8],[18,18],[8,18]], contourKind: 'exact', holes: 0, meanDiff: .5, maxDiff: .8, rankScore: 1 }],
            coverage: { gridPixels: w*h, validPixels: w*h-36, validRatio: 1-36/(w*h) }, parameters: { ...body, threshold: .12 },
            truncated: false, warnings: [], elapsedMs: 1 };
        }, body);
        await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) });
      } catch (e) { errors.push(e.stack); await req.abort(); }
    });
    await page.goto('http://127.0.0.1:' + server.address().port + '/viewer');
    await page.waitForSelector('[data-e2e=diff-open]');
    assert.equal(await page.$('[data-e2e=diff-visibility]'), null);
    await page.evaluate(async () => {
      const p = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
      window.testStores = { viewer: p._s.get('viewer'), diff: p._s.get('diff') };
      const c = document.createElement('canvas'); c.width = c.height = 256;
      const ctx = c.getContext('2d'); const gradient = ctx.createLinearGradient(0,0,256,256);
      gradient.addColorStop(0, '#305040'); gradient.addColorStop(1, '#a0b0c0');
      ctx.fillStyle = gradient; ctx.fillRect(0,0,256,256);
      const blob = await new Promise(r => c.toBlob(r, 'image/jpeg'));
      testStores.viewer.addFiles([new File([blob], 'PAN.jpg'), new File([blob], 'PAN_old.jpg')]);
    });
    await page.waitForFunction(() => testStores.viewer.recs.length === 2 && testStores.viewer.recs.every(r => r.src && r.thumb && r.route === 'jpg'));
    await page.evaluate(() => {
      const { viewer: v, diff: d } = testStores;
      v.recs.forEach(r => { r.sceneDir = '/synthetic/same-scene'; });
      d.aId = v.recs[0].id; d.bId = v.recs[1].id; d.scope = 'visible';
      const p = v.panes[0]; v.setPaneView(p.side, { scale: 5, ox: p.rect.w/2-640, oy: p.rect.h/2-640 });
    });
    assert.equal(await page.evaluate(() => testStores.diff.analyze()), true);
    await page.evaluate(() => {
      const { viewer: v, diff: d } = testStores, p = v.panes[0];
      v.setPaneView(p.side, { scale: 1, ox: p.rect.w/2-128, oy: p.rect.h/2-128 });
      d.display = 'heat'; d.selectRegion('r001');
      window.cachedHeat = d.heatLayer; window.cachedResult = d.result;
      window.cameraBefore = JSON.stringify(v.panes[0].view);
    });
    await page.waitForSelector('.viewer-legend');
    const pixel = (tx, ty) => page.evaluate(([tx,ty]) => {
      const p = testStores.viewer.panes[0], c = document.querySelector('.view-canvas');
      return [...c.getContext('2d').getImageData(Math.round(p.rect.x+p.view.ox+tx*p.view.scale), Math.round(p.rect.y+p.view.oy+ty*p.view.scale),1,1).data];
    }, [tx,ty]);
    assert.deepEqual(await pixel(5,5), [32,44,57,255], 'ROI exterior is opaque unanalyzed background');
    assert.deepEqual(await pixel(128,128), [99,113,128,255], 'NoData has its own background');
    await page.$eval('[data-e2e=diff-open]', e => e.click());
    await page.waitForSelector('.diff-popover');
    assert.equal(await page.$eval('.diff-popover', e => Math.round(e.getBoundingClientRect().width)), 336);
    assert.equal(await page.$eval('.diff-advanced', e => e.open), false);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(() => testStores.diff.resultVisible), true);
    await page.click('[data-e2e=diff-visibility]');
    await page.waitForFunction(() => !document.querySelector('.viewer-legend'));
    assert.notDeepEqual(await pixel(5,5), [32,44,57,255]);
    await page.click('[data-e2e=diff-visibility]');
    await page.waitForSelector('.viewer-legend');
    assert.deepEqual(await pixel(5,5), [32,44,57,255]);
    assert.equal(await page.evaluate(() => testStores.diff.selectedId), 'r001');
    await page.click('[data-e2e=diff-open]');
    await page.waitForSelector('.diff-popover');
    for (const mode of ['regions', 'overlay', 'heat']) {
      await page.$eval('[role=radiogroup][aria-label="显示方式"]', (el, mode) => {
        const labels={regions:'原图标记',overlay:'热力叠加',heat:'独立热力图'};
        [...el.querySelectorAll('button')].find(b=>b.textContent.trim()===labels[mode]).click();
      }, mode);
      if(mode==='overlay') {
        await page.waitForSelector('[aria-label="热力叠加不透明度"]');
        await page.$eval('[aria-label="热力叠加不透明度"]', el=>{el.value='.6';el.dispatchEvent(new Event('input',{bubbles:true}));});
        assert.equal(await page.evaluate(()=>testStores.diff.opacity),.6);
      }
      await page.waitForFunction(mode => document.querySelector('.diff-visibility').getAttribute('aria-pressed') === 'true' && testStores.diff.display === mode, {}, mode);
      assert.equal(await page.evaluate(() => JSON.stringify(testStores.viewer.panes[0].view) === cameraBefore && testStores.diff.heatLayer === cachedHeat && testStores.diff.result === cachedResult), true);
    }
    await page.keyboard.press('Escape');
    // Split and flicker use each actual pane record, with identical mapped heat coverage.
    assert.equal(await page.evaluate(() => testStores.diff.openFlicker()), true);
    await page.evaluate(async () => { const v=testStores.viewer; v.setCompareMode('split'); await v.activate(v.recs[0].id,'A'); await v.activate(v.recs[1].id,'B'); });
    await page.waitForFunction(() => testStores.viewer.panes.length === 2);
    assert.equal(await page.evaluate(() => testStores.viewer.panes.every(p => !!testStores.diff.layerFor(p.rec))), true);
    assert.equal(await page.evaluate(() => testStores.diff.openFlicker()), true);
    await page.waitForFunction(() => testStores.viewer.flickerReady);
    assert.equal(await page.evaluate(() => testStores.viewer.setTourLayer(true)), true);
    await page.waitForFunction(() => testStores.viewer.flickerShowingAlt);
    assert.equal(await page.evaluate(() => !!testStores.diff.layerFor(testStores.viewer.panes[0].rec)), true);
    await page.evaluate(() => testStores.viewer.setCompareMode('off'));
    // Candidate navigation still drives the existing Camera, then restore a full overview for visual QA.
    assert.equal(await page.evaluate(() => testStores.diff.locateRegion('r001')), true);
    await page.evaluate(() => { const v=testStores.viewer,p=v.panes[0]; v.setPaneView(p.side,{scale:2,ox:p.rect.w/2-256,oy:p.rect.h/2-256}); });
    await page.$eval('[data-e2e=diff-open]', e => e.click());
    await page.waitForSelector('.diff-popover');
    for (const width of [1366, 1920]) {
      await page.setViewport({width,height:1000});
      await page.waitForFunction(() => document.querySelector('.diff-popover').getBoundingClientRect().width === 336);
      const rect = await page.$eval('[data-e2e=diff-visibility]', e => ({left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right}));
      assert.ok(rect.left >= 0 && rect.right <= width);
      await page.screenshot({path:path.join(__dirname,'artifacts','diff-visualization-'+width+'.png')});
    }
    await page.evaluate(() => testStores.diff.clearResults());
    await page.waitForFunction(() => !document.querySelector('[data-e2e=diff-visibility]') && !document.querySelector('.viewer-legend'));
    assert.equal(calls, 1, 'visual controls never reanalyze');
    assert.deepEqual(errors, []);
    console.log('PASS: visibility, cached restore, modes, ROI/NoData pixels, 336px panel, Camera, split/flicker, candidate location, 1366/1920 screenshots');
  } finally { if(browser) await browser.close(); await new Promise(r=>server.close(r)); }
})().catch(e => { console.error(e); process.exitCode=1; });
