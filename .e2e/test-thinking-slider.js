// Self-contained UI regression against the production build; no backend required.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { launchPage } = require('./launchBrowser');
const { startStaticServer, sleep } = require('./vue-lib');

(async () => {
  const server = await startStaticServer({ dist: path.join(__dirname, '../frontend/dist') });
  const { browser, page, errors } = await launchPage();
  const shots = path.join(__dirname, 'shots');
  fs.mkdirSync(shots, { recursive: true });
  try {
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (!url.pathname.startsWith('/api/')) return request.continue();
      if (url.pathname.endsWith('/queue/events')) return; // Hold the mocked SSE connection open.
      let body = {};
      if (url.pathname.endsWith('/sessions')) body = request.method() === 'POST' ? { session_id: 'thinking-ui' } : { sessions: [] };
      else if (url.pathname.endsWith('/messages')) body = { messages: [] };
      else if (url.pathname.endsWith('/plugins')) body = { plugins: [] };
      else if (url.pathname.endsWith('/goals')) body = [];
      else if (url.pathname.endsWith('/queue')) body = { tasks: [] };
      else if (url.pathname.includes('clarification')) body = { clarifications: [] };
      request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.setViewport({ width: 1100, height: 780 });
    await page.goto(server.url + '/chat');
    await page.waitForSelector('.thinking-trigger');
    await page.waitForFunction(() => !document.querySelector('.cp-input').disabled);
    const store = fn => page.evaluate(fn);
    const open = async () => {
      await page.click('.thinking-trigger');
      await page.waitForSelector('.thinking-slider');
      await sleep(240);
    };
    const value = () => page.$eval('.thinking-slider', el => Number(el.getAttribute('aria-valuenow')));
    const position = () => page.$eval('.thinking-slider', el => parseFloat(getComputedStyle(el).getPropertyValue('--thinking-position')));
    const layout = () => page.evaluate(() => {
      const rect = selector => { const b = document.querySelector(selector).getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
      return { foot: rect('.cp-foot'), input: rect('.cp-input'), trigger: rect('.thinking-trigger') };
    });
    assert.equal(await page.$('.thinking-drawer'), null, 'drawer starts closed');
    assert.equal(await page.$('.thinking-tools'), null, 'no dedicated white row');
    const before = await layout();
    assert(Math.abs(before.trigger.y + before.trigger.h / 2 - before.input.y - before.input.h / 2) < 1, 'trigger is in the input row');
    await open();
    assert.deepEqual(await layout(), before, 'opening must not move the composer or messages');
    assert.equal(await value(), 2);
    assert.equal(await page.$eval('.thinking-slider', el => el === document.activeElement), true);
    const drawer = await page.$('.thinking-drawer');
    const drawerBox = await drawer.boundingBox();
    assert(drawerBox.y + drawerBox.height < before.trigger.y, 'drawer floats above the trigger');
    await drawer.screenshot({ path: path.join(shots, 'thinking-drawer-detail.png') });
    await page.screenshot({ path: path.join(shots, 'thinking-drawer-chat.png') });

    await page.keyboard.press('ArrowLeft'); assert.equal(await value(), 1);
    await page.keyboard.press('ArrowRight'); assert.equal(await value(), 2);
    await page.keyboard.press('End'); assert.equal(await value(), 3);
    await page.keyboard.press('ArrowRight'); assert.equal(await value(), 3);
    await page.keyboard.press('Home'); assert.equal(await value(), 0);
    await page.keyboard.press('ArrowDown'); assert.equal(await value(), 0);
    await sleep(360);
    const box = await (await page.$('.thinking-slider')).boundingBox();
    const y = box.y + 30;
    for (let i = 0; i < 4; i++) {
      await page.mouse.click(box.x + box.width * (i + .5) / 4, y);
      assert.equal(await value(), i, 'click selects the visible gear');
      assert.equal(await page.$eval('.thinking-trigger', el => el.getAttribute('aria-label')), `思考强度：${['极速', '快速', '均衡', '深度'][i]}`);
    }
    await page.keyboard.press('Home');
    await sleep(360);
    const x = box.x + box.width / 8;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + box.width * .64, y, { steps: 10 });
    assert.equal(await value(), 0, 'drag preview does not change the request selection');
    assert(await position() > 2.4, 'thumb follows the pointer');
    await page.mouse.up(); assert.equal(await value(), 3);
    await sleep(360);
    await page.keyboard.press('Home');
    await sleep(65);
    const mid = await position();
    assert(mid > 0 && mid < 3, 'selection animates between gears');
    await page.mouse.move(x, y); await page.mouse.down();
    assert(Math.abs(await position() - mid) < .55, 'interrupted snap continues from visible position');
    await page.mouse.move(x + 20, y, { steps: 3 });
    await page.$eval('.thinking-slider', el => el.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true })));
    await page.mouse.up();
    assert.equal(await value(), 0, 'cancel restores the committed profile');
    await sleep(360);
    assert(Math.abs(await position()) < .01);

    await page.keyboard.press('Escape');
    assert.equal(await page.$eval('.thinking-trigger', el => el.getAttribute('aria-expanded')), 'false');
    assert.equal(await page.$eval('.thinking-trigger', el => el === document.activeElement), true);
    assert.equal(await page.$eval('.thinking-drawer', el => el.inert && getComputedStyle(el).pointerEvents === 'none'), true, 'fade-out stops intercepting input immediately');
    await page.waitForSelector('.thinking-drawer', { hidden: true });
    await open();
    await page.click('.cp-input');
    await page.waitForSelector('.thinking-drawer', { hidden: true });
    assert.equal(await page.$eval('.cp-input', el => el === document.activeElement), true, 'outside click reaches the input');
    await open();
    await page.keyboard.press('Tab');
    await page.waitForSelector('.thinking-drawer', { hidden: true });
    await open();
    await page.click('.thinking-close');
    await page.waitForSelector('.thinking-drawer', { hidden: true });
    assert.equal(await page.$eval('.thinking-trigger', el => el === document.activeElement), true);

    // Reopening during the leave animation must not retain inert/hidden state.
    await open();
    await page.click('.thinking-trigger');
    await page.click('.thinking-trigger');
    await sleep(240);
    assert.equal(await page.$eval('.thinking-drawer', el => el.inert || el.getAttribute('aria-hidden') === 'true'), false);
    await page.click('.thinking-description');
    assert.equal(await page.$eval('.thinking-trigger', el => el.getAttribute('aria-expanded')), 'true', 'clicking drawer text keeps it open');
    await page.focus('.thinking-slider');
    await page.keyboard.press('Home'); await sleep(360);
    const touch = await page.createCDPSession();
    await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true });
    await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    for (let step = 1; step <= 6; step++) {
      await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + box.width * .5 * step / 6, y }] });
    }
    assert.equal(await value(), 0, 'touch drag previews without committing');
    await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    assert.equal(await value(), 2, 'touch release commits the selected gear');
    await touch.send('Emulation.setTouchEmulationEnabled', { enabled: false });
    await touch.detach();
    await page.focus('.thinking-slider');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.thinking-drawer', { hidden: true });

    // Changing the UI while a request is active only selects the next request.
    await store(() => {
      const chat = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat');
      chat.sending = true;
      chat.thinkingStatus = '推理控制不可用（未启用或已降级）';
    });
    await open();
    await page.keyboard.press('End'); assert.equal(await value(), 3);
    assert.match(await page.$eval('.thinking-status', el => el.textContent), /不可用/);
    await page.keyboard.press('Escape');
    await store(() => { document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat').sending = false; });
    await page.waitForSelector('.thinking-drawer', { hidden: true });

    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await open();
    await page.keyboard.press('Home'); await sleep(30);
    assert(Math.abs(await position()) < .01, 'reduced motion updates immediately');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.thinking-drawer', { hidden: true });

    // The same component must fit the narrow viewer sidebar, including lock transitions.
    await page.setViewport({ width: 1366, height: 900 });
    await page.goto(server.url + '/viewer');
    await page.waitForSelector('.at-input');
    assert.equal(await page.$eval('.thinking-trigger', el => el.disabled), true);
    await page.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 8;
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg'));
      const viewer = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      await viewer.openSceneJpg({ name: '思考强度 UI 验证图', W: 8, H: 8, sceneId: 'thinking-fixture', lqPath: null }, blob);
    });
    await page.waitForFunction(() => !document.querySelector('.thinking-trigger').disabled);
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'no-preference' }]);
    const footerHeight = await page.$eval('.at-foot', el => el.getBoundingClientRect().height);
    await open();
    assert.equal(await page.$eval('.at-foot', el => el.getBoundingClientRect().height), footerHeight);
    const bounds = await page.evaluate(() => {
      const a = document.querySelector('.thinking-drawer').getBoundingClientRect(), b = document.querySelector('.ctx-rail').getBoundingClientRect();
      return { left: a.left, right: a.right, parentLeft: b.left, parentRight: b.right, inputWidth: document.querySelector('.at-input').getBoundingClientRect().width, scrollHeight: document.querySelector('.at-scroll').clientHeight };
    });
    assert(bounds.left >= bounds.parentLeft && bounds.right <= bounds.parentRight, 'drawer fits sidebar');
    assert(bounds.inputWidth >= 90, 'composer keeps useful text width');
    await page.keyboard.press('ArrowLeft'); assert.equal(await value(), 1);
    await sleep(360);
    await page.screenshot({ path: path.join(shots, 'thinking-drawer-viewer.png') });
    await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').clearRecs());
    await page.waitForSelector('.thinking-drawer', { hidden: true });
    assert.equal(await page.$eval('.thinking-trigger', el => el.disabled), true, 'locking closes the drawer');
    assert.deepEqual(errors, []);
    console.log('PASS: inline layout, floating drawer, focus, four-gear selection, keys/bounds, pointer/touch preview and commit, cancel, interrupted animation, rapid reopen, non-blocking close, outside click, Tab/Esc, request status, reduced motion, sidebar fit and lock cleanup.');
  } finally {
    await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
