// 场景聚焦遮罩（Spotlight Focus）真实 Chromium 回归：
// 真滚动容器 + 真 Vue 组件 + 真 store；卡片用等高色块代替 FileListCard。
// 验坐标（跟随卡片/裁剪/分块完备）、层级（工具条在上、点击穿透）、三档模式、
// 折叠收场、减少动态、持久化，以及多景时遮罩节点数不随列表增长。
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { pathToFileURL } = require('url');
const assert = require('assert/strict');
const { launchPage } = require('./launchBrowser');
const root = path.resolve(__dirname, '..');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/** 等两帧：一次给 rAF 合帧，一次给 Vue 的补丁落地。 */
const frames = (page) => page.evaluate(() => new Promise((resolve) => {
  requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
}));

(async () => {
  const { build } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/vite/dist/node/index.js')).href);
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sr-spotlight-e2e-'));
  await build({
    root: path.join(root, 'frontend'),
    configFile: path.join(root, 'frontend/vite.config.ts'),
    base: '/',
    build: { outDir, rollupOptions: { input: path.join(root, 'frontend/tests/spotlight.html') } },
  });
  const server = http.createServer((req, res) => {
    const file = path.resolve(outDir, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(outDir + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return; }
    const type = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    const session = await launchPage();
    browser = session.browser;
    const page = session.page;
    page.on('pageerror', (error) => console.error('browser error:', error.message));
    page.on('console', (message) => { if (message.type() === 'error') console.error(message.text()); });
    await page.setViewport({ width: 1200, height: 900 });
    await page.goto(origin + '/tests/spotlight.html');
    await page.waitForFunction(() => !!window.spotlightTest);
    await page.evaluate(async () => {
      const { insert, nextTick } = window.spotlightTest;
      insert(12);
      await nextTick();
    });
    await frames(page);

    const initial = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(initial.layerPresent, false, '没有选中行时不该有遮罩');
    assert.equal(initial.anchorZ, '10', '遮罩锚点的 z-index 应为 10');
    assert.equal(Math.round(initial.anchorHeight), 0, '锚点不占列表布局高度');
    console.log('initial', JSON.stringify({ layerPresent: initial.layerPresent, anchorZ: initial.anchorZ }));

    // ---- 选中一景：窗口套住同景两张卡 ----
    await page.evaluate(async () => {
      const { select, nextTick } = window.spotlightTest;
      select(['S1']);
      await nextTick();
    });
    await delay(420);                    // 让 250ms 的窗口移动跑完
    const sel = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(sel.mode, 'frosted', '默认档应为柔焦毛玻璃');
    assert.equal(sel.layerPresent, true);
    assert.equal(sel.focusIds.length, 2, '一景两张卡都进聚焦窗口');
    assert.equal(sel.holeCount, 1, '相邻两张卡合成一个镂空窗口');
    assert.equal(sel.allFocusCovered, true, '镂空窗口应完整套住同景两张卡');
    assert.equal(sel.holeOverlapsOtherCard, false, '窗口不能盖到别景的卡');
    assert.equal(sel.blockOverlapsHole, false, '遮罩块不能压进镂空窗口');
    assert.equal(sel.blocksOutsidePort, 0, '遮罩块不能超出侧栏滚动视口');
    assert.equal(sel.partition.badHole, 0, '窗口内不该有遮罩块');
    assert.equal(sel.partition.badMask, 0, '窗口外的每一点都要被恰好一块盖住 ' + JSON.stringify(sel.partition));
    assert.equal(sel.layerPointerEvents, 'none', '遮罩层必须点击穿透');
    assert.equal(sel.maskedCardHit.hit, sel.maskedCardHit.expected, '被压暗的卡仍可被指针命中');
    assert.equal(sel.maskedCardHit.hitIsLayer, false);
    assert.equal(sel.toolHit, 'tools', '工具条必须在遮罩之上');
    assert.match(sel.backdropFilter, /blur\(3px\)/, '毛玻璃档要真的做背景模糊');
    assert.ok(sel.blockCount > 0 && sel.blockCount <= 8, '分块数量应保持在个位数');
    console.log('selected', JSON.stringify({
      mode: sel.mode, holeCount: sel.holeCount, blockCount: sel.blockCount,
      partition: sel.partition, backdrop: sel.backdropFilter,
    }));
    await page.screenshot({ path: path.join(__dirname, 'spotlight-focus.png') });

    // ---- 滚动：窗口同帧贴住卡片 ----
    await page.evaluate(() => window.spotlightTest.scrollBy(200));
    await frames(page);
    const scrolled = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(scrolled.scrollTop, 200);
    assert.equal(scrolled.holeCount, 1);
    assert.equal(scrolled.allFocusCovered, true, '滚动后窗口要贴住卡片');
    assert.equal(scrolled.partition.badMask, 0);
    assert.equal(scrolled.partition.badHole, 0);
    console.log('scrolled', JSON.stringify({ scrollTop: scrolled.scrollTop, covered: scrolled.allFocusCovered }));

    // ---- 卡片滚出视口：只留整块遮罩，不画越界窗口 ----
    await page.evaluate(() => window.spotlightTest.scrollTop(2000));
    await frames(page);
    const away = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(away.holeCount, 0, '图组完全在视口外时不该有镂空窗口');
    assert.ok(away.blockCount >= 1, '视口内应被遮罩完全盖住');
    assert.equal(away.partition.badMask, 0);
    console.log('offscreen', JSON.stringify({ holes: away.holeCount, blocks: away.blockCount }));

    // ---- 换景 + 滚它到眼前：窗口跟过去 ----
    await page.evaluate(async () => {
      const { select, scrollCardIntoView, nextTick } = window.spotlightTest;
      select(['S9']);
      await nextTick();
      scrollCardIntoView('S9_PAN.tif');
    });
    await frames(page);
    await delay(320);
    const followed = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(followed.holeCount, 1);
    assert.equal(followed.allFocusCovered, true, '滚到眼前后窗口应套住新的那一景');
    assert.equal(followed.partition.badMask, 0);
    console.log('followed', JSON.stringify({ scrollTop: Math.round(followed.scrollTop), covered: followed.allFocusCovered }));

    // ---- 三档模式 ----
    await page.evaluate(() => window.spotlightTest.store.setSpotlightMode('dim'));
    await frames(page);
    const dim = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(dim.mode, 'dim');
    assert.equal(dim.backdropFilter, 'none', '暗化档不做模糊');
    assert.equal(dim.blockBackground, 'rgba(17, 35, 32, 0.32)');
    assert.equal(await page.evaluate(() => localStorage.getItem('sr.viewer.spotlightMode')), 'dim', '档位要持久化');

    await page.evaluate(() => window.spotlightTest.store.setSpotlightMode('none'));
    await frames(page);
    const none = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(none.mode, 'none');
    assert.equal(none.blockCount, 0, '无遮罩档不铺任何遮罩块');
    assert.equal(none.holeCount, 1, '无遮罩档仍保留聚焦描边');
    console.log('modes', JSON.stringify({ dimBg: dim.blockBackground, noneBlocks: none.blockCount, noneRings: none.holeCount }));

    await page.evaluate(() => window.spotlightTest.store.setSpotlightMode('frosted'));
    await frames(page);

    // ---- 窗口缩放：几何重算，不越界 ----
    await page.setViewport({ width: 1000, height: 700 });
    await frames(page);
    await delay(80);
    const resized = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(resized.allFocusCovered, true, '窗口缩放后窗口仍要套住卡片');
    assert.equal(resized.blocksOutsidePort, 0);
    assert.equal(resized.partition.badMask, 0);
    assert.equal(resized.partition.badHole, 0);
    console.log('resized', JSON.stringify({ port: resized.port, covered: resized.allFocusCovered }));

    // ---- 收起侧栏：直接收场，展开后重新落位 ----
    await page.evaluate(() => { window.spotlightTest.store.sidebarCollapsed = true; });
    await frames(page);
    await delay(320);                    // 等 240ms 的淡出走完再断言
    assert.equal(await page.evaluate(() => !!document.querySelector('.spot-layer')), false, '侧栏收起时不该有遮罩');
    await page.evaluate(() => { window.spotlightTest.store.sidebarCollapsed = false; });
    await frames(page);
    await delay(60);
    const reopened = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(reopened.layerPresent, true);
    assert.equal(reopened.allFocusCovered, true);
    console.log('collapse', JSON.stringify({ hidden: true, restored: reopened.layerPresent }));

    // ---- 多景：遮罩节点数不随列表增长 ----
    await page.evaluate(async () => {
      const { insert, nextTick } = window.spotlightTest;
      insert(50);
      await nextTick();
    });
    await frames(page);
    let maxNodes = 0;
    for (let i = 0; i < 10; i++) {
      const n = await page.evaluate(async (s) => {
        const { select, nextTick } = window.spotlightTest;
        select(['S' + s]);
        await nextTick();
        return document.querySelectorAll('.spot-block, .spot-ring').length;
      }, (i * 7) % 50 + 1);
      maxNodes = Math.max(maxNodes, n);
    }
    assert.ok(maxNodes <= 8, `遮罩节点数应恒为个位数，实际 ${maxNodes}`);
    assert.equal(await page.evaluate(() => document.querySelectorAll('.spot-layer').length), 1);
    console.log('manyScenes', JSON.stringify({ cards: await page.evaluate(() => document.querySelectorAll('.card').length), maxNodes }));

    // ---- 取消选中：淡出并清场 ----
    await page.evaluate(() => window.spotlightTest.clear());
    await delay(400);
    assert.equal(await page.evaluate(() => !!document.querySelector('.spot-layer')), false, '取消选中后遮罩应移除');

    // ---- 减少动态：窗口直接到位 ----
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.setViewport({ width: 1200, height: 900 });
    await frames(page);
    await page.evaluate(async () => {
      const { select, scrollCardIntoView, nextTick } = window.spotlightTest;
      select(['S2']);
      await nextTick();
      scrollCardIntoView('S2_PAN.tif');
    });
    await frames(page);
    await delay(40);                     // 不做插值：两帧内就该到位
    const reduced = await page.evaluate(() => window.spotlightTest.inspect());
    assert.equal(reduced.holeCount, 1);
    assert.equal(reduced.allFocusCovered, true, '减少动态时窗口应立即到位');
    console.log('reducedMotion', JSON.stringify({ covered: reduced.allFocusCovered, holes: reduced.holeCount }));
    await page.emulateMediaFeatures([]);

    // ---- 持久化：刷新后读回同一档位 ----
    await page.evaluate(() => window.spotlightTest.store.setSpotlightMode('dim'));
    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => !!window.spotlightTest);
    assert.equal(await page.evaluate(() => window.spotlightTest.mode()), 'dim', '刷新后应读回持久化的档位');
    await page.evaluate(() => window.spotlightTest.store.setSpotlightMode('frosted'));
    console.log('persisted', JSON.stringify({ mode: 'dim' }));

    console.log('spotlight-focus e2e OK');
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
