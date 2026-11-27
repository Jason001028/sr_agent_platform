// Launch the real API and the production-built image-open fixture first.
const puppeteer = require('puppeteer-core');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

async function main() {
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH, headless: true });
  const out = path.resolve(process.env.TILE_ARTIFACTS || '.e2e/fixtures/p2-evidence');
  const tag = process.env.TILE_BROWSER || 'chrome';
  await fs.mkdir(out, { recursive: true });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 880, deviceScaleFactor: Number(process.env.TILE_DPR || 1) });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto('http://127.0.0.1:5179/tests/image-open.html?tileDebug=1');
    await page.waitForFunction(() => window.imageOpenTest);
    await page.evaluate(async () => {
      const fixture = window.imageOpenTest;
      const rows = await (await fetch('http://127.0.0.1:8766/fixture/tile-scenes')).json();
      const bench = window.tileAcceptance = {
        rows, requests: [], blended: 0, closed: 0, slow: false, failOnce: true, times: {},
      };
      const originalFetch = window.fetch;
      window.fetch = async (url, options = {}) => {
        if (!/\/tiles\/\d+\/\d+\/\d+/.test(String(url))) return originalFetch(url, options);
        const l0 = String(url).includes('/tiles/0/');
        const request = { url: String(url), started: performance.now(), aborted: false };
        bench.requests.push(request);
        options.signal?.addEventListener('abort', () => { request.aborted = true; }, { once: true });
        const response = await originalFetch(url, options);
        request.network = performance.now() - request.started;
        if (!response.ok) return response;
        request.tile = JSON.parse(response.headers.get('X-SR-Tile'));
        if (bench.slow && l0) {
          await new Promise(resolve => setTimeout(resolve, 90 + ((request.tile.tileX + request.tile.tileY) % 4) * 40));
          if (options.signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
          if (bench.failOnce) {
            bench.failOnce = false;
            return new Response('corrupt PNG', { headers: response.headers });
          }
        }
        return response;
      };
      const draw = CanvasRenderingContext2D.prototype.drawImage;
      CanvasRenderingContext2D.prototype.drawImage = function (...args) {
        if (this.canvas.classList.contains('view-canvas') && args.length === 9 && this.globalAlpha > 0) {
          if (args[0] instanceof ImageBitmap) {
            bench.times.firstTile ??= performance.now();
            if (this.globalAlpha < 1) bench.blended++;
          } else {
            bench.times.overview ??= performance.now();
          }
        }
        return draw.apply(this, args);
      };
      const close = ImageBitmap.prototype.close;
      ImageBitmap.prototype.close = function () { bench.closed++; return close.call(this); };
      fixture.store.setPreviewDiv(2);
      bench.openStarted = performance.now();
      bench.open = fixture.openScene(rows[0]);
    });
    const canvas = '.view-canvas';
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const alpha = cv.getContext('2d').getImageData(cv.width / 2, cv.height / 2, 1, 1).data[3];
      if (alpha === 0) return false;
      return true;
    }, {}, canvas);
    const initial = await page.evaluate(async () => {
      const bench = window.tileAcceptance;
      const store = window.imageOpenTest.store;
      return { error: await bench.open, w: store.activeRec.W, h: store.activeRec.H, div: store.activeRec.detailDiv,
        tw: store.activeRec.thumb.width, th: store.activeRec.thumb.height };
    });
    assert.deepEqual(initial, { error: '', w: 49475, h: 49454, div: 16, tw: 3092, th: 3091 });
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const state = JSON.parse(cv.dataset.tiles)[0];
      return state?.total > 0 && state.ready === state.total && cv.dataset.tileAnimating === 'false';
    }, {}, canvas);
    await page.screenshot({ path: path.join(out, tag + '-overview.png') });
    await page.evaluate(() => {
      const bench = window.tileAcceptance;
      bench.times.zoom = performance.now();
    });
    await page.click('[data-e2e="native-zoom"]');
    // While delayed native tiles are arriving, the old geographic content is still drawable.
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const state = JSON.parse(cv.dataset.tiles)[0];
      return state?.level === 0 && state.ready < state.total
        && cv.getContext('2d').getImageData(cv.width / 2, cv.height / 2, 1, 1).data[3] === 255;
    }, {}, canvas);
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const state = JSON.parse(cv.dataset.tiles)[0];
      if (state?.level === 0 && state.ready > 0) window.tileAcceptance.times.firstNative ??= performance.now();
      if (state?.level !== 0 || state.total === 0 || state.ready !== state.total || cv.dataset.tileAnimating !== 'false') return false;
      window.tileAcceptance.times.nativeComplete = performance.now();
      return true;
    }, {}, canvas);
    await page.screenshot({ path: path.join(out, tag + '-native.png') });
    const seam = await page.evaluate(async selector => {
      const { store } = window.imageOpenTest;
      const cv = document.querySelector(selector);
      const dpr = Math.min(devicePixelRatio, 2);
      const rec = store.activeRec, view = store.view, { w, h } = store.panes[0].rect;
      const sx = view.scale * rec.thumb.width / rec.W, sy = view.scale * rec.thumb.height / rec.H;
      const line = Math.round((w / 2 - view.ox) / (512 * sx));
      const row = Math.floor((h / 2 - view.oy) / (512 * sy));
      const seamX = Math.round((view.ox + line * 512 * sx) * dpr);
      const y = Math.floor(h * dpr / 2);
      const top = Math.round((view.oy + row * 512 * sy) * dpr);
      const bottom = Math.round((view.oy + (row + 1) * 512 * sy) * dpr);
      const sampleY = Math.floor((y - top + .5) * 512 / (bottom - top));
      const actual = cv.getContext('2d').getImageData(seamX - 1, y, 2, 1).data;
      const pixel = document.createElement('canvas');
      pixel.width = pixel.height = 512;
      const ctx = pixel.getContext('2d');
      const expected = [];
      for (const [col, x] of [[line - 1, 511], [line, 0]]) {
        const tile = await fetch(`http://127.0.0.1:8766/api/image-open/assets/${rec.previewAsset.id}/tiles/0/${col}/${row}`);
        const image = await createImageBitmap(await tile.blob());
        ctx.drawImage(image, 0, 0);
        image.close();
        expected.push(ctx.getImageData(x, sampleY, 1, 1).data[0]);
      }
      return { actual: [actual[0], actual[4]], expected, alpha: [actual[3], actual[7]] };
    }, canvas);
    assert.deepEqual(seam.alpha, [255, 255]);
    assert.deepEqual(seam.actual, seam.expected);
    await page.evaluate(() => {
      const { store } = window.imageOpenTest;
      const thumb = store.activeRec.thumb;
      store.pendingRect = { x0: thumb.width / 2 - 10, y0: thumb.height / 2 - 10,
        x1: thumb.width / 2 + 10, y1: thumb.height / 2 + 10 };
      store.commitRect();
      window.tileAcceptance.roi = JSON.stringify(store.getRois());
      if (store.getRois().length !== 1) throw Error('ROI did not commit');
    });
    await page.evaluate(() => {
      const bench = window.tileAcceptance;
      bench.slow = true;
      bench.times.adversarial = performance.now();
      window.imageOpenTest.store.onPan(-3072, 2048);
    });
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const state = JSON.parse(cv.dataset.tiles)[0];
      return state?.level === 0 && state.ready < state.total
        && cv.getContext('2d').getImageData(cv.width / 2, cv.height / 2, 1, 1).data[3] === 255;
    }, {}, canvas);
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const state = JSON.parse(cv.dataset.tiles)[0];
      if (state?.level !== 0 || state.ready !== state.total || cv.dataset.tileAnimating === 'true') return false;
      window.tileAcceptance.times.adversarialComplete = performance.now();
      return true;
    }, {}, canvas);
    const interactions = await page.evaluate(async selector => {
      const store = window.imageOpenTest.store;
      const cv = document.querySelector(selector);
      const ctx = cv.getContext('2d');
      const bench = window.tileAcceptance;
      const rec = store.activeRec;
      const { w, h } = store.panes[0].rect;
      const old = store.view;
      const sourceX = v => (w / 2 - v.ox) / v.scale * rec.W / rec.thumb.width;
      const anchor = sourceX(old);
      const times = [];
      for (let i = 0; i < 36; i++) {
        await new Promise(resolve => requestAnimationFrame(now => { times.push(now); resolve(); }));
        store.onWheel(w / 2, h / 2, i < 18 ? .78 : 1 / .78);
      }
      const drift = Math.abs(anchor - sourceX(store.view));
      store.zoomNative();
      const scale = store.view.scale;
      let blank = 0;
      for (let i = 0; i < 16; i++) {
        store.onPan(i % 2 ? 160 : -170, i % 2 ? -80 : 85);
        const now = await new Promise(resolve => requestAnimationFrame(resolve));
        times.push(now);
        if (ctx.getImageData(cv.width / 2, cv.height / 2, 1, 1).data[3] !== 255) blank++;
      }
      bench.slow = false;
      if (JSON.stringify(store.getRois()) !== bench.roi) throw Error('ROI moved during zoom or pan');
      store.undoRoi();
      if (store.getRois().length !== 0) throw Error('ROI undo failed after Tile transitions');
      store.redoRoi();
      if (JSON.stringify(store.getRois()) !== bench.roi) throw Error('ROI redo moved the polygon');
      store.setPaneView('A', { scale, ox: w * .7 - (rec.thumb.width - 1) * scale,
        oy: h * .7 - (rec.thumb.height - 1) * scale });
      return { drift, blank, frames: times.slice(1).map((t, i) => t - times[i]) };
    }, canvas);
    assert.ok(interactions.drift < 1e-6, JSON.stringify(interactions));
    assert.equal(interactions.blank, 0);
    await page.waitForFunction(() => window.tileAcceptance.requests.some(r => r.tile?.level === 0
      && r.tile.tileX === 96 && r.tile.tileY === 96 && r.tile.width === 323 && r.tile.height === 302));
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const state = JSON.parse(cv.dataset.tiles)[0];
      return state?.total === state?.ready && cv.dataset.tileAnimating === 'false';
    }, {}, canvas);
    await page.screenshot({ path: path.join(out, tag + '-edge.png') });
    await page.evaluate(async () => {
      const fixture = window.imageOpenTest;
      const large = fixture.store.activeRec.id;
      await fixture.openAsset(window.tileAcceptance.rows[1].id);
      const small = fixture.store.activeRec.id;
      fixture.store.setCompareMode('split');
      await fixture.store.activate(large, 'A');
      await fixture.store.activate(small, 'B');
      fixture.store.fit();
    });
    await page.waitForFunction(selector => {
      const cv = document.querySelector(selector);
      const states = JSON.parse(cv.dataset.tiles);
      return states.length === 2 && states.every(s => s?.total > 0 && s.total === s.ready)
        && cv.dataset.tileAnimating === 'false';
    }, {}, canvas);
    await page.screenshot({ path: path.join(out, tag + '-dual.png') });
    const pair = await page.evaluate(async () => {
      const { store, tour } = window.imageOpenTest;
      window.tileAcceptance.dual = {
        bytes: Number(document.querySelector('.view-canvas').dataset.tileBytes),
        overview: store.recs.reduce((sum, rec) => sum + (rec.thumb ? rec.thumb.width * rec.thumb.height * 4 : 0), 0),
      };
      const [pan, nosr] = store.recs;
      await store.activate(pan.id, 'A');
      for (const [rec, role] of [[pan, 'pan'], [nosr, 'nosr']]) {
        rec.sceneDir = '/synthetic/p2';
        rec.stageKind = role === 'pan' ? 'input' : 'nosr';
        rec.tourIdentity = { id: rec.id, role, sequence: 'synthetic-p2', width: rec.W, height: rec.H, sourceRevision: rec.srcRev };
      }
      if (!store.selectTourPair(pan.id, nosr.id)) throw Error(store.flickerReason || 'Unable to pair synthetic PAN/NOSR');
      store.fit();
      const centre = () => {
        const p = store.panes[0];
        return [(p.rect.w / 2 - p.view.ox) / p.view.scale / p.rec.thumb.width,
          (p.rect.h / 2 - p.view.oy) / p.view.scale / p.rec.thumb.height];
      };
      const p = centre();
      if (!store.setTourLayer(true)) throw Error(store.flickerReason);
      const n = centre();
      store.setTourLayer(false);
      tour.compare = true;
      tour.mode = 'move';
      tour.interval = 600;
      if (!tour.start()) throw Error(tour.reason);
      window.tileAcceptance.tourCamera = { ...store.flickerView };
      return Math.max(Math.abs(p[0] - n[0]), Math.abs(p[1] - n[1]));
    });
    assert.ok(pair < .001);
    await page.waitForFunction(() => {
      const { store, tour } = window.imageOpenTest;
      const from = window.tileAcceptance.tourCamera, now = store.flickerView;
      return tour.status === 'playing'
        && (Math.hypot(now.ox - from.ox, now.oy - from.oy) > 3 || Math.abs(now.scale - from.scale) > .02);
    }, { timeout: 10000 });
    await page.evaluate(() => {
      const { store, tour } = window.imageOpenTest;
      tour.stop();
      store.setCompareMode('off');
    });
    const evidence = await page.evaluate(async selector => {
      const store = window.imageOpenTest.store;
      const cv = document.querySelector(selector);
      const bench = window.tileAcceptance;
      const closedBefore = bench.closed;
      const removed = store.recs.find(rec => rec.W === 2300);
      const version = removed.tileSource.info.rasterVersion;
      store.removeRec(removed.id);
      for (let i = 0; i < 12 && JSON.parse(cv.dataset.tileVersions).includes(version); i++) {
        await new Promise(resolve => requestAnimationFrame(resolve));
      }
      const stats = (data, percentile) => {
        const sorted = [...data].sort((a, b) => a - b);
        return Number((sorted[Math.floor((sorted.length - 1) * percentile)] ?? 0).toFixed(2));
      };
      const relative = (value, start) => Number((value - start).toFixed(2));
      return {
        dpr: devicePixelRatio, overviewMs: relative(bench.times.overview, bench.openStarted),
        firstTileMs: relative(bench.times.firstTile, bench.openStarted),
        firstNativeMs: relative(bench.times.firstNative, bench.times.zoom),
        nativeReadyMs: relative(bench.times.nativeComplete, bench.times.zoom),
        adversarialReadyMs: relative(bench.times.adversarialComplete, bench.times.adversarial),
        tileRequestP95Ms: stats(bench.requests.flatMap(r => r.network === undefined ? [] : [r.network]), .95),
        peakTileBytes: Number(cv.dataset.tilePeakBytes), bytesDual: bench.dual.bytes, overviewDual: bench.dual.overview,
        bytesAfterRemoval: Number(cv.dataset.tileBytes), releasedOnRemoval: bench.closed - closedBefore,
        removedVersionReleased: !JSON.parse(cv.dataset.tileVersions).includes(version),
        aborted: bench.requests.filter(r => r.aborted).length, blendedDraws: bench.blended,
        failedDecodeInjected: !bench.failOnce,
        rasterVersions: new Set(bench.requests.flatMap(r => r.tile?.rasterVersion ?? [])).size,
      };
    }, canvas);
    assert.equal(evidence.rasterVersions, 2);
    assert.ok(evidence.aborted > 0 && evidence.blendedDraws > 0);
    assert.ok(evidence.removedVersionReleased && evidence.releasedOnRemoval > 0, JSON.stringify(evidence));
    assert.ok(evidence.peakTileBytes <= 128 * 1024 * 1024);
    assert.equal(evidence.failedDecodeInjected, true);
    assert.deepEqual(errors, []);
    const intervals = interactions.frames.filter(t => t > 0).sort((a, b) => a - b);
    const report = { browser: await browser.version(), ...evidence,
      anchorDriftSourcePixels: interactions.drift, blankCenterFrames: interactions.blank,
      seam: { rendered: seam.actual, directTiles: seam.expected },
      alignedFlickerDrift: pair, comparisonTourMoved: true, roiUndoRedo: true,
      frameP95Ms: intervals[Math.floor((intervals.length - 1) * .95)], slowFramesOver25ms: intervals.filter(t => t > 25).length,
      measuredFrames: intervals.length };
    await fs.writeFile(path.join(out, tag + '.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(report);
  } catch (error) {
    console.error(await page.evaluate(() => {
      const store = window.imageOpenTest?.store;
      const tour = window.imageOpenTest?.tour;
      const cv = document.querySelector('.view-canvas');
      return { page: location.href, error: store?.error, records: store?.recs.map(r => [r.name,r.status,r.thumb?.width]),
        tiles: cv?.dataset.tiles, panes: store?.panes.map(p => [p.side,p.rec?.name]), dimensions: [cv?.width,cv?.height],
        requests: window.imageOpenTest?.requests.slice(-10), bench: window.tileAcceptance?.times,
        tour: [tour?.status,tour?.reason,tour?.current,store?.flickerView,window.tileAcceptance?.tourCamera] };
    }).catch(() => null), errors);
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
