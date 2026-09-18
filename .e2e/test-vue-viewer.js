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
const path = require('path');
const { launchPage } = require('./launchBrowser');

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

// 上传文件并等待解码完成，返回 activeRec 摘要
async function uploadAndWait(page, filePath, timeoutMs) {
  const input = await page.$('input[type=file]');
  if (!input) throw new Error('未找到 input[type=file]');
  await input.uploadFile(filePath);
  const t0 = Date.now();
  for (;;) {
    const rec = await page.evaluate(() => window.__viewer.activeRec());
    if (rec && rec.status) {
      if (rec.status.indexOf('完成') === 0) return rec;
      if (rec.status.indexOf('解码失败') === 0) throw new Error('解码失败: ' + rec.status);
    }
    if (Date.now() - t0 > (timeoutMs || 30000)) throw new Error('超时等待解码，最近状态: ' + (rec && rec.status));
    await sleep(200);
  }
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
    for (const k of ['planExport', 'enterDraw', 'exitDraw', 'buildMaskJson', 'exportMaskJson',
      'thumbToOrig', 'getRois', 'genMask', 'wandSelect', 'maskGen', 'setSparseMin',
      'setDrawTool', 'commitRect', 'undoRoi', 'clearRois', 'mergeRois', 'delClick',
      'openSceneJpg', 'openLocalImage', 'submitSr', 'recs', 'activeRec',
      'qcImport', 'qcClose', 'qcSetStatus', 'qcOutput', 'qcState', 'qcOpenByName']) {
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
      // 定位原图中心 (128, 64) → 视图居中 + 红叉 marker
      await page.evaluate(() => {
        const inputs = document.querySelectorAll('.loc input');
        const setVal = (el, v) => { el.value = v; el.dispatchEvent(new Event('input')); };
        setVal(inputs[0], '128');
        setVal(inputs[1], '64');
      });
      await page.click('.loc-btn');
      await sleep(250);
      const centerRed = await page.evaluate(() => {
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
      assert(centerRed > 0, `定位后中心出现红叉 marker (红像素 ${centerRed})`);

      // 粘「X,Y」一对数进 X 框（掩膜中心点坐标那种形态）→ 拆成两个框 → 定位到 (64,32)
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
      await page.evaluate(() => {
        const x = document.querySelectorAll('.loc input')[0];
        x.value = '64,32';
        x.dispatchEvent(new Event('input'));
      });
      await sleep(150);
      const pairVals = await page.evaluate(() =>
        [...document.querySelectorAll('.loc input')].map((el) => el.value));
      assert(pairVals[0] === '64' && pairVals[1] === '32',
        `粘「64,32」→ 两个框各得一半 (${pairVals.join(' / ')})`);
      await page.click('.loc-btn');
      await sleep(250);
      assert(await sampleCenterRed() > 0, '拆开后的「定位」跳到 (64,32)（中心出现红叉）');

      // 拷了整行（三个数）不许当坐标对：截前两个会把标记跳到别处，必须出声
      await page.evaluate(() => {
        const x = document.querySelectorAll('.loc input')[0];
        x.value = '1,64,32';
        x.dispatchEvent(new Event('input'));
      });
      await sleep(150);
      const badPair = await page.evaluate(() => {
        const err = document.querySelector('.err-box');
        return {
          vals: [...document.querySelectorAll('.loc input')].map((el) => el.value),
          err: err ? err.textContent.trim() : '',
        };
      });
      assert(badPair.err.indexOf('坐标对认不出来') >= 0 && badPair.vals[0] === '1,64,32',
        `三个数不猜、按原样留着并报错 (${badPair.err.slice(0, 20)}…)`);

      await sleep(7600);   // marker 7s 过期
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
