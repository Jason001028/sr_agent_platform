// 盘阵场景页浏览器回归（/scenes → 懒生成 → 静态 JPG → viewer route='jpg' → 掩码换算）
// ---------------------------------------------------------------------------
// 前置：`cd frontend && npm run build`（本测试读 frontend/dist）。
// 拓扑：真 uvicorn（SR_SCENES_ROOT = 临时盘阵根）+ 本地静态服务**同时**顶替 nginx 的
//      两个 location：`/disk-array/` → 场景根（alias）、其余 → dist。
//      页面经 evaluateOnNewDocument 注入 window.__SR_CFG__ = { apiBase, staticBase }。
// 覆盖：列表/检索 → 盘阵 .jpg 源行（最小原型 §4.7：不烘焙直接开）→ 老景没预览 = 灰块
//      「已自动清除」（不点不烤；退路是路径栏粘目录）+ 当天新景的懒生成 + 静态读 JPG +
//      同构 rec → 派生件（_preview.jpg 缓存 / <名字>_mask.tif）不入
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

/* ---------------- 「当天产出的新景」夹具 ----------------
 * 场景库按**文件名里的日期**判断新旧（老景 + 没预览 → 灰块「已自动清除」，见
 * lib/scene.ts 的 presumedPurged）。夹具的日期必须取**跑测当天**：写死一个日期，
 * 过几天它自己就变成「老景」，C/F 两段的前提当场失效。 */
const FRESH_ROW = (() => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `GF01_WFV01_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}120000`;
})();

/** 造一个「今天产出的景」：2000×1000 的 .tif + 场景判据要的那份 meta.xml。 */
function addFreshScene(root) {
  const dir = path.join(root, FRESH_ROW);
  fs.mkdirSync(dir, { recursive: true });
  makeTif(path.join(dir, FRESH_ROW + '.tif'), 2000, 1000);
  fs.writeFileSync(path.join(dir, FRESH_ROW + '_meta.xml'),
    '<?xml version="1.0" encoding="UTF-8"?>');
}

/** 拆掉它（连同这一景里烤出来的预览缓存）—— 行数得回到 3，后面的断言都按 3 写。 */
function dropFreshScene(root) {
  fs.rmSync(path.join(root, FRESH_ROW), { recursive: true, force: true });
}

/* ---------------- 页面读取/操作助手 ----------------
   列一律按 CSS 类取（`.c-sat` / `.c-dims` / …），**不按 td 下标**：2026-09-22 加了
   首列勾选框（8 列 → 9 列），按下标写的断言会整体错位一位、还照样「通过」——
   取到隔壁列的文字然后与期望值比对，失败信息指向的地方和真正的原因差一整列。
   类名在 ScenesPage.vue 的 th/td 上，加列时只需改这里的一处映射。 */
const COLS = ['sat', 'sensor', 'date', 'name', 'dims', 'size', 'tag', 'act'];

function rows(page) {
  return page.evaluate((cols) =>
    [...document.querySelectorAll('.sp-tbl tbody tr')].map((tr) => {
      if (tr.querySelector('td.empty')) return null;
      const cell = (c) => tr.querySelector('td.c-' + c);
      if (!cell('act')) return null;            // 结构不对的行（表头/空行）不当行看
      const tag = tr.querySelector('td.c-tag .tag');
      const btn = tr.querySelector('td.c-act button');
      const box = tr.querySelector('td.c-pick input[type=checkbox]');
      const out = { pick: !!box, picked: !!(box && box.checked) };
      for (const c of cols) {
        if (c === 'tag' || c === 'act') continue;
        out[c] = cell(c) ? cell(c).textContent.trim() : '';
      }
      out.tag = tag ? tag.textContent.trim() : '';
      out.btn = btn ? btn.textContent.trim() : '';
      return out;
    }).filter(Boolean), COLS);
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

/** 点某场景行的「打开」按钮。灰块「已自动清除」是 disabled 的 —— 这里会直接拒绝
 *  （返回 false → 抛），这正是「不可交互」那条断言的判据。 */
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
      const dm = r.querySelector('td.c-dims');
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
 * 恒为「JPG 源」、烤完也不翻（B 段打开后那一行仍是「JPG 源」）。
 * 注意按钮文案不再是「要不要先烤」的读法（2026-09-22 起只有「打开」/灰块两种），
 * 所以它单独用**不是**收尾信号：必须先在 node 侧看到这一趟的产物（预览落盘）
 * 才能断定点击已被处理，再用它确认 open() 收尾（`openingId` 清掉）。 */
const waitRowBtnByDims = (page, name, dims, want, timeoutMs = 30000) =>
  waitFor(page, (n, d, w) => {
    const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
      const td = r.querySelector('td.name');
      const dm = r.querySelector('td.c-dims');
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

/** 用「盘阵场景」路径栏打开一个场景目录，等这一趟**收尾**（按钮从「打开中…」回到
 *  「打开」）。
 *
 *  这是灰块那条路的正经退路：老景一律按「已自动清除」呈现之后，「粘路径打开」就是
 *  打开老景的唯一入口 —— 所以它得有人钉着。`.spb-in` 是 v-model，必须补一次 input
 *  事件（同 setFilter），只写 el.value 组件里还是空的。 */
async function openViaPathBar(page, dir, done, timeoutMs = 30000) {
  await page.evaluate((p) => {
    const el = document.querySelector('.spb .spb-in');
    if (!el) throw new Error('路径栏输入框不存在');
    el.value = p;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, dir);
  const ok = await page.evaluate(() => {
    const b = [...document.querySelectorAll('.spb button')]
      .find((x) => x.textContent.trim() === '打开' && !x.disabled);
    if (!b) return false;
    b.click();
    return true;
  });
  if (!ok) throw new Error('路径栏「打开」按钮未找到或已禁用');
  // 收尾判据分两步，都**不是**「按钮文案变回去了」——那是竞态（第一次轮询可能还在
  // 点击之前）。先等这次打开在 node 侧看得见的结果（预览落到盘上），再等按钮解禁
  // （busy 含 openingId，解禁即 open() 的 finally 已经跑完）。
  await waitNode(done, timeoutMs, '路径栏打开（等落盘）');
  await waitFor(page, () => {
    const b = [...document.querySelectorAll('.spb button')]
      .find((x) => /打开/.test(x.textContent));
    return !!b && !b.disabled;
  }, timeoutMs, '路径栏收尾');
}

/** 点顶部导航的某一条（`RouterLink` → `<a>`；clickByText 只找 button）。
 *  **页内路由跳转**（不 page.goto —— 后者整页重载会清掉 pinia store，那正是 J6 要验的东西）。 */
async function clickNav(page, text) {
  const ok = await page.evaluate((t) => {
    const a = [...document.querySelectorAll('.nav-links a')]
      .find((x) => x.textContent.trim() === t);
    if (!a) return false;
    a.click();
    return true;
  }, text);
  if (!ok) throw new Error('导航链接未找到: ' + text);
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

/* ---------------- J 段（清除预览缓存）助手 ---------------- */

/** 勾选/取消某行的清除复选框（首列 `td.c-pick input[type=checkbox]`）。
 *  点的必须是 input：`.sp-tbl` 里 `tr.querySelector('button')` 是「打开」那颗按钮，
 *  首列若做成按钮形状的勾选框，B/C/F/I 段的行按钮就会取错元素。 */
async function clickRowPick(page, name) {
  const ok = await page.evaluate((n) => {
    const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
      const td = r.querySelector('td.name');
      return td && td.textContent.trim() === n;
    });
    if (!tr) return false;
    const b = tr.querySelector('td.c-pick input[type=checkbox]');
    if (!b || b.disabled) return false;
    b.click();
    return true;
  }, name);
  if (!ok) throw new Error('行勾选框未找到或已禁用: ' + name);
}

/** 读工具行/结果行里某个元素的文本（空白折叠成单空格，便于 includes 断言） */
const barText = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s);
  return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
}, sel);

/** 往「全部清除」的确认词输入框里打字。v-model 认 input 事件（同 setFilter），
 *  所以这里不能只写 el.value —— 那样 DOM 上有字、组件里的状态还是空的。 */
async function typeClearWord(page, v) {
  await page.evaluate((val) => {
    const el = document.querySelector('.scb-in');
    if (!el) throw new Error('确认词输入框不存在');
    el.value = val;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, v);
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

  // 路径栏里粘的是**用户会粘的那种形态**（Windows 形态 `W:\…`）：后端按 SR_DRIVE_MAP
  // 映射回这个临时盘阵根（与 test-manual-scene.js 同一套做法）。开发机上 os.tmpdir()
  // 在 `C:\` 下，不映射的话 to_posix_array_path 会以「未知盘符 C:」当场拒掉，
  // 灰块那条退路就永远走不通 —— 而它正是本脚本要钉的东西。
  const TMP_POSIX = tmp.replace(/\\/g, '/');
  const winPath = (p) => 'W:\\' + path.relative(tmp, p).replace(/\//g, '\\');

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
      // 路径栏（POST /api/scenes/resolve）要过归一化 + 白名单两道：见上面 winPath。
      SR_DRIVE_MAP: `W:=${TMP_POSIX}`,
      SR_ALLOWED_ROOTS: 'W:\\',
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
      assert(rs[1].tag === '已清除' && rs[1].btn === '已自动清除',
        `老景 + 没预览 = 「已清除」+ 灰块「已自动清除」（${rs[1].tag}/${rs[1].btn}）`);
      assert(rs[2].tag === '已清除', '带 .hdr 的 TIFF 同样按老景处理（日期取文件名/目录里那个）');
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

      /* ---------- C. 老景没预览 = 灰块；当天的新景照旧可点（点下去才懒生成） ---------- */
      console.log('\n[C] 老景没预览 → 灰块「已自动清除」（不点、不烤）');
      const previewJpg = path.join(scenesRoot, HDR_ROW, HDR_ROW + '_preview.jpg');
      assert(!fs.existsSync(previewJpg), '点之前盘上没有这张图的预览缓存');
      const beforeC = countUrl(previewRe);
      let refusedC = false;
      try { await clickRowButton(page, HDR_ROW); } catch (e) { refusedC = true; }
      assert(refusedC, '灰块点了也白点：按钮已禁用（点击助手拒绝，不是「点了没反应」）');
      await sleep(400);
      assert(countUrl(previewRe) === beforeC,
        `一次 /preview 都没发（不点就不烤）(${countUrl(previewRe) - beforeC})`);
      assert(!fs.existsSync(previewJpg), '盘上也没多出预览缓存');
      rs = await rows(page);
      assert(rs[2].tag === '已清除' && rs[2].btn === '已自动清除',
        `那一格当场就是灰块，不用等点一次才知道（${rs[2].tag}/${rs[2].btn}）`);

      // 新景（当天产出、同样没有预览）**不该**跟着变灰：平台只为**产物**急烤，从不
      // 预烤输入影像（`_bake_product_preview` 的注释：用户到底要不要看，在打开之前
      // 无法知道）——「没有预览」的行恰恰是还没被打开过的新景，一律变灰会把
      // 「选景 → 打开 → 画掩码 → 提交 SR」这条主链路挡掉。这里把这条链走完。
      console.log('\n[C2] 当天产出的新景：懒生成 + 静态直读（主链路仍在）');
      addFreshScene(scenesRoot);
      await relist('检索');
      await waitRows(page, 4);
      const freshRow = (await rows(page)).find((r) => r.name === FRESH_ROW);
      // 按钮文案只剩「打开」两种取值（能点 / 灰块），不再按「要不要先烤」分文案：
      // 烤不烤是点下去之后的事。
      assert(freshRow && freshRow.tag === '未生成' && freshRow.btn === '打开',
        `新景 = 「未生成」+ 可点的「打开」（${freshRow && freshRow.tag}/`
        + `${freshRow && freshRow.btn}）`);
      const previewReC = countUrl(previewRe);
      await clickRowButton(page, FRESH_ROW);
      await waitRowTag(page, FRESH_ROW, '已生成');
      assert(countUrl(previewRe) === previewReC + 1,
        `调用 1 次 /api/scenes/{id}/preview 懒生成 (${countUrl(previewRe) - previewReC})`);
      const freshJpg = path.join(scenesRoot, FRESH_ROW, FRESH_ROW + '_preview.jpg');
      assert(fs.existsSync(freshJpg), `后端落盘 ${path.basename(freshJpg)}`);
      assert(countUrl(new RegExp(`^${base}${DISK_PREFIX}${FRESH_ROW}/`
        + `${FRESH_ROW}_preview\\.jpg\\?div=2$`)) >= 1,
        '静态读图走 /disk-array/<场景>/…_preview.jpg?div=2（nginx alias 位；'
        + '查询串是击穿 max-age 用的，location 匹配不看它）');
      await waitRowBtn(page, FRESH_ROW, '打开');   // 按钮落定再读，理由见 waitRowBtn
      const rowsNow = await rows(page);
      const freshNow = rowsNow.find((r) => r.name === FRESH_ROW);
      assert(freshNow.tag === '已生成' && freshNow.btn === '打开',
        `列表行就地翻牌为「已生成」+「打开」（${freshNow.tag}/${freshNow.btn}）`);
      assert(rowsNow.filter((r) => r.tag === '已清除').length === 2,
        '没打开的两张老景不受影响（仍是灰块）');
      dropFreshScene(scenesRoot);
      await relist('检索');
      await waitRows(page, 3);

      /* ---------- D. 派生件不入列表 ---------- */
      console.log('\n[D] 烘焙出的 _preview.jpg 不算新场景');
      await relist('检索');
      await waitRows(page, 3);
      rs = await rows(page);
      assert(rs.length === 3, `重检索仍是 3 行（is_scene_file 排除派生的 _preview.jpg）(${rs.length})`);
      assert(rs.every((r) => !r.name.endsWith('_preview')), '没有一行是 _preview.jpg 缓存');
      assert(rs.every((r) => r.tag === '已清除' || r.tag === 'JPG 源'),
        `重检索没有把灰块翻回来（没人给老景烤过）(${rs.map((r) => r.tag).join(',')})`);

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
      // 开的是**老景**（HDR 行在列表里是灰块），走**路径栏**粘场景目录 —— 灰块那条路
      // 的退路就在这里被钉住：列表不给点之后，粘路径是打开老景的唯一入口，而且它同样
      // 会懒生成预览（J 段要清的就是这一份）。掩码断言仍针对 3200×2000 那一景。
      console.log('\n[F] 路径栏粘场景目录打开老景 → 「去查看器」页内跳转 → rec 与掩码换算');
      await openViaPathBar(page, winPath(path.join(scenesRoot, HDR_ROW)),
        () => fs.existsSync(previewJpg));
      assert(fs.existsSync(previewJpg), `路径栏打开也懒生成了预览 ${path.basename(previewJpg)}`);
      await clickByText(page, '去查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '跳 /viewer');
      await waitFor(page, () => !!window.__viewer, 15000, '__viewer 钩子');
      const recs = await page.evaluate(() => window.__viewer.recs());
      assert(recs.length === 3,
        `页内跳转保留 3 个 rec（未整页重载）：JPG 源 + C2 的新景 + 这一景 (${recs.length})`);
      assert(recs.every((r) => r.route === 'jpg'), '三个 rec 都是 route=jpg');

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
      //   * 按钮现在**不再**透露「打开前会不会先烤一下」（2026-09-22 起按钮只有
      //     「打开」与灰块「已自动清除」两种取值，见 ScenesPage）。所以这一支
      //     （栅格赢 → 打开时改烤栅格那份）不能靠按钮文案钉了，由下面那次点击的
      //     `/preview` 计数 + 烤出来的像素尺寸 + 布局文案三条实证钉住。
      // 把两列绑在一起判（"有更清晰的栅格 ⇒ tag 也该翻"）会逼着去改那个 tag 判据，
      // 而那正是本轮划在界外的事（§六.3）。
      const jpgRow = rs.find((r) => r.name === JPG_ROW && r.dims === '400×200');
      assert(jpgRow.tag === 'JPG 源' && jpgRow.btn === '打开',
        `tag 仍是「JPG 源」、按钮是可点的「打开」（这一行还是那个显示件）`
        + `(${jpgRow.tag}/${jpgRow.btn})`);

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
      // 先等**这一趟的产物**落到盘上（node 侧看得见，点击确实被处理了），再等按钮
      // 从「打开中…」回到「打开」（open() 收尾、rec 已经建好）。按钮文案本身现在
      // 烘焙前后都是「打开」，单独等它会读到点击之前的稳态。
      const b2Jpg = path.join(jpgRowDir, JPG_ROW + '_preview.jpg');
      await waitNode(() => fs.existsSync(b2Jpg), 30000, 'B2 那份预览落盘');
      await waitRowBtnByDims(page, JPG_ROW, '400×200', '打开');
      assert(countUrl(previewRe) === b2Preview + 1,
        `打开这个 jpg 行打了一次 /preview（栅格那份预览由后端落到同一处）`
        + `(${countUrl(previewRe) - b2Preview})`);
      assert(countUrl(new RegExp(`^${base}${DISK_PREFIX}${JPG_ROW}/`
        + `${JPG_ROW}_preview\\.jpg\\?div=2$`)) >= 1,
        '图走静态 /disk-array/<场景>/<场景>_preview.jpg?div=2');
      assert(fs.existsSync(b2Jpg), '盘上真落了那份预览（不是"没报错"就算过）');

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

      /* ---------- J. 清除预览缓存（勾选 → 清除选定 / 全部清除） ---------- */
      // 清的是**盘阵上**那份 `<源 stem>_preview.jpg`（缓存），判据里带 `srprev:` 规则戳。
      // 本节的断言分两半，后一半才是这个功能真正的风险所在：
      //   删掉的必须是缓存；
      //   留下的必须是**生产数据**（场景源 .tif/.jpg 与 `<编号>_mask.tif`）——
      //   所以逐个数着断，不是「没报错就算过」。
      console.log('\n[J] 清除预览缓存：勾选 → 清除选定 → 全部清除（要确认词）');
      await page.goto(base + '/scenes', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitRows(page, 3);
      const subDir = path.join(scenesRoot, 'sub', SUB_ROW);   // 嵌套目录那一景
      const srcFiles = [
        path.join(scenesRoot, JPG_ROW, JPG_ROW + '.jpg'),      // 源即显示件（§4.7）
        path.join(subDir, SUB_ROW + '.tif'),
        path.join(scenesRoot, HDR_ROW, HDR_ROW + '.tif'),
        path.join(scenesRoot, HDR_ROW, HDR_ROW + '_mask.tif'),  // 掩膜：SR 的输入
        path.join(subDir, SUB_ROW + '_mask.tif'),
      ];
      const srcSizes = srcFiles.map((p) => fs.statSync(p).size);
      assert(srcSizes.every((n) => n > 0), `清之前 5 份生产数据都在（${srcSizes.join(',')} 字节）`);
      const jpgPreview = path.join(jpgRowDir, JPG_ROW + '_preview.jpg');   // B2 段烤的那份

      rs = await rows(page);
      assert(rs.length === 3 && rs.every((r) => r.pick),
        `每行首列都有勾选框（${rs.filter((r) => r.pick).length}/3）`);
      assert(await page.evaluate(() => {
        const th = document.querySelector('.sp-tbl thead th.c-pick');
        return !!th && th.textContent.trim() === '';
      }), '表头勾选列留空（「全选」只在工具行上，表头再放一颗会变成两个全选）');

      /** 读某颗按钮的禁用态（找不到 = null） */
      const btnDisabled = (label) => page.evaluate((t) => {
        const b = [...document.querySelectorAll('button')]
          .find((x) => x.textContent.trim() === t);
        return b ? b.disabled : null;
      }, label);
      assert(await btnDisabled('清除选定') === true, '未勾选时「清除选定」禁用（没得清）');
      assert(await btnDisabled('全部清除') === false, '列表里有可清的行 → 「全部清除」可用');

      await clickRowPick(page, HDR_ROW);
      assert((await barText(page, '.scb-count')).includes('已选 1'),
        `勾一行后计数跟上（${await barText(page, '.scb-count')}）`);
      assert(await btnDisabled('清除选定') === false, '勾选后「清除选定」可用');

      // J1. 取消必须是真的取消：确认条先弹，取消后盘上文件一个不动。
      await clickByText(page, '清除选定');
      await waitFor(page, () => !!document.querySelector('.scb-ask'), 10000, '确认条');
      const askText = await barText(page, '.scb-ask-text');
      assert(askText.includes('不进回收站'), `确认条写明不可逆（${askText.slice(0, 34)}…）`);
      assert(fs.existsSync(previewJpg), '还没点确认 → 盘上文件没动');
      await clickByText(page, '取消');
      await waitFor(page, () => !document.querySelector('.scb-ask'), 10000, '确认条收起');
      assert(fs.existsSync(previewJpg), '点「取消」后文件仍在');

      // J2. 清除选定：删缓存 + 该行从列表移除（不重新检索）。
      await clickByText(page, '清除选定');
      await waitFor(page, () => !!document.querySelector('.scb-ask'), 10000, '确认条');
      await clickByText(page, '确认清除');
      await waitRows(page, 2);
      assert(!fs.existsSync(previewJpg), `盘上的缓存被删掉（${path.basename(previewJpg)}）`);
      rs = await rows(page);
      assert(!rs.some((r) => r.name === HDR_ROW), '该行从列表移除（没重新检索）');
      assert(rs.length === 2, `其余行原地留下（${rs.map((r) => r.name).join(',')}）`);
      const sumText = await barText(page, '.scb-sum');
      assert(sumText.includes('重新检索可回来'),
        `汇总行说清「移除」是页面局部的（${sumText}）`);
      const chipAfter = await page.evaluate(
        () => document.querySelector('.sp-src').textContent.replace(/\s+/g, ' '));
      assert(/命中 2\b/.test(chipAfter), `命中计数随移除递减（${chipAfter}）`);
      assert(srcFiles.every((p) => fs.existsSync(p)),
        '场景源与掩膜一个都没少（清的是缓存，不是数据）');

      // J2b. **不是本平台烤的同名件一个字节都不动**，且必须出现在明细里。
      // 这是整个功能最该被钉住的一条：删除判据里「有 srprev: 规则戳」那一把锁
      // 如果在浏览器里没生效，误删的就是生产数据目录里别人的文件。
      const foreign = path.join(subDir, 'MANUAL_preview.jpg');
      fs.writeFileSync(foreign, 'not our preview');
      await relist('检索');
      await waitRows(page, 3);
      await clickRowPick(page, SUB_ROW);
      await clickByText(page, '清除选定');
      await waitFor(page, () => !!document.querySelector('.scb-ask'), 10000, '确认条');
      await clickByText(page, '确认清除');
      await waitFor(page, () => !!document.querySelector('.scb-det'), 15000, '明细区');
      assert(fs.readFileSync(foreign, 'utf8') === 'not our preview',
        '没有规则戳的同名件没被删（判据生效，字节都没动）');
      rs = await rows(page);
      assert(rs.some((r) => r.name === SUB_ROW), '结论是「跳过」的行留在列表里（要让人看见）');
      const det = await barText(page, '.scb-det');
      assert(det.includes('MANUAL_preview.jpg') && det.includes('规则戳'),
        `明细逐条给出没删的那份与原因（${det.slice(0, 56)}…）`);

      // J3. 清了就真的没了 → 重新检索回来是老景 + 没预览 = 灰块（也不会自动重烤）。
      //     重烤只能走**路径栏**：列表那一格已经不给点了（这正是「已自动清除」现行口径：
      //     老景 + 盘上一份预览都没有 = 推定被清掉了），而这一景的文件其实还在盘上。
      //     灰块是**推定**不是事实，退路必须钉住 —— 这里就是钉它的地方。
      await relist('检索');
      await waitRows(page, 3);
      const hdrBack = (await rows(page)).find((r) => r.name === HDR_ROW);
      assert(hdrBack && hdrBack.tag === '已清除' && hdrBack.btn === '已自动清除',
        `清掉缓存后重检索：这一行又成了灰块（${hdrBack && hdrBack.tag}/`
        + `${hdrBack && hdrBack.btn}）`);
      assert(!fs.existsSync(previewJpg), '检索本身不会顺手把缓存烤回来');
      const beforeReopen = countUrl(previewRe);
      await openViaPathBar(page, winPath(path.join(scenesRoot, HDR_ROW)),
        () => fs.existsSync(previewJpg));
      assert(countUrl(previewRe) === beforeReopen + 1,
        `路径栏打开重新烘焙（/preview 又发一次：本地 blob 缓存也随清除失效了）`
        + `(${countUrl(previewRe) - beforeReopen})`);
      assert(fs.existsSync(previewJpg), '盘上重新落了这份缓存');
      // 打开成功也是「这一景还在盘上」的实证：列表里同一景的那一行要跟着翻回来，
      // 不能继续挂着按推定画的灰块（store.open 末尾按场景目录对回列表那一行）。
      await waitRowTag(page, HDR_ROW, '已生成');
      const hdrFlipped = (await rows(page)).find((r) => r.name === HDR_ROW);
      assert(hdrFlipped.btn === '打开',
        `那一行当场翻回来（${hdrFlipped.tag}/${hdrFlipped.btn}）——`
        + '灰块是推定，被这一趟证伪了');
      // 打开**真的收尾**这件事由 openViaPathBar 的第二个判据保证（busy 里的 openingId
      // 已清，即 open() 的 finally 跑完）：工具行整排的禁用
      // （SceneCacheBar 的 busy = clearing ‖ loading ‖ openingId）随之解除，下面
      // 「全部清除」才点得动（2026-09-22 实测撞过一次点不动）。

      // J4. 全部清除：范围 = 当前列表，且必须输入确认词。
      await clickByText(page, '全部清除');
      await waitFor(page, () => !!document.querySelector('.scb-ask'), 10000, '确认条');
      const allText = await barText(page, '.scb-ask-text');
      assert(allText.includes('当前列表里的 3 项'),
        `确认条写明范围是当前列表（${allText.slice(0, 40)}…）`);
      assert(await btnDisabled('确认清除') === true, '没输入确认词 → 「确认清除」禁用');
      await typeClearWord(page, '清');
      assert(await btnDisabled('确认清除') === true, '确认词不完整 → 仍禁用（不是「有字就行」）');
      await typeClearWord(page, '清除全部');
      assert(await btnDisabled('确认清除') === false, '输入确认词后才放行');
      await clickByText(page, '确认清除');
      await waitRows(page, 1);   // 3 → 1：SUB 那一景有同名外来件，结论是「跳过」，行留下
      assert(!fs.existsSync(previewJpg) && !fs.existsSync(jpgPreview),
        '两份缓存（本景 re-baked 的 + B2 段烤的）都清了');
      rs = await rows(page);
      assert(rs.length === 1 && rs[0].name === SUB_ROW,
        `跳过的行不跟着消失（剩 ${rs.map((r) => r.name).join(',')}）——`
        + '「清不到」和「清干净了」是两件事，行去留按结论分');
      assert(fs.readFileSync(foreign, 'utf8') === 'not our preview',
        '全部清除也没碰那份外来件');
      assert(srcFiles.every((p) => fs.existsSync(p))
        && srcFiles.every((p, i) => fs.statSync(p).size === srcSizes[i]),
        '5 份生产数据原样还在、字节数一字不差（宁可不删也不删错）');

      // J4b. 盘上真的没缓存了 → 再清一次：整批「无需清除」，行全走、空态文案换一种。
      // 空态有两个来源（清空 / 本来就没命中），这里走的是前者的文案分支。
      fs.rmSync(foreign);
      await relist('检索');
      await waitRows(page, 3);
      await clickByText(page, '全部清除');
      await waitFor(page, () => !!document.querySelector('.scb-ask'), 10000, '确认条');
      await typeClearWord(page, '清除全部');
      await clickByText(page, '确认清除');
      await waitFor(page, () => !!document.querySelector('.sp-tbl td.empty'), 15000, '空态行');
      const emptyAfter = await page.evaluate(
        () => document.querySelector('.sp-tbl td.empty').textContent.replace(/\s+/g, ' ').trim());
      assert(emptyAfter.includes('已从列表移除'),
        `空态说的是「已移除」而不是「没有场景」（${emptyAfter}）`);

      // J6. 切走再回来：**不自动重检索**，上一轮的清除结果原样还在。
      //     用户报的缺陷就是这个：以前 ScenesPage 每次挂载都 list()，回来时被摘掉的行
      //     全冒出来、结果汇总行消失，看着像「全部清除没生效」（其实盘上那份缓存确实
      //     已经删了，那一刻没有任何东西撤销它）。判据两条：页面上结果还在 + 没有新的
      //     /api/scenes 往返 —— 只断前者的话，一次「查完又清一遍」的自动检索也能蒙混。
      const listBeforeNav = countUrl(listRe);
      await clickNav(page, '查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '切到查看器');
      await clickNav(page, '场景库');
      await waitFor(page, () => location.pathname.endsWith('/scenes'), 15000, '切回场景库');
      await sleep(500);   // 给「万一还在发的检索」留出落网的时间（自动检索挂在 onMounted）
      assert(countUrl(listRe) === listBeforeNav,
        `回到场景库没有再自动检索（新增 ${countUrl(listRe) - listBeforeNav} 次 /api/scenes）`);
      const emptyAgain = await page.evaluate(() => {
        const td = document.querySelector('.sp-tbl td.empty');
        return td ? td.textContent.replace(/\s+/g, ' ').trim() : '';
      });
      assert(emptyAgain.includes('已从列表移除'),
        `被摘掉的行没有自己回来（${emptyAgain || '（空态行都没有）'}）`);
      const sumAgain = await barText(page, '.scb-sum');
      assert(sumAgain.includes('已从列表移除'),
        `上一轮的结果汇总行还在（${sumAgain.slice(0, 40)}…）`);
      assert(await page.evaluate(() => {
        const el = document.querySelector('.sp-when');
        return !!el && /上次检索 \d{2}:\d{2}/.test(el.textContent.replace(/\s+/g, ' '));
      }), '列表头写明这份数据是「上次检索」的（没有它就无法解释新场景为何不出现）');

      // J5. 回得来：重新检索 → 3 行，且不会自动重烤。
      await relist('检索');
      await waitRows(page, 3);
      rs = await rows(page);
      const hdrAgain = rs.find((r) => r.name === HDR_ROW);
      assert(hdrAgain && hdrAgain.tag === '已清除' && hdrAgain.btn === '已自动清除',
        `清完全部后没有自动重烤：这一景回到「老景 + 没预览」= 灰块`
        + `（${hdrAgain && hdrAgain.tag}/${hdrAgain && hdrAgain.btn}）`);
      assert(!fs.existsSync(previewJpg) && !fs.existsSync(jpgPreview),
        '检索本身不会顺手把缓存烤回来');

      /* ---------- K. 盘阵上文件已经没了：打开撞 404 → 那一格变「已自动清除」 ---------- */
      // 现实里的形态就是「列表比盘阵旧」：列表是「上次检索」那一刻的快照，盘阵上的生产
      // 数据却会被自动清除。于是行还在、文件已经没了 —— 点「打开」只能撞 404，而且
      // 点几次都是同一个 404（用户报的就是这个：功能「鸡肋」）。这里按同一顺序造：
      // 先在列好表之后删掉那一景的 .jpg 源（JPG 源行不烘焙、直接打静态 URL，正是那条
      // 路），再点「打开」。
      //
      // 这一段与 C/灰块那条路互补：灰块是**没点就推定**（老景 + 没预览），这里是
      // **点下去撞了真的 404** 之后的实证 —— 两者落到同一格同一个文案上。
      console.log('\n[K] 盘阵上文件已被清掉 → 打开撞 404 → 那一格改「已自动清除」');
      const jpgSrc = path.join(scenesRoot, JPG_ROW, JPG_ROW + '.jpg');
      const jpgBackup = fs.readFileSync(jpgSrc);
      fs.rmSync(jpgSrc);
      const listBefore404 = countUrl(listRe);
      await clickRowButton(page, JPG_ROW);
      await waitRowBtn(page, JPG_ROW, '已自动清除');
      assert(countUrl(listRe) === listBefore404,
        `打开失败不顺手重新检索（新增 ${countUrl(listRe) - listBefore404} 次 /api/scenes）`);
      const err404 = await page.evaluate(() => {
        const el = document.querySelector('.sp-err');
        return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
      });
      assert(err404.includes('HTTP 404') && err404.includes('已自动清除'),
        `错误行如实报 404 并说明这一行已作废（${err404.slice(0, 64)}…）`);
      rs = await rows(page);
      const goneRow = rs.find((r) => r.name === JPG_ROW);
      assert(goneRow && goneRow.tag === '已清除',
        `预览列跟着改口径（不是「已生成 / 未生成」）(${goneRow && goneRow.tag})`);
      assert(await page.evaluate((n) => {
        const tr = [...document.querySelectorAll('.sp-tbl tbody tr')].find((r) => {
          const td = r.querySelector('td.name');
          return td && td.textContent.trim() === n;
        });
        const b = tr && tr.querySelector('td.c-act button');
        return !!b && b.disabled;
      }, JPG_ROW), '「已自动清除」是不可点的灰块（不是一颗还能点的按钮）');
      // 「不可交互」要真的不可交互：再点一次，e2e 的点击助手直接拒绝（它要求按钮没禁用）
      let refused = false;
      try { await clickRowButton(page, JPG_ROW); } catch (e) { refused = true; }
      assert(refused, '再点它也是白点：按钮已禁用（点击助手拒绝，不是「点了没反应」）');

      // 列表刷新后它自己就没了 —— 这个标记不是「记住就好」的遮羞布：文件真没了，
      // 重新检索（盘上事实重读一遍）它就不再出现。
      await relist('检索');
      await waitRows(page, 2);
      rs = await rows(page);
      assert(!rs.some((r) => r.name === JPG_ROW),
        `重新检索后它不再出现（盘上真没了）：${rs.map((r) => r.name).join(',')}`);
      fs.writeFileSync(jpgSrc, jpgBackup);   // 收尾：把夹具还原（后面新加的小节别看到半解析的世界）

      /* ---------- H. 全程无错 ---------- */
      console.log('\n[H] 全程无错');
      // K 段那个 404 是**刻意**打出来的（浏览器会把它记进控制台），不是应用缺陷。
      // 只放行这一条：条数对不上就说明还有别的失败混进来了。
      const deliberate404 = /status of 404/;
      const notFound = errors.filter((e) => deliberate404.test(e));
      assert(notFound.length === 1,
        `控制台里的 404 恰好是刻意那一次（${notFound.length}）`);
      const appErrors = errors.filter(
        (e) => !isIgnorableConsole(e) && !deliberate404.test(e));
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
