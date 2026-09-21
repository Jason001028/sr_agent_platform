// 阶段3 验收：Vue3 查看器浏览器回归（跑 frontend/dist + 静态服务）
// ------------------------------------------------------------------
// 对齐 HTML 原版各浏览器套件的**等价行为**，全部经 window.__viewer 钩子 + DOM 驱动：
//   A. bootstrap：应用挂载、__viewer 齐全、无外部请求
//   B. 解码路由 + 像素：rgb8→utif、u16_whitezero→chunked(反相)、f32_grad→chunked、
//      bigtiff_strips→chunked（BigTIFF 必须走 geotiff，UTIF 不能解）
//   C. 稀疏：setSparseMin(1000) 后 gray16_grad → route='sparse' + 渐变 golden
//   D. 拉伸切换重绘（thumb 像素变化）
//   E. 像素定位：locatePixel → 视图居中 + 红叉 marker + 7s 过期
//   F. 掩码冒烟：enterDraw → commitRect/getRois 造 ROI → buildMaskJson 坐标反算 →
//      genMask 两次下载 → mergeRois 重叠→1 区 → del 红闪+移除 → undo/clear
//   G. 待修复清单：导入 → 原样 round-trip → 拒收无关 .txt → 标记终态/中间态 →
//      写回文本上半部分逐字不变 + 下半部分只含终态 → 未完成筛选 → 刷新仍在 → ✕ 清空
// （HTML 版原有的 G. 导出段已于 2026-09-15 删除：最小原型取消了浏览器侧 JPG 导出 /
//   输出目录一条链路（sr-minimal-prototype-plan.md §4.5），setSaver / reExportJpg /
//   scanPendingExports / jpgStatus 等钩子随之从 e2eHooks.ts 移除。针对已取消功能的
//   断言留着只会长期报红，故整段删除；导出链路的等价回归见 .e2e/test-scenes.js 的
//   「C. 懒烘焙」段 —— 现在是服务端烘焙，前端不再写文件。）
// 用法: cd .e2e && node test-vue-viewer.js
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { launchPage } = require('./launchBrowser');
const drag = require('./lib/drag');

const DIST = path.resolve(__dirname, '..', 'frontend', 'dist');
const FIXTURES = path.resolve(__dirname, '..', 'frontend', 'fixtures');
const F = (n) => path.join(FIXTURES, n);
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

let pass = 0;
function assert(cond, msg) {
  if (!cond) throw new Error('断言失败: ' + msg);
  console.log('  ✓ ' + msg);
  pass++;
}

function startServer() {
  const server = http.createServer((req, res) => {
    let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const m = urlPath.match(/^\/(viewer|chat|queue)\/$/);
    if (m) { res.writeHead(301, { location: '/' + m[1] }); res.end(); return; }
    if (urlPath === '/') urlPath = '/index.html';
    let filePath = path.join(DIST, urlPath);
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(DIST, 'index.html');
    }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(500); res.end(String(err)); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 等**活动侧**那张解码完成，返回 activeRec 摘要。
// 分屏下用不上「上传」这个动作时（拖放落图），就只调这个 —— 拖进来的那张会成为活动侧。
//
// 「状态就绪」与「画面可交互」是两件事：状态先写好，随后装载收尾还有一段**缩放预热**
// 在盖着遮罩跑（见 stores/viewer.warmZoom）。遮罩是个普通元素、盖在画布上，真实
// `page.mouse.wheel` 会被它按命中测试吃掉（合成 dispatchEvent 才绕得过）—— 不等它
// 收起，后面那段滚轮断言测的就不是画布。所以这里一并等遮罩收起才返回。
async function waitDecoded(page, timeoutMs) {
  const t0 = Date.now();
  for (;;) {
    const rec = await page.evaluate(() => window.__viewer.activeRec());
    if (rec && rec.status) {
      if (rec.status.indexOf('完成') === 0) {
        const masked = await page.evaluate(() =>
          !!(window.__viewer.overlayVisible && window.__viewer.overlayVisible()));
        if (!masked) return rec;
      }
      if (rec.status.indexOf('解码失败') === 0) throw new Error('解码失败: ' + rec.status);
    }
    if (Date.now() - t0 > (timeoutMs || 30000)) throw new Error('超时等待解码，最近状态: ' + (rec && rec.status));
    await sleep(200);
  }
}

// 上传文件并等待解码完成，返回 activeRec 摘要
async function uploadAndWait(page, filePath, timeoutMs) {
  const input = await page.$('input[type=file]');
  if (!input) throw new Error('未找到 input[type=file]');
  await input.uploadFile(filePath);
  return waitDecoded(page, timeoutMs);
}

// 页内把 activeRec.thumb 画到 size×size 再采样统计（等价 HTML capture.js sampleThumb）
function sampleThumb(page, size) {
  return page.evaluate((s) => {
    const rec = window.__viewer.activeRec();
    if (!rec || !rec.thumb) throw new Error('无 activeRec.thumb');
    const tw = rec.thumbW, th = rec.thumbH;
    const size = s || 256;
    const c = document.createElement('canvas');
    c.width = size; c.height = size;
    const g = c.getContext('2d');
    g.imageSmoothingEnabled = true;
    g.drawImage(rec.thumb, 0, 0, size, size);
    const d = g.getImageData(0, 0, size, size).data;
    let sum = 0, sumL = 0, sumR = 0, n = 0, black = 0, white = 0, min = 255, max = 0;
    const split = Math.floor(size * 0.2);
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const i = (y * size + x) * 4;
        const v = (d[i] + d[i + 1] + d[i + 2]) / 3;
        sum += v; n++;
        if (x < split) sumL += v;
        if (x >= size - split) sumR += v;
        if (v < 2) black++;
        if (v > 252) white++;
        if (v < min) min = v; if (v > max) max = v;
      }
    }
    return {
      tw, th,
      mean: sum / n,
      meanL: sumL / (split * size),
      meanR: sumR / (split * size),
      blackPct: (black / n) * 100,
      whitePct: (white / n) * 100,
      min, max,
    };
  }, size);
}

// 数整张 drawCanvas 上「红主导」（r > b+20）像素——del 闪红检测（正常 ROI 是蓝主导）
function countRedOnDrawCanvas(page) {
  return page.evaluate(() => {
    const c = document.querySelector('canvas.draw-canvas');
    if (!c) return 0;
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i] > d[i + 2] + 20 && d[i] > 120) n++;
    }
    return n;
  });
}

async function main() {
  const server = await startServer();
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  console.log(`[test-vue-viewer] 静态服务 ${base}  (dist=${DIST})`);

  const { browser, page, errors } = await launchPage();
  const externalRequests = [];
  page.on('request', (req) => {
    const u = req.url();
    if (!u.startsWith(base) && !u.startsWith('data:')) externalRequests.push(u);
  });

  try {
    /* ============ A. bootstrap ============ */
    console.log('--- A. bootstrap ---');
    await page.goto(base + '/viewer', { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForSelector('.app-nav', { timeout: 10000 });
    await page.waitForFunction(() => !!(window.__viewer && window.__viewer.recs), { timeout: 15000 });
    const hookKeys = await page.evaluate(() => Object.keys(window.__viewer).sort());
    // 与 e2eHooks.ts 的 ViewerHook 一一对应（导出链路的钩子已随功能取消，见文件头）
    // **子集检查**：只对缺键报错，多了不报 —— 但这是 window.__viewer 的成文契约，
    // 新增钩子必须补在这里（否则下一个人以为它不存在）。
    for (const k of ['planExport', 'enterDraw', 'exitDraw', 'buildMaskJson', 'exportMaskJson',
      'thumbToOrig', 'getRois', 'genMask', 'wandSelect', 'maskGen', 'setSparseMin',
      'setDrawTool', 'commitRect', 'undoRoi', 'clearRois', 'mergeRois', 'delClick',
      'openSceneJpg', 'openLocalImage', 'submitSr',
      'openScenePath', 'tryLinkScenes', 'bakeMaskToServer', 'modal', 'hideModal',
      'recs', 'activeRec', 'activeStretch', 'setStretch', 'previewDiv', 'setPreviewDiv',
      'qcImport', 'qcClose', 'qcSetStatus', 'qcOutput', 'qcSetTarget', 'qcSync',
      'qcState', 'qcOpenByName',
      'cmpMode', 'setCmpMode', 'cmpStripOpen', 'setCmpStripOpen', 'cmpPanes',
      'activeSide', 'setActiveSide', 'splitRatio', 'setSplitRatio', 'splitX', 'cmpReset',
      'dragHint', 'cmpList', 'cmpClear', 'ctxRail', 'setCtxRail']) {
      if (!hookKeys.includes(k)) throw new Error('__viewer 缺钩子 ' + k);
    }
    assert(true, `__viewer 钩子齐全 (${hookKeys.length} 个)`);
    assert(externalRequests.length === 0, `无外部请求 (${JSON.stringify(externalRequests)})`);

    /* ============ B. 解码路由 + 像素 ============ */
    console.log('--- B. 解码路由 + 像素 ---');
    {
      // rgb8: 8bit RGB 小图 → UTIF
      let rec = await uploadAndWait(page, F('rgb8.tif'), 30000);
      assert(rec.route === 'utif', `rgb8 → utif (实际 ${rec.route})`);
      assert(rec.W === 64 && rec.H === 64, `rgb8 尺寸 64×64 (${rec.W}×${rec.H})`);
      const px = await page.evaluate(() => {
        const t = window.__viewer.activeRec().thumb;
        const d = t.getContext('2d').getImageData(5, 7, 1, 1).data;
        return [d[0], d[1], d[2]];
      });
      assert(Math.abs(px[0] - 5) <= 3 && Math.abs(px[1] - 7) <= 3 && Math.abs(px[2] - 12) <= 3,
        `rgb8 像素(5,7)≈(5,7,12) 实为 (${px[0]},${px[1]},${px[2]})`);

      // u16_whitezero: 16bit WhiteIsZero → chunked + 反相
      rec = await uploadAndWait(page, F('u16_whitezero.tif'), 30000);
      assert(rec.route === 'chunked', `u16_whitezero → chunked (实际 ${rec.route})`);
      assert(rec.W === 256 && rec.H === 128, `u16_whitezero 尺寸 256×128 (${rec.W}×${rec.H})`);
      let s = await sampleThumb(page, 256);
      assert(s.max - s.min > 150, `u16_whitezero 反相渐变全幅 (min=${s.min} max=${s.max})`);
      assert(s.meanR - s.meanL > 40, `u16_whitezero 反相后右亮左暗 (L=${s.meanL.toFixed(1)} R=${s.meanR.toFixed(1)})`);

      // f32_grad: 32bit 浮点 → chunked
      rec = await uploadAndWait(page, F('f32_grad.tif'), 30000);
      assert(rec.route === 'chunked', `f32_grad → chunked (实际 ${rec.route})`);
      assert(rec.W === 128 && rec.H === 128, `f32_grad 尺寸 128×128 (${rec.W}×${rec.H})`);
      s = await sampleThumb(page, 128);
      assert(s.max - s.min > 100 && s.blackPct < 5, `f32_grad 有渐变无大量黑 (min=${s.min} max=${s.max})`);

      // bigtiff_strips: BigTIFF 即使 8bit 也必须走 chunked（UTIF 不能解）→ 证明 needGeo 修复
      rec = await uploadAndWait(page, F('bigtiff_strips.tif'), 30000);
      assert(rec.route === 'chunked', `bigtiff_strips → chunked（BigTIFF 修复生效，实际 ${rec.route}）`);
      assert(rec.W === 256 && rec.H === 256 && rec.thumbW === 256 && rec.thumbH === 256,
        `bigtiff_strips 尺寸/缩略图 256×256 (thumb ${rec.thumbW}×${rec.thumbH})`);
      s = await sampleThumb(page, 256);
      assert(s.max - s.min > 150, `bigtiff_strips 行内渐变 (min=${s.min} max=${s.max})`);
    }

    /* ============ C. 稀疏条带路由 ============ */
    console.log('--- C. 稀疏条带路由 ---');
    {
      await page.evaluate(() => window.__viewer.setSparseMin(1000));
      const rec = await uploadAndWait(page, F('gray16_grad.tif'), 30000);
      assert(rec.route === 'sparse', `gray16_grad → sparse (实际 ${rec.route})`);
      assert(rec.status.indexOf('稀疏条带预览') >= 0, `状态含稀疏条带预览: ${rec.status}`);
      assert(rec.thumbW === 256 && rec.thumbH === 256, `稀疏缩略图 256×256 (${rec.thumbW}×${rec.thumbH})`);
      const s = await sampleThumb(page, 256);
      assert(s.max - s.min > 150 && s.meanR - s.meanL > 40,
        `稀疏渐变正确 (L=${s.meanL.toFixed(1)} R=${s.meanR.toFixed(1)} min=${s.min} max=${s.max})`);
      await page.evaluate(() => window.__viewer.setSparseMin(100000000));   // 恢复默认
    }

    /* ============ D. 拉伸切换重绘 ============ */
    console.log('--- D. 拉伸切换重绘 ---');
    {
      // 通过 DOM 点击文件列表第 2 项激活已解码的 u16_whitezero（更接近真实交互）
      await page.evaluate(() => {
        document.querySelectorAll('.file-item')[1].click();
      });
      await sleep(300);
      const s1 = await sampleThumb(page, 256);
      await page.select('select', 'sqrt');
      await sleep(300);
      const s2 = await sampleThumb(page, 256);
      assert(Math.abs(s2.mean - s1.mean) > 5,
        `线性→sqrt 重绘生效 (mean ${s1.mean.toFixed(1)} → ${s2.mean.toFixed(1)})`);
    }

    /* ============ E. 像素定位 ============ */
    console.log('--- E. 像素定位 ---');
    {
      const sampleCenterRed = () => page.evaluate(() => {
        const c = document.querySelector('canvas.tif-canvas');
        const g = c.getContext('2d');
        const w = c.width, h = c.height;
        const d = g.getImageData(Math.floor(w / 2) - 30, Math.floor(h / 2) - 30, 60, 60).data;
        let n = 0;
        for (let i = 0; i < d.length; i += 4) {
          if (d[i] > 180 && d[i + 1] < 130 && d[i + 2] < 130) n++;
        }
        return n;
      });
      const locVal = () => page.evaluate(() => document.querySelector('.loc input').value);
      const setLoc = (v) => page.evaluate((val) => {
        const el = document.querySelector('.loc input');
        el.value = val;
        el.dispatchEvent(new Event('input'));
      }, v);
      const errText = () => page.evaluate(() => {
        const err = document.querySelector('.err-box');
        return err ? err.textContent.trim() : '';
      });

      // X、Y 合流成一个框：只有这一个输入框，未输入时靠占位文本说清填什么形态，
      // 且占位文本必须整句看得见（框比它窄就成了「输入坐标（X,…」这种半句话提示）。
      const locBox = await page.evaluate(() => {
        const inputs = document.querySelectorAll('.loc input');
        const el = inputs[0];
        const cs = getComputedStyle(el);
        const c = document.createElement('canvas').getContext('2d');
        c.font = `${cs.fontSize} ${cs.fontFamily}`;
        const textW = c.measureText(el.placeholder).width;
        const inner = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
        return { n: inputs.length, ph: el.placeholder, textW: Math.round(textW), inner: Math.round(inner) };
      });
      assert(locBox.n === 1, `定位只有一个输入框（X、Y 已合流，实际 ${locBox.n} 个）`);
      assert(locBox.ph === '输入坐标（X,Y）:123.456,456.123',
        `未输入时的占位提示（${locBox.ph}）`);
      assert(locBox.inner >= locBox.textW,
        `占位提示整句放得下（可用 ${locBox.inner}px ≥ 文本 ${locBox.textW}px）`);

      // 定位原图中心 (128, 64)：单框里填「X,Y」→ 视图居中 + 红叉 marker
      await setLoc('128,64');
      await page.click('.loc-btn');
      await sleep(250);
      const centerRed = await sampleCenterRed();
      assert(centerRed > 0, `定位后中心出现红叉 marker (红像素 ${centerRed})`);
      assert(await locVal() === '128,64', `定位成功后框里是规范化「X,Y」(实际 ${await locVal()})`);

      // 掩膜中心点坐标那种形态：全角逗号照样认，成功后框里回写成半角「X,Y」→ 跳到 (64,32)
      await setLoc('64，32');
      await page.click('.loc-btn');
      await sleep(250);
      assert(await sampleCenterRed() > 0, '全角逗号分隔的「64，32」同样跳到 (64,32)（中心出现红叉）');
      assert(await locVal() === '64,32', `回写成半角「X,Y」(实际 ${await locVal()})`);

      // 单个数（手输到一半的形态）不是坐标对：不许拿它跳，必须出声且原样留着
      await setLoc('64');
      await page.click('.loc-btn');
      await sleep(150);
      assert((await errText()).indexOf('坐标对认不出来') >= 0 && await locVal() === '64',
        `单个数不猜、按原样留着并报错 (${(await errText()).slice(0, 20)}…)`);

      // 拷了整行（三个数）同样不许当坐标对：截前两个会把标记跳到别处，必须出声
      await setLoc('1,64,32');
      await page.click('.loc-btn');
      await sleep(150);
      assert((await errText()).indexOf('坐标对认不出来') >= 0 && await locVal() === '1,64,32',
        `三个数不猜、按原样留着并报错 (${(await errText()).slice(0, 20)}…)`);

      await sleep(7600);   // marker 7s 过期（从上一次**成功**定位起算，失败那两次不动 marker）
      const centerRedAfter = await page.evaluate(() => {
        const c = document.querySelector('canvas.tif-canvas');
        const g = c.getContext('2d');
        const w = c.width, h = c.height;
        const d = g.getImageData(Math.floor(w / 2) - 30, Math.floor(h / 2) - 30, 60, 60).data;
        let n = 0;
        for (let i = 0; i < d.length; i += 4) {
          if (d[i] > 180 && d[i + 1] < 130 && d[i + 2] < 130) n++;
        }
        return n;
      });
      assert(centerRedAfter === 0, `红叉 7s 后过期消失 (剩余 ${centerRedAfter})`);
    }

    /* ============ F. 掩码冒烟 ============ */
    console.log('--- F. 掩码冒烟 ---');
    {
      await page.evaluate(() => {
        window.__dl = [];
        const origClick = HTMLAnchorElement.prototype.click;
        HTMLAnchorElement.prototype.click = function () {
          if (this.download) window.__dl.push({ name: this.download, href: this.href });
          return origClick.apply(this, arguments);
        };
        window.__viewer.enterDraw();
        window.__viewer.commitRect({ x0: 10, y0: 10, x1: 50, y1: 50 });   // 矩形
        window.__viewer.getRois().push([[40, 40], [80, 40], [80, 80], [40, 80]]);  // 多边形（与矩形重叠）
      });
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 2, 'ROI 2 个（矩形 + 多边形）');

      const json = await page.evaluate(() => window.__viewer.buildMaskJson());
      assert(json.width === 256 && json.height === 128, `buildMaskJson 尺寸 ${json.width}×${json.height}`);
      assert(json.polygons.length === 2 && json.polygons[0].points.length === 4, 'polygons 2 个各 4 点');
      const p0 = json.polygons[0].points[0];
      assert(Math.abs(p0[0] - 10) <= 1 && Math.abs(p0[1] - 10) <= 1,
        `缩略图→原图坐标反算 (${p0[0]},${p0[1]})≈(10,10)`);

      // genMask → mask.tif + mask.txt 两次下载
      await page.evaluate(() => window.__viewer.genMask());
      const dl = await page.evaluate(() => window.__dl);
      const names = dl.map((d) => d.name).sort();
      assert(names.length === 2 && names[0] === 'u16_whitezero_mask.tif' && names[1] === 'u16_whitezero_mask.txt',
        `genMask 两次下载 (${names.join(', ')})`);
      const sizes = await page.evaluate(async () => {
        const out = [];
        for (const d of window.__dl) {
          const b = await (await fetch(d.href)).blob();
          out.push(b.size);
        }
        return out;
      });
      assert(sizes[0] > 0 && sizes[1] > 0, `下载内容非空 (mask.tif=${sizes[0]}B mask.txt=${sizes[1]}B)`);

      // 合并重叠 → 1 区
      await page.evaluate(() => window.__viewer.mergeRois());
      const merged = await page.evaluate(() => window.__viewer.getRois().length);
      assert(merged === 1, `合并重叠 → 1 连通区 (实际 ${merged})`);

      // del：命中 + 200ms 红闪 + 移除
      await page.evaluate(() => {
        window.__viewer.setDrawTool('del');
        window.__viewer.delClick(30, 30);
      });
      await sleep(80);
      const redDuring = await countRedOnDrawCanvas(page);
      assert(redDuring > 0, `删除命中红闪 (红主导像素 ${redDuring})`);
      await sleep(300);
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 0, 'del 200ms 后移除');

      // undo / clear
      await page.evaluate(() => {
        window.__viewer.getRois().push([[0, 0], [5, 0], [5, 5], [0, 5]]);
      });
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 1, '新增 1 区');
      await page.evaluate(() => window.__viewer.undoRoi());
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 0, 'undo 移除');
      await page.evaluate(() => {
        window.__viewer.getRois().push([[0, 0], [5, 0], [5, 5], [0, 5]]);
        window.__viewer.getRois().push([[10, 10], [20, 10], [20, 20], [10, 20]]);
        window.__viewer.clearRois();
      });
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 0, 'clear 清空');
    }

    /* ============ G. 待修复清单（导入 / 标记 / 写回） ============ */
    console.log('--- G. 待修复清单 ---');
    {
      // 样例照抄质检部门真实给的那份形态：上半部分问题行（第一列尾部带逗号、desc 在括号里），
      // 空行，下半部分我们自己补的处置结果。制表符走 T 常量拼接 —— 直接写转义在这种
      // 长中文串里改一次错一次，拼出来的是同一个字符，但肉眼能看清列边界在哪。
      const T = '\t';
      const N6 = 'JL1KF02B02_PMS07_20260917122028_200538707_101_0006_001_L1';
      const N20 = 'JL1KF02B04_PMS03_20260917120612_200538728_101_0020_001_L1';
      const N21 = 'JL1KF02B04_PMS03_20260917120612_200538728_101_0021_001_L1';
      const N22 = 'JL1KF02B04_PMS03_20260917120612_200538728_101_0022_001_L1';
      const issue = (n, row, col, ty, who) => n + ',' + T
        + '产品存在伪影 (问题类型:产品存在伪影 行列号:' + row + ',' + col + ' 影像类型:' + ty + ' )' + T + who;
      const TOP = [
        issue(N6, '30766.11', '21862.51', 'PAN', '李鹏飞'),
        issue(N20, '7300.26', '1737.98', 'pan', '李佳峻'),
        issue(N21, '30768.07', '10886.95', 'PAN', '李佳峻'),
        issue(N22, '20931.93', '4841.22', 'pan', '李佳峻'),
      ].join('\n');
      const SAMPLE = TOP + '\n\n' + N6 + T + '修复通过\n';

      const imported = await page.evaluate((t) => window.__viewer.qcImport('待修复清单.txt', t), SAMPLE);
      assert(imported === true, '导入标准格式清单');
      let qs = await page.evaluate(() => window.__viewer.qcState());
      assert(qs.loaded && qs.total === 4, `4 行问题全部列出 (${qs.total})`);
      // 下半部分那行是「已修复」的既有状态，导入时就该读出来
      assert(qs.done === 1 && qs.statuses[N6] === 'fixed', '0006 从下半部分读到「已修复」');

      // 什么都不改直接写回 == 原文（上半部分逐字保留 + 下半部分只重排终态行的最强保证）
      const rt = await page.evaluate(() => window.__viewer.qcOutput());
      assert(rt === SAMPLE, '未改动时写回 == 原文（含空行与列内空白）');

      // 拖无关 .txt 进来（画布上就是拖拽入口）：一份问题行都解不出来时不许顶掉当前清单
      const junk = await page.evaluate((t) => window.__viewer.qcImport('掩膜中心点坐标.txt', t), '1,2\n3,4\n');
      assert(junk === false, '解不出问题行的 .txt 被拒收');
      assert(await page.evaluate(() => window.__viewer.qcOutput()) === SAMPLE, '拒收后现有清单原封不动');

      // DOM：面板真的渲染出来了（钩子绕过 UI，这一条是 UI 那一半的证据）。
      // 计数与徽标都要 trim：模板里插值两侧的换行会被 Vue 压成单个空格。
      const dom = await page.evaluate(() => {
        const c = document.querySelector('.qc-count');
        return {
          count: c ? c.textContent.trim() : null,
          rows: document.querySelectorAll('.qc-list .qc-row').length,
          badges: [...document.querySelectorAll('.qc-badge')].map((e) => e.textContent.trim()),
          l2: [...document.querySelectorAll('.qc-row .qc-l2')].map((e) => e.textContent.replace(/\s+/g, ' ').trim()),
        };
      });
      assert(dom.count === '1/4' && dom.rows === 4, `面板渲染 4 行、计数 ${dom.count}`);
      assert(dom.badges.length === 1 && dom.badges[0].indexOf('已修复') >= 0,
        `只有 0006 带状态徽标 (${dom.badges.join(',')})`);
      // 行列号是 (行, 列)：0020 行必须「行 7300.26」在「列 1737.98」**之前**。
      // 不比整串：.qc-l2 是 flex，分隔符两侧的空白文本节点编译期就被压掉了，
      // 逐字比对会假红；顺序 + 三个字段在场才是真正要守的东西。
      const l2 = dom.l2[1] || '';
      assert(l2.indexOf('行 7300.26') >= 0 && l2.indexOf('列 1737.98') > l2.indexOf('行 7300.26')
        && l2.indexOf('pan') > 0 && l2.indexOf('李佳峻') > 0,
        `0020 行按「行,列」显示坐标与影像类型/责任人「${l2}」`);

      // 标记：0020 已修复、0021 驳回、0022 只到「已提交任务」（中间态）
      await page.evaluate(([a, b, c]) => {
        window.__viewer.qcSetStatus(a, 'fixed');
        window.__viewer.qcSetStatus(b, 'rejected');
        window.__viewer.qcSetStatus(c, 'submitted');
      }, [N20, N21, N22]);
      // 中间态不进文档：0022 在面板里是「已提交任务」，写回文本里不该有它的行
      const out = await page.evaluate(() => window.__viewer.qcOutput());
      const chunks = out.split('\n\n');
      assert(chunks.length === 2 && chunks[0] === TOP, '写回：上半部分与原文逐字相同');
      assert(chunks[1] === [N6, N20, N21].map((n, i) => n + T + (i === 2 ? '驳回' : '修复通过')).join('\n') + '\n',
        '写回：下半部分只含终态行（顺序随上半部分，中间态不落文档）');
      qs = await page.evaluate(() => window.__viewer.qcState());
      assert(qs.done === 3, `计数按终态算 (${qs.done}/4)`);

      // 再点当前状态 = 取消（写成一个 toggle，省一个「清除」按钮）
      await page.evaluate((n) => window.__viewer.qcSetStatus(n, 'rejected'), N21);
      assert((await page.evaluate(() => window.__viewer.qcOutput())).indexOf('驳回') < 0, '再点一次取消该行状态');
      await page.evaluate((n) => window.__viewer.qcSetStatus(n, 'rejected'), N21);

      // 「未完成」筛选：0022 停在中间态 → 只剩它一行
      await page.evaluate(() => {
        [...document.querySelectorAll('.qc-ob')].find((b) => b.textContent.trim() === '未完成').click();
      });
      await sleep(120);
      assert(await page.evaluate(() => document.querySelectorAll('.qc-list .qc-row').length) === 1,
        '「未完成」只留没出终态的行');
      await page.evaluate(() => {
        [...document.querySelectorAll('.qc-ob')].find((b) => b.textContent.trim() === '未完成').click();
      });

      // 刷新 → 原文与状态都还在（localStorage）
      await page.reload({ waitUntil: 'networkidle0' });
      await page.waitForFunction(() => !!(window.__viewer && window.__viewer.qcState), { timeout: 15000 });
      qs = await page.evaluate(() => window.__viewer.qcState());
      assert(qs.loaded && qs.total === 4 && qs.sourceName === '待修复清单.txt',
        `刷新后清单还在 (${qs.total} 行，源 ${qs.sourceName})`);
      assert(qs.done === 3, `刷新后状态还在 (${qs.done}/4)`);
      assert(await page.evaluate(() => window.__viewer.qcOutput()) === out, '刷新后写回文本与刷新前一致');

      // ✕：回到未导入态，缓存一并清掉（否则下次打开又冒出来）
      await page.evaluate(() => window.__viewer.qcClose());
      qs = await page.evaluate(() => window.__viewer.qcState());
      const lsLeft = await page.evaluate(() => localStorage.getItem('sr.viewer.qcList'));
      assert(!qs.loaded && qs.total === 0, '✕ 后回到未导入态');
      assert(lsLeft === null, '✕ 后 localStorage 缓存被清掉');
      assert(await page.evaluate(() => window.__viewer.qcOutput()) === '', '✕ 后写回文本为空');
    }

    /* ============ H. 图像对比（关闭 / 点选对比 / 分屏对比） ============ */
    // 这一段不经后端：两张 fixture 都是 256×256（gray16_grad / bigtiff_strips），同尺寸，
    // 于是两格里看到的差异只来自拉伸，不掺几何 —— 顺带避开「产物是输入 2 倍」那条待核事实。
    console.log('--- H. 图像对比 ---');
    {
      // 默认视口 800×600 放不下「左栏 400 + 右栏 400 + 画布」，而这一段要验的就是
      // 「进分屏自动收右栏、退出恢复」。1366 与 test-manual-scene 的工具栏守卫同一条宽度。
      await page.setViewport({ width: 1366, height: 768 });
      await sleep(400);
      await drag.installDragKit(page);     // 每次导航后都要重装（G 段末尾 reload 过）

      const panes = () => page.evaluate(() => window.__viewer.cmpPanes());
      const rail = () => page.evaluate(() => window.__viewer.ctxRail());
      const hintDom = () => page.evaluate(() => {
        const el = document.querySelector('[data-e2e="cmp-hint"]');
        return el ? { side: el.getAttribute('data-side'), text: el.textContent.trim() } : null;
      });
      const toastText = () => page.evaluate(() => {
        const t = document.querySelector('.toast');
        return t ? t.textContent.trim() : '';
      });

      // 关闭态：没有对比条、没有叠加层、没有点选清单。G 段末尾 reload 过 → 文件列表已空。
      assert(await page.evaluate(() => window.__viewer.cmpMode()) === 'off'
        && await page.evaluate(() => window.__viewer.cmpStripOpen()) === false,
        '初始是「关闭」且对比条收起');
      assert(await page.evaluate(() => !document.querySelector('[data-e2e="cmp-bar"]')
        && !document.querySelector('[data-e2e="cmp-overlay"]')
        && !document.querySelector('[data-e2e="cmp-list"]')),
        '关闭态：无对比条 / 无叠加层 / 无点选清单');

      // 工具栏入口 → 展开一条，三选一，默认「关闭」
      await page.click('[data-e2e="cmp-open"]');
      await sleep(150);
      const bar = await page.evaluate(() => {
        const modes = [...document.querySelectorAll('.cmp-mode')];
        const on = document.querySelector('.cmp-mode.on');
        return {
          n: modes.length,
          labels: modes.map((b) => b.textContent.trim()),
          on: on ? on.textContent.trim() : null,
          checked: modes.map((b) => b.getAttribute('aria-checked')),
        };
      });
      assert(bar.n === 3 && bar.on === '关闭' && bar.checked.join(',') === 'true,false,false',
        `对比条三选一、默认「关闭」(${bar.labels.join(' / ')})`);

      // 第一张走选文件那条路（落活动侧）
      const rec1 = await uploadAndWait(page, F('gray16_grad.tif'), 30000);
      // 本地文件没有 sceneId → 三枚场景芯片整排禁用（廉价通道要先有盘阵关联）
      const chips = await page.evaluate(() => {
        const btns = [...document.querySelectorAll('.cmp-scene')];
        const note = document.querySelector('[data-e2e="cmp-sib-note"]');
        return { n: btns.length, disabled: btns.filter((b) => b.disabled).length, note: note ? note.textContent.trim() : null };
      });
      assert(chips.n === 3 && chips.disabled === 3 && !!chips.note,
        `本地文件时三枚场景芯片禁用并说明原因（${chips.note}）`);

      // 右栏先展开：进分屏要「自动收起」，退出要「恢复成展开」
      await page.evaluate(() => window.__viewer.setCtxRail(true));
      await sleep(200);
      assert((await rail()).open === true, '进分屏前右栏是展开的');

      // 切分屏（走 DOM 按钮，不只是钩子）
      await page.click('[data-e2e="cmp-mode-split"]');
      await sleep(300);
      const geom = await page.evaluate(() => {
        const p = window.__viewer.cmpPanes();
        const r = document.querySelector('canvas.view-canvas').getBoundingClientRect();
        return { n: p.length, a: p[0], b: p[1], splitX: window.__viewer.splitX(), cw: r.width };
      });
      assert(geom.n === 2 && geom.a.rect.x === 0 && geom.b.rect.x === geom.a.rect.w,
        '分屏 = 两块格子，左起排列');
      assert(geom.a.rect.w + geom.b.rect.w === Math.round(geom.cw),
        `两块宽度正好铺满画布 (${geom.a.rect.w}+${geom.b.rect.w} = ${Math.round(geom.cw)})`);
      assert(geom.splitX > 0 && geom.splitX === geom.a.rect.w && geom.n === 2
        && geom.b.rect.w > 0,
        `分隔位置与左格同源、右格非空 (splitX=${geom.splitX})`);
      assert(geom.a.recId === rec1.id && geom.b.recId === null && geom.a.active && !geom.b.active,
        '当前这张进左格，右格空着，活动侧是左格');
      const railIn = await rail();
      assert(railIn.open === false && railIn.prev === true,
        `进分屏自动收起右栏（记住了进入前的状态 prev=${railIn.prev}）`);

      // 右格占位 + 两侧文件名条（占位是虚的，名字是实的）
      const halves = await page.evaluate(() => {
        const h = [...document.querySelectorAll('.cmp-half')];
        const t = (s) => { const e = document.querySelector(s); return e ? e.textContent.replace(/\s+/g, ' ').trim() : null; };
        return {
          n: h.length,
          b: h[1] ? h[1].textContent.trim() : null,
          aHasEmpty: h[0] ? !!h[0].querySelector('.cmp-empty') : null,
          tagA: t('[data-e2e="cmp-tag-a"]'), tagB: t('[data-e2e="cmp-tag-b"]'),
        };
      });
      assert(halves.n === 2 && halves.b === '把影像拖到这一侧' && halves.aHasEmpty === false,
        `右格出落位占位（"${halves.b}"）`);
      assert(halves.tagA.indexOf('gray16_grad.tif') >= 0 && halves.tagB.indexOf('空') >= 0,
        `两侧标签各说自己的图（${halves.tagA} | ${halves.tagB}）`);

      // 画布区（含叠加层的文字）不许起文本选择：起点压在右格标签 / 右格空位提示上拖动，
      // 必须照旧平移，且拉不出任何选中文字。**这是「拖画面 vs 换格」那次的根**——
      // 叠加层里只要有一层可选中的文字，第一次拖先把文字选中，此后**每一次**压在选中
      // 文字上的拖动都被 Chrome 当成「拖选中内容」走原生拖放：页面收到 dragover →
      // 落位提示（「放在右侧」）亮起，看起来像马上要换格；而画布始终收不到 mousedown，
      // 平移一动不动。解码遮罩当初就漏了 `user-select: none`（现在有了）。
      const overlayPts = await page.evaluate(() => {
        const at = (sel, atRight) => {
          const el = document.querySelector(sel);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {
            x: Math.round(atRight ? r.right - 8 : r.left + r.width / 2),
            y: Math.round(r.top + r.height / 2),
          };
        };
        return { tag: at('[data-e2e="cmp-tag-b"]', true), empty: at('.cmp-empty-text', false) };
      });
      for (const [name, pt] of [['右格文件名标签', overlayPts.tag], ['右格空位提示文字', overlayPts.empty]]) {
        assert(!!pt, `找得到${name}（叠加层结构没变）`);
        const b = await panes();
        await page.mouse.move(pt.x, pt.y);
        await page.mouse.down();
        await page.mouse.move(pt.x + 40, pt.y, { steps: 2 });
        await sleep(150);
        const sel = await page.evaluate(() => String(window.getSelection() || ''));
        await page.mouse.up();
        await sleep(80);
        const a = await panes();
        assert(sel === '', `在${name}上起手拖动不产生文本选择（选中="${sel}"）`);
        assert(Math.abs(a[0].view.ox - b[0].view.ox - 40) < 1e-9,
          `在${name}上起手拖动照样是平移 (A.ox ${b[0].view.ox.toFixed(1)}→${a[0].view.ox.toFixed(1)})`);
      }
      // 上面拉过视角，落图断言按「适配态」算，先回正
      await page.evaluate(() => window.__viewer.cmpReset());
      await sleep(150);

      const pts = await drag.canvasPoints(page);
      const overRight = await drag.dragOverOnly(page, {
        files: [{ path: F('bigtiff_strips.tif') }], clientX: pts.right, clientY: pts.midY,
      });
      assert(overRight.hint.active && overRight.hint.side === 'B',
        `拖到右半 → 落位提示归右侧 (${JSON.stringify(overRight.hint)})`);
      const hd = await hintDom();
      assert(hd && hd.side === 'B' && hd.text.indexOf('放在右侧') >= 0,
        `提示只画右半（data-side=${hd && hd.side}，"${hd && hd.text}"）`);
      assert(overRight.defaultPrevented === true,
        'dragover 一律 preventDefault（不拦的话浏览器会导航到拖进来的文件）');

      const overOut = await drag.dragOverOnly(page, {
        files: [{ path: F('bigtiff_strips.tif') }], clientX: pts.outside, clientY: pts.outsideY,
      });
      assert(overOut.hint.side === null, `画布外的落点没有归属 (${JSON.stringify(overOut.hint)})`);
      assert(await hintDom() === null, '画布外不显示落位提示');

      // 落位提示只对**真带文件**的拖放亮。页面内拖放（拖一段选中的文字、拖个链接）
      // 同样发 dragover，但它 drop 时 `files` 是空的、什么都放不进来 —— 提示一亮，
      // 用户就以为画面里拖一下要换格（这正是「拖动换区撞上两图调换」的表现）。
      const overText = await drag.dragOverText(page, { clientX: pts.right, clientY: pts.midY });
      assert(overText.types.indexOf('Files') < 0 && overText.hint.active === false && await hintDom() === null,
        `不带文件的 dragover 不亮落位提示 (types=${JSON.stringify(overText.types)}, hint=${JSON.stringify(overText.hint)})`);
      assert(overText.defaultPrevented === true, '不带文件的 dragover 一样 preventDefault（别放浏览器去导航）');
      // 紧跟着再来一次真拖文件的形态：提示必须照旧亮（别把整条路一起关了）
      const overRight2 = await drag.dragOverOnly(page, {
        files: [{ path: F('bigtiff_strips.tif') }], clientX: pts.right, clientY: pts.midY,
      });
      assert(overRight2.hint.active && overRight2.hint.side === 'B',
        `带 Files 的 dragover 提示照旧 (${JSON.stringify(overRight2.hint)})`);

      // 真正落图：落右半 → 进右格、成为活动侧、不顶掉左格
      const ratioBefore = await page.evaluate(() => window.__viewer.splitRatio());
      const dropped = await drag.dropFiles(page, {
        files: [{ path: F('bigtiff_strips.tif') }], clientX: pts.right, clientY: pts.midY,
      });
      assert(dropped.defaultPrevented === true, 'drop 被接管（defaultPrevented）');
      assert(dropped.after === dropped.before + 1, `右半落图新增 1 条 (${dropped.before}→${dropped.after})`);
      assert(await hintDom() === null, 'drop 之后落位提示立刻熄掉');
      const rec2 = await waitDecoded(page, 30000);
      const p2 = await panes();
      assert(p2[0].recId === rec1.id && p2[1].recId === rec2.id,
        '两张分列两侧，左格那张没被顶掉');
      assert(await page.evaluate(() => window.__viewer.activeSide()) === 'B',
        '落图的那一侧成为活动侧（掩码/云量/任务状态跟着它）');
      assert(Math.abs(await page.evaluate(() => window.__viewer.splitRatio()) - ratioBefore) < 1e-12,
        '落图不动分隔比例');
      // 右格那张是在**非活动侧**解码完成的：旧守卫（`if (activeId === rec.id) fit()`）
      // 会漏掉它，让右格停在 {scale:1, ox:0, oy:0}（图缩在左上角）。ox>0 只有适配过才有。
      assert(p2[1].view.ox > 0 && Math.abs(p2[1].view.ox - p2[0].view.ox) < 1.5,
        `非活动侧解码完也各自适配过 (A.ox=${p2[0].view.ox.toFixed(1)} B.ox=${p2[1].view.ox.toFixed(1)})`);
      assert(await page.evaluate(() => !document.querySelectorAll('.cmp-half')[1].querySelector('.cmp-empty')),
        '右格占位消失');

      // 画布外落图：不新增、有提示、仍然 preventDefault
      const before4 = await page.evaluate(() => window.__viewer.recs().length);
      const outDrop = await drag.dropFiles(page, {
        files: [{ path: F('f32_grad.tif') }], clientX: pts.outside, clientY: pts.outsideY,
      });
      assert(outDrop.defaultPrevented === true && outDrop.after === before4,
        `画布外落图：接管但不新增 rec (${before4})`);
      const tt = await toastText();
      assert(tt.indexOf('只能把影像拖到画布上') >= 0, `画布外落图有提示（"${tt}"）`);
      assert((await panes())[1].recId === rec2.id, '画布外那一次没动到画面上这两张');

      // `.txt` 在任何模式、任何位置都照旧进待修复清单 —— 这是「dropEffect 永远 copy」
      // （而不是画布外设 'none'）的唯一理由：设成 none 会抑制 drop 事件。
      const txtPath = path.join(os.tmpdir(), 'sr-e2e-qc-' + process.pid + '.txt');
      fs.writeFileSync(txtPath,
        'JL1KF02B04_PMS03_20260917120612_200538728_101_0030_001_L1,\t'
        + '产品存在伪影 (问题类型:产品存在伪影 行列号:1,2 影像类型:PAN )\t李佳峻\n', 'utf8');
      const txtDrop = await drag.dropFiles(page, {
        files: [{ path: txtPath }], clientX: 200, clientY: 300, target: '.sidebar',
      });
      await sleep(400);
      const qs2 = await page.evaluate(() => window.__viewer.qcState());
      assert(txtDrop.defaultPrevented && qs2.loaded && qs2.total === 1,
        `分屏下把 .txt 拖到侧栏仍进待修复清单（${qs2.total} 行）`);
      await page.evaluate(() => window.__viewer.qcClose());
      fs.unlinkSync(txtPath);

      // 滚轮同步：在左半滚一格 → 两侧各按同一倍数缩放
      const v0 = await panes();
      await page.mouse.move(pts.left, pts.midY);
      await page.mouse.wheel({ deltaY: -100 });
      await sleep(200);
      const v1 = await panes();
      assert(Math.abs(v1[0].view.scale - v0[0].view.scale * 1.2) < 1e-9
        && Math.abs(v1[1].view.scale - v0[1].view.scale * 1.2) < 1e-9,
        `真实滚轮同步缩放两侧 (A ${v0[0].view.scale.toFixed(3)}→${v1[0].view.scale.toFixed(3)}, `
        + `B ${v0[1].view.scale.toFixed(3)}→${v1[1].view.scale.toFixed(3)})`);

      // 锚点守恒：自己派发一次滚轮，坐标可控。**两格都要守恒，且各自守恒在自己那一格的
      // 局部坐标系里** —— 这里抓到过一个真 bug：右格的锚点被算成 `rect.x + u*rect.w`
      // （多算一个左格宽），一滚右格整幅飞出视野。
      //
      // 坐标必须全用**整数 client 坐标**推：`MouseEvent.clientX/clientY` 的 IDL 类型是
      // `long`，构造时传小数会被取整；而处理函数是拿 `clientX - rect.left` 反推格局部的，
      // rect.left 本身是小数 —— 传 `r.left + 300` 进去，处理函数看到的是 299.8125，
      // 与测试这边以为的 300 差 0.19px，肉眼看着「守恒」但 1e-6 的断言会红。
      const anchor = await page.evaluate(() => {
        const c = document.querySelector('canvas.view-canvas');
        const r = c.getBoundingClientRect();
        const clientX = Math.round(r.left + 300), clientY = Math.round(r.top + 200);
        const mx = clientX - r.left, my = clientY - r.top;     // 与 onWheel 同源
        const pair = (p, x, y) => [(x - p.view.ox) / p.view.scale, (y - p.view.oy) / p.view.scale];
        const before = window.__viewer.cmpPanes();
        const A = before[0], B = before[1];
        const u = (mx - A.rect.x) / A.rect.w;                  // 指针在左格里的相对位置
        const bx = u * B.rect.w;                               // 右格里的同一相对位置（右格自己的局部坐标）
        c.dispatchEvent(new WheelEvent('wheel', {
          clientX, clientY, deltaY: -100, bubbles: true, cancelable: true,
        }));
        const after = window.__viewer.cmpPanes();
        return {
          a0: pair(A, mx, my), a1: pair(after[0], mx, my),
          b0: pair(B, bx, my), b1: pair(after[1], bx, my),
          sa0: A.view.scale, sa1: after[0].view.scale,
          sb0: B.view.scale, sb1: after[1].view.scale,
        };
      });
      const near = (p, q) => Math.abs(p[0] - q[0]) < 1e-9 && Math.abs(p[1] - q[1]) < 1e-9;
      assert(Math.abs(anchor.sa1 - anchor.sa0 * 1.2) < 1e-9
        && Math.abs(anchor.sb1 - anchor.sb0 * 1.2) < 1e-9,
        `两格各按同一倍数缩放（A ${anchor.sa0}→${anchor.sa1}, B ${anchor.sb0}→${anchor.sb1}）`);
      assert(near(anchor.a0, anchor.a1),
        `指针所在格：指针下的缩略图坐标守恒 `
        + `(A ${anchor.a0[0].toFixed(2)},${anchor.a0[1].toFixed(2)} → `
        + `${anchor.a1[0].toFixed(2)},${anchor.a1[1].toFixed(2)})`);
      assert(near(anchor.b0, anchor.b1),
        `另一格锚在「同一相对位置」（B 的缩略图坐标守恒 `
        + `${anchor.b0[0].toFixed(2)},${anchor.b0[1].toFixed(2)} → `
        + `${anchor.b1[0].toFixed(2)},${anchor.b1[1].toFixed(2)}）`);

      // 拖动同步：在左半按下 → 活动侧切到左格，两格同量平移
      await page.mouse.move(pts.left, pts.midY);
      await page.mouse.down();
      await sleep(80);
      assert(await page.evaluate(() => window.__viewer.activeSide()) === 'A',
        '在左半按下 → 活动侧切到左格');
      const v1b = await panes();          // 上面两次滚轮之后的基线，别用更早的 v1
      await page.mouse.move(pts.left + 40, pts.midY, { steps: 1 });
      await sleep(150);
      const v2 = await panes();
      assert(Math.abs(v2[0].view.ox - v1b[0].view.ox - 40) < 1e-9
        && Math.abs(v2[1].view.ox - v1b[1].view.ox - 40) < 1e-9,
        `拖动同量平移两侧 (A.ox ${v1b[0].view.ox.toFixed(1)}→${v2[0].view.ox.toFixed(1)}, `
        + `B.ox ${v1b[1].view.ox.toFixed(1)}→${v2[1].view.ox.toFixed(1)})`);
      assert(Math.abs(v2[0].view.oy - v1b[0].view.oy) < 1e-9, '只横向拖 → 纵向不动');
      await page.mouse.up();

      // 拖分隔线 / 改比例：只重新分配，不重新适配
      const scaleBefore = (await panes())[0].view.scale;
      await page.evaluate(() => window.__viewer.setSplitRatio(0.7));
      await sleep(200);
      const s7 = await panes();
      assert(Math.abs(await page.evaluate(() => window.__viewer.splitRatio()) - 0.7) < 1e-9
        && s7[0].rect.w > s7[1].rect.w,
        `比例 0.7 → 左格更宽 (${s7[0].rect.w} vs ${s7[1].rect.w})`);
      assert(Math.abs(s7[0].view.scale - scaleBefore) < 1e-9,
        '改比例不重新适配（露出同一变换的更多）');
      assert(await page.evaluate(() => localStorage.getItem('sr.viewer.cmpSplitRatio')) === '0.7',
        '分隔比例记进 localStorage');

      // 回正：比例回 0.5 且两侧重新适配
      await page.click('[data-e2e="cmp-reset"]');
      await sleep(200);
      const sReset = await panes();
      assert(Math.abs(await page.evaluate(() => window.__viewer.splitRatio()) - 0.5) < 1e-9,
        '回正 → 比例回 0.5');
      assert(sReset[0].view.ox > 0 && Math.abs(sReset[0].view.ox - sReset[1].view.ox) < 1.5,
        `回正 → 两侧重新适配 (ox ${sReset[0].view.ox.toFixed(1)} / ${sReset[1].view.ox.toFixed(1)})`);

      // 分隔线抓取带的判定阈值：按在带上但没挪过 4px = 什么都不做（比例不动、画面也不动）。
      // 没有阈值时按下就先按指针位置改一次比例，而刚进分屏时线正好落在画布正中 —— 随手在
      // 中间按下拖动很容易压在抓取带上：那一下既不平移，比例还跟着指针跳（用户看到的
      // 「图不动、左右在换」）。过阈值之后按**位移增量**改比例，线不再被吸到指针上。
      const divGeo = await page.evaluate(() => {
        const c = document.querySelector('canvas.view-canvas').getBoundingClientRect();
        return {
          x: Math.round(c.left + window.__viewer.splitX()),
          y: Math.round(c.top + c.height / 2),
          w: c.width,
        };
      });
      const ratio0 = await page.evaluate(() => window.__viewer.splitRatio());
      const vTap0 = await panes();
      await page.mouse.move(divGeo.x + 3, divGeo.y);
      await page.mouse.down();
      await page.mouse.move(divGeo.x + 5, divGeo.y, { steps: 1 });     // 阈值内
      await sleep(150);
      const ratioTap = await page.evaluate(() => window.__viewer.splitRatio());
      await page.mouse.up();
      await sleep(80);
      const vTap1 = await panes();
      assert(Math.abs(ratioTap - ratio0) < 1e-12,
        `抓取带内按下、挪不到 4px → 比例不动 (${ratio0} → ${ratioTap})`);
      assert(Math.abs(vTap1[0].view.ox - vTap0[0].view.ox) < 1e-12,
        '阈值内那一按什么都不做（画面也不动）');

      await page.mouse.move(divGeo.x + 3, divGeo.y);
      await page.mouse.down();
      await page.mouse.move(divGeo.x + 23, divGeo.y, { steps: 1 });    // 挪 20px
      await sleep(150);
      const ratioDrag = await page.evaluate(() => window.__viewer.splitRatio());
      await page.mouse.up();
      await sleep(80);
      const byDelta = ratio0 + 20 / divGeo.w;        // 按位移增量
      const byPointer = ratio0 + 23 / divGeo.w;      // 按指针绝对位置（旧行为，会多算按下时那 3px）
      assert(Math.abs(ratioDrag - byDelta) < 1e-6,
        `过阈值后按位移增量改比例 (期望 ${byDelta.toFixed(4)}，实际 ${ratioDrag.toFixed(4)}）`);
      assert(Math.abs(ratioDrag - byPointer) > 1e-4,
        `线不再被吸到指针上（按绝对位置会得 ${byPointer.toFixed(4)}）`);
      await page.click('[data-e2e="cmp-reset"]');    // 回正，别把改过的比例留给后面的用例
      await sleep(200);

      // 点选对比：整块画布一个落位区，拖入即覆盖当前这张
      await page.click('[data-e2e="cmp-mode-click"]');
      await sleep(300);
      const pClick = await panes();
      assert(await page.evaluate(() => window.__viewer.cmpMode()) === 'click'
        && pClick.length === 1 && pClick[0].rect.x === 0,
        '点选对比：回到单格、不分左右');
      const railClick = await rail();
      assert(railClick.open === true && railClick.prev === null,
        '退出分屏 → 右栏恢复成进入前的展开状态');
      // 布局变了（右栏恢复展开 → 画布变窄），落点要重新量，不能沿用分屏时那套坐标
      const pts2 = await drag.canvasPoints(page);
      const overClick = await drag.dragOverOnly(page, {
        files: [{ path: F('f32_grad.tif') }], clientX: pts2.right, clientY: pts2.midY,
      });
      assert(overClick.hint.side === 'A', '点选对比：落位区是整块画布（只有一个归属）');
      const hd2 = await hintDom();
      assert(hd2 && hd2.text.indexOf('覆盖当前这张') >= 0,
        `点选对比的提示说清是覆盖（"${hd2 && hd2.text}"）`);

      const before5 = await page.evaluate(() => window.__viewer.recs().length);
      const overwrite = await drag.dropFiles(page, {
        files: [{ path: F('f32_grad.tif') }], clientX: pts2.right, clientY: pts2.midY,
      });
      assert(overwrite.after === before5 + 1, `点选对比：拖入新增一条 (${before5}→${overwrite.after})`);
      const rec4 = await waitDecoded(page, 30000);
      const p3 = await panes();
      assert(p3.length === 1 && p3[0].recId === rec4.id && rec4.name === 'f32_grad.tif',
        '画面上换成新拖入的那张（原来是哪张就顶掉哪张）');
      assert(await page.evaluate(() => window.__viewer.recs().length) === before5 + 1,
        '被顶掉的那张仍在文件列表里');
      // 顶掉的那张（点选前活动侧的 rec1）确实已经不在画面上了
      assert(p3[0].recId !== rec1.id, '当前这张被顶掉（不是两张同时在画面上）');

      // 点选清单：显示在《待修复清单》上方，点一行换到活动侧，「清除」只移出清单
      const cl = await page.evaluate(() => {
        const rows = [...document.querySelectorAll('[data-e2e="cmp-row"]')];
        const count = document.querySelector('[data-e2e="cmp-count"]');
        const qc = document.querySelector('.qc-sec') || document.querySelector('.qc-list');
        const list = document.querySelector('[data-e2e="cmp-list"]');
        return {
          list: window.__viewer.cmpList(),
          rows: rows.length,
          count: count ? count.textContent.trim() : null,
          recs: window.__viewer.recs().length,
          // 清单在《待修复清单》上面：同一个滚动容器里 DOM 顺序在前
          above: !!(list && qc) ? !!(list.compareDocumentPosition(qc) & Node.DOCUMENT_POSITION_FOLLOWING) : null,
        };
      });
      assert(cl.list.length === cl.rows && cl.list.length === cl.recs && cl.rows >= 3,
        `点选清单列出当前文件列表全体（${cl.rows} 行，计数 ${cl.count}）`);
      assert(cl.above === true, '点选清单排在《待修复清单》上方');

      // 点第一行 → 画面上换成它
      await page.evaluate(() => {
        document.querySelectorAll('[data-e2e="cmp-row"]')[0].click();
      });
      await sleep(300);
      const p4 = await panes();
      assert(p4[0].recId === cl.list[0], `点清单一行 → 画面上换成那张 (rec ${cl.list[0]})`);

      // 「清除」：只把这一条移出清单，屏幕/文件列表/像素都不动。
      // **清的是非活动的那一行**：行内「清除」少写了 `.stop` 的话，行自己的
      // `activate` 会跟着触发，活动侧就会被换掉 —— 清活动行看不出这个区别。
      const keep = await page.evaluate(() => ({
        active: window.__viewer.activeRec().id, recs: window.__viewer.recs().length,
      }));
      await page.evaluate(() => {
        document.querySelectorAll('[data-e2e="cmp-row"]')[1]
          .querySelector('[data-e2e="cmp-row-clear"]').click();
      });
      await sleep(250);
      const afterClear = await page.evaluate(() => ({
        list: window.__viewer.cmpList(),
        recs: window.__viewer.recs().length,
        active: window.__viewer.activeRec().id,
        pane: window.__viewer.cmpPanes()[0].recId,
      }));
      assert(afterClear.list.length === cl.list.length - 1
        && afterClear.list.indexOf(cl.list[1]) < 0, '「清除」把这一条移出清单');
      assert(afterClear.recs === keep.recs && afterClear.active === keep.active
        && afterClear.pane === keep.active,
        '「清除」不动文件列表条目、不动像素、不动屏幕上那张');

      // 回「关闭」：单格、叠加层撤掉、清单清空，右栏保持展开
      await page.click('[data-e2e="cmp-mode-off"]');
      await sleep(300);
      const off = await page.evaluate(() => ({
        mode: window.__viewer.cmpMode(),
        panes: window.__viewer.cmpPanes().length,
        list: window.__viewer.cmpList(),
        bar: !!document.querySelector('[data-e2e="cmp-bar"]'),
        overlay: !!document.querySelector('[data-e2e="cmp-overlay"]'),
        listDom: !!document.querySelector('[data-e2e="cmp-list"]'),
      }));
      assert(off.mode === 'off' && off.panes === 1, '回「关闭」→ 单格');
      assert(off.overlay === false, '回「关闭」→ 叠加层撤掉（画布上不再有分隔线/提示）');
      assert(off.list.length === 0 && off.listDom === false, '回「关闭」→ 点选清单清空并撤掉');
      assert(off.bar === true, '对比条仍展开（模式回关闭，条不收）');

      // 关闭模式下全窗口拖放行为一字未改：拖到侧栏（画布之外）照样打开
      const before6 = await page.evaluate(() => window.__viewer.recs().length);
      const sideDrop = await drag.dropFiles(page, {
        files: [{ path: F('u16_whitezero.tif') }], clientX: 200, clientY: 300, target: '.sidebar',
      });
      assert(sideDrop.after === before6 + 1,
        `关闭模式：拖到侧栏也能打开（落点不参与决策）(${before6}→${sideDrop.after})`);
      await waitDecoded(page, 30000);
    }


    console.log('--- I. 设置浮层（右上角） ---');
    {
      assert(await page.evaluate(() => !!document.querySelector('[data-e2e="set-open"]')),
        '工具栏右端有「设置」入口');
      assert(await page.evaluate(() => !document.querySelector('[data-e2e="set-panel"]')),
        '默认没有浮层（不点不出现）');

      await page.click('[data-e2e="set-open"]');
      await sleep(200);
      const st = await page.evaluate(() => {
        const p = document.querySelector('[data-e2e="set-panel"]');
        const sw = document.querySelector('[data-e2e="set-prefetch"]');
        const line = document.querySelector('[data-e2e="set-cache-line"]');
        return {
          panel: !!p,
          role: p ? p.getAttribute('role') : null,
          sw: !!sw,
          swRole: sw ? sw.getAttribute('role') : null,
          checked: sw ? sw.getAttribute('aria-checked') : null,
          line: line ? line.textContent.trim() : null,
          clear: !!document.querySelector('[data-e2e="set-cache-clear"]'),
          btnOn: !!document.querySelector('[data-e2e="set-open"].on'),
        };
      });
      assert(st.panel && st.role === 'dialog' && st.btnOn,
        '点开后出浮层（role=dialog），入口呈选中态');
      assert(st.sw && st.swRole === 'switch' && st.checked === 'false',
        '预取开关默认关（首次、没记过）');
      assert(st.line && /^\d+ 项 \/ [\d.]+ MB$/.test(st.line) && st.clear,
        `缓存行与「清空」按钮都在（"${st.line}"）`);

      // 打开开关：立刻写 localStorage（重新载入后仍是开）
      await page.click('[data-e2e="set-prefetch"]');
      await sleep(150);
      assert(await page.evaluate(() => document.querySelector('[data-e2e="set-prefetch"]')
        .getAttribute('aria-checked')) === 'true'
        && await page.evaluate(() => localStorage.getItem('sr.viewer.cmpPrefetch')) === '1',
        '开关打开 → aria-checked=true 且记进 localStorage');

      await page.click('[data-e2e="set-cache-clear"]');
      await sleep(150);
      const zeroed = await page.evaluate(() =>
        document.querySelector('[data-e2e="set-cache-line"]').textContent.trim());
      assert(zeroed.indexOf('0 项') === 0, `「清空」后缓存行归零（"${zeroed}"）`);

      await page.keyboard.press('Escape');
      await sleep(200);
      assert(await page.evaluate(() => !document.querySelector('[data-e2e="set-panel"]')),
        'Esc 关闭浮层');

      // 再开一次：状态还在；然后点浮层之外关闭（画布中心那一下该被透明捕手吃掉）
      await page.click('[data-e2e="set-open"]');
      await sleep(200);
      assert(await page.evaluate(() => document.querySelector('[data-e2e="set-prefetch"]')
        .getAttribute('aria-checked')) === 'true', '重开浮层：开关状态还在');
      const cen = await page.evaluate(() => {
        const r = document.querySelector('canvas.view-canvas').getBoundingClientRect();
        return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
      });
      await page.mouse.click(cen.x, cen.y);
      await sleep(200);
      assert(await page.evaluate(() => !document.querySelector('[data-e2e="set-panel"]')),
        '点浮层之外关闭');

      // 收尾复位：后面的段不该带着「预取开」跑
      await page.evaluate(() => window.__viewer.setCmpPrefetch(false));
      assert(await page.evaluate(() => localStorage.getItem('sr.viewer.cmpPrefetch')) === '0'
        && await page.evaluate(() => window.__viewer.cmpPrefetchOn()) === false,
        '开关复位成默认关（存 0 也算关）');
    }

    console.log('--- J. 对比模式换图保持视图 ---');
    {
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      await sleep(200);

      const panes = () => page.evaluate(() => window.__viewer.cmpPanes());
      const viewOf = async () => (await panes())[0].view;
      const rectOf = async () => (await panes())[0].rect;
      /** fitView 的等价副本（判据在 lib/viewMath.fitView：min(cw/tw, ch/th, 1) + 居中）。 */
      const fitOf = (tw, th, r) => {
        const s = Math.min(r.w / tw, r.h / th, 1);
        return { scale: s, ox: (r.w - tw * s) / 2, oy: (r.h - th * s) / 2 };
      };
      const near3 = (a, b) => Math.abs(a.scale - b.scale) < 1e-9
        && Math.abs(a.ox - b.ox) < 1e-9 && Math.abs(a.oy - b.oy) < 1e-9;
      /** 点文件列表一行（关闭模式下换图只有这条入口）。 */
      const clickFile = async (id) => {
        const ok = await page.evaluate((rid) => {
          const i = window.__viewer.recs().findIndex((r) => r.id === rid);
          const rows = [...document.querySelectorAll('.file-item')];
          if (i < 0 || !rows[i]) return false;
          rows[i].click();
          return true;
        }, id);
        assert(ok, `文件列表里有 rec ${id}`);
        await sleep(300);
      };
      /** 点「点选清单」一行（对比模式下才有这一块）。 */
      const clickRow = async (id) => {
        const ok = await page.evaluate((rid) => {
          const i = window.__viewer.cmpList().indexOf(rid);
          const rows = [...document.querySelectorAll('[data-e2e="cmp-row"]')];
          if (i < 0 || !rows[i]) return false;
          rows[i].click();
          return true;
        }, id);
        assert(ok, `点选清单里有 rec ${id}`);
        await sleep(300);
      };
      /** 把当前这张推到非默认形态：真实滚轮放大 + 真实拖动平移。 */
      const nudge = async () => {
        const c = await page.evaluate(() => {
          const r = document.querySelector('canvas.view-canvas').getBoundingClientRect();
          return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
        });
        await page.mouse.move(c.x, c.y);
        await page.mouse.wheel({ deltaY: -100 });
        await sleep(150);
        await page.mouse.down();
        await page.mouse.move(c.x + 60, c.y + 30, { steps: 1 });
        await page.mouse.up();
        await sleep(200);
      };

      const info = await page.evaluate(() => window.__viewer.recs()
        .map((r) => ({ id: r.id, name: r.name, tw: r.thumbW, th: r.thumbH }))
        .filter((r) => r.tw > 0 && r.th > 0));
      const key = (r) => r.tw + '×' + r.th;
      let pair = null;
      for (let i = 0; i < info.length && !pair; i++) {
        for (let j = i + 1; j < info.length; j++) {
          if (key(info[i]) === key(info[j])) { pair = [info[i], info[j]]; break; }
        }
      }
      const diff = pair ? info.find((r) => key(r) !== key(pair[0])) : null;
      assert(!!pair && !!diff, '既有同缩略图尺寸的两张、也有尺寸不同的一张（'
        + info.map((r) => r.name + '=' + key(r)).join(', ') + '）');

      // 1) 关闭模式基线：换图仍旧「看全幅」——这条守住「关闭模式行为一字不改」
      await clickFile(pair[0].id);
      await nudge();
      const movedOff = await viewOf();
      const fit0 = fitOf(pair[0].tw, pair[0].th, await rectOf());
      assert(!near3(movedOff, fit0),
        `关闭模式：视图已被推到非默认形态 (scale ${fit0.scale.toFixed(3)}→${movedOff.scale.toFixed(3)})`);
      await clickFile(pair[1].id);
      const vOff = await viewOf();
      assert(near3(vOff, fitOf(pair[1].tw, pair[1].th, await rectOf())),
        `关闭模式换图仍然重新适配整幅 (scale=${vOff.scale.toFixed(4)})`);

      // 2) 进「点选对比」：同尺寸换图 → 缩放与位置一个数都不动
      await page.evaluate(() => window.__viewer.setCmpMode('click'));
      await sleep(250);
      await clickRow(pair[0].id);
      await nudge();
      const vA = await viewOf();
      const rA = await rectOf();
      assert(!near3(vA, fitOf(pair[0].tw, pair[0].th, rA)),
        `对比模式：视图已被推到非默认形态 (scale=${vA.scale.toFixed(4)})`);
      await clickRow(pair[1].id);
      const vB = await viewOf();
      assert(vA.scale === vB.scale && vA.ox === vB.ox && vA.oy === vB.oy,
        `两张缩略图同尺寸 → 换图后视图逐字不变 `
        + `(A ${vA.scale}/${vA.ox.toFixed(2)}/${vA.oy.toFixed(2)} → `
        + `B ${vB.scale}/${vB.ox.toFixed(2)}/${vB.oy.toFixed(2)})`);

      // 3) 换到尺寸不同的那张：归一化视野守恒（看的是同一片相对区域），scale 确实变了
      await clickRow(diff.id);
      const vC = await viewOf();
      const rC = await rectOf();
      const nw = (v, r, tw) => r.w / (v.scale * tw);
      const nl = (v, tw) => -v.ox / (v.scale * tw);
      const nt = (v, th) => -v.oy / (v.scale * th);
      assert(Math.abs(nw(vC, rC, diff.tw) - nw(vB, rA, pair[1].tw)) < 1e-9
        && Math.abs(nl(vC, diff.tw) - nl(vB, pair[1].tw)) < 1e-9
        && Math.abs(nt(vC, diff.th) - nt(vB, pair[1].th)) < 1e-9,
        `换到不同尺寸：归一化可视范围守恒 (${nw(vC, rC, diff.tw).toFixed(4)} vs `
        + `${nw(vB, rA, pair[1].tw).toFixed(4)})`);
      assert(Math.abs(vC.scale - vB.scale) > 1e-9,
        `换了张尺寸不同的图 → scale 确实变了 (${vB.scale.toFixed(4)}→${vC.scale.toFixed(4)})`);

      // 4) 回「关闭」：换图又是「看全幅」
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      await sleep(250);
      await clickFile(pair[0].id);
      const vOff2 = await viewOf();
      assert(near3(vOff2, fitOf(pair[0].tw, pair[0].th, await rectOf())),
        `回「关闭」后换图重新适配 (scale=${vOff2.scale.toFixed(4)})`);
      assert(await page.evaluate(() => window.__viewer.cmpMode()) === 'off',
        '收尾：模式回「关闭」（后面的断言不该带着对比模式跑）');
    }

    assert(errors.length === 0, `无浏览器错误 (${JSON.stringify(errors.slice(0, 5))})`);
    console.log(`\n[test-vue-viewer] ✅ 全部通过 (${pass} 项断言)`);
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((err) => {
  console.error('\n[test-vue-viewer] ❌ 失败:', err.message);
  process.exit(1);
});
