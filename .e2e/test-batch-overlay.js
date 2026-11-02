// 批量检测叠加层浏览器回归（PR D / P1 2）
// ------------------------------------------------------------------
// 跑 frontend/dist + 静态服务，假造 `overlay.json` / `overlay.png`：
//   A. 身份匹配：来源 image_key 一致才画；本地未绑定影像绝不画
//   B. 坐标：叠加快照按 pane.rect 裁剪，影像矩形/候选点用格内坐标（只加一次偏移）
//   C. 缩放/平移：格子 view 变化后矩形与候选顶点跟随
//   D. 分屏：两格各用各的 view；右侧裁剪窗口 = 右侧 pane.rect，不漏进左格
//   E. 刷新：换了 sha 后按钮重新取文档，<img> 缓存键跟着变
//   F. 文档失败：只显示失败原因，不画任何候选；重试成功后恢复
//   G. PNG 失败：文档成功但图片 503，不得显示“真实检测”/候选，重试后恢复
//   H. 换图：身份变化后立即停止绘制
// 用法: node test-batch-overlay.js
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '../frontend/dist');
const IDENTITY = 'scene-v1:' + 'a'.repeat(64) + ':' + 'b'.repeat(64);
const OTHER_IDENTITY = 'scene-v1:' + 'a'.repeat(64) + ':' + 'c'.repeat(64);
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64');

const state = { mode: 'ok', pngMode: 'ok', sha: 's1', meta: 0, png: 0 };

function overlayDoc() {
  return {
    contract_version: 'overlay.v1',
    goal_id: 'goal-1',
    item_id: 'item-1',
    item_version: 3,
    item_status: 'MASK_READY',
    source: { image_key: IDENTITY, revision: 'rev-1', width: 800, height: 600 },
    provenance: 'real',
    detector_name: 'flatline-scan',
    detector_version: '1.0.0',
    candidates: [{
      candidate_id: 'flat_row-0001',
      kind: 'bbox',
      bbox: [0, 10, 799, 12],
      points: null,
      label: 'flat_row',
    }],
    overlay: {
      url: '/api/goals/goal-1/items/item-1/overlay.png',
      width: 80,
      height: 60,
      sha256: state.sha,
    },
  };
}

async function dropJpeg(page, name, color) {
  await page.evaluate(async ({ name, color }) => {
    const cv = document.createElement('canvas');
    cv.width = 800; cv.height = 600;
    const ctx = cv.getContext('2d');
    ctx.fillStyle = color; ctx.fillRect(0, 0, 800, 600);
    const blob = await new Promise((r) => cv.toBlob(r, 'image/jpeg'));
    const dt = new DataTransfer();
    dt.items.add(new File([blob], name, { type: 'image/jpeg' }));
    // 分屏时 dropSide 按 clientX/clientY 判格；给定坐标才落进画布（默认 0,0 会被拒）。
    const stage = document.querySelector('.stage').getBoundingClientRect();
    document.querySelector('.stage').dispatchEvent(new DragEvent('drop', {
      dataTransfer: dt, bubbles: true, cancelable: true,
      clientX: stage.left + 40, clientY: stage.top + 40,
    }));
  }, { name, color });
}

const PINIA = "document.querySelector('#app').__vue_app__.config.globalProperties.$pinia";
const VIEWER = PINIA + "._s.get('viewer')";

(async () => {
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://local').pathname;
    if (u === '/api/scenes/fixture/spatial-identity') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ image_key: IDENTITY }));
      return;
    }
    if (u === '/api/goals/goal-1/items/item-1/overlay') {
      state.meta += 1;
      if (state.mode === 'fail') {
        res.writeHead(503, { 'Content-Type': 'application/json' });
        res.end('{"detail":"fixture unavailable"}');
        return;
      }
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Cache-Control', 'no-store');
      res.end(JSON.stringify(overlayDoc()));
      return;
    }
    if (u === '/api/goals/goal-1/items/item-1/overlay.png') {
      state.png += 1;
      if (state.pngMode === 'fail') {
        res.writeHead(503, { 'Content-Type': 'application/json' });
        res.end('{"detail":"overlay png unavailable"}');
        return;
      }
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-store');
      res.end(PNG);
      return;
    }
    if (u.startsWith('/api/')) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end('{}');
      return;
    }
    let f = path.join(DIST, u === '/' ? 'index.html' : u);
    if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(DIST, 'index.html');
    const mime = { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' }[path.extname(f)];
    res.setHeader('Content-Type', mime || 'application/octet-stream');
    res.end(fs.readFileSync(f));
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  let browser;
  try {
    browser = await launchBrowser();
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.setViewport({ width: 1366, height: 900, deviceScaleFactor: 1 });
    await page.goto(`http://127.0.0.1:${server.address().port}/viewer?batch_goal=goal-1&batch_item=item-1`);
    await page.waitForSelector('.stage');
    await page.waitForSelector('[data-e2e=batch-overlay]');

    // A. 本地影像没有服务端身份：不画，但入口与加载状态存在。
    await dropJpeg(page, 'overlay_fixture_A.jpg', '#204a2e');
    await page.waitForFunction(`${VIEWER}?.activeRec?.thumb`);
    await page.evaluate(`${VIEWER}.hideModal()`);
    assert.equal(await page.$$('.batch-pane').then((a) => a.length), 0, '未绑定身份的本地影像不得绘制叠加层');
    assert.ok(await page.$('[data-e2e=batch-overlay]'));

    // 绑定到与服务端同一物理影像的身份。
    await page.evaluate(`${VIEWER}.activeRec.sceneId='fixture'`);
    await page.waitForFunction(`${VIEWER}.activeRec.spatialIdentity?.startsWith('scene-v1:')`);
    await page.waitForSelector('[data-e2e=batch-overlay-chip]');
    await page.waitForFunction("document.querySelectorAll('.batch-pane').length===1");
    assert.equal(await page.$eval('[data-e2e=batch-overlay-chip]', (e) => e.getAttribute('data-provenance')), 'real');
    assert.match(await page.$eval('[data-e2e=batch-overlay-chip]', (e) => e.textContent), /真实检测/);
    assert.equal(await page.$$('[data-e2e=batch-overlay-candidate]').then((a) => a.length), 1);
    assert.equal(state.meta, 1);
    assert.equal(state.png, 1);

    // B. 叠加层矩形 = 当前格子 view 下的整幅影像范围（格子内坐标 + 格子偏移）。
    const geom = async () => page.evaluate(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      const stage = document.querySelector('.stage').getBoundingClientRect();
      const panes = [...document.querySelectorAll('.batch-pane')].map((el) => {
        const clip = el.getBoundingClientRect();
        const box = el.querySelector('.batch-image');
        const image = box ? box.getBoundingClientRect() : null;
        const at = (r) => ({
          left: r.left - stage.left, top: r.top - stage.top,
          width: r.width, height: r.height,
        });
        return {
          side: el.getAttribute('data-pane'),
          clip: at(clip),
          image: image ? at(image) : null,
          overflow: getComputedStyle(el).overflow,
          polygons: [...el.querySelectorAll('polygon')].map((poly) => {
            const svg = poly.closest('svg').getBoundingClientRect();
            const points = poly.points;
            return Array.from({ length: points.numberOfItems }, (_, i) => {
              const point = points.getItem(i);
              return [svg.left - stage.left + point.x, svg.top - stage.top + point.y];
            });
          }),
        };
      });
      return {
        panes,
        views: v.panes.map((p) => ({
          side: p.side, view: { ...p.view }, rect: { ...p.rect },
          thumb: p.rec?.thumb ? { w: p.rec.thumb.width, h: p.rec.thumb.height } : null,
        })),
      };
    });
    // 裁剪窗口就是 pane.rect；影像盒与候选顶点是格内坐标（view 偏移已含，rect 不再加）。
    const expectClip = (rect) => ({
      left: rect.x, top: rect.y, width: rect.w, height: rect.h,
    });
    const expectImage = (view, rect, thumb) => ({
      left: rect.x + view.ox,
      top: rect.y + view.oy,
      width: thumb.w * view.scale,
      height: thumb.h * view.scale,
    });
    const SOURCE_W = 800, SOURCE_H = 600;   // overlayDoc().source
    const expectCandidate = (view, rect, thumb, sx, sy) => [
      rect.x + view.ox + (sx / SOURCE_W) * thumb.w * view.scale,
      rect.y + view.oy + (sy / SOURCE_H) * thumb.h * view.scale,
    ];
    const close = (a, b, label) => {
      for (const key of ['left', 'top', 'width', 'height']) {
        assert.ok(Math.abs(a[key] - b[key]) < 0.75,
          `${label} ${key}: got ${a[key]}, want ${b[key]}`);
      }
    };
    const closePoint = (a, b, label) => {
      assert.ok(Math.abs(a[0] - b[0]) < 0.75 && Math.abs(a[1] - b[1]) < 0.75,
        `${label}: got ${a}, want ${b}`);
    };
    // 候选 [0,10,799,12] 的顶点：bbox 顺序是 (x0,y0),(x1,y0),(x1,y1),(x0,y1)。
    const CANDIDATE_VERTICES = [[0, [0, 10]], [2, [799, 12]]];
    const checkPane = (pane, view, rect, thumb, label) => {
      close(pane.clip, expectClip(rect), `${label} clip`);
      assert.equal(pane.overflow, 'hidden', `${label} 必须裁剪到 pane.rect`);
      assert.ok(pane.image, `${label} 缺影像盒`);
      close(pane.image, expectImage(view, rect, thumb), `${label} image`);
      assert.equal(pane.polygons.length, 1, `${label} 候选轮廓数`);
      assert.equal(pane.polygons[0].length, 4, `${label} bbox 必须是四角`);
      for (const [index, [sx, sy]] of CANDIDATE_VERTICES) {
        closePoint(pane.polygons[0][index],
          expectCandidate(view, rect, thumb, sx, sy),
          `${label} vertex ${index}`);
      }
    };
    // Vue 提交 DOM 补丁在下一帧；快照前让浏览器落下两帧，避免读到上一帧的样式。
    const settle = () => page.evaluate(
      () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true)))));
    // 进分屏会收起右侧栏，舞台宽度带过渡；等画布尺寸与真实布局对齐再采样。
    const waitLayout = () => page.waitForFunction(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      const stage = document.querySelector('.stage');
      return v.canvasSize.w === stage.clientWidth
        && !document.getAnimations().some((a) => a.transitionProperty === 'width');
    }, { polling: 'raf' });

    let snap = await geom();
    assert.equal(snap.panes.length, 1);
    assert.equal(snap.panes[0].side, 'A');
    checkPane(snap.panes[0], snap.views[0].view, snap.views[0].rect, snap.views[0].thumb, 'initial');

    // C. 缩放/平移后按同一格自己的 view 跟随（候选顶点也必须跟随）。
    await page.evaluate(`${VIEWER}.panes[0].view.ox += 37; ${VIEWER}.panes[0].view.oy -= 21; ${VIEWER}.panes[0].view.scale *= 1.2;`);
    await page.waitForFunction(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      const p = v.panes[0], el = document.querySelector('.batch-image');
      if (!el || !p.rec?.thumb) return false;
      const r = el.getBoundingClientRect();
      return Math.abs(r.width - p.rec.thumb.width * p.view.scale) < 0.75;
    });
    await settle();
    snap = await geom();
    checkPane(snap.panes[0], snap.views[0].view, snap.views[0].rect, snap.views[0].thumb, 'after zoom/pan');

    // D. 分屏：每格先裁 pane.rect，再在格内用该格自己的 view 定位影像。
    await page.evaluate(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      v.setCompareMode('split'); v.paneB = v.paneA; v.resetSplit();
    });
    await page.evaluate(`${VIEWER}.hideModal()`);
    await page.waitForFunction("document.querySelectorAll('.batch-pane').length===2");
    await page.waitForFunction(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      return v.panes.length === 2 && v.panes.every((p) => p.rec?.thumb);
    });
    await waitLayout();
    await settle();
    snap = await geom();
    assert.deepEqual(snap.panes.map((p) => p.side), ['A', 'B']);
    for (let i = 0; i < 2; i += 1) {
      checkPane(snap.panes[i], snap.views[i].view, snap.views[i].rect, snap.views[i].thumb, `split pane ${snap.views[i].side}`);
    }
    assert.ok(snap.panes[1].clip.left >= snap.panes[0].clip.left + snap.panes[0].clip.width,
      'B 格裁剪窗口不得伸进 A 格');
    const beforeA = snap.panes[0];
    await page.evaluate(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      v.panes[1].view.ox += 17; v.panes[1].view.scale *= 1.25; v.panes[1].view.oy -= 9;
    });
    await page.waitForFunction(() => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      const els = document.querySelectorAll('.batch-pane');
      if (els.length !== 2) return false;
      const stage = document.querySelector('.stage').getBoundingClientRect();
      const r = els[1].querySelector('.batch-image').getBoundingClientRect(), p = v.panes[1];
      return p.rec?.thumb && Math.abs((r.left - stage.left) - (p.rect.x + p.view.ox)) < 0.75;
    });
    await settle();
    snap = await geom();
    for (let i = 0; i < 2; i += 1) {
      checkPane(snap.panes[i], snap.views[i].view, snap.views[i].rect, snap.views[i].thumb, `split after B move ${snap.views[i].side}`);
    }
    assert.ok(snap.panes[1].image.width > snap.panes[1].clip.width,
      '放大后影像应超出裁剪窗口，才能真正验证裁剪');
    assert.ok(Math.abs(snap.panes[0].clip.left - beforeA.clip.left) < 0.75, 'A 格不跟随 B 格的缩放/平移');

    // E. 刷新：服务端换了 sha，文档重新获取，<img> 缓存键跟着变。
    state.sha = 's2';
    const metaBefore = state.meta;
    await page.click('[data-e2e=batch-overlay-chip] .batch-chip-btn');
    await page.waitForFunction("document.querySelector('[data-e2e=batch-overlay-img]')?.getAttribute('src')?.includes('v=s2')");
    assert.equal(state.meta, metaBefore + 1, '刷新必须重新取 overlay.json');

    // F. 加载失败只显示原因，不画看起来像结果的候选；重试成功后恢复。
    state.mode = 'fail';
    await page.click('[data-e2e=batch-overlay-chip] .batch-chip-btn');
    await page.waitForSelector('[data-e2e=batch-overlay-error]');
    assert.match(await page.$eval('[data-e2e=batch-overlay-error]', (e) => e.textContent), /服务端返回 503/);
    assert.equal(await page.$$('.batch-pane').then((a) => a.length), 0, '失败时不得保留旧叠加层');
    state.mode = 'ok';
    state.sha = 's3';
    await page.click('[data-e2e=batch-overlay-error] .batch-chip-btn');
    await page.waitForFunction("document.querySelector('[data-e2e=batch-overlay-img]')?.getAttribute('src')?.includes('v=s3')");
    assert.equal(await page.$$('[data-e2e=batch-overlay-error]').then((a) => a.length), 0);
    assert.ok((await page.$$('.batch-pane')).length >= 1);

    // G. 文档成功但 PNG 失败：不得显示“真实检测”或候选，只显示错误与重试。
    await page.waitForFunction("document.querySelector('[data-e2e=batch-overlay-chip]')");
    state.pngMode = 'fail';
    state.sha = 's4';
    await page.click('[data-e2e=batch-overlay-chip] .batch-chip-btn');
    await page.waitForFunction(() => {
      const el = document.querySelector('[data-e2e=batch-overlay-error]');
      return el && el.textContent.includes('叠加层图片加载失败');
    });
    assert.equal(await page.$$('[data-e2e=batch-overlay-chip]').then((a) => a.length), 0,
      'PNG 失败时不得继续声称真实检测');
    assert.equal(await page.$$('[data-e2e=batch-overlay-candidate]').then((a) => a.length), 0,
      'PNG 失败时不得保留候选轮廓');
    assert.equal(await page.$$('.batch-pane').then((a) => a.length), 0,
      'PNG 失败时整格隐藏');
    const pngBefore = state.png;
    state.pngMode = 'ok';
    await page.click('[data-e2e=batch-overlay-error] .batch-chip-btn');
    await page.waitForFunction("document.querySelector('[data-e2e=batch-overlay-chip]')");
    assert.ok(state.png > pngBefore, '重试必须重新请求 PNG');
    await page.waitForFunction("document.querySelectorAll('.batch-pane').length===2");
    assert.equal(await page.$$('[data-e2e=batch-overlay-candidate]').then((a) => a.length), 2);

    // H. 身份变化（换图/换格）立即停止绘制；恢复后重新出现。
    await page.evaluate((other) => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      v.activeRec.spatialIdentity = other;
    }, OTHER_IDENTITY);
    await page.waitForFunction("document.querySelectorAll('.batch-pane').length===0");
    assert.ok(await page.$('[data-e2e=batch-overlay-chip]'), '身份不匹配时入口仍在，只是不画');
    await page.evaluate((identity) => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      v.activeRec.spatialIdentity = identity;
    }, IDENTITY);
    await page.waitForFunction("document.querySelectorAll('.batch-pane').length===2");

    // 真正换一张本地影像：A 格换成本地新图（无服务端身份）→ A 格停画；
    // B 格仍是原图，叠加层只属于它 —— 换图也不会把身份带到另一张影像上。
    const oldId = await page.evaluate(`${VIEWER}.activeRec.id`);
    await dropJpeg(page, 'overlay_fixture_B.jpg', '#6b2a20');
    await page.evaluate(`${VIEWER}.hideModal()`);
    await page.waitForFunction((old) => {
      const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      return v.activeRec?.thumb && v.activeRec.id !== old;
    }, {}, oldId);
    await page.waitForFunction("document.querySelectorAll('.batch-pane').length===1");
    assert.equal(await page.$eval('.batch-pane', (e) => e.getAttribute('data-pane')), 'B',
      '换图后只有仍匹配身份的 B 格保留叠加层');

    assert.deepEqual(errors, []);
    console.log('Batch overlay browser checks passed: identity gating, clip window per pane (overflow hidden, no neighbor leak), pane-local projection with vertex-level screen coords under zoom/pan/split, refresh cache-bust, document 503 without stale overlay, PNG 503 hides candidates and provenance chip, PNG retry, identity change, real image change');
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
