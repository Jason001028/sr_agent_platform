// 盘阵场景页浏览器回归（/scenes → 懒生成 → 静态 JPG → viewer route='jpg' → 掩码换算）
// ---------------------------------------------------------------------------
// 前置：`cd frontend && npm run build`（本测试读 frontend/dist）。
// 拓扑：真 uvicorn（SR_SCENES_ROOT = 临时盘阵根）+ 本地静态服务**同时**顶替 nginx 的
//      两个 location：`/disk-array/` → 场景根（alias）、其余 → dist。
//      页面经 evaluateOnNewDocument 注入 window.__SR_CFG__ = { apiBase, staticBase }。
// 覆盖：列表/检索 → 盘阵 .jpg 源行（最小原型 §4.7：不烘焙直接开）→ 「生成并打开」的
//      懒生成 + 静态读 JPG + 同构 rec → 派生件（_preview.jpg 缓存 / <名字>_mask.tif）不入
//      列表 → 「去查看器」保状态跳转 → 掩码按**元数据** W/H 换算 → 「提交 SR」带出目录 →
//      /queue 预填表单（§4.3）→ 打开失败必须落在 .sp-err（错误不写 viewer 的错误条）。
// 说明：本文件 2026-09-15 重建。原文件（45 断言）随 .e2e/ 被 gitignore 丢失，断言按
//      当前实现（commit 8c197fc）重写，见 docs/status/current-question.md。
//      注意 window.__viewer 与 .toolbar 只挂 /viewer（ViewerPage.vue），所以「打开场景」
//      在 /scenes 做（要验的就是场景页），rec / 掩码 / 工具栏的断言在用「去查看器」
//      按钮**页内跳转**后在 /viewer 做 —— 不能 page.goto，那会整页重载丢掉 pinia store。
// 用法：cd .e2e && node test-scenes.js
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, spawnSync } = require('child_process');
const { launchPage } = require('./launchBrowser');

const REPO = path.resolve(__dirname, '..');
const DIST = path.join(REPO, 'frontend', 'dist');
const DISK_PREFIX = '/disk-array/';
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

async function waitHealth(url, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return; } catch { /* 未起 */ }
    if (Date.now() - t0 > ms) throw new Error('后端 /api/health 超时: ' + url);
    await sleep(200);
  }
}

/* ---------------- 盘阵侧：静态服务（顶替 nginx 的两条 location） ---------------- */
function startStaticServer(scenesRoot) {
  const server = http.createServer((req, res) => {
    let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    let filePath;
    if (urlPath.startsWith(DISK_PREFIX)) {
      filePath = path.join(scenesRoot, urlPath.slice(DISK_PREFIX.length));  // alias <root>/;
    } else {
      if (urlPath === '/') urlPath = '/index.html';
      filePath = path.join(DIST, urlPath);
      if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        filePath = path.join(DIST, 'index.html');     // createWebHistory 回退
      }
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

/* ---------------- 盘阵 fixture（真 TIFF + 真 JPEG + 掩膜） ---------------- */
// GF07A03：.hdr 报 3200×2000 而 tif 实际 1600×800 —— 等价于盘阵上「显示 JPG 比源图小」
// 的真实比例（烘焙规则 v2 是各边 1/2，降采样后的图就是比源图小）。掩码按 JPG 尺寸换算
// 就会错、按元数据换算才对 —— 这是本文件里唯一能被证伪的错误路径，所以 fixture 尺寸必须
// 不一致。
// 注意这条 .hdr 谎报恰好让 1/2 规则**落在 tif 的真实尺寸上**：max_edge 由元数据算出 =
// 3200/2 = 1600，而 tif 只有 1600 宽 → ps = min(1, 1600/1600) = 1.0 → 预览就是 1600×800
// 整幅（不是 1600×1000）。所以这里的 JPG 尺寸断言证明不了 1/2 规则 —— 那件事由
// test-manual-scene.js 的 G 段钉（裸 TIF，无 .hdr，400×200 → 200×100）。
const FIXTURE_PY = `
import os, sys
import numpy as np, tifffile
from PIL import Image
root = sys.argv[1]

def tif(rel, w, h):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    arr = (np.arange(w * h, dtype=np.uint32).reshape(h, w) % 4096).astype(np.uint16)
    tifffile.imwrite(p, arr, photometric="minisblack")
    return p

def jpg(rel, w, h):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    arr = (np.arange(w * h, dtype=np.uint32).reshape(h, w) % 256).astype(np.uint8)
    Image.fromarray(arr, mode="L").save(p, "JPEG", quality=80)
    return p

def scene(dir_rel, name, ext, w, h, hdr=None):
    """造一个真机形态的场景目录，返回相对 root 的路径。

    真机布局是 <root>/<年/月/日>/<生产编号>/<生产编号>.tif，同目录里躺着一份
    <生产编号>_meta.xml —— scene_search 的场景判据（is_scene_dir）。平铺的裸文件
    不会被列出，所以 fixture 必须按目录造。
    """
    rel = os.path.join(dir_rel, name, name + "." + ext) if dir_rel \\
        else os.path.join(name, name + "." + ext)
    d = os.path.dirname(os.path.join(root, rel))
    os.makedirs(d, exist_ok=True)
    (tif if ext == "tif" else jpg)(rel, w, h)
    if hdr:
        with open(os.path.join(root, os.path.splitext(rel)[0] + ".hdr"), "w") as f:
            f.write(hdr)
    with open(os.path.join(d, name + "_meta.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>'
                "<SolarAzimuth>181.79</SolarAzimuth>")
    return rel

scene("", "GF07A03_PMS01_20260722125045", "tif", 1600, 800,
      hdr="samples = 3200\\nlines = 2000\\nbands = 1\\n")
scene("sub", "KF02B04_PMS05_20260723083000", "tif", 900, 450)
scene("", "GF04_PMS02_20260801120000", "jpg", 400, 200)
# 「<目录名>_mask.tif」：后端 §4.3 的掩膜推导读的就是这个名（<lq_path>/<leaf>_mask.tif），
# 真机上它和场景同名同目录。故意留在 fixture 里：is_scene_file 的白名单把它挡在列表外，
# 3 个场景必须仍是 3 行 —— 一旦失效，页面上每个场景都会多出一行（卫星/传感器从掩膜
# 文件名解析、尺寸取掩膜 TIFF 头）。
tif("GF07A03_PMS01_20260722125045/GF07A03_PMS01_20260722125045_mask.tif", 8, 8)
tif("sub/KF02B04_PMS05_20260723083000/KF02B04_PMS05_20260723083000_mask.tif", 8, 8)
`;

function makeFixtures(scenesRoot) {
  const r = spawnSync('python', ['-c', FIXTURE_PY, scenesRoot], { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('fixture 生成失败: ' + (r.stderr || r.stdout));
}

/* ---------------- B2 段中途补的「同名栅格」 ---------------- */
// 为什么不在 FIXTURE_PY 里一次造好：B2 是**两行同名**的形态，会改掉行数 / 行序 /
// rec 指纹那一批断言的前提，只能等它们都跑完再补。数值与 FIXTURE_PY 的 tif() 同款。
const TIF_PY = `
import os, sys
import numpy as np, tifffile
p = sys.argv[1]
w, h = int(sys.argv[2]), int(sys.argv[3])
os.makedirs(os.path.dirname(p), exist_ok=True)
arr = (np.arange(w * h, dtype=np.uint32).reshape(h, w) % 4096).astype(np.uint16)
tifffile.imwrite(p, arr, photometric="minisblack")
`;

function makeTif(file, w, h) {
  const r = spawnSync('python', ['-c', TIF_PY, file, String(w), String(h)],
    { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('TIFF 生成失败: ' + (r.stderr || r.stdout));
}

/* ---------------- 页面读取/操作助手 ---------------- */
function rows(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('.sp-tbl tbody tr')].map((tr) => {
      if (tr.querySelector('td.empty')) return null;
      const tds = [...tr.querySelectorAll('td')];
      if (tds.length < 8) return null;
      const tag = tds[6].querySelector('.tag');
      const btn = tds[7].querySelector('button');
      return {
        sat: tds[0].textContent.trim(), sensor: tds[1].textContent.trim(),
        date: tds[2].textContent.trim(), name: tds[3].textContent.trim(),
        dims: tds[4].textContent.trim(), size: tds[5].textContent.trim(),
        tag: tag ? tag.textContent.trim() : '',
        btn: btn ? btn.textContent.trim() : '',
      };
    }).filter(Boolean));
}

const waitRows = (page, n, timeoutMs) =>
  waitFor(page, (want) => {
    const rs = [...document.querySelectorAll('.sp-tbl tbody tr')]
      .filter((tr) => !tr.querySelector('td.empty'));
    return rs.length === want;
  }, timeoutMs, `行数=${n}`, n);

/** 等行名（含顺序）完全等于 want —— 行数相同时也能等到真正的重渲染 */
const waitRowNames = (page, want, timeoutMs = 15000) =>
  waitFor(page, (w) => {
    const got = [...document.querySelectorAll('.sp-tbl tbody tr')]
      .filter((tr) => !tr.querySelector('td.empty'))
      .map((tr) => { const td = tr.querySelector('td.name'); return td ? td.textContent.trim() : ''; });
    return got.join(',') === w;
  }, timeoutMs, `行=${want}`, want.join(','));

/** node 侧轮询（page.evaluate 读不到上面的请求计数器） */
async function waitNode(fn, timeoutMs, label) {
  const t0 = Date.now();
  for (;;) {
    if (fn()) return;
    if (Date.now() - t0 > timeoutMs) throw new Error('超时等待 ' + label);
    await sleep(100);
  }
}

/** 等某行的预览标签变成 want（打开后列表行就地翻牌） */
const waitRowTag = (page, name, want, timeoutMs = 20000) =>
  waitFor(page, (n, w) => {
    const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
      const td = r.querySelector('td.name');
      return td && td.textContent.trim() === n;
    });
    const tag = tr && tr.querySelector('td .tag');
    return !!tag && tag.textContent.trim() === w;
  }, timeoutMs, `行「${name}」标签=${want}`, name, want);

/** 等某行的按钮文案变成 want。**「已生成」标签比按钮先翻**：lib/api.ts 的
 *  fetchSceneJpg 在去取静态 JPG **之前**就把 `row.hasPreview` 置真了，而
 *  scenes.open() 要等字节到手、解码完成，才在 finally 里清 openingId。所以
 *  「等标签 → 立即读按钮」是竞态：机器忙时会读到还没清的「打开中…」。 */
const waitRowBtn = (page, name, want, timeoutMs = 20000) =>
  waitFor(page, (n, w) => {
    const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
      const td = r.querySelector('td.name');
      return td && td.textContent.trim() === n;
    });
    const btn = tr && tr.querySelector('td button');
    return !!btn && btn.textContent.trim() === w;
  }, timeoutMs, `行「${name}」按钮=${want}`, name, want);

/** 点某场景行的「打开 / 生成并打开」按钮（按钮文案随状态变） */
async function clickRowButton(page, name) {
  const ok = await page.evaluate((n) => {
    const tb = document.querySelector('.sp-tbl tbody');
    if (!tb) return false;
    const tr = [...tb.querySelectorAll('tr')].find((r) => {
      const td = r.querySelector('td.name');
      return td && td.textContent.trim() === n;
    });
    if (!tr) return false;
    const b = tr.querySelector('button');
    if (!b || b.disabled) return false;
    b.click();
    return true;
  }, name);
  if (!ok) throw new Error('行按钮未找到或已禁用: ' + name);
}

/** 按「行名 + 尺寸列」点行按钮。
 *
 * 只有 B2 段用得上：那时同一场景在库里是**两行同名**（`<编号>.jpg` 与
 * `<编号>.tif`），列表没有 rel 列，尺寸是唯一分得开的那一列。 */
async function clickRowButtonByDims(page, name, dims) {
  const ok = await page.evaluate((n, d) => {
    const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
      const td = r.querySelector('td.name');
      const dm = r.querySelectorAll('td')[4];
      return td && td.textContent.trim() === n
        && dm && dm.textContent.trim() === d;
    });
    if (!tr) return false;
    const b = tr.querySelector('button');
    if (!b || b.disabled) return false;
    b.click();
    return true;
  }, name, dims);
  if (!ok) throw new Error(`行按钮未找到或已禁用: ${name} / ${dims}`);
}

/** 等「行名 + 尺寸列」那一行的**按钮**变成 want（B2 段专用，理由同上）。
 *
 * 这里等按钮而不是等「已生成」标签：JPG 源行的 tag 由 `isImageSource` 决定，
 * 恒为「JPG 源」、烤完也不翻（B 段打开后那一行仍是「JPG 源」）。能翻的是按钮 ——
 * 烤完 `previewNeedsBake` 转假，「生成并打开」回到「打开」。 */
const waitRowBtnByDims = (page, name, dims, want, timeoutMs = 30000) =>
  waitFor(page, (n, d, w) => {
    const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
      const td = r.querySelector('td.name');
      const dm = r.querySelectorAll('td')[4];
      return td && td.textContent.trim() === n && dm && dm.textContent.trim() === d;
    });
    const b = tr && tr.querySelector('td button');
    return !!b && b.textContent.trim() === w;
  }, timeoutMs, `行「${name}/${dims}」按钮=${want}`, name, dims, want);

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

/** 按序号写 .sp-filters 里的输入框（0 关键词 / 1 卫星 / 2 传感器 / 3 起 / 4 止） */
async function setFilter(page, idx, value) {
  await page.evaluate((i, v) => {
    const el = document.querySelectorAll('.sp-filters .sp-in')[i];
    if (!el) throw new Error('过滤器 #' + i + ' 不存在');
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, idx, value);
}

/** 读 .qp-form 某字段：label 的 span 以 label 开头即命中（避免 lq/mask 串位） */
async function readField(page, label) {
  return page.evaluate((l) => {
    const lab = [...document.querySelectorAll('.qp-form label')]
      .find((x) => { const s = x.querySelector('span'); return s && s.textContent.trim().startsWith(l); });
    if (!lab) return { ok: false, value: null, readonly: false, cands: 0 };
    const inp = lab.querySelector('input');
    if (!inp) return { ok: false, value: null, readonly: false, cands: 0 };
    // cands = 该 input 挂的 datalist 里的候选数（lq_path 的历史目录下拉）
    const dl = inp.getAttribute('list')
      ? document.getElementById(inp.getAttribute('list')) : null;
    return {
      ok: true, value: inp.value, readonly: inp.readOnly,
      cands: dl ? dl.querySelectorAll('option').length : 0,
    };
  }, label);
}

function isIgnorableConsole(msg) {
  return /AbortError|ERR_ABORTED|queue\/events|net::ERR/i.test(msg);
}

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sr-e2e-scenes-'));
  const scenesRoot = path.join(tmp, 'scenes');
  fs.mkdirSync(scenesRoot, { recursive: true });
  makeFixtures(scenesRoot);
  const workDir = path.join(tmp, 'work');
  fs.mkdirSync(workDir, { recursive: true });

  const apiPort = await freePort();
  const apiBase = `http://127.0.0.1:${apiPort}`;
  console.log(`[test-scenes] 临时: scenes=${scenesRoot}`);

  const child = spawn('python', ['-m', 'backend.api'], {
    cwd: REPO,
    env: {
      ...process.env,
      SR_AGENT_DB: path.join(tmp, 'db.sqlite'),
      SR_SCENES_ROOT: scenesRoot,
      SR_SLURM_WORK_DIR: workDir,
      SR_LLM_MOCK: '1',
      SR_SLURM_FAKE: '1',
      SR_QUEUE_POLL_SEC: '0.3',
      SR_API_HOST: '127.0.0.1',
      SR_API_PORT: String(apiPort),
    },
  });
  child.stdout.on('data', () => {});
  child.stderr.on('data', () => {});

  // 列表按 (date, id) 倒序：2026-08-01 → 07-23 → 07-22
  const JPG_ROW = 'GF04_PMS02_20260801120000';       // 盘阵 .jpg 源（§4.7）
  const SUB_ROW = 'KF02B04_PMS05_20260723083000';    // 嵌套目录里的未烘焙 TIFF
  const HDR_ROW = 'GF07A03_PMS01_20260722125045';    // .hdr 3200×2000 / tif 1600×800

  try {
    await waitHealth(apiBase + '/api/health');

    const server = await startStaticServer(scenesRoot);
    const base = `http://127.0.0.1:${server.address().port}`;
    console.log(`[test-scenes] 静态 ${base}（dist + ${DISK_PREFIX}→场景根）；API ${apiBase}`);

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
    // `?div=` 必须吃：档位进了 URL（换档位要击穿 nginx 的 max-age）
    const previewRe = new RegExp(`^${apiBase}/api/scenes/[^/]+/preview\\?div=\\d+$`);
    const listRe = new RegExp(`^${apiBase}/api/scenes\\?`);
    /** 点「检索」/「重置」并等**真的**完成一次列表往返（行数不变时也能等） */
    const relist = async (label) => {
      const before = countUrl(listRe);
      await clickByText(page, label);
      await waitNode(() => countUrl(listRe) > before, 15000, `${label} 的列表往返`);
      await sleep(250);
    };

    await page.evaluateOnNewDocument((cfg) => { window.__SR_CFG__ = cfg; },
      { apiBase, staticBase: base });
    // 档位钉到 ÷2：本脚本测的是「列表 → 懒生成 → 静态直读」这条链，不是档位本身。
    // 夹具的 .hdr 与尺寸断言都是按 1/2 配的，跟随产品默认的 ÷4 只会把「链通了没有」
    // 淹在一堆数字改动里。try/catch 是必须的：回调在 about:blank 上也跑，opaque
    // origin 下 localStorage 抛 SecurityError。
    await page.evaluateOnNewDocument(() => {
      try {
        localStorage.setItem('sr.previewDiv', '2');
      } catch (e) { /* about:blank：真实页面加载时会再跑一次 */ }
    });

    try {
      /* ---------- A. 列表 ---------- */
      console.log('\n[A] 盘阵场景列表');
      await page.goto(base + '/scenes', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitRows(page, 3);
      const srcText = await page.evaluate(() => document.querySelector('.sp-src').textContent.trim());
      assert(srcText.includes('盘阵') && !srcText.includes('fake 回退'), `来源 chip = 盘阵（${srcText}）`);
      assert(/扫\s*3\b/.test(srcText) && /命中\s*3\b/.test(srcText),
        '扫描 3 / 命中 3（三个影像；两个 _mask.tif 与烘焙出的 .preview.jpg 都不算场景）');

      let rs = await rows(page);
      // 掩膜不是场景：`<目录名>_mask.tif` 是「提交 SR」的*输入*（§4.3）。真机目录里它和
      // 场景同名同目录，一旦 is_scene_file 漏掉这条规则，页面上每个场景都会多出一行
      // （卫星/传感器从掩膜文件名解析、尺寸取掩膜 TIFF 头）。下面两条是镜像断言。
      assert(rs.length === 3, `列表 3 行（多出来的一定是掩膜/派生件）(${rs.length})`);
      assert(rs.every((r) => !/_mask$/i.test(r.name)),
        `掩膜未出现在列表（${rs.map((r) => r.name).join(',')}）`);
      assert(rs.map((r) => r.name).join(',') === [JPG_ROW, SUB_ROW, HDR_ROW].join(','),
        `按 (日期,id) 倒序：${rs.map((r) => r.name).join(' | ')}`);
      assert(rs[0].sat === 'GF04' && rs[0].sensor === 'PMS02' && rs[0].date === '2026-08-01',
        `文件名解析出卫星/传感器/日期（${rs[0].sat}/${rs[0].sensor}/${rs[0].date}）`);
      assert(rs[2].dims === '3200×2000',
        `.hdr 优先于 TIFF 头：尺寸列 = 3200×2000（实际 tif 1600×800）(${rs[2].dims})`);
      assert(rs[1].dims === '900×450', `无 .hdr 时探测 TIFF 头：900×450 (${rs[1].dims})`);
      assert(rs[0].dims === '400×200', `JPG 源行尺寸走 Pillow 头：400×200 (${rs[0].dims})`);
      assert(rs.every((r) => r.size !== '0 B' && /\d/.test(r.size)),
        `大小列非 0（${rs.map((r) => r.size).join(', ')}）`);
      assert(rs[1].tag === '未生成' && rs[1].btn === '生成并打开',
        `未烘焙 TIFF 行 = 「未生成」+「生成并打开」（${rs[1].tag}/${rs[1].btn}）`);
      assert(rs[2].tag === '未生成', '带 .hdr 的 TIFF 也标「未生成」（按缓存存在与否判定）');
      assert(rs[0].tag === 'JPG 源' && rs[0].btn === '打开',
        `盘阵 .jpg 源行 = 「JPG 源」+「打开」（§4.7 不烘焙）(${rs[0].tag}/${rs[0].btn})`);

      /* ---------- B. 盘阵 .jpg 源行：不烘焙，直接开 ---------- */
      console.log('\n[B] 盘阵 .jpg 源行：跳过懒生成');
      await clickRowButton(page, JPG_ROW);
      await sleep(600);
      assert(countUrl(previewRe) === 0,
        `没有为 JPG 源发 /preview 请求（后端不为它烘焙）(${countUrl(previewRe)})`);
      assert(!fs.existsSync(path.join(scenesRoot, JPG_ROW, JPG_ROW + '_preview.jpg')),
        '盘上确实没有生成它的预览缓存');
      assert(countUrl(new RegExp(`^${base}${DISK_PREFIX}${JPG_ROW}/${JPG_ROW}\\.jpg$`)) >= 1,
        '静态读的是源文件本身 /disk-array/<场景>/<场景>.jpg');
      assert(await page.evaluate(() => location.pathname.endsWith('/scenes')),
        '打开场景不跳路由（留在 /scenes）');
      rs = await rows(page);
      assert(rs[0].tag === 'JPG 源' && rs[0].btn === '打开',
        `打开后该行标签/按钮不变（本来就是「显示就绪图」）(${rs[0].tag}/${rs[0].btn})`);

      /* ---------- C. 未烘焙 TIFF：懒生成 + 静态直读 ---------- */
      console.log('\n[C] 「生成并打开」= 懒生成预览 + 静态直读');
      const previewJpg = path.join(scenesRoot, HDR_ROW, HDR_ROW + '_preview.jpg');
      assert(!fs.existsSync(previewJpg), '点击前盘上没有这张图的预览缓存');
      await clickRowButton(page, HDR_ROW);
      await waitRowTag(page, HDR_ROW, '已生成');
      assert(countUrl(previewRe) === 1,
        `调用 1 次 /api/scenes/{id}/preview 懒生成 (${countUrl(previewRe)})`);
      assert(fs.existsSync(previewJpg), `后端落盘 ${path.basename(previewJpg)}`);
      assert(countUrl(new RegExp(`^${base}${DISK_PREFIX}${HDR_ROW}/${HDR_ROW}_preview\\.jpg\\?div=2$`)) >= 1,
        '静态读图走 /disk-array/<场景>/…_preview.jpg?div=2（nginx alias 位；'
        + '查询串是击穿 max-age 用的，location 匹配不看它）');
      await waitRowBtn(page, HDR_ROW, '打开');   // 按钮落定再读，理由见 waitRowBtn
      const rowsNow = await rows(page);
      assert(rowsNow[2].tag === '已生成' && rowsNow[2].btn === '打开',
        `列表行就地翻牌为「已生成」+「打开」（${rowsNow[2].tag}/${rowsNow[2].btn}）`);
      assert(rowsNow[1].tag === '未生成', '未打开的第三张图不受影响（仍是「未生成」）');

      /* ---------- D. 派生件不入列表 ---------- */
      console.log('\n[D] 烘焙出的 _preview.jpg 不算新场景');
      await relist('检索');
      await waitRows(page, 3);
      rs = await rows(page);
      assert(rs.length === 3, `重检索仍是 3 行（is_scene_file 排除派生的 _preview.jpg）(${rs.length})`);
      assert(rs.every((r) => !r.name.endsWith('_preview')), '没有一行是 _preview.jpg 缓存');
      assert(rs[2].tag === '已生成', '重检索后已生成的仍是「已生成」（缓存被识别）');

      /* ---------- E. 检索过滤 ---------- */
      console.log('\n[E] 检索过滤（卫星/传感器/日期/关键词/重置）');
      await setFilter(page, 1, 'GF07A03');
      await relist('检索');
      await waitRowNames(page, [HDR_ROW]);
      rs = await rows(page);
      assert(rs.length === 1 && rs[0].name === HDR_ROW, `卫星过滤 → 1 行（${rs[0].name}）`);

      await setFilter(page, 1, '');
      await setFilter(page, 2, 'PMS02');
      await relist('检索');
      await waitRowNames(page, [JPG_ROW]);
      rs = await rows(page);
      assert(rs.length === 1 && rs[0].name === JPG_ROW, `传感器过滤 → 1 行（${rs[0].name}）`);

      await setFilter(page, 2, '');
      await setFilter(page, 3, '2026-07-23');
      await relist('检索');
      await waitRowNames(page, [JPG_ROW, SUB_ROW]);
      rs = await rows(page);
      assert(rs.every((r) => r.date >= '2026-07-23'),
        `起始日期过滤 → ${rs.length} 行，均 ≥ 2026-07-23（${rs.map((r) => r.date).join(',')}）`);
      assert(rs.length === 2, '日期下界生效：2026-07-22 的 .hdr 场景被排除');

      await setFilter(page, 3, '');
      await setFilter(page, 0, 'zzz-不存在');
      await relist('检索');
      await waitFor(page, () => !!document.querySelector('.sp-tbl td.empty'), 10000, '空态行');
      const emptyText = await page.evaluate(
        () => document.querySelector('.sp-tbl td.empty').textContent.trim());
      assert(emptyText.includes('没有匹配场景'), `无命中给出空态文案（${emptyText}）`);

      await relist('重置');
      await waitRows(page, 3);
      const cleared = await page.evaluate(() =>
        [...document.querySelectorAll('.sp-filters .sp-in')].map((e) => e.value));
      assert(cleared.every((v) => v === ''), `重置清空全部过滤输入（${JSON.stringify(cleared)}）`);
      assert(await page.evaluate(() => !document.querySelector('.sp-tbl td.empty')),
        '重置后空态行消失（又列出全部 3 行）');

      /* ---------- F. 页内跳转 /viewer：rec 同构 + 掩码按元数据换算 ---------- */
      console.log('\n[F] 「去查看器」页内跳转 → rec 与掩码换算');
      await clickRowButton(page, HDR_ROW);       // 最后激活它：掩码断言针对 3200×2000 的场景
      await sleep(400);
      await clickByText(page, '去查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '跳 /viewer');
      await waitFor(page, () => !!window.__viewer, 15000, '__viewer 钩子');
      const recs = await page.evaluate(() => window.__viewer.recs());
      assert(recs.length === 2, `页内跳转保留 2 个 rec（未整页重载）(${recs.length})`);
      assert(recs.every((r) => r.route === 'jpg'), '两个 rec 都是 route=jpg');

      const rec = await page.evaluate(() => window.__viewer.activeRec());
      assert(rec.name === HDR_ROW, `激活的是最后打开的场景（${rec.name}）`);
      assert(rec.W === 3200 && rec.H === 2000,
        `rec 用元数据尺寸 W/H = ${rec.W}×${rec.H}（不是 JPG 的 1600×800）`);
      assert(rec.thumbW === 1600 && rec.thumbH === 800,
        `缩略图画布 = JPG 实际像素 ${rec.thumbW}×${rec.thumbH}`);
      assert(rec.layout.includes('盘阵 JPG'), `布局文案：${rec.layout}`);
      assert(rec.status === '场景就绪', `状态是短标签、不再重抄名字与尺寸（${rec.status}）`);
      const other = recs.find((r) => r.name === JPG_ROW);
      assert(other && other.W === 400 && other.H === 200 && other.thumbW === 400,
        `JPG 源 rec 的 W/H = Pillow 头尺寸 ${other && other.W}×${other && other.H}`);

      await page.evaluate(() => window.__viewer.commitRect({ x0: 100, y0: 50, x1: 300, y1: 150 }));
      const rois = await page.evaluate(() => window.__viewer.getRois().length);
      assert(rois === 1, `画了 1 个 ROI（getRois=${rois}）`);
      const mask = await page.evaluate(() => window.__viewer.buildMaskJson());
      assert(mask.width === 3200 && mask.height === 2000,
        `掩码画布 = 元数据全分辨率 ${mask.width}×${mask.height}`);
      const poly = mask.polygons[0].points;
      assert(poly.length === 4, '矩形 ROI → 4 点多边形');
      assert(JSON.stringify(poly[0]) === JSON.stringify([200, 125]),
        `缩略图 (100,50) → 全分辨率 (200,125)：x×2 / y×2.5（${JSON.stringify(poly[0])}）`);
      assert(JSON.stringify(poly[2]) === JSON.stringify([600, 375]),
        `对角点 (300,150) → (600,375)（${JSON.stringify(poly[2])}）`);
      await page.evaluate(() => window.__viewer.clearRois());
      assert(await page.evaluate(() => window.__viewer.getRois().length) === 0, '清空 ROI 后掩码为空');

      const toolbar = await page.evaluate(() => {
        const sel = document.querySelector('.toolbar select');
        const sr = [...document.querySelectorAll('.toolbar button')]
          .find((b) => b.textContent.trim() === '提交 SR');
        return {
          stretch: sel ? sel.value : null,
          stretchDisabled: sel ? sel.disabled : null,
          stretchTitle: sel ? (sel.getAttribute('title') || '') : '',
          srDisabled: sr ? sr.disabled : null,
        };
      });
      assert(toolbar.stretch === 'equal' && toolbar.stretchDisabled === false,
        `场景路径拉伸可改，起手直方图均衡（${toolbar.stretch}/disabled=${toolbar.stretchDisabled}）`);
      assert(toolbar.stretchTitle.includes('烘焙'),
        `拉伸控件说明服务器烘焙与二次拉伸的关系（${toolbar.stretchTitle.slice(0, 40)}…）`);
      assert(toolbar.srDisabled === false,
        '「提交 SR」可用（sceneId + lqPath 都已带上）');

      // 起手值不是只写在下拉框上：这张图**确实**按直方图均衡重画过。
      // 修复前 route==='jpg' 会跳过绘制，画面永远停在服务器烤的那份底图上。
      const paintedAtOpen = await page.evaluate(() => window.__viewer.activeRec().paintedMode);
      assert(paintedAtOpen === 'equal',
        `场景 rec 的 paintedMode = 起手值（${paintedAtOpen}）`);

      // 换模式 → 画布像素随之变化（「无法使用其他选项」的直接反证）
      const thumbMean = () => page.evaluate(() => {
        const c = window.__viewer.activeRec().thumb;
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let s = 0;
        for (let i = 0; i < d.length; i += 4) s += d[i];
        return +(s / (d.length / 4)).toFixed(2);
      });
      const meanEqual = await thumbMean();
      await page.select('.toolbar select', 'log');
      await sleep(400);
      const meanLog = await thumbMean();
      assert(Math.abs(meanLog - meanEqual) > 1,
        `切到对数后画布像素变化（均衡均值 ${meanEqual} → 对数均值 ${meanLog}）`);

      // 拉伸是每张图各自的属性：改了这张，别的已打开场景 rec 不受影响
      const afterSwitch = await page.evaluate(() => {
        const act = window.__viewer.activeRec();
        return {
          sel: document.querySelector('.toolbar select').value,
          painted: act.paintedMode,
          others: window.__viewer.recs().filter((r) => r.name !== act.name)
            .map((r) => r.paintedMode),
        };
      });
      assert(afterSwitch.sel === 'log' && afterSwitch.painted === 'log',
        `下拉与 rec 记录同步（sel=${afterSwitch.sel}/painted=${afterSwitch.painted}）`);
      assert(afterSwitch.others.every((m) => m === 'equal'),
        `别的场景 rec 不被连带改掉（${JSON.stringify(afterSwitch.others)}）`);

      /* ---------- G. 提交 SR → /queue 预填表单（§4.3） ---------- */
      console.log('\n[G] 「提交 SR」带出目录 → /queue 预填表单');
      await clickByText(page, '提交 SR');
      await waitFor(page, () => location.pathname.endsWith('/queue'), 15000, '跳 /queue');
      await waitFor(page, () => !!document.querySelector('.qp-form .qp-draft-tip'), 10000, '带入提示');
      const lq = await readField(page, 'lq_path');
      const maskField = await readField(page, 'mask_path');
      const suffix = await readField(page, '后缀');
      // 场景目录 = 真机的「生产编号」目录（scene 文件的父目录），不是盘阵根。
      // 后端回的 lq_path 一律盘阵 POSIX 形态（_scene_row 用 as_posix()）：它会与
      // /api/queue 行 params.lq_path（提交侧归一化后的值）逐字比对，宿主形态在
      // Windows 开发机上永远比不中。Linux 上 as_posix() 与 str() 同值。
      assert(lq.ok && lq.value === path.join(scenesRoot, HDR_ROW).replace(/\\/g, '/'),
        `lq_path 带入场景目录 ${lq.value}`);
      // 2026-09-15 起 lq_path 不再是只读：原型期 SR_LOCKED_DIR 没有 API 暴露，前端无从
      // 得知"唯一合法目录"，只能拿历史任务里的目录当下拉候选，输入框仍须可手改。
      // 此刻队列为空 → 候选 0；有候选的情况在 test-platform.js §B 断（那里有真任务）。
      assert(!lq.readonly, 'lq_path 不再只读（允许从历史目录下拉里选）');
      assert(lq.cands === 0, `队列为空 → 目录下拉无候选（cands=${lq.cands}）`);
      assert(maskField.ok && maskField.readonly
        && maskField.value === path.join(scenesRoot, HDR_ROW).replace(/\\/g, '/')
                              + '/' + HDR_ROW + '_mask.tif',
        `mask_path 只读展示后端推导值 ${maskField.value}`);
      // 2026-09-16 起后缀不再预填 'sr'：留空即由后端按 SR 团队配置文件里的
      // <Suffix> 决定（services/run_sr.py::default_suffix）。预填的值会作为
      // 显式参数压过配置文件，所以这里断"必须是空的"。
      assert(suffix.ok && suffix.value.trim() === '',
        `后缀默认留空、交后端按 SR 配置决定 (${suffix.value})`);
      const tasks = await page.evaluate(async (ab) => {
        const r = await fetch(ab + '/api/queue');
        return (await r.json()).tasks.length;
      }, apiBase);
      assert(tasks === 0, `未自动提交：队列仍为空 (${tasks})`);

      /* ---------- B2. .jpg 源配上同名栅格 → 显示源改用服务端那份 ---------- */
      // 真机常态：场景目录里 `<编号>.tif` 与 `<编号>.jpg` 并存，而那份预生成的 jpg
      // 分辨率不够，实际预览要改成服务端从配套 .tif 下采样。判据只有一条：
      //     round(max(栅格长边)/div) > max(显示件长边)
      // 这里栅格 1600×800、显示件 400×200、档位钉在 ÷2 → 800 > 400 成立。
      //
      // **为什么这一段排在最后**：补上这个同名栅格会让同一个场景在库里变成**两行
      // 同名**（`<编号>.tif` 与 `<编号>.jpg` 都过 is_scene_file 的白名单，只有 rel
      // 能分辨），行数 / 行序 / rec 指纹的断言全在前面（A/D/E/F）。所以补栅格只能
      // 在它们之后做，做完再删掉 —— 后面的 I 段要把这份 jpg 换成非图字节验失败
      // 路径，留着栅格的话那次打开会走服务端烘焙、反倒成功了。
      console.log('\n[B2] .jpg 源配上同名栅格 → 预览改用服务端从栅格下采样');
      const jpgRowDir = path.join(scenesRoot, JPG_ROW);
      const rowTif = path.join(jpgRowDir, JPG_ROW + '.tif');
      assert(!fs.existsSync(rowTif), '补之前该目录里没有同名栅格（B 段「JPG 源」的前提）');
      makeTif(rowTif, 1600, 800);
      await page.goto(base + '/scenes', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitRows(page, 4);
      rs = await rows(page);
      // 两行同名，只有尺寸列分得开：一个还是那份 jpg 的 400×200，一个是栅格的 1600×800。
      assert(rs.filter((r) => r.name === JPG_ROW).length === 2,
        `同一场景现在两行同名（${rs.map((r) => r.name + '/' + r.dims).join(' | ')}）`);
      assert(rs.filter((r) => r.name === JPG_ROW && r.dims === '400×200').length === 1
        && rs.filter((r) => r.name === JPG_ROW && r.dims === '1600×800').length === 1,
        '一行是 jpg 源（400×200）、一行是栅格（1600×800）');
      // 两列在这里**故意不同步**，各自说的不是一件事：
      //   * tag「JPG 源」说的是**这一行是什么**（§4.7：源即显示件的行），判据是
      //     `isImageSource`，本轮一个字没动 —— 它没变成「未生成」是对的；
      //   * 按钮说的是**打开时会不会先烤一下**，由 `previewNeedsBake` 决定，而它
      //     新增的栅格分支现在为真（有更清晰的一份可烤）→「打开」变「生成并打开」。
      // 把两者绑在一起判（"有更清晰的栅格 ⇒ tag 也该翻"）会逼着去改那个 tag 判据，
      // 而那正是本轮划在界外的事（§六.3）。
      const jpgRow = rs.find((r) => r.name === JPG_ROW && r.dims === '400×200');
      assert(jpgRow.tag === 'JPG 源',
        `tag 仍是「JPG 源」（这一行还是那个显示件，不是「未生成」）(${jpgRow.tag})`);
      assert(jpgRow.btn === '生成并打开',
        `按钮改成「生成并打开」（打开前得先烤栅格那份）(${jpgRow.btn})`);

      // 行自己的三个字段（`hasPreview` / `jpgUrl` / `previewDiv`）说的是**源 jpg**
      // 自己，语义必须原样：它们是 GUI 那条「源即显示件」红线的判据，一漂移，
      // 从场景库再打开就会跳过懒生成、去打一个 404 的静态 URL。
      const listRow = await page.evaluate(async (ab, want) => {
        const r = await fetch(ab + '/api/scenes?query=' + encodeURIComponent(want));
        const rows2 = (await r.json()).results;
        return rows2.filter((x) => x.name === want)
          .map((x) => ({ rel: x.rel, W: x.W, H: x.H, hasPreview: x.hasPreview,
            jpgUrl: x.jpgUrl, previewDiv: x.previewDiv, rasterPreview: x.rasterPreview }));
      }, apiBase, JPG_ROW);
      const jpgJson = listRow.find((x) => (x.rel || '').endsWith('.jpg'));
      assert(jpgJson && jpgJson.hasPreview === true
        && String(jpgJson.jpgUrl).endsWith(JPG_ROW + '.jpg') && jpgJson.previewDiv === null,
        `jpg 行自身字段不变（hasPreview=${jpgJson && jpgJson.hasPreview}/`
        + `previewDiv=${jpgJson && jpgJson.previewDiv}）`);
      assert(jpgJson.rasterPreview
        && jpgJson.rasterPreview.rasterW === 1600 && jpgJson.rasterPreview.rasterH === 800
        && jpgJson.rasterPreview.jpgW === 400 && jpgJson.rasterPreview.jpgH === 200,
        `后端回报了两边尺寸供判定（栅格 ${jpgJson.rasterPreview.rasterW}×`
        + `${jpgJson.rasterPreview.rasterH} / jpg ${jpgJson.rasterPreview.jpgW}×`
        + `${jpgJson.rasterPreview.jpgH}）`);

      const b2Preview = countUrl(previewRe);
      await clickRowButtonByDims(page, JPG_ROW, '400×200');
      await waitRowBtnByDims(page, JPG_ROW, '400×200', '打开');
      assert(countUrl(previewRe) === b2Preview + 1,
        `打开这个 jpg 行打了一次 /preview（栅格那份预览由后端落到同一处）`
        + `(${countUrl(previewRe) - b2Preview})`);
      assert(countUrl(new RegExp(`^${base}${DISK_PREFIX}${JPG_ROW}/`
        + `${JPG_ROW}_preview\\.jpg\\?div=2$`)) >= 1,
        '图走静态 /disk-array/<场景>/<场景>_preview.jpg?div=2');
      assert(fs.existsSync(path.join(jpgRowDir, JPG_ROW + '_preview.jpg')),
        '盘上真落了那份预览（不是"没报错"就算过）');

      await clickByText(page, '去查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '跳 /viewer');
      await waitFor(page, () => !!window.__viewer, 15000, '__viewer 钩子');
      const b2rec = await page.evaluate(() => window.__viewer.activeRec());
      assert(b2rec && b2rec.name === JPG_ROW, `激活的是刚打开那条（${b2rec && b2rec.name}）`);
      // 像素是**服务端从 1600×800 栅格烤的** 800×400，不是那份 400×200 的 jpg。
      assert(b2rec.thumbW === 800 && b2rec.thumbH === 400,
        `像素取服务端烤的 800×400（${b2rec.thumbW}×${b2rec.thumbH}）`);
      assert(b2rec.layout.includes('服务端已烘焙'),
        `布局文案如实写服务端烤的（${b2rec.layout}）`);
      // 行声明的 W/H 仍是那份 jpg 的 400×200 —— 显示源换了，**行的身份没换**：
      // 掩码仍按 400×200 换算（这条行是 jpg 自己的场景，与栅格那行是两个场景）。
      assert(b2rec.W === 400 && b2rec.H === 200,
        `rec 的 W/H 仍是 jpg 行自己的 400×200（${b2rec.W}×${b2rec.H}）`);

      // 收尾：删掉栅格，行数回到 3 —— 后面的失败路径用例要的是「没有同名栅格」。
      fs.rmSync(rowTif, { force: true });

      /* ---------- I. 打开失败必须落在 .sp-err（错误只写 scenes.error） ---------- */
      // 反例保护：这两处失败以前写进 viewer 的错误条（挂在 /viewer、6 秒自消失），
      // 在 /scenes 上表现为「点了按钮没反应」。这里把 JPG 换成非图字节：静态服务照旧
      // 200（不产生网络错误干扰 H 段），createImageBitmap 解码失败 → openSceneJpg 抛
      // → scenes.error → .sp-err。用 setCacheEnabled(false) 绕开 §B 已缓存的旧字节。
      console.log('\n[I] 打开失败 → .sp-err（不写 viewer 的错误条）');
      await page.setCacheEnabled(false);
      await page.goto(base + '/scenes', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitRows(page, 3);
      const jpgFile = path.join(scenesRoot, JPG_ROW, JPG_ROW + '.jpg');
      const jpgKeep = fs.readFileSync(jpgFile);
      fs.writeFileSync(jpgFile, Buffer.from('not a jpeg at all'));
      await clickRowButton(page, JPG_ROW);
      await waitFor(page, () => {
        const e = document.querySelector('.sp-err');
        return e && e.textContent.trim().length > 0;
      }, 15000, '失败提示 .sp-err');
      const errText = await page.evaluate(
        () => document.querySelector('.sp-err').textContent.trim());
      assert(errText.includes('打开「' + JPG_ROW + '」失败'),
        `失败原因显示在场景页（${errText.slice(0, 56)}…）`);
      assert(await page.evaluate(() => location.pathname.endsWith('/scenes')),
        '打开失败不跳路由（留在 /scenes）');
      fs.writeFileSync(jpgFile, jpgKeep);

      /* ---------- H. 全程无错 ---------- */
      console.log('\n[H] 全程无错');
      const appErrors = errors.filter((e) => !isIgnorableConsole(e));
      assert(appErrors.length === 0, `无浏览器错误 (${JSON.stringify(appErrors.slice(0, 4))})`);
      assert(external.length === 0, `无外部网络请求（离线约束）(${external.slice(0, 3).join(', ')})`);

      console.log(`\n[test-scenes] ✅ 全部通过 (${pass} 项断言)`);
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
  console.error('\n[test-scenes] ❌ 失败:', err.message);
  process.exit(1);
});
