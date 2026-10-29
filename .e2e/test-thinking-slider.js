const assert = require('node:assert/strict');
const { launchPage } = require('./launchBrowser');

(async () => {
  const { browser, page } = await launchPage();
  try {
    await page.setViewport({ width: 1100, height: 780 });
    await page.goto('http://127.0.0.1:5174/chat');
    await page.waitForSelector('.thinking-slider');
    const slider = await page.$('.thinking-slider');
    const value = () => slider.evaluate(el => Number(el.getAttribute('aria-valuenow')));
    const position = () => slider.evaluate(el => parseFloat(getComputedStyle(el).getPropertyValue('--thinking-position')));
    const box = await slider.boundingBox();
    assert.equal(box.width, 68); assert.equal(box.height, 30);
    assert.equal(await value(), 2);
    await slider.focus();
    await page.keyboard.press('ArrowLeft'); assert.equal(await value(), 1);
    await page.keyboard.press('ArrowRight'); assert.equal(await value(), 2);
    await page.keyboard.press('End'); assert.equal(await value(), 3);
    await page.keyboard.press('ArrowRight'); assert.equal(await value(), 3);
    await slider.click(); assert.equal(await value(), 0);
    assert.equal(await slider.evaluate(el => el.classList.contains('fade-only')), true);
    await page.keyboard.press('ArrowLeft'); assert.equal(await value(), 0);
    await new Promise(r => setTimeout(r, 360));
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + 110, y, { steps: 8 });
    assert.equal(await value(), 0, 'preview must not change the request selection');
    const preview = await position(); assert(preview > .9 && preview < 1.09);
    await page.mouse.up(); assert.equal(await value(), 1, 'long gesture changes only one gear');
    await new Promise(r => setTimeout(r, 360));
    assert(Math.abs(await position() - 1) < .01);
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + 18, y, { steps: 4 }); await page.mouse.up();
    assert.equal(await value(), 2);
    await new Promise(r => setTimeout(r, 60));
    const mid = await position(); assert(mid > 1 && mid < 2, 'snap must interpolate');
    await page.mouse.move(x, y); await page.mouse.down();
    assert(Math.abs(await position() - mid) < .25, 'interrupt must preserve the current animated position');
    await page.mouse.move(x - 25, y, { steps: 4 }); await page.mouse.up();
    assert.equal(await value(), 1);
    await new Promise(r => setTimeout(r, 360));
    const visible = await slider.evaluate(el => [...el.querySelectorAll('.gear-label')].filter(label => Number(getComputedStyle(label).opacity) > .99).map(label => label.textContent));
    assert.deepEqual(visible, ['快速']);
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.keyboard.press('End');
    await new Promise(r => setTimeout(r, 40));
    assert(Math.abs(await position() - 3) < .01);
    await page.screenshot({ path: '.e2e/shots/thinking-slider.png' });
    console.log('PASS: dimensions, keys, bounds, click wrap, drag preview, one-step limit, snap interpolation, interruption, single label, reduced motion');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

