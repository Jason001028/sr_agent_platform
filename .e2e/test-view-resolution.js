// Run node .e2e/serve-local-load.mjs, then optionally set TILE_ARTIFACTS and SR_E2E_BROWSER.
const { launchBrowser, findBrowser } = require('./launchBrowser');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

async function main() {
  const browser = await launchBrowser({ executablePath: findBrowser() });
  const out = path.resolve(process.env.TILE_ARTIFACTS || '.e2e/artifacts/view-resolution');
  const tag = process.env.TILE_BROWSER || 'browser';
  await fs.mkdir(out, { recursive: true });
  try {
    for (const dpr of [1, 2, 3]) {
      const page = await browser.newPage();
      page.on('pageerror', error => console.error(error));
      await page.setViewport({ width: 1440, height: 880, deviceScaleFactor: dpr });
      await page.goto('http://127.0.0.1:5179/tests/image-open.html?tileDebug=1');
      await page.waitForFunction(() => window.imageOpenTest);
      await page.evaluate(async () => {
        const { store } = window.imageOpenTest;
        const image = document.createElement('canvas');
        image.width = 1024;
        image.height = 1024;
        const ctx = image.getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 1024, 1024);
        gradient.addColorStop(0, '#102822');
        gradient.addColorStop(.6, '#bac2b4');
        gradient.addColorStop(1, '#243e32');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 1024, 1024);
        const jpeg = await new Promise(resolve => image.toBlob(resolve, 'image/jpeg'));
        await store.openLocalImage(new File([jpeg], 'P2.3 fixture.jpg', { type: 'image/jpeg' }));
        window.lodFixture = { pending: [], hold: true, fail: true, requested: [], jpeg };
        const original = window.fetch;
        window.fetch = async (url, options) => {
          const match = String(url).match(/\/assets\/p23\/tiles\/(\d+)\/(\d+)\/(\d+)/);
          if (!match) return original(url, options);
          const [level, tileX, tileY] = match.slice(1).map(Number);
          const fixture = window.lodFixture;
          fixture.requested.push(level);
          if (level === 0 && fixture.hold) await new Promise(resolve => fixture.pending.push(resolve));
          options.signal.throwIfAborted();
          if (level === 0 && fixture.fail) {
            fixture.fail = false;
            return new Response('{}', { status: 503 });
          }
          const step = 2 ** level, span = 512 * step;
          const x = tileX * span, y = tileY * span;
          const sourceWidth = Math.min(span, 49475 - x), sourceHeight = Math.min(span, 49454 - y);
          const width = Math.ceil(sourceWidth / step), height = Math.ceil(sourceHeight / step);
          const tile = document.createElement('canvas');
          tile.width = width;
          tile.height = height;
          const paint = tile.getContext('2d');
          paint.drawImage(image, x / 49475 * 1024, y / 49454 * 1024,
            sourceWidth / 49475 * 1024, sourceHeight / 49454 * 1024, 0, 0, width, height);
          const png = await new Promise(resolve => tile.toBlob(resolve));
          return new Response(png, { headers: { 'X-SR-Tile': JSON.stringify({
            x, y, width, height, sourceWidth, sourceHeight, level, tileX, tileY,
            band: 1, sourceVersion: 'fixture', rasterVersion: 'fixture', stretchVersion: 'fixture',
          }) } });
        };
      });
      const label = '[data-e2e="view-resolution"]';
      const waitLabel = text => page.waitForFunction((selector, value) =>
        document.querySelector(selector)?.textContent.trim() === value, {}, label, text);
      const shot = name => dpr === 1 && page.screenshot({ path: path.join(out, tag + '-' + name + '.png') });
      await waitLabel('整图预览 · 实际约 1/1');
      await shot('no-tile');
      await page.evaluate(() => {
        const { store } = window.imageOpenTest;
        const rec = store.activeRec;
        Object.assign(rec, { W: 49475, H: 49454, route: 'jpg', paintedMode: 'equal',
          tileSource: { asset: { id: 'p23' }, key: 'p23', info: {
            width: 49475, height: 49454, tileSize: 512, maxLevel: 16, band: 1,
            sourceVersion: 'fixture', rasterVersion: 'fixture', stretchVersion: 'fixture',
          } } });
        store.fit();
      });
      await waitLabel('全图概览 · 按需高清');
      await shot('overview');
      await page.click('[data-e2e="native-zoom"]');
      await waitLabel(dpr === 3 ? '视图 1.5:1 · L0 加载中' : '原生 1:1 · 高清加载中');
      await page.waitForFunction(() => window.lodFixture.requested.includes(0));
      await shot('native-loading');
      await page.evaluate(() => {
        const fixture = window.lodFixture;
        window.imageOpenTest.store.onPan(-1700, 1200);
        fixture.hold = false;
        fixture.pending.splice(0).forEach(release => release());
      });
      await page.waitForFunction(() => {
        const state = JSON.parse(document.querySelector('.view-canvas').dataset.tilePresentation)[0];
        return state.readyLOD === 0 && state.drawn === state.visible && state.visible > 0;
      });
      await waitLabel(dpr === 3 ? '视图 1.5:1 · L0' : '原生 1:1 · 已就绪');
      assert.equal(await page.evaluate(() => window.lodFixture.fail), false);
      await shot('native-ready');
      for (const level of [1, 2]) {
        await page.evaluate(level => {
          const { store } = window.imageOpenTest;
          const p = store.panes[0];
          const scale = p.rec.W / p.rec.thumb.width / Math.min(devicePixelRatio, 2) / 2 ** level;
          store.setPaneView(p.side, { scale, ox: (p.rect.w - p.rec.thumb.width * scale) / 2,
            oy: (p.rect.h - p.rec.thumb.height * scale) / 2 });
        }, level);
        const denominator = Number((2 ** level * Math.min(dpr, 2) / dpr).toPrecision(3));
        await waitLabel(`视图 1:${denominator} · L${level}`);
      }
      await page.evaluate(async () => {
        const { store } = window.imageOpenTest;
        const tiled = store.activeRec.id;
        await store.openLocalImage(new File([window.lodFixture.jpeg], 'Comparison.jpg', { type: 'image/jpeg' }));
        const jpeg = store.activeRec.id;
        store.setCompareMode('split');
        await store.activate(jpeg, 'B');
        store.fit();
        await store.activate(tiled, 'A');
        store.fit();
      });
      await page.waitForFunction(() => window.imageOpenTest.store.resolutionLabels.length === 2);
      const split = await page.evaluate(() => ({
        a: document.querySelector('[data-pane="A"].view-resolution')?.textContent,
        b: document.querySelector('[data-pane="B"].view-resolution')?.textContent,
        labels: window.imageOpenTest.store.resolutionLabels,
      }));
      assert.equal(split.a, '全图概览 · 按需高清', JSON.stringify(split));
      assert.match(split.b, /整图预览/);
      console.log(await browser.version(), 'DPR', dpr, 'passed');
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
