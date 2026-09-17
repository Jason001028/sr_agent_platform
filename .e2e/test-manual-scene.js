// 盘阵任意场景目录：打开 → 画掩码 → 写入盘阵 → 就地提交 SR（手工入口浏览器回归）
// ---------------------------------------------------------------------------
// 前置：`cd frontend && npm run build`（本测试读 frontend/dist）。
// 拓扑：真 uvicorn + dist 静态服务；页面经 evaluateOnNewDocument 注入
//       window.__SR_CFG__ = { apiBase, staticBase: '' }。
//       盘阵根是临时目录：SR_DRIVE_MAP 把 `W:\` 映射到它（用户粘贴的是 Windows
//       形态），SR_ALLOWED_ROOTS 同时列 `W:\` 与临时目录本身 —— 后者里那条
//       "宿主盘符映射到自身"的开发机补丁与 backend/tests/__init__.py::
//       allowed_roots_env 同一套（Linux 上 Path(r).drive 为空，不生效）。
// 覆盖：
//   A. 场景库页：输入框预填**当天**前缀 → 粘 `W:\GSHC2IMPS\PRODUCT\<y>\<m>\<d>\<编号>`
//      → 「打开」（不扫盘：只 resolve 这一次）→ 页内跳查看器 → rec 带上盘阵目录；
//   B. 画矩形 → 「保存掩码到盘阵」→ Node 侧断言 `<场景目录>/<编号>_mask.tif` 落盘；
//   C. 「提交 SR」→ /queue 预填（不自动提交）→ 确认提交 → 假调度器跑到「完成」；
//   D. 猜错必须报错：粘不存在的编号 → `.sp-err` 带候选路径与原因，查看器不新增 rec；
//   E. 本地文件反推：文件名带成像时间戳 → 命中关联（提交按钮转可用）；目录不存在 →
//      报错且按钮仍禁用；文件名里没有日期 → 只提示手填，一个 resolve 请求都不发；
//   F. PAN.tif（RC）场景：掩码名取**输入名的 stem**（`PAN_mask.tif`），不取目录名 ——
//      这正是"写出去的掩码与提交时去找的那份不一致"的陷阱（§6）。
//   G. 粘**单个 .tif 文件路径**（不在场景目录里）：照样能看，且预览按 1/2 烤进源图
//      自己的目录；但它不能提交 SR（lqPath 为 null → 按钮禁用）。
// 用法：cd .e2e && node test-manual-scene.js
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, spawnSync } = require('child_process');
const { launchPage } = require('./launchBrowser');

const REPO = path.resolve(__dirname, '..');
const DIST = path.join(REPO, 'frontend', 'dist');
const LOCAL_TIF = path.join(REPO, 'frontend', 'fixtures', 'gray16_grad.tif');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pass = 0;
function assert(cond, msg) {
  if (!cond) throw new Error('断言失败: ' + msg);
  console.log('  ✓ ' + msg);
  pass++;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port;
      s.close(() => resolve(p));
    });
  });
}

async function waitFor(page, fn, timeoutMs = 15000, label = 'waitFor', ...args) {
  const t0 = Date.now();
  for (;;) {
    const v = await page.evaluate(fn, ...args);
    if (v) return v;
    if (Date.now() - t0 > timeoutMs) throw new Error(`超时等待 ${label}`);
    await sleep(150);
  }
}

/** node 侧轮询（页面里读不到的东西，如文件是否落盘） */
async function waitNode(fn, timeoutMs, label) {
  const t0 = Date.now();
  for (;;) {
    if (fn()) return;
    if (Date.now() - t0 > timeoutMs) throw new Error('超时等待 ' + label);
    await sleep(100);
  }
}

async function waitHealth(url, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return; } catch { /* 未起 */ }
    if (Date.now() - t0 > ms) throw new Error('后端 /api/health 超时: ' + url);
    await sleep(200);
  }
}

/* ---------------- dist 静态服务（手工场景的图走 API，不需要 /disk-array 别名） ---------------- */
function startStaticServer() {
  const server = http.createServer((req, res) => {
    let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (urlPath === '/') urlPath = '/index.html';
    let filePath = path.join(DIST, urlPath);
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(DIST, 'index.html');     // createWebHistory 回退
    }
    fs.readFile(filePath, (err, data) => {
      if (err) {
        res.writeHead(err.code === 'ENOENT' ? 404 : 500,
          { 'content-type': 'text/plain; charset=utf-8' });
        res.end(String(err.code || err));
        return;
      }
      res.writeHead(200, {
        'content-type': MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

/* ---------------- 盘阵 fixture：真机布局 <根>/GSHC2IMPS/PRODUCT/<y>/<m>/<d>/<编号> ---------------- */
// 每个场景目录含 `<编号>.tif`（SC）或 `PAN.tif`（RC）+ `<目录名>_meta.xml` —— 正是
// scene_search 的判据。**故意不放掩码**：手工入口要验的就是"没有掩码 → 现画 → 写到
// 服务端"，90% 的生产场景就是这个状态。
const FIXTURE_PY = `
import os, sys
import numpy as np, tifffile

root, ymd, sc_name, pan_name, prod_name = sys.argv[1:6]

def tif(p, w, h):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    arr = (np.arange(w * h, dtype=np.uint32).reshape(h, w) % 4096).astype(np.uint16)
    tifffile.imwrite(p, arr, photometric="minisblack")

def scene(d, image_name, w, h, scene_name):
    os.makedirs(d, exist_ok=True)
    tif(os.path.join(d, image_name), w, h)
    with open(os.path.join(d, scene_name + "_meta.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>'
                "<SolarAzimuth>181.79</SolarAzimuth>")
    return d

base = os.path.join(root, "GSHC2IMPS", "PRODUCT", ymd[:4], ymd[4:6], ymd[6:8])
scene(os.path.join(base, sc_name), sc_name + ".tif", 1600, 800, sc_name)
scene(os.path.join(base, pan_name), "PAN.tif", 640, 320, pan_name)

# 生产树真形态：<日>/<卫星型号>/<段级目录>/<景级目录>（多两层）。
# 段级目录名 = 景级目录名去掉「景号」那一段（真机实测的层级关系）。
tok = prod_name.split("_")
prod_dir = os.path.join(base, tok[0], "_".join(tok[:5] + tok[6:]), prod_name)
scene(prod_dir, "PAN.tif", 512, 256, prod_name)

# 裸 TIF（G 段）：**不在任何场景目录里**（没有 _meta.xml、父目录也不是场景名），
# 用来验「粘单个 .tif 文件路径」。400×200 是特意选的：旧规则（长边 8192 封顶）
# 会把它整幅留下（400×200），新规则（各边 1/2）烤出 200×100 —— 尺寸断言因此
# 能区分两套规则，而不是两边都给同一个值。
tif(os.path.join(root, "loose", "LOOSE_" + ymd + "120000.tif"), 400, 200)
`;

function makeFixtures(root, ymd, scName, panName, prodName) {
  const r = spawnSync('python',
    ['-c', FIXTURE_PY, root, ymd, scName, panName, prodName],
    { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('fixture 生成失败: ' + (r.stderr || r.stdout));
}

/* ---------------- 页面助手 ---------------- */
async function clickByText(page, text) {
  const ok = await page.evaluate((t) => {
    const b = [...document.querySelectorAll('button')]
      .find((x) => x.textContent.trim() === t && !x.disabled);
    if (!b) return false;
    b.click();
    return true;
  }, text);
  if (!ok) throw new Error(`按钮未找到或已禁用: ${text}`);
}

/** 点顶部导航的 RouterLink（页内跳转，保 pinia store） */
async function clickLink(page, text) {
  const ok = await page.evaluate((t) => {
    const a = [...document.querySelectorAll('a')].find((x) => x.textContent.trim() === t);
    if (!a) return false;
    a.click();
    return true;
  }, text);
  if (!ok) throw new Error(`导航链接未找到: ${text}`);
}

/** 写盘阵场景栏（.spb-in）输入框并点「打开」 */
async function openPathBar(page, value) {
  const set = await page.evaluate((v) => {
    const el = document.querySelector('.spb-in');
    if (!el) return false;
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));   // 驱动 v-model
    return true;
  }, value);
  if (!set) throw new Error('未找到 .spb-in（盘阵场景栏）');
  // 查看器侧的「打开」会跟着 viewer.busy 一起禁用（上一张图还在解码时点不动），
  // 所以先等它可用 —— 否则断言会误报成"打不开"。
  await waitFor(page, () => {
    const b = [...document.querySelectorAll('.spb button')]
      .find((x) => x.textContent.trim() === '打开');
    return !!(b && !b.disabled);
  }, 30000, '「打开」按钮可用');
  await clickByText(page, '打开');
}

const readPathBar = (page) =>
  page.evaluate(() => {
    const el = document.querySelector('.spb-in');
    return el ? el.value : null;
  });

/** 读 .qp-form 某字段（label 的 span 以 label 开头即命中，避免 lq/mask 串位） */
async function readField(page, label) {
  return page.evaluate((l) => {
    const lab = [...document.querySelectorAll('.qp-form label')]
      .find((x) => { const s = x.querySelector('span'); return s && s.textContent.trim().startsWith(l); });
    if (!lab) return { ok: false, value: null };
    const inp = lab.querySelector('input');
    return inp ? { ok: true, value: inp.value } : { ok: false, value: null };
  }, label);
}

/** 工具栏两个按钮的可用性（提交 SR / 保存掩码到盘阵） */
const toolbarState = (page) =>
  page.evaluate(() => {
    const btns = [...document.querySelectorAll('.toolbar button')];
    const on = (t) => {
      const b = btns.find((x) => x.textContent.trim() === t);
      return b ? !b.disabled : null;
    };
    return { sr: on('提交 SR'), bake: on('保存掩码到盘阵') };
  });

const recCount = (page) =>
  page.evaluate(() => (window.__viewer ? window.__viewer.recs().length : -1));

/** is a real TIFF file?（掩码落盘只看"是不是一张真图"，不解析内容） */
function isTiff(file) {
  if (!fs.existsSync(file)) return false;
  const fd = fs.openSync(file, 'r');
  try {
    const b = Buffer.alloc(4);
    fs.readSync(fd, b, 0, 4, 0);
    return (b[0] === 0x49 && b[1] === 0x49 && b[2] === 0x2a)
      || (b[0] === 0x4d && b[1] === 0x4d && b[2] === 0x00);
  } finally {
    fs.closeSync(fd);
  }
}

function isIgnorableConsole(msg) {
  return /AbortError|ERR_ABORTED|queue\/events|net::ERR/i.test(msg);
}

/** 读一张 JPEG 的**像素**尺寸（Node 没有解码器，借 fixture 用的那个 Python + Pillow）。
    读不到返回 null —— 调用处用断言把「没生成」和「尺寸不对」分开报。 */
function jpegSize(p) {
  if (!fs.existsSync(p)) return null;
  const r = spawnSync('python', ['-c',
    'import sys;from PIL import Image;im=Image.open(sys.argv[1]);print(im.width, im.height)',
    p], { encoding: 'utf-8' });
  if (r.status !== 0) return null;
  const m = r.stdout.trim().split(/\s+/).map(Number);
  return m.length === 2 && m.every((n) => Number.isFinite(n)) ? { w: m[0], h: m[1] } : null;
}

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sr-e2e-manual-'));
  // 当天（=「今天」按钮与输入框预填的口径）。fixture 与粘贴的路径都用它，
  // 测试才不会因为跨日 / 固定日期而失效。
  const now = new Date();
  const y = String(now.getFullYear());
  const mo = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  const ymd = y + mo + d;

  const TMP_POSIX = tmp.replace(/\\/g, '/');
  const ARRAY = path.join(tmp, 'GSHC2IMPS', 'PRODUCT', y, mo, d);
  const ARRAY_POSIX = TMP_POSIX + '/GSHC2IMPS/PRODUCT/' + y + '/' + mo + '/' + d;
  const WIN_PREFIX = 'W:\\GSHC2IMPS\\PRODUCT\\' + y + '\\' + mo + '\\' + d;

  const SC = 'JL1KF02B03_PMS02_' + ymd + '124710_200536960_101_0005_001_L1_PAN';
  const PAN = 'JL1KF02B03_PMS02_' + ymd + '130500_200536960_101_0005_001_L1_PAN';
  // 有日期、盘阵上不存在 —— 用来验"猜错必须报错"
  const MISSING = 'JL1KF02B03_PMS02_' + ymd + '135900_200536960_101_0005_001_L1_PAN';
  const SC_DIR = path.join(ARRAY, SC);
  const PAN_DIR = path.join(ARRAY, PAN);
  // 生产树（多两层）用的**合成短名**：Windows 路径上限 260，而 os.tmpdir()
  // 前缀已占 ~70 字符 —— 用真机那种 57 字符的名字再加两层目录必然超限
  // （本机 >250 就 FileNotFoundError）。名字仍符合生产命名规则（段号 3 位、
  // 景号 4 位），段级目录名 = 去掉景号那一段。
  const PROD = 'A_B_' + ymd + '124710_200536960_102_0025_001';
  const PROD_MID = 'A_B_' + ymd + '124710_200536960_102_001';
  const PROD_DIR = path.join(ARRAY, 'A', PROD_MID, PROD);

  const datahub = path.join(tmp, 'datahub');
  const workDir = path.join(tmp, 'work');
  const upDir = path.join(tmp, 'upload');
  fs.mkdirSync(datahub, { recursive: true });
  fs.mkdirSync(workDir, { recursive: true });
  fs.mkdirSync(upDir, { recursive: true });
  makeFixtures(tmp, ymd, SC, PAN, PROD);

  // 上传用的本地 TIF：真图（能解码），只改文件名 —— 反推只用文件名里的时间戳。
  const upOk = path.join(upDir, SC + '.tif');
  const upProd = path.join(upDir, PROD + '.tif');
  const upMiss = path.join(upDir, MISSING + '.tif');
  const upNoDate = path.join(upDir, 'local_nodate.tif');
  fs.copyFileSync(LOCAL_TIF, upOk);
  fs.copyFileSync(LOCAL_TIF, upProd);
  fs.copyFileSync(LOCAL_TIF, upMiss);
  fs.copyFileSync(LOCAL_TIF, upNoDate);

  // 开发机是 Windows：临时目录带盘符，而盘阵路径一律经 pathguard 归一，所以既要
  // 把 `W:\` 映射到盘阵根，也得补一条"宿主盘符映射到自身"（backend/tests/__init__.py
  // ::allowed_roots_env 同一套规则，Linux 上不生效）。
  const hostDrive = path.parse(tmp).root.replace(/[\\/]+$/, '');   // "C:"
  const posixTmp = '/' + TMP_POSIX.replace(/^\/+/, '');
  const drives = ['W:=' + TMP_POSIX, hostDrive + '=' + hostDrive];

  const apiPort = await freePort();
  const apiBase = `http://127.0.0.1:${apiPort}`;
  console.log(`[test-manual-scene] 临时: 盘阵=${tmp}`);

  const child = spawn('python', ['-m', 'backend.api'], {
    cwd: REPO,
    env: {
      ...process.env,
      SR_AGENT_DB: path.join(tmp, 'db.sqlite'),
      SR_SCENES_ROOT: datahub,
      SR_SLURM_WORK_DIR: workDir,
      SR_LLM_MOCK: '1',
      SR_SLURM_FAKE: '1',
      SR_SLURM_FAKE_T_MS: '250',
      SR_QUEUE_POLL_SEC: '0.3',
      SR_API_HOST: '127.0.0.1',
      SR_API_PORT: String(apiPort),
      SR_DRIVE_MAP: drives.join(';'),
      SR_ALLOWED_ROOTS: ['W:\\', posixTmp].join(';'),
    },
  });
  child.stdout.on('data', () => {});
  child.stderr.on('data', () => {});

  try {
    await waitHealth(apiBase + '/api/health');

    const server = await startStaticServer();
    const base = `http://127.0.0.1:${server.address().port}`;
    console.log(`[test-manual-scene] 静态 ${base} ← dist；API ${apiBase}`);

    const { browser, page, errors } = await launchPage();
    const external = [];
    const seen = [];
    page.on('request', (req) => {
      const u = req.url();
      seen.push(u);
      if (!u.startsWith(base) && !u.startsWith(apiBase) && !u.startsWith('data:')) {
        external.push(u);
      }
    });
    const countUrl = (re) => seen.filter((u) => re.test(u)).length;
    const resolveRe = new RegExp(`^${apiBase}/api/scenes/resolve$`);
    const previewRe = new RegExp(`^${apiBase}/api/scenes/[^/]+/preview$`);

    await page.evaluateOnNewDocument((cfg) => { window.__SR_CFG__ = cfg; },
      { apiBase, staticBase: '' });

    try {
      /* ---------- A. 场景库页：当天前缀 + 手工打开盘阵场景 ---------- */
      console.log('\n[A] 场景库页：粘 Windows 形态路径 → 打开（不扫盘）');
      await page.goto(base + '/scenes', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitFor(page, () => !!document.querySelector('.spb-in'), 15000, '盘阵场景栏');

      const prefilled = await readPathBar(page);
      assert(prefilled === WIN_PREFIX,
        `输入框预填当天前缀（${prefilled}）`);
      assert((await page.evaluate(() =>
        [...document.querySelectorAll('.spb button')].map((b) => b.textContent.trim()).join(',')))
        === '打开,今天', '场景栏给出「打开 / 今天」两个按钮');

      const scWin = WIN_PREFIX + '\\' + SC;
      await openPathBar(page, scWin);
      await waitNode(() => countUrl(resolveRe) === 1, 15000, 'resolve 往返');
      assert(countUrl(resolveRe) === 1,
        '只 stat 用户给的那一个目录（resolve 恰好一次，无列举）');
      // 库外场景没有静态 jpgUrl，取图走 /preview（首次要生成预览缓存）。等这次
      // 响应到达再断言"没报错"—— 早一步看 .sp-err 是空的，什么都证明不了。
      try {
        await page.waitForResponse((r) => previewRe.test(r.url()), { timeout: 60000 });
      } catch {
        const txt = await page.evaluate(() => {
          const el = document.querySelector('.sp-err');
          return el ? el.textContent.trim() : '（.sp-err 为空）';
        });
        throw new Error('首次预览没有生成：' + txt);
      }
      assert(await page.evaluate(() => !document.querySelector('.sp-err')),
        '打开成功：场景页没有错误提示');

      await clickByText(page, '去查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '跳 /viewer');
      await waitFor(page, () => !!window.__viewer, 15000, '__viewer 钩子');
      await waitFor(page, () => window.__viewer.recs().length === 1, 30000, '场景进查看器');
      const rec = await page.evaluate(() => window.__viewer.activeRec());
      assert(rec.name === SC, `查看器打开的就是这个场景（${rec.name}）`);
      assert(rec.route === 'jpg', `手工场景走盘阵 JPG 路由（route=${rec.route}）`);
      assert(rec.W === 1600 && rec.H === 800,
        `尺寸取影像头 1600×800（${rec.W}×${rec.H}）`);
      assert(rec.lqPath === SC_DIR.replace(/\\/g, '/'),
        `rec 带上盘阵目录（${rec.lqPath}）`);
      assert(rec.serverMaskPath === SC_DIR.replace(/\\/g, '/') + '/' + SC + '_mask.tif',
        `掩码路径由后端推导、与提交同源（…${rec.serverMaskPath.slice(-24)}）`);

      const scMask = path.join(SC_DIR, SC + '_mask.tif');
      assert(!fs.existsSync(scMask), '该场景本来没有掩码（生产上 90% 如此）');
      const before = await toolbarState(page);
      assert(before.sr === true && before.bake === true,
        `工具栏「提交 SR」「保存掩码到盘阵」均可用（sr=${before.sr}/bake=${before.bake}）`);

      /* ---------- B. 画掩码 → 写进盘阵 ---------- */
      console.log('\n[B] 画矩形 → 「保存掩码到盘阵」→ 文件落进场景目录');
      await page.evaluate(() => window.__viewer.commitRect({ x0: 100, y0: 50, x1: 300, y1: 150 }));
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 1, '画了 1 个 ROI');
      await clickByText(page, '保存掩码到盘阵');
      await waitNode(() => isTiff(scMask), 30000, '掩码落盘');
      assert(fs.statSync(scMask).size > 0, `掩码写进场景目录（${scMask}）`);
      const toastB = await page.evaluate(() => {
        const t = document.querySelector('.toast');
        return t ? t.textContent.trim() : '';
      });
      assert(toastB.includes('掩码已写入盘阵'), `提示写入位置（${toastB.slice(0, 44)}…）`);
      assert(await page.evaluate(() => window.__viewer.activeRec().serverMaskPath)
        === SC_DIR.replace(/\\/g, '/') + '/' + SC + '_mask.tif',
        '写入后记录的是服务端权威路径（不是前端自己拼的）');

      /* ---------- C. 提交 SR → 队列 ---------- */
      console.log('\n[C] 「提交 SR」→ /queue 预填 → 确认提交 → 假调度器跑完');
      await clickByText(page, '提交 SR');
      await waitFor(page, () => location.pathname.endsWith('/queue'), 15000, '跳 /queue');
      await waitFor(page, () => !!document.querySelector('.qp-form .qp-draft-tip'),
        10000, '带入提示');
      const lq = await readField(page, 'lq_path');
      const maskField = await readField(page, 'mask_path');
      assert(lq.ok && lq.value === SC_DIR.replace(/\\/g, '/'),
        `lq_path = 场景目录（${lq.value}）`);
      assert(maskField.ok && maskField.value
        === SC_DIR.replace(/\\/g, '/') + '/' + SC + '_mask.tif',
        `mask_path 只读展示后端推导值（…${(maskField.value || '').slice(-20)}）`);
      const tasks0 = await page.evaluate(async (ab) => {
        const r = await fetch(ab + '/api/queue');
        return (await r.json()).tasks.length;
      }, apiBase);
      assert(tasks0 === 0, `未自动提交：队列仍为空 (${tasks0})`);

      await clickByText(page, '提交 SR');
      await waitFor(page, () => {
        const t = document.querySelector('.qp-tbl tbody tr td .tag');
        return t && t.textContent.trim() === '完成';
      }, 25000, '队列 COMPLETED');
      const row = await page.evaluate(() => {
        const tr = document.querySelector('.qp-tbl tbody tr');
        const tds = tr ? [...tr.querySelectorAll('td')] : [];
        const m = tds[4] ? tds[4].querySelector('.qp-mask') : null;
        return { tag: tds[0] ? tds[0].textContent.trim() : '', mask: m ? m.textContent.trim() : '' };
      });
      assert(row.tag === '完成', `手工场景的作业跑完（首行徽标「${row.tag}」）`);
      assert(row.mask === '掩膜 ' + SC + '_mask.tif',
        `任务行显示实际用的掩膜（「${row.mask}」）`);

      /* ---------- D. 猜错必须报错 ---------- */
      console.log('\n[D] 粘一个不存在的编号 → 报错带候选与原因，不新增 rec');
      const beforeRecs = await recCount(page);
      assert(beforeRecs === 1, `此时查看器里有 1 个 rec（${beforeRecs}）`);
      await clickLink(page, '场景库');
      await waitFor(page, () => location.pathname.endsWith('/scenes'), 15000, '回 /scenes');
      await waitFor(page, () => !!document.querySelector('.spb-in'), 15000, '场景栏');
      await openPathBar(page, WIN_PREFIX + '\\' + MISSING);
      await waitFor(page, () => {
        const e = document.querySelector('.sp-err');
        return e && e.textContent.trim().length > 0;
      }, 15000, '失败提示 .sp-err');
      const errD = await page.evaluate(() => document.querySelector('.sp-err').textContent.trim());
      assert(errD.includes(MISSING) && errD.includes('目录不存在'),
        `错误里给出试过的候选与原因（${errD.slice(0, 72)}…）`);
      assert(await page.evaluate(() => location.pathname.endsWith('/scenes')),
        '失败不跳路由（留在 /scenes）');
      assert(await recCount(page) === beforeRecs,
        '打开失败不留任何可提交的东西（rec 数不变）');

      /* ---------- E. 本地文件反推 ---------- */
      console.log('\n[E] 查看器选本地 tif → 按文件名反推盘阵目录');
      await clickLink(page, '查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '回 /viewer');
      const input = await page.$('input[type=file]');
      if (!input) throw new Error('未找到 input[type=file]');
      await input.uploadFile(upOk);
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.lqPath;
      }, 20000, '反推关联命中');
      const linked = await page.evaluate(() => {
        const rs = window.__viewer.recs();
        return rs[rs.length - 1];
      });
      assert(linked.lqPath === SC_DIR.replace(/\\/g, '/'),
        `命中即关联上盘阵目录（${linked.lqPath}）`);
      await waitFor(page, () => {
        const t = document.querySelector('.toast');
        return t && t.textContent.includes('已关联盘阵目录');
      }, 10000, '关联 toast');
      assert(await page.evaluate(() => {
        const b = [...document.querySelectorAll('.toolbar button')]
          .find((x) => x.textContent.trim() === '提交 SR');
        return b && !b.disabled;
      }), '关联成功后「提交 SR」由灰转可用');

      // 生产树真形态（<日>/<卫星型号>/<段级目录>/<景级目录>）：多两层也要命中。
      // 反推候选由后端算（前端只发裸文件名），这里钉的就是那条生产树候选。
      await input.uploadFile(upProd);
      await waitFor(page, (want) => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.lqPath === want;
      }, 20000, '生产树反推关联命中', PROD_DIR.replace(/\\/g, '/'));
      assert(true, `生产树命中：${PROD_DIR.replace(/\\/g, '/')}`);

      // 文件名里有日期但盘阵上没有这个目录 → 报错，按钮仍禁用
      await input.uploadFile(upMiss);
      await waitFor(page, () => {
        const e = document.querySelector('.err-box');
        return e && e.textContent.includes('未能关联盘阵目录');
      }, 20000, '反推失败的报错');
      const errE = await page.evaluate(() => document.querySelector('.err-box').textContent.trim());
      assert(errE.includes(MISSING) && errE.includes('目录不存在'),
        `反推落空时说明原因与候选（${errE.slice(0, 60)}…）`);
      assert(await page.evaluate(() => {
        const rs = window.__viewer.recs();
        return rs[rs.length - 1].lqPath === null;
      }), '没命中就不写 lqPath（绝不静默提交）');
      assert(await page.evaluate(() => {
        const b = [...document.querySelectorAll('.toolbar button')]
          .find((x) => x.textContent.trim() === '提交 SR');
        return b && b.disabled;
      }), '「提交 SR」保持禁用');

      // 文件名里没有成像时间戳 → 后端报「没有时间戳」，提示手粘目录；不写 lqPath
      await input.uploadFile(upNoDate);
      await waitFor(page, () => {
        const e = document.querySelector('.err-box');
        return e && e.textContent.includes('时间戳');
      }, 20000, '无时间戳报错');
      assert(await page.evaluate(() => {
        const rs = window.__viewer.recs();
        return rs[rs.length - 1].lqPath === null;
      }), '取不到日期就不写 lqPath（绝不静默提交）');

      /* ---------- F. PAN.tif（RC）场景的掩码命名 ---------- */
      console.log('\n[F] PAN.tif 场景：掩码名取输入名 stem，不取目录名');
      await openPathBar(page, WIN_PREFIX + '\\' + PAN);
      // 期望值必须当实参传进页面：page.evaluate 的函数体在页面里跑，够不到 Node 变量
      await waitFor(page, (want) => {
        const r = window.__viewer.activeRec();
        return !!r && r.lqPath === want;
      }, 20000, 'PAN 场景打开', PAN_DIR.replace(/\\/g, '/'));
      const panRec = await page.evaluate(() => window.__viewer.activeRec());
      assert(panRec.serverMaskPath === PAN_DIR.replace(/\\/g, '/') + '/PAN_mask.tif',
        `掩码路径按输入影像命名（…${panRec.serverMaskPath.slice(-16)}）`);
      await page.evaluate(() => window.__viewer.commitRect({ x0: 20, y0: 20, x1: 200, y1: 160 }));
      await clickByText(page, '保存掩码到盘阵');
      const panMask = path.join(PAN_DIR, 'PAN_mask.tif');
      await waitNode(() => isTiff(panMask), 30000, 'PAN 掩码落盘');
      assert(true, `PAN 场景的掩码写进 ${panMask}`);
      assert(!fs.existsSync(path.join(PAN_DIR, PAN + '_mask.tif')),
        '没有按目录名写出那份不会被提交读到的掩码');

      // 队列表单显示的是**后端给的**掩码路径，不是前端 derivedMaskPath 那份拿
      // 目录名顶输入名 stem 的镜像 —— 这个场景两者恰好不同名，是唯一能把它俩区
      // 分开的地方。前端镜像会显示 <目录名>_mask.tif，那样用户以为要准备的文件
      // 压根不是提交时读的那个。
      await clickByText(page, '提交 SR');
      await waitFor(page, () => location.pathname.endsWith('/queue'), 15000, '跳 /queue');
      await waitFor(page, () => !!document.querySelector('.qp-form .qp-draft-tip'),
        10000, '带入提示');
      const panMaskField = await readField(page, 'mask_path');
      assert(panMaskField.ok
        && panMaskField.value === PAN_DIR.replace(/\\/g, '/') + '/PAN_mask.tif',
        `队列表单显示后端权威掩码名（…${(panMaskField.value || '').slice(-20)}）`);
      /* ---------- G. 粘单个 .tif 文件路径（裸 TIF 入口） ---------- */
      console.log('\n[G] 粘单个 .tif 文件路径：能看，但不能提交 SR');
      const LOOSE = 'LOOSE_' + ymd + '120000';
      const LOOSE_DIR = path.join(tmp, 'loose');
      const looseJpg = path.join(LOOSE_DIR, LOOSE + '.preview.jpg');
      assert(!fs.existsSync(looseJpg), '打开前这个裸 TIF 旁边没有预览缓存');

      await clickLink(page, '场景库');
      await waitFor(page, () => location.pathname.endsWith('/scenes'), 15000, '回 /scenes');
      const resolveBefore = countUrl(resolveRe);
      await openPathBar(page, 'W:\\loose\\' + LOOSE + '.tif');
      await waitNode(() => countUrl(resolveRe) === resolveBefore + 1, 15000,
        '裸 TIF 的 resolve 往返');
      assert(countUrl(resolveRe) === resolveBefore + 1,
        '裸文件同样只 stat 用户给的那一条路径（resolve 恰好一次）');
      assert(await page.evaluate(() => !document.querySelector('.sp-err')),
        '裸 TIF 打开成功：场景页没有错误提示');

      await clickByText(page, '去查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '跳 /viewer');
      await waitFor(page, (want) => {
        const r = window.__viewer.activeRec();
        return !!r && r.name === want;
      }, 30000, '裸 TIF 进查看器', LOOSE);
      const looseRec = await page.evaluate(() => window.__viewer.activeRec());
      assert(looseRec.route === 'jpg', `裸 TIF 也走 JPG 路由显示（route=${looseRec.route}）`);
      assert(looseRec.W === 400 && looseRec.H === 200,
        `尺寸取影像头 400×200（${looseRec.W}×${looseRec.H}）`);
      assert(looseRec.lqPath === null,
        `不在场景目录里 → 不写 lqPath（${JSON.stringify(looseRec.lqPath)}）`);
      assert(await page.evaluate(() => {
        const b = [...document.querySelectorAll('.toolbar button')]
          .find((x) => x.textContent.trim() === '提交 SR');
        return b && b.disabled;
      }), '「提交 SR」保持禁用（这张图在盘阵上跑不了 SR）');

      // 1/2 尺度的落盘证据：缓存文件名与源同目录同名 + .preview.jpg；像素恰好一半。
      // 400×200 → 200×100；旧规则（长边 8192 封顶）会留下整幅 400×200。
      await waitNode(() => !!jpegSize(looseJpg), 30000, '裸 TIF 的预览落盘');
      const sz = jpegSize(looseJpg);
      assert(sz.w === 200 && sz.h === 100,
        `预览 JPG 各边为源图 1/2（400×200 → ${sz.w}×${sz.h}）`);
      assert(fs.existsSync(looseJpg)
        && path.dirname(looseJpg) === LOOSE_DIR,
        `预览烤在源图**自己的目录**里（${path.basename(looseJpg)}）`);

      /* ---------- H. 全程无错 ---------- */
      console.log('\n[H] 全程无错');
      // D / E 两处是**刻意**打出来的 404（猜错的路径），E 里还有一次刻意打的
      // 400（文件名没有时间戳，后端据此拒绝反推）—— 浏览器对任何非 2xx 响应都会
      // 往控制台写一条，这不算程序缺陷，但也不能睁一只眼闭一只眼：数目必须恰好
      // 等于刻意的那几次，多一条就是有别的资源没取到。
      const deliberate = /status of (404|400)/;
      const notFound = errors.filter((e) => /status of 404/.test(e));
      assert(notFound.length === 2,
        `控制台里的 404 恰好是刻意的那两次（${notFound.length}）`);
      const badRequest = errors.filter((e) => /status of 400/.test(e));
      assert(badRequest.length === 1,
        `控制台里的 400 恰好是刻意的那一次（无时间戳反推，${badRequest.length}）`);
      const appErrors = errors.filter((e) => !isIgnorableConsole(e) && !deliberate.test(e));
      assert(appErrors.length === 0, `无浏览器错误 (${JSON.stringify(appErrors.slice(0, 4))})`);
      assert(external.length === 0,
        `无外部网络请求（离线约束）(${external.slice(0, 3).join(', ')})`);

      console.log(`\n[test-manual-scene] ✅ 全部通过 (${pass} 项断言)`);
    } finally {
      await browser.close();
      server.close();
    }
  } finally {
    child.kill();
    await sleep(600);
    for (let i = 0; i < 3; i++) {
      try { fs.rmSync(tmp, { recursive: true, force: true }); break; }
      catch (e) { if (i === 2) console.warn('[cleanup] 临时目录未删净:', e.message); await sleep(300); }
    }
  }
}

main().catch((err) => {
  console.error('\n[test-manual-scene] ❌ 失败:', err.message);
  process.exit(1);
});
