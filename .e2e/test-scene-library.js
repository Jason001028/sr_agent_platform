// Mock HTTP smoke test against Vite preview (production build); no production data writes.
const assert = require('node:assert/strict');
const { launchBrowser } = require('./launchBrowser');

(async () => {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 1000 });
    page.on("pageerror", e => console.error("PAGE", e.message));
    let submitted;
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (request.url().includes('/api/')) {
        if (request.url().endsWith('/api/scene-library') && request.method() === 'POST') {
          submitted = JSON.parse(request.postData());
          return request.respond({ status: 200, contentType: 'application/json', body: '{"ok":true,"count":1}' });
        }
        return request.respond({ status: 200, contentType: 'application/json', body: '{"results":[],"count":0,"scanned":0,"source":"disk"}' });
      }
      request.continue();
    });
    await page.goto(process.env.SR_LIBRARY_UI_URL || 'http://127.0.0.1:5176/viewer');
    await page.waitForSelector('[data-e2e="ft-add-library"]');
    await page.evaluate(async () => {
      const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
      const store = pinia._s.get('viewer');
      store.recs.push({ id: 901, name: 'GF07_PAN.tif', W: 4096, H: 4096, size: 100,
        sceneId: 'mock-scene', status: 'ready', statusCls: 'ok', route: 'jpg',
        thumb: null, src: null, file: new File([], 'GF07_PAN.tif'), stageLabel: 'PAN' });
      store.selectedIds = [901]; store.selectMode = true;
    });
    await page.click('[data-e2e="ft-add-library"]');
    await page.waitForSelector('dialog[open]');
    assert.equal(await page.$eval('[data-e2e="ft-add-library"] img', e => e.width), 16);
    await page.evaluate(() => [...document.querySelectorAll('dialog button')].find(e => e.textContent === '建筑').click());
    await page.type('dialog input', 'batch-01');
    await page.screenshot({ path: '.e2e/scene-library-sandbox.png' });
    await page.click('dialog .primary');
    await page.waitForSelector('.success');
    assert.equal(submitted.entries[0].scene_id, 'mock-scene');
    assert.deepEqual(submitted.entries[0].tags.content, ['建筑']);
    assert.deepEqual(submitted.entries[0].tags.custom, ['batch-01']);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('dialog'));
    console.log('PASS: icon, modal, batch tags, custom tag, submit, Escape');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

