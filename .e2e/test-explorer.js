// Bundled Vue + real card component; no protocol registration / native launch.
const path = require('path');
const fs = require('fs');
const http = require('http');
const { pathToFileURL } = require('url');
const assert = require('assert/strict');
const { launchPage } = require('./launchBrowser');
const root = path.resolve(__dirname, '..');
(async () => {
  const { build } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/vite/dist/node/index.js')).href);
  const outDir = path.join(root, '.e2e', 'fixtures', 'explorer-build');
  await build({ root: path.join(root, 'frontend'), configFile: path.join(root, 'frontend/vite.config.ts'),
    base: '/', build: { outDir, rollupOptions: { input: path.join(root, 'frontend/tests/explorer.html') } } });
  const server = http.createServer((req, res) => {
    const file = path.resolve(outDir, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(outDir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404).end(); return; }
    const type = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.webp': 'image/webp' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type }); fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const session = await launchPage(); browser = session.browser;
    const page = session.page;
    await page.setViewport({ width: 1000, height: 760 });
    await page.goto(`http://127.0.0.1:${server.address().port}/tests/explorer.html`);
    await page.waitForFunction(() => !!window.explorerTest);
    await page.waitForSelector('.file-item .explorer-open');
    assert.equal(await page.$$eval('.explorer-open', els => els.filter(e => !e.disabled).length), 2);
    assert.equal(await page.$$eval('.explorer-open:disabled', els => els.length), 1);
    const placement = await page.$eval('.file-item', card => {
      const button = card.querySelector('.explorer-open').getBoundingClientRect();
      const close = card.querySelector('.close').getBoundingClientRect();
      const name = card.querySelector('.name').getBoundingClientRect();
      return { compact: button.width <= 26 && button.height <= 24, beforeClose: button.right <= close.left,
        textClearance: name.right - parseFloat(getComputedStyle(card.querySelector('.name')).paddingRight) <= button.left };
    });
    assert.deepEqual(placement, { compact: true, beforeClose: true, textClearance: true });
    const before = await page.evaluate(() => ({ active: window.explorerTest.store.activeId, count: window.explorerTest.store.recs.length }));
    await page.click('.explorer-open');
    await page.waitForSelector('dialog[open]');
    assert.equal(await page.$eval('dialog[open] input', input => input.value), '/DiskArray/GSHC2IMPS/PRODUCT/测试场景');
    const protocol = await page.$eval('dialog[open] .explorer-actions a', a => a.href);
    assert.match(protocol, /^sr-explorer:\/\/open\/[A-Za-z0-9_-]+$/);
    const installer = await page.$eval('dialog[open] a[download]', a => a.href);
    assert.ok(installer.endsWith('/client-explorer/SrExplorer-Setup.zip'));
    const archive = Buffer.from(await (await fetch(installer)).arrayBuffer());
    assert.equal(archive.readUInt32LE(0), 0x04034b50);
    for (const name of ['Install-SrExplorer.cmd', 'Install-SrExplorer.ps1', 'Chrome-SrExplorerPolicy.ps1']) {
      const source = fs.readFileSync(path.join(root, 'frontend/public/client-explorer', name), 'utf8').replace(/\r?\n/g, '\r\n');
      assert.ok(archive.includes(Buffer.from(source)), `installer package must contain current ${name}`);
    }
    assert.equal(await page.$eval('dialog[open] details', el => el.open), false);
    assert.deepEqual(await page.evaluate(() => ({ active: window.explorerTest.store.activeId, count: window.explorerTest.store.recs.length })), before);
    await page.screenshot({path: path.join(root, '.e2e/fixtures/explorer-setup.png')});
    await page.keyboard.press('Escape');
    assert.equal(await page.$$eval('dialog[open]', els => els.length), 0);
    await page.screenshot({path: path.join(root, '.e2e/fixtures/explorer-cards.png')});
    // Right-click remains an escape hatch if a previously configured client stops working.
    await page.evaluate(() => localStorage.setItem('sr-explorer:configured:v1', '1'));
    await page.click('.explorer-open', {button: 'right'});
    await page.waitForSelector('dialog[open]');
    await page.keyboard.press('Escape');
    await page.evaluate(async () => { window.explorerTest.store.toggleSelectMode(); await window.explorerTest.nextTick(); });
    assert.equal(await page.$$eval('.explorer-open', els => els.length), 0);
    assert.deepEqual(session.errors, []);
    console.log('PASS: card placement, local/linked eligibility, setup, download, Unicode URI, click isolation, Escape, reconfiguration, multiselect.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
