// Reuses the production renderer with deterministic, already-decoded PNG fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { buildSync } = require('../frontend/node_modules/esbuild');
const { launchPage } = require('./launchBrowser');

async function main() {
  const out = path.resolve(process.argv[2] || 'docs/status/resampling-quality-audit');
  const bundle = buildSync({
    entryPoints: [path.join(__dirname, '../frontend/src/lib/tileRenderer.ts')],
    bundle: true, format: 'esm', write: false, define: { 'import.meta.env.DEV': 'false' },
  }).outputFiles[0].text;
  const files = new Set(['C-P1-L0-api-pixels.png', 'D-P1-L1-api-pixels.png', 'B-P0-Q85-decoded.png']);
  const server = http.createServer((req, res) => {
    const name = req.url.slice(1);
    if (name === 'renderer.js') res.writeHead(200, { 'Content-Type': 'text/javascript' }).end(bundle);
    else if (files.has(name)) {
      res.writeHead(200, { 'Content-Type': 'image/png' });
      fs.createReadStream(path.join(out, name)).pipe(res);
    } else res.writeHead(200, { 'Content-Type': 'text/html' }).end('<title>Resampling canvas audit</title>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const launched = await launchPage();
    browser = launched.browser;
    const { page } = launched;
    await page.goto('http://127.0.0.1:' + server.address().port);
    const result = await page.evaluate(async () => {
      const { TileRenderer } = await import('/renderer.js');
      const get = async name => createImageBitmap(await (await fetch(name)).blob());
      const [l0, l1, overview] = await Promise.all([
        get('C-P1-L0-api-pixels.png'), get('D-P1-L1-api-pixels.png'), get('B-P0-Q85-decoded.png'),
      ]);
      function canvas(w, h) {
        const cv = document.createElement('canvas');
        cv.width = w;
        cv.height = h;
        const ctx = cv.getContext('2d', { willReadFrequently: true });
        return { ctx, values: () => ctx.getImageData(0, 0, w, h).data };
      }
      function difference(expected, observed) {
        let different = 0, sum = 0, transparent = 0;
        for (let i = 0; i < observed.length; i += 4) {
          const delta = Math.abs(expected[i]-observed[i]);
          different += +(delta > 0);
          sum += delta;
          transparent += +(observed[i+3] !== 255);
        }
        return { compared: observed.length/4, different, mae: sum/(observed.length/4), transparent };
      }
      const ref = canvas(1024,1024);
      ref.ctx.drawImage(l0,0,0);
      const expected = ref.values();
      const results = {};
      for (const dpr of [1,2]) {
        const renderer = new TileRenderer(() => {});
        // The lab isolates image drawing and opacity. Scheduler requests are covered by its own E2E.
        renderer.scheduler.update = () => {};
        const source = { key: 'roi', info: { width:1024,height:1024,tileSize:512,maxLevel:2 } };
        const cache = (level, x, y, bitmap, ready, fade) => {
          const span = 512 * 2 ** level, key = `roi/${level}/${x}/${y}`;
          renderer.scheduler.cache.set(key, { key, sourceKey:source.key, ready, fade, bitmap,
            tile:{level,x:x*span,y:y*span,width:512,height:512,sourceWidth:span,sourceHeight:span} });
        };
        cache(1,0,0,await createImageBitmap(l1),0,0);
        const pane = { source, tw:overview.width, th:overview.height,
          rect:{x:0,y:0,w:1024/dpr,h:1024/dpr},
          view:{scale:Math.max(1024/overview.width,1024/overview.height)/dpr,ox:0,oy:0} };
        const { ctx, values } = canvas(1024,1024);
        const paint = now => {
          ctx.resetTransform();
          ctx.clearRect(0,0,1024,1024);
          ctx.setTransform(dpr,0,0,dpr,0,0);
          renderer.update(new Map([['A',pane]]),[source],now,dpr,false);
          if (!renderer.covers('A')) ctx.drawImage(overview,0,0,1024/dpr,1024/dpr);
          const active = renderer.draw(ctx,'A',now,dpr,false);
          return { active, covers:renderer.covers('A'), lod:renderer.presentation('A'),
            ...difference(expected,values()) };
        };
        // The real native transform preserves the source coordinate at the pane centre.
        // Use a square basis so this fixture aligns the ROI origin to the canvas origin.
        pane.tw = pane.th = Math.max(pane.tw,pane.th);
        pane.view.scale = 1024/pane.tw/dpr;
        const fallback = paint(100);
        for (let y=0;y<2;y++) for (let x=0;x<2;x++)
          cache(0,x,y,await createImageBitmap(l0,x*512,y*512,512,512),200,160);
        results[dpr] = { fallback, fading:paint(250), settled:paint(380) };
        renderer.scheduler.close();
      }
      const zoom = smooth => {
        const c = canvas(1024,1024);
        c.ctx.imageSmoothingEnabled = smooth;
        c.ctx.drawImage(overview,0,0,1024,1024);
        return c.values();
      };
      const smooth = difference(zoom(false),zoom(true));
      return { userAgent:navigator.userAgent, results, p0_smoothing_on_vs_off:smooth };
    });
    for (const r of Object.values(result.results)) {
      assert.equal(r.settled.different,0);
      assert.equal(r.settled.transparent,0);
      assert.equal(r.settled.covers,true);
      assert.equal(r.settled.lod.readyLOD,0);
      assert.equal(r.fading.active,true);
      assert.equal(r.fading.covers,false);
      assert.equal(r.fallback.lod.readyLOD,null);
    }
    result.browser = await browser.version();
    if (process.env.AUDIT_VIEWER_URL) {
      await page.goto(process.env.AUDIT_VIEWER_URL);
      await page.waitForFunction(() => !!window.imageOpenTest);
      await page.evaluate(async () => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 128;
        canvas.getContext('2d').fillRect(0, 0, 128, 128);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg'));
        const { store } = window.imageOpenTest;
        await store.openLocalImage(new File([blob], 'audit.jpg', { type: 'image/jpeg' }));
        const fetchOriginal = window.fetch;
        window.fetch = (url, options) => String(url).includes('/assets/audit/')
          ? Promise.resolve(new Response('', { status: 404 })) : fetchOriginal(url, options);
        Object.assign(store.activeRec, {
          W: 1024, H: 1024, route: 'jpg', paintedMode: 'equal',
          tileSource: { asset: { id: 'audit' }, key: 'audit',
            info: { width: 1024, height: 1024, tileSize: 512, maxLevel: 10 } },
        });
        store.zoomNative();
      });
      await page.waitForFunction(() =>
        JSON.parse(document.querySelector('.view-canvas').dataset.tilePresentation)[0]?.targetLOD === 0);
      await page.evaluate(() => window.imageOpenTest.store.setStretchBounds(10, 200));
      await page.waitForFunction(() =>
        JSON.parse(document.querySelector('.view-canvas').dataset.tilePresentation)[0] === null);
      result.viewer_custom_stretch = await page.evaluate(() => ({
        tile_presentation: JSON.parse(document.querySelector('.view-canvas').dataset.tilePresentation)[0],
        custom: !!window.imageOpenTest.store.activeRec.displayStretch,
      }));
      assert.equal(result.viewer_custom_stretch.custom, true);
      assert.equal(result.viewer_custom_stretch.tile_presentation, null);
    }
    fs.writeFileSync(path.join(out,'canvas.json'),JSON.stringify(result,null,2)+'\n');
    console.log(JSON.stringify(result));
  } finally {
    if (browser) await browser.close();
    server.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
