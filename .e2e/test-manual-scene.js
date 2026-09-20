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
//   A. 场景库页：输入框预填**当天**前缀 → 粘 `W:\GSHC2IMPS\PRODUCT\<y>\<m>\<d>\
//      <卫星型号>\<段级目录>\<景级目录>` → 「打开」（不扫盘：只 resolve 这一次）
//      → 页内跳查看器 → rec 带上盘阵目录；
//   B. 画矩形 → 「保存掩码到盘阵」→ Node 侧断言 `<场景目录>/<编号>_mask.tif` 落盘；
//   C. 「提交 SR」→ /queue 预填（不自动提交）→ 确认提交 → 假调度器跑到「完成」；
//   D. 猜错必须报错：粘不存在的编号 → `.sp-err` 带候选路径与原因，查看器不新增 rec；
//   E. 拖本地文件进查看器 → 按**文件名 + 字节数**双指纹反推盘阵目录：
//      命中（同名同字节）→ 不做本地解码，直接换成服务端烘焙 JPG（拖入那条端点
//      /preview-drop；产物落**生产场景目录** `<编号>_preview.jpg`，不落临时缓存），
//      rec 升级成 route='jpg' + lqPath + sceneId，提交按钮转可用；
//      同名但字节数不同 / 目录不存在 → 报错（写进 rec.linkNote）且按钮仍禁用，
//      退回本地解码；文件名里没有日期 → 只提示手填，一个 resolve 请求都不发；
//   E2. 拖盘阵上的 `<编号>.jpg`（与同名 .tif 同目录）：名字对得上就关联成同一场景，
//      **像素用拖进来那张原图**（不调 /preview-drop、不调 /preview），掩码能落盘；
//      关联不上 → 弹窗给后端原因（`.notice-modal`），状态栏不卡在「正在关联…」；
//   E3. 拖**纯 RC** 目录（里面只有 `PAN.tif`）那份 `<编号>.jpg`：也要关联上。
//      jpg 名比的是**场景目录名**，不是栅格输入的 stem —— 比后者的话，纯 RC
//   E4. 盘阵上那份显示件**不够清晰**时（栅格 1600×800 vs 同名 jpg 320×160，
//      ÷2 烤出 800 > 320）→ 改用服务端从栅格烤的那份：走 /preview-drop、面板文案
//      回到默认那句。E2 是它的对照组（盘阵上没有同名 jpg；就算有，800×400 在 ÷2
//      下也判 jpg 赢 —— 判据是**严格大于**）。
//      场景恒 404，而它恰恰是 SR 真要跑的场景（真机「极少出现盘阵小标」的根因）；
//   F. PAN.tif（RC）场景：掩码名取**输入名的 stem**（`PAN_mask.tif`），不取目录名 ——
//      这正是"写出去的掩码与提交时去找的那份不一致"的陷阱（§6）。
//   G. 粘**单个 .tif 文件路径**（不在场景目录里）：照样能看，且预览按 1/2 烤进源图
//      自己的目录；但它不能提交 SR（lqPath 为 null → 按钮禁用）；
//   H. 《待修复清单》写回盘阵（POST /api/qclist/write）：导入一份 **GBK** 清单
//      （Node 编不出 GBK，用 Python 转码落盘）→ 粘 `W:\…\待修复清单.txt` → 同步 →
//      从磁盘按字节读回：未改动时与原文**完全一致**（GBK 没被转成 UTF-8）、标了终态
//      之后上半部分仍逐字不动；粘一个不存在的路径 → 后端原话进错误条、不新建文件。
//   J. 图像对比的**窗口拖放**（A–H 走的都是 input.uploadFile，那是另一条路）：真
//      DragEvent（.e2e/lib/drag.js）拖盘阵那份 `<编号>.jpg` 到**右半** → 新图只进右格、
//      左格那张不动、活动侧转右；仍走同一条盘阵关联链（resolve +1、/preview-drop +1、
//      生产那条 /preview +0）；只发 dragover（落位提示）**一个请求都不发**。
// 用法：cd .e2e && node test-manual-scene.js
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, spawnSync } = require('child_process');
const { launchPage } = require('./launchBrowser');
const drag = require('./lib/drag');

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

/* ---------------- 盘阵 fixture：真机布局 <根>/GSHC2IMPS/PRODUCT/<y>/<m>/<d>/<卫星型号>/<段级>/<景级> ---------------- */
// 每个场景目录含 `<编号>.tif`（SC）或 `PAN.tif`（RC）+ `<目录名>_meta.xml` —— 正是
// scene_search 的判据。**故意不放掩码**：手工入口要验的就是"没有掩码 → 现画 → 写到
// 服务端"，90% 的生产场景就是这个状态。
const FIXTURE_PY = `
import os, sys
import numpy as np, tifffile

root, ymd, sc_name, pan_name, prod_name, win_name = sys.argv[1:7]

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

# 生产树的层级关系（真机实测）：<日>/<卫星型号>/<段级目录>/<景级目录>，
# 段级目录名 = 景级目录名去掉「景号」那一段（…_101_0005_001 → …_101_001）。
# 反推**只认这一种形态**（旧扁平形态那条兜底候选已删），所以夹具必须是它。
def tree(scene_name):
    tok = scene_name.split("_")
    return tok[0], "_".join(tok[:5] + tok[6:])

base = os.path.join(root, "GSHC2IMPS", "PRODUCT", ymd[:4], ymd[4:6], ymd[6:8])
sat, mid = tree(sc_name)
scene(os.path.join(base, sat, mid, sc_name), sc_name + ".tif", 1600, 800, sc_name)
sat_p, mid_p = tree(pan_name)
# RC 形态：目录里只有 PAN.tif（名字里没有成像时刻，反推认不出它，只靠粘路径打开）。
scene(os.path.join(base, sat_p, mid_p, pan_name), "PAN.tif", 640, 320, pan_name)

# 输入影像叫 <景级目录名>.tif（不是 PAN.tif）：E 段拖的就是「这张图自己」，
# 双指纹要求名字也对得上 —— PAN.tif 那种 RC 形态的名字拆不出成像时间戳，
# 压根到不了指纹这一步（前端只会发文件名）。RC 形态另由 PAN_DIR 覆盖（F 段）。
sat_d, mid_d = tree(prod_name)
prod_dir = os.path.join(base, sat_d, mid_d, prod_name)
scene(prod_dir, prod_name + ".tif", 512, 256, prod_name)

# E4（工作流 B）那条的栅格：1600×800，与同目录那份**只有 320×160** 的
# <编号>.jpg 差着 5 倍。jpg 由 makeJpg 在 JS 侧落盘（本函数只管 .tif），
# 但**尺寸的对比关系必须在这里定死**：÷2 下 round(1600/2)=800 > 320 → 栅格赢。
# 三层都要拼上（段级之下还有景级目录本身）：少一层就把 tif 与 meta 写进段级目录，
# 反推看到的是一份「缺 <目录名>_meta.xml」的目录，前后端都认不出这个场景。
sat_w, mid_w = tree(win_name)
scene(os.path.join(base, sat_w, mid_w, win_name),
      win_name + ".tif", 1600, 800, win_name)

# 裸 TIF（G 段）：**不在任何场景目录里**（没有 _meta.xml、父目录也不是场景名），
# 用来验「粘单个 .tif 文件路径」。400×200 是特意选的：旧规则（长边 8192 封顶）
# 会把它整幅留下（400×200），新规则（各边 1/2）烤出 200×100 —— 尺寸断言因此
# 能区分两套规则，而不是两边都给同一个值。
tif(os.path.join(root, "loose", "LOOSE_" + ymd + "120000.tif"), 400, 200)
`;

function makeFixtures(root, ymd, scName, panName, prodName, winName) {
  const r = spawnSync('python',
    ['-c', FIXTURE_PY, root, ymd, scName, panName, prodName, winName],
    { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('fixture 生成失败: ' + (r.stderr || r.stdout));
}

/* ---------------- E2 段要拖的「盘阵 <编号>.jpg」替身 ---------------- */
// 真机上这份 jpg 是 8bit 显示就绪的预览产物：与同目录的 <编号>.tif **同名不同
// 后缀、字节数也必然不等**。尺寸取场景各边 1/2（1600×800 → 800×400），与真机那
// 份预览同尺度，缩略图 → 原图仍是 1:4 的换算。
const JPG_PY = `
import sys
import numpy as np
from PIL import Image
w, h = int(sys.argv[2]), int(sys.argv[3])
a = (np.arange(w * h, dtype=np.uint32).reshape(h, w) % 256).astype(np.uint8)
Image.fromarray(a, mode="L").save(sys.argv[1], "JPEG", quality=85)
`;

function makeJpg(file, w, h) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const r = spawnSync('python', ['-c', JPG_PY, file, String(w), String(h)],
    { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('JPEG 生成失败: ' + (r.stderr || r.stdout));
}

/* ---------------- 页面助手 ---------------- */
/**
 * 点一个按钮（按文字精确匹配）。
 *
 * 按钮在（禁用）状态时会重试到 timeoutMs —— 页面上好几个按钮跟着 store 的 loading
 * 一起灰（如「去查看器」吃 `scenes.loading`），只差一拍就判失败会把「机器忙」误报成
 * 「按钮没了」。真的等不到才抛，并把候选按钮一起带出来：不然只能看到「未找到」，
 * 无从判断是文案变了、按钮一直灰着，还是根本没渲染。
 */
async function clickByText(page, text, timeoutMs = 15000) {
  const t0 = Date.now();
  for (;;) {
    const r = await page.evaluate((t) => {
      const all = [...document.querySelectorAll('button')];
      const b = all.find((x) => x.textContent.trim() === t && !x.disabled);
      if (b) { b.click(); return { ok: true }; }
      return { ok: false, near: all
        .filter((x) => x.textContent.includes(t.slice(0, 2)))
        .map((x) => `${x.textContent.trim()}${x.disabled ? '(禁用)' : ''}`) };
    }, text);
    if (r.ok) return;
    if (Date.now() - t0 > timeoutMs) {
      throw new Error(`按钮未找到或已禁用: ${text}`
        + `（候选：${r.near.join(' / ') || '无'}）`);
    }
    await sleep(150);
  }
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

/** 侧栏文件卡上那颗「盘阵」小标的文案；没渲染出来 → null。
 *
 *  小标是**另一个条件**（`FileList.vue` 的 `v-if="rec.route === 'jpg'"`），与
 *  `__viewer.recs()` 里那个 `route` 字段不是一回事：store 里对了、DOM 不更新
 *  （响应式代理那条坑，见交接文档）时，只查 store 是查不出来的。 */
const badgeOf = (page, name) =>
  page.evaluate((n) => {
    const item = [...document.querySelectorAll('.file-item')]
      .find((el) => {
        const nm = el.querySelector('.name');
        return nm && nm.textContent.includes(n);
      });
    const b = item && item.querySelector('.name .scn');
    return b ? b.textContent.trim() : null;
  }, name);

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

/** UTF-8 文本文件 → GBK 落盘。**只能借 Python**：Node 的 Buffer 不支持 'gbk'
    （只有 utf8/latin1/utf16le/base64/hex），而盘阵上的清单正是 GBK。 */
function writeGbk(text, dst) {
  const r = spawnSync('python', ['-c',
    'import sys;open(sys.argv[2],"wb").write(sys.argv[1].encode("gbk"))',
    text, dst], { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('写 GBK 文件失败: ' + r.stderr);
}

/** 按 GBK 读回来（验「写回的是 GBK 而不是 UTF-8」）。 */
function readGbk(p) {
  const r = spawnSync('python', ['-c',
    'import sys;sys.stdout.buffer.write(open(sys.argv[1],encoding="gbk").read().encode("utf-8"))',
    p], { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('读 GBK 文件失败: ' + r.stderr);
  return r.stdout;
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
  // 临时根下的目录 → 用户会粘的 Windows 形态（W: 映射到临时根，见后端 SR_DRIVE_MAP）
  const winPath = (p) => 'W:\\' + path.relative(tmp, p).replace(/\//g, '\\');
  const WIN_PREFIX = winPath(ARRAY);       // 日期目录本身（场景栏预填的那一层）

  // 场景名一律用**合成短名**：六层生产树（<卫星型号>/<段级> 两层）要叠在
  // os.tmpdir() 前缀（~78 字符）之下，真机那种 61 字符的名字必然顶爆 Windows 260
  // 的路径上限（>250 就 FileNotFoundError）；盘阵是 Linux，没这个限制。判据一个
  // 不少 —— 卫星型号段、3 位段号、4 位景号、14 位成像时刻都在（后端反推要它）。
  const SC = 'A_B_' + ymd + '124710_200536960_101_0005_001';
  const PAN = 'A_B_' + ymd + '130500_200536960_101_0005_001';
  // 有日期、盘阵上不存在 —— 用来验"猜错必须报错"
  const MISSING = 'A_B_' + ymd + '135900_200536960_101_0005_001';
  const PROD = 'A_B_' + ymd + '124710_200536960_102_0025_001';
  // E4：盘阵上那份显示件（320×160）明显不如同名栅格（1600×800）的那一类场景。
  const WIN = 'A_B_' + ymd + '140000_200536960_101_0005_001';
  // 场景目录 = <日>/<卫星型号>/<段级目录>/<景级目录>；段级名 = 去掉景号那一段。
  // 与后端 pathguard.scene_name_layers 同一套规则，夹具 python 侧也这么拼。
  const treeOf = (name) => {
    const t = name.split('_');
    return [t[0], t.slice(0, 5).concat(t.slice(6)).join('_'), name];
  };
  const SC_DIR = path.join(ARRAY, ...treeOf(SC));
  const PAN_DIR = path.join(ARRAY, ...treeOf(PAN));
  const PROD_DIR = path.join(ARRAY, ...treeOf(PROD));
  const WIN_DIR = path.join(ARRAY, ...treeOf(WIN));

  const datahub = path.join(tmp, 'datahub');
  // 拖拽入口的临时预览缓存根（1 天 TTL，按 YYYY-MM-DD 分桶）
  const tmpPreviews = path.join(tmp, 'tmp-previews');
  const workDir = path.join(tmp, 'work');
  const upDir = path.join(tmp, 'upload');
  fs.mkdirSync(datahub, { recursive: true });
  fs.mkdirSync(tmpPreviews, { recursive: true });
  fs.mkdirSync(workDir, { recursive: true });
  fs.mkdirSync(upDir, { recursive: true });
  makeFixtures(tmp, ymd, SC, PAN, PROD, WIN);

  // 反推命中的两份必须**真的**是从盘阵上拷出来的那一份文件：判据是「文件名 +
  // 字节数」双指纹，拿 fixture 里那张凑数的小图（名字对、字节数不对）会被后端
  // 正确地拒掉 —— 那正是下面 upBadBytes 要单独立一条的用例。
  const upOk = path.join(upDir, SC + '.tif');
  const upProd = path.join(upDir, PROD + '.tif');
  const upMiss = path.join(upDir, MISSING + '.tif');
  const upNoDate = path.join(upDir, 'local_nodate.tif');
  const upBadBytes = path.join(upDir, 'badbytes', SC + '.tif');
  // E2：拖盘阵上那份 .jpg 进来（生产全名，与同名 .tif 同目录）
  const upJpg = path.join(upDir, SC + '.jpg');
  const upJpgMiss = path.join(upDir, MISSING + '.jpg');
  fs.mkdirSync(path.dirname(upBadBytes), { recursive: true });
  fs.copyFileSync(path.join(SC_DIR, SC + '.tif'), upOk);          // 同名同字节
  fs.copyFileSync(path.join(PROD_DIR, PROD + '.tif'), upProd);    // 同名同字节
  fs.copyFileSync(LOCAL_TIF, upMiss);                             // 名字对、盘阵上没有
  fs.copyFileSync(LOCAL_TIF, upNoDate);                           // 名字里没有时间戳
  fs.copyFileSync(LOCAL_TIF, upBadBytes);                         // 同名**不同字节**
  makeJpg(upJpg, 800, 400);                                       // 与 SC.tif 同目录同名
  makeJpg(upJpgMiss, 800, 400);                                   // 有日期、盘阵上没有
  // E4：盘阵上**真有**那份显示件（320×160，比同名栅格差得远），另外再复制一份
  // **尺寸不同**的到本地拖进来。两份尺寸必须不一样，断言才分得清像素来自哪边：
  // 640×320 是本地那份，「服务端从栅格烤的」是 1600×800 ÷2 = 800×400。
  const winJpg = path.join(WIN_DIR, WIN + '.jpg');
  const upWinJpg = path.join(upDir, WIN + '.jpg');
  makeJpg(winJpg, 320, 160);
  makeJpg(upWinJpg, 640, 320);

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
      // 拖拽入口的临时预览缓存：指向临时目录（不是系统 /tmp），E 段要靠它断言
      // 「拖进来的图烤在了临时缓存里，没往生产数据目录撒文件」。
      SR_TEMP_PREVIEWS_ROOT: tmpPreviews,
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
    // `?div=` 是必须吃的：档位进了 URL（换档位要击穿 nginx 的 max-age）。
    const previewRe = new RegExp(`^${apiBase}/api/scenes/[^/]+/preview\\?div=\\d+$`);
    // 拖入那道入口是**另一个端点**：产物落生产场景目录，只写一次、长期可用
    const dropPreviewRe =
      new RegExp(`^${apiBase}/api/scenes/[^/]+/preview-drop\\?div=\\d+$`);

    await page.evaluateOnNewDocument((cfg) => { window.__SR_CFG__ = cfg; },
      { apiBase, staticBase: '' });
    // 档位钉到 ÷2：本脚本测的是「拖入 / 关联 / 掩码 / 提交」这条链，不是档位
    // 本身 —— 尺寸断言（1600×800 → 800×400）都是按 1/2 写的，跟随产品默认的
    // ÷4 会让每一处都变成 400×200，把「链通了没有」淹在一堆数字改动里。
    // 必须 try/catch：这个回调在 about:blank 上也会跑，opaque origin 下
    // localStorage 抛 SecurityError，而本脚本末尾有一条 appErrors 必须为 0 的断言。
    await page.evaluateOnNewDocument(() => {
      try {
        localStorage.setItem('sr.previewDiv', '2');
      } catch (e) { /* opaque origin（about:blank）：真实页面加载时会再跑一次 */ }
    });

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

      const scWin = winPath(SC_DIR);
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
      // 等 activeRec 而不是 recs().length：openSceneJpg 现在**先**把 rec 推进列表
      // 再去解码烘焙字节，只看条数会撞进「推了但还没装好」那一瞬（route 还是 null）。
      await waitFor(page, () => {
        const r = window.__viewer.activeRec();
        return !!r && r.route === 'jpg';
      }, 30000, '场景装进查看器');
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

      // 工具栏在 1366 视口（真机常见宽度）下不许横向溢出：定位组件右侧新插的
      // 档位拖动条是有代价的，宽度预算得有人守着 —— 溢出时右端的「提交 SR」会被
      // 挤出可视区，而那正是这条链最后要点的那个按钮。
      await page.setViewport({ width: 1366, height: 768 });
      const tb = await page.evaluate(() => {
        const el = document.querySelector('.toolbar');
        if (!el) return null;
        const box = el.getBoundingClientRect();
        return {
          scrollW: el.scrollWidth,
          clientW: el.clientWidth,
          over: [...el.children]
            .filter((c) => c.getBoundingClientRect().right > box.right + 0.5)
            .map((c) => c.className || c.tagName),
        };
      });
      assert(tb && tb.scrollW <= tb.clientW + 1,
        `1366 视口下工具栏不横向溢出（scrollW=${tb && tb.scrollW} / clientW=${tb && tb.clientW}）`);
      assert(tb && tb.over.length === 0,
        `没有控件被挤出工具栏右缘（${tb && tb.over.join(',')}）`);
      const divsel = await page.evaluate(() => {
        const el = document.querySelector('[data-e2e="preview-div"]');
        const r = el ? el.querySelector('input[type=range]') : null;
        return el ? { text: el.textContent.trim(), value: r ? r.value : null,
          max: r ? r.max : null } : null;
      });
      assert(divsel && divsel.max === '4',
        `档位拖动条在定位组件右侧、5 档（${divsel && JSON.stringify(divsel)}）`);
      assert(divsel && divsel.value === '0' && divsel.text.includes('1/2'),
        `拖动条停在 localStorage 里那个档位（本脚本钉的是 ÷2）(${divsel && divsel.text})`);
      await page.setViewport({ width: 800, height: 600 });

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
      await openPathBar(page, winPath(path.join(ARRAY, ...treeOf(MISSING))));
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

      /* ---------- E. 拖本地文件 → 双指纹反推 → 走服务端烘焙 JPG ---------- */
      console.log('\n[E] 查看器拖本地 tif → 同名同字节才关联，命中即换服务端 JPG');
      await clickLink(page, '查看器');
      await waitFor(page, () => location.pathname.endsWith('/viewer'), 15000, '回 /viewer');
      const input = await page.$('input[type=file]');
      if (!input) throw new Error('未找到 input[type=file]');
      const lastRec = () => page.evaluate(() => {
        const rs = window.__viewer.recs();
        return rs[rs.length - 1];
      });
      const srEnabled = () => page.evaluate(() => {
        const b = [...document.querySelectorAll('.toolbar button')]
          .find((x) => x.textContent.trim() === '提交 SR');
        return b ? !b.disabled : null;
      });

      const previewBefore = countUrl(previewRe);
      const dropBefore = countUrl(dropPreviewRe);
      // 平台自己那份 `<编号>.preview.jpg` 在 A 段粘路径打开时就已经落下了（那条链
      // 走 /preview，落源同目录）。这里要钉的是「拖入链**不碰**它」—— 用 mtime
      // 而不是存在性：它本来就该在，判存在性等于什么都没验。
      const platJpg = path.join(SC_DIR, SC + '.preview.jpg');
      const platMtime = fs.existsSync(platJpg) ? fs.statSync(platJpg).mtimeMs : null;
      assert(platMtime !== null, 'A 段粘路径打开时已落下平台那份 .preview.jpg');
      await input.uploadFile(upOk);
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.lqPath;
      }, 20000, '双指纹反推命中');
      const linked = await lastRec();
      assert(linked.name === SC + '.tif',
        `rec 还是用户拖进来的那个文件（${linked.name}）`);
      assert(linked.lqPath === SC_DIR.replace(/\\/g, '/'),
        `命中即关联上盘阵目录（${linked.lqPath}）`);
      // 命中之后**不再做本地解码**：像素来自服务端烘焙 JPG（各边 1/2），
      // 尺寸是元数据的 1600×800、缩略图 800×400 —— 本地解码这张 2.5MB 的
      // uint16 也出得来缩略图，所以判据取 route 与字节来源。
      assert(linked.route === 'jpg',
        `命中即升级成盘阵 JPG 路由（route=${linked.route}）`);
      assert(linked.W === 1600 && linked.H === 800,
        `尺寸取影像头 1600×800（${linked.W}×${linked.H}）`);
      assert(linked.thumbW === 800 && linked.thumbH === 400,
        `像素来自服务端 1/2 烘焙 JPG（缩略图 ${linked.thumbW}×${linked.thumbH}）`);
      assert(!!linked.sceneId, `升级后带上场景 id（${String(linked.sceneId).slice(0, 12)}…）`);
      assert(await srEnabled(), '关联成功后「提交 SR」由灰转可用');
      assert(countUrl(dropPreviewRe) === dropBefore + 1
        && countUrl(previewRe) === previewBefore,
        '取的是拖入专用端点 /preview-drop，没碰生产那条 /preview');
      // 产物落在**生产场景目录**里（`<编号>_preview.jpg`）：烤一次长期可用，
      // 而不是每天第一次拖入都重烤一遍 —— 这是这次改动的要点。
      const dropJpg = path.join(SC_DIR, SC + '_preview.jpg');
      assert(fs.existsSync(dropJpg),
        `拖入的预览写进生产场景目录（${path.basename(dropJpg)}）`);
      // 平台自己那份长期缓存（点号名）不该被这条链动过：两份产物分工不同
      // （点号那份给场景库的静态 URL 用、下划线那份是拖入链的），混了的话
      // `hasPreview`/`previewDiv` 那套判定就跟着乱。
      assert(fs.statSync(platJpg).mtimeMs === platMtime,
        '拖入链没动平台自己那份 <编号>.preview.jpg（两份产物各归各的）');
      // 场景目录可写 → **不该**走兜底：临时缓存桶里一个新文件都不该有
      const bucket = path.join(tmpPreviews, y + '-' + mo + '-' + d);
      assert(!fs.existsSync(bucket), '场景目录可写时不动临时缓存（兜底没被触发）');

      await waitFor(page, () => {
        const t = document.querySelector('.toast');
        return t && t.textContent.includes('已关联盘阵目录');
      }, 10000, '关联 toast');

      // 生产树真形态（<日>/<卫星型号>/<段级目录>/<景级目录>）：多两层也要命中。
      // 反推候选由后端算（前端只发裸文件名），这里钉的就是那条生产树候选。
      await input.uploadFile(upProd);
      await waitFor(page, (want) => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.lqPath === want;
      }, 20000, '生产树反推关联命中', PROD_DIR.replace(/\\/g, '/'));
      const prodLinked = await lastRec();
      assert(prodLinked.route === 'jpg',
        `生产树命中并升级成 JPG 路由：${PROD_DIR.replace(/\\/g, '/')}`);
      // 生产树命中同样落**它自己的**场景目录（不是上一个场景那个目录）
      const prodJpg = path.join(PROD_DIR, PROD + '_preview.jpg');
      assert(fs.existsSync(prodJpg),
        `生产树命中的预览落它自己的场景目录（${path.basename(prodJpg)}）`);
      // 这个场景只被拖入链碰过（没从场景库打开过）→ 平台那份压根不该存在
      assert(!fs.existsSync(path.join(PROD_DIR, PROD + '.preview.jpg')),
        '拖入链不写平台自己那份 <编号>.preview.jpg');

      // 同名**不同字节**：最要紧的一条回归钉子 —— 只比名字的话，用户拖进来的
      // 是另一张图，掩码坐标会整片落在别的影像上。必须拒绝并退回本地解码。
      await input.uploadFile(upBadBytes);
      // 等到「报错 + 本地解码已收尾」：route 要等 decodeRec 跑完才有值，早一步
      // 看到的是 null，断言会误判成「没退回本地解码」。
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.linkNote && r.route && r.thumbW > 0;
      }, 30000, '字节数不符 → 报错并退回本地解码');
      const bad = await lastRec();
      assert(bad.linkNote.includes('字节数'),
        `同名不同字节被拒，原因写进 linkNote（${bad.linkNote.slice(0, 60)}…）`);
      assert(bad.lqPath === null, '指纹不符就不写 lqPath（绝不静默提交）');
      assert(['utif', 'chunked', 'sparse'].includes(bad.route),
        `退回浏览器本地解码（route=${bad.route}）`);
      assert((await srEnabled()) === false, '「提交 SR」保持禁用');

      // 文件名里有日期但盘阵上没有这个目录 → 报错，按钮仍禁用
      await input.uploadFile(upMiss);
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.linkNote;
      }, 20000, '反推失败的报错');
      const miss = await lastRec();
      assert(miss.linkNote.includes(MISSING) && miss.linkNote.includes('目录不存在'),
        `反推落空时说明原因与候选（${miss.linkNote.slice(0, 60)}…）`);
      assert(miss.lqPath === null, '没命中就不写 lqPath（绝不静默提交）');
      assert((await srEnabled()) === false, '「提交 SR」保持禁用');

      // 文件名里没有成像时间戳 → 后端报「没有时间戳」，提示手粘目录；不写 lqPath
      await input.uploadFile(upNoDate);
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.linkNote && r.linkNote.includes('时间戳');
      }, 20000, '无时间戳报错');
      assert(await page.evaluate(() => {
        const rs = window.__viewer.recs();
        return rs[rs.length - 1].lqPath === null;
      }), '取不到日期就不写 lqPath（绝不静默提交）');

      /* ---------- E2. 拖盘阵上的 .jpg → 关联同一场景目录 ---------- */
      // 用户的下手方式：直接从盘阵目录里把那张 <编号>.jpg 拖进查看器。它与
      // <编号>.tif 同目录、同 stem、不同后缀 —— 名字那一半对得上就算同一个场景
      // （字节数那一半对 JPEG 无意义：那是另一份产物，永远不可能与 TIF 同字节）。
      console.log('\n[E2] 拖盘阵 .jpg → 关联同场景目录，掩码能落盘');
      const bakeBefore = countUrl(previewRe);
      const dropBakeBefore = countUrl(dropPreviewRe);
      await input.uploadFile(upJpg);
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.route === 'jpg';
      }, 20000, '拖入的 jpg 关联上场景目录');
      const jpgRec = await lastRec();
      assert(jpgRec.name === SC + '.jpg',
        `rec 还是用户拖进来的那个文件（${jpgRec.name}）`);
      assert(jpgRec.lqPath === SC_DIR.replace(/\\/g, '/'),
        `关联到同一场景目录（${jpgRec.lqPath}）`);
      assert(!!jpgRec.sceneId, `升级后带上场景 id（${String(jpgRec.sceneId).slice(0, 12)}…）`);
      assert(jpgRec.W === 1600 && jpgRec.H === 800,
        `W/H 取影像头 1600×800（${jpgRec.W}×${jpgRec.H}）—— 掩码换算回原图就靠它`);
      // 本次改动的要点：像素用**拖进来那张 jpg 自己**的，不去服务端烤一份预览。
      // 早先只有 .tif 才试关联，jpg 一律 route='img'；改成两条路合并后，若照搬
      // tif 那条（调 /preview-drop）就会拿服务端缩图顶掉用户自己拖的图。
      assert(countUrl(dropPreviewRe) === dropBakeBefore
        && countUrl(previewRe) === bakeBefore,
        '没走 /preview-drop 也没走 /preview（用拖进来的原图，不服务端烘焙）');
      assert(jpgRec.thumbW === 800 && jpgRec.thumbH === 400,
        `缩略图就是拖进来那张 jpg 的像素（${jpgRec.thumbW}×${jpgRec.thumbH}）`);
      assert(jpgRec.layout.includes('拖入的原图'),
        `布局按实际来源写，不谎称「服务端已烘焙」（${jpgRec.layout}）`);
      // 用户看得见的那一件事：侧栏文件卡上那颗「盘阵」小标。它由 route 决定，
      // 但**必须落到 DOM 上**才算数（store 对、页面不更新是踩过的坑）。
      const jpgBadge = await badgeOf(page, SC + '.jpg');
      assert(jpgBadge === '盘阵', `侧栏出现「盘阵」小标（${jpgBadge}）`);
      const jpgBtns = await toolbarState(page);
      assert(jpgBtns.bake === true && jpgBtns.sr === true,
        `「保存掩码到盘阵」「提交 SR」均由灰转亮（bake=${jpgBtns.bake}/sr=${jpgBtns.sr}）`);
      // B 段写过同一份掩码，先撤掉 —— 否则"文件在"证明不了是这次写的
      fs.rmSync(scMask, { force: true });
      await page.evaluate(() => window.__viewer.commitRect({ x0: 80, y0: 40, x1: 240, y1: 120 }));
      await clickByText(page, '保存掩码到盘阵');
      await waitNode(() => isTiff(scMask), 30000, 'jpg 场景的掩码落盘');
      assert(fs.statSync(scMask).size > 0,
        `掩码写进场景目录（${path.basename(scMask)}）`);

      // 关联不上时**弹窗**说清（拖 jpg 的人多半就是冲着场景目录来的），
      // 而不是一句 6 秒就消失的 toast —— 后端那句常常是「哪个目录缺什么」。
      await input.uploadFile(upJpgMiss);
      await waitFor(page, () => !!document.querySelector('.notice-modal'), 20000, '失败弹窗');
      const modal = await page.evaluate(() => {
        const el = document.querySelector('.notice-modal');
        return {
          title: el.querySelector('.nm-title').textContent.trim(),
          body: el.querySelector('.nm-body').textContent.trim(),
          hint: el.querySelector('.nm-hint') ? el.querySelector('.nm-hint').textContent.trim() : '',
        };
      });
      assert(modal.body.includes(MISSING) && modal.body.includes('目录不存在'),
        `弹窗给出后端的原因与候选（${modal.body.slice(0, 56)}…）`);
      assert(modal.hint.includes('盘阵场景'),
        `弹窗给出下一步怎么办（${modal.hint.slice(0, 30)}…）`);
      // 关联失败后状态栏不能卡在「正在关联盘阵目录…」—— jpg 这条路后面没有解码
      // 会去覆盖它（tif 那条有），得由关联自己把文案放回去。
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && !r.status.includes('正在关联');
      }, 10000, '状态文案复位');
      const missJpg = await lastRec();
      assert(missJpg.route === 'img' && missJpg.lqPath === null,
        `没关联上就仍是本地图片（route=${missJpg.route}/lqPath=${JSON.stringify(missJpg.lqPath)}）`);
      assert((await toolbarState(page)).bake === false, '「保存掩码到盘阵」保持禁用');
      assert(await badgeOf(page, MISSING + '.jpg') === null,
        '没关联上的 jpg 不出现「盘阵」小标');
      await page.evaluate(() => window.__viewer.hideModal());
      await waitFor(page, () => !document.querySelector('.notice-modal'), 5000, '弹窗关闭');
      assert(true, '弹窗可关（「知道了」/ Esc 同一条路）');

      /* ---------- E3. 拖**纯 RC** 目录（只有 PAN.tif）里的 <编号>.jpg ---------- */
      // 真机报的「极少出现盘阵小标」就是这个：RC 场景目录里只有 `PAN.tif`
      // （`<编号>.tif` 不存在），而那份显示件叫 `<编号>.jpg`。早先后端拿 jpg 名去
      // 比**栅格输入**的 stem（"PAN"），永远比不过 —— 于是只有「目录里恰好还躺着
      // `<编号>.tif`」的场景（SC，或 RC 目录留着上游 SC 产物）才关联得上，看着就
      // 像随机。E2 那一条走的是 SC 目录，复现不出这个 bug。
      console.log('\n[E3] 拖纯 RC 目录（只有 PAN.tif）的 <编号>.jpg → 仍要关联上');
      const upPanJpg = path.join(upDir, PAN + '.jpg');
      makeJpg(upPanJpg, 320, 160);                  // 各边 1/2，与真机那份显示件同尺度
      await input.uploadFile(upPanJpg);
      await waitFor(page, () => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return r && r.route === 'jpg';
      }, 20000, '纯 RC 目录的 jpg 关联上场景目录');
      const panJpgRec = await lastRec();
      assert(panJpgRec.name === PAN + '.jpg',
        `rec 还是用户拖进来的那个文件（${panJpgRec.name}）`);
      assert(panJpgRec.lqPath === PAN_DIR.replace(/\\/g, '/'),
        `关联到 PAN 场景目录（${panJpgRec.lqPath}）`);
      // 掩码名仍按**输入影像**的 stem（PAN_mask.tif）—— jpg 只是显示件，
      // 关联上之后这条 rec 就是那个 RC 场景，提交时读的还是 PAN.tif。
      assert(String(panJpgRec.serverMaskPath).endsWith('/PAN_mask.tif'),
        `掩码路径仍是输入影像那份（…${String(panJpgRec.serverMaskPath).slice(-16)}）`);
      assert(panJpgRec.W === 640 && panJpgRec.H === 320,
        `W/H 取影像头 640×320（${panJpgRec.W}×${panJpgRec.H}）`);
      assert(panJpgRec.layout.includes('拖入的原图'),
        `像素用拖进来那张（${panJpgRec.layout}）`);
      const panJpgBadge = await badgeOf(page, PAN + '.jpg');
      assert(panJpgBadge === '盘阵',
        `纯 RC 场景的 jpg 也出「盘阵」小标（${panJpgBadge}）`);

      /* ---------- E4. 盘阵那份显示件不够清晰 → 改用服务端从栅格烤的那份 ---------- */
      // 真机上这件事就是用户报的那句话：目录里那份预生成的显示件（PAN.jpg 之类）
      // 分辨率不够，实际预览得改成服务端从配套 .tif 下采样。判据只有一条：
      //   round(max(栅格长边)/div) > max(显示件长边)
      // 这边栅格 1600×800、显示件 320×160、档位钉在 ÷2（本脚本开头）→ 800 > 320
      // 成立，于是像素改用服务端那份。E2 是**对照组**：那边盘阵上没有同名 jpg，
      // 就算补一个 800×400 的，÷2 烤出 800 也不严格大于 800 → 判 jpg 赢，一字不动。
      console.log('\n[E4] 同名栅格更清晰 → 预览改用服务端下采样（工作流 B）');
      const winBakeBefore = countUrl(previewRe);
      const winDropBefore = countUrl(dropPreviewRe);
      await input.uploadFile(upWinJpg);
      // 等到**新加的那条** rec 装载走完：只等 route 会撞上「已关联、像素还没到」，
      // 只判状态更糟 —— 上一条（E3 那条）本来就是「场景就绪」，条件立刻为真、
      // 等于没等。失败也放行，好把后端那句话原样带进断言消息里。
      const recsBefore = await page.evaluate(() => window.__viewer.recs().length);
      await input.uploadFile(upWinJpg);
      try {
        await waitFor(page, (n) => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          return rs.length > n && r.route === 'jpg'
            && (r.status === '场景就绪' || r.statusCls === 'err');
        }, 30000, '拖入的 jpg 关联上并换成服务端预览', recsBefore);
      } catch (e) {
        const dbg = await page.evaluate(() => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          const m = document.querySelector('.notice-modal');
          return { n: rs.length, last: r && { name: r.name, route: r.route,
            status: r.status, cls: r.statusCls, linkNote: r.linkNote },
            modal: m ? m.querySelector('.nm-body').textContent.trim() : null,
            err: document.querySelector('.err-box')?.textContent.trim() ?? null };
        });
        throw new Error(`E4 诊断：${JSON.stringify(dbg)}`);
      }
      const winRec = await lastRec();
      const winErr = await page.evaluate(
        () => document.querySelector('.err-box')?.textContent.replace(/\s+/g, ' ').trim() ?? '');
      assert(winRec.status === '场景就绪',
        `装载走完没报错（status=${winRec.status}${winErr ? ' / ' + winErr : ''}）`);
      assert(winRec.lqPath === WIN_DIR.replace(/\\/g, '/'),
        `关联到那个场景目录（${winRec.lqPath}）`);
      // 走的是**拖入那条**端点：落点在场景目录里（<编号>_preview.jpg），
      // 不是生产那条 <stem>.preview.jpg —— 两条落点不同，混了会互相顶掉。
      // 计数断言要**等**：页面那边的状态与 Node 这边的 `request` 回调是两条独立
      // 的投递（同一条 CDP 连接，但 evaluate 的响应可能抢在那条 request 事件前面
      // 回到 Node），加载已完成而计数还没涨是常态、不是缺陷。所以先等它涨上来。
      await waitNode(() => countUrl(dropPreviewRe) >= winDropBefore + 1, 15000,
        '拖入预览这一跳的请求计数');
      // 面板文案回到默认那句：走服务端时前端不传 layout，由 openSceneJpg 自己写。
      assert(winRec.layout.includes('服务端已烘焙'),
        `面板如实说是服务端烤的（${winRec.layout}）`);
      assert(winRec.layout.includes('1/2'),
        `并写明是哪一档（${winRec.layout}）`);
      // 像素来自**服务端从栅格烤的** 800×400，不是本地那份 640×320：
      // 两边的尺寸是特意错开的，这一条就是「谁赢」的可执行判据。
      assert(winRec.thumbW === 800 && winRec.thumbH === 400,
        `像素取服务端从 1600×800 栅格烤的 800×400（${winRec.thumbW}×${winRec.thumbH}）`);
      assert(winRec.W === 1600 && winRec.H === 800,
        `W/H 仍是影像头尺寸 1600×800（${winRec.W}×${winRec.H}）`);
      assert(!winRec.layout.includes('拖入的原图'),
        '不谎称用的是拖进来那张原图');
      // 盘上真有那么一份产物：这是「改了显示源」与「只是没报错」的分界。
      const winDrop = path.join(WIN_DIR, WIN + '_preview.jpg');
      await waitNode(() => jpegSize(winDrop), 30000, '拖入预览落盘');
      const winSz = jpegSize(winDrop);
      assert(winSz.w === 800 && winSz.h === 400,
        `落盘那份也是 800×400（${winSz.w}×${winSz.h}）`);
      // 计数在这里才断（上面等过一次、中间又过了几拍页面往返，该到的请求都到了）：
      // **恰好一次** /preview-drop、且一次都没碰生产那条 /preview。写成 +N 而不是
      // 不断言，是为了让「重复拖入 / 重复取图」这类回归在这里就露头。
      assert(countUrl(dropPreviewRe) === winDropBefore + 1
        && countUrl(previewRe) === winBakeBefore,
        `只走了一次 /preview-drop，没碰生产那条 /preview`
        + `（drop +${countUrl(dropPreviewRe) - winDropBefore}`
        + ` / preview +${countUrl(previewRe) - winBakeBefore}）`);

      /* ---------- F. PAN.tif（RC）场景的掩码命名 ---------- */
      console.log('\n[F] PAN.tif 场景：掩码名取输入名 stem，不取目录名');
      await openPathBar(page, winPath(PAN_DIR));
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

      /* ---------- H. 《待修复清单》写回盘阵（POST /api/qclist/write） ---------- */
      console.log('\n[H] 导入 GBK 清单 → 粘盘阵路径 → 同步 → 从磁盘按字节读回');
      // 真机页面是 http://内网IP，而浏览器的 File System Access API 是 [SecureContext]
      // 标的 —— 原先那条「原地覆盖写盘」在真机上永远点不通。写盘改走后端（§3.7），
      // 于是这一步头一次进得了回归。三件事要在这条链路上钉死：
      //   ① 导入认得出 GBK（下半部分的状态词是 GBK 字节）；
      //   ② 写回**真按 GBK 写**（浏览器只有 UTF-8 编码器，原先只能降级成 UTF-8+BOM）；
      //   ③ 上半部分逐字没动。
      const QC_TXT = '待修复清单.txt';
      const QC = path.join(tmp, QC_TXT);            // 在 W:\ 底下（= 白名单内）
      const qcTop = SC + ',\t产品存在伪影 (问题类型:产品存在伪影 行列号:7300.26,1737.98 影像类型:pan )\t李佳峻';
      const qcDoc0 = qcTop + '\n\n' + SC + '\t修复通过\n';
      writeGbk(qcDoc0, QC);
      const qcBytes0 = fs.readFileSync(QC);
      assert(!qcBytes0.toString('utf-8').includes('产品存在伪影'),
        'fixture 真按 GBK 落盘（按 UTF-8 读是乱码）');

      const qcInput = await page.$('.qc-h input[type=file]');
      assert(!!qcInput, '清单面板的导入入口在 DOM 里（那个隐藏 input）');
      await qcInput.uploadFile(QC);
      await waitFor(page, () => window.__viewer.qcState().loaded, 10000, '清单导入');
      const qs2 = await page.evaluate(() => window.__viewer.qcState());
      assert(qs2.total === 1 && qs2.statuses[SC] === 'fixed',
        `GBK 清单解析出 1 行、下半部分读到「已修复」（${qs2.total} 行）`);
      assert(qs2.sourceEncoding === 'gbk', `按 GBK 解码（${qs2.sourceEncoding}）`);
      assert(qs2.targetPath === '', '刚导入时写回路径是空的（要人自己粘）');

      const foot = await page.evaluate(() => {
        const b = document.querySelector('.qc-sync');
        return { path: !!document.querySelector('.qc-path'), disabled: b ? b.disabled : null };
      });
      assert(foot.path && foot.disabled === true, '页脚有路径输入框，路径为空时「同步」禁用');

      // 粘 W:\ 形态路径 → 同步。什么都不改时写回 == 原文，且必须是**同一个字节序列**：
      // 这一条同时证明「GBK 没被转成 UTF-8」「上半部分逐字保留」「换行符没被改」。
      await page.evaluate((p) => window.__viewer.qcSetTarget(p), winPath(QC));
      assert(await page.evaluate(() => window.__viewer.qcSync()) === true, '同步成功（后端写盘）');
      assert(fs.readFileSync(QC).equals(qcBytes0),
        '未改动时写回：磁盘字节与原文完全一致（GBK 仍是 GBK）');

      // 标一个终态再同步：下半部分整个重排，上半部分仍逐字不动。
      await page.evaluate((n) => window.__viewer.qcSetStatus(n, 'rejected'), SC);
      assert(await page.evaluate(() => window.__viewer.qcSync()) === true, '改标后再同步成功');
      const qcBytes1 = fs.readFileSync(QC);
      assert(readGbk(QC) === qcTop + '\n\n' + SC + '\t驳回\n',
        '写回内容 = 上半部分原文 + 空行 + 终态行（按 GBK 读回逐字比对）');
      assert(qcBytes1.length < qcBytes0.length,
        `「驳回」比「修复通过」短，字节数随之变小（${qcBytes0.length} → ${qcBytes1.length}）`);
      const qcToast = await page.evaluate(
        () => document.querySelector('.toast')?.textContent.replace(/\s+/g, ' ').trim() ?? '');
      assert(qcToast.indexOf('已同步至') >= 0 && qcToast.indexOf('GBK') >= 0,
        `成功提示带盘阵路径与编码（${qcToast}）`);

      // 拒绝路径：后端原话进错误条，不静默；且别的文件一个字节没动。
      await page.evaluate(() => window.__viewer.qcSetTarget('W:\\没有这份清单.txt'));
      assert(await page.evaluate(() => window.__viewer.qcSync()) === false,
        '盘阵上不存在那份清单：同步失败（不新建文件）');
      const qcErr = await page.evaluate(
        () => document.querySelector('.err-box')?.textContent.replace(/\s+/g, ' ').trim() ?? '');
      assert(/文件不存在/.test(qcErr), `错误条给的是后端原话（${qcErr}）`);
      assert(fs.readFileSync(QC).equals(qcBytes1), '失败的那次没有碰任何文件');
      await page.evaluate(() => window.__viewer.qcClose());

      /* ---------- J. 分屏对比：窗口拖放落右半 ---------- */
      // A–H 走的都是 `input.uploadFile`（文件选择框那条路）。真实用户是把图**拖**进
      // 画布的：那条链挂在 window 上，落点决定进哪一格（TifCanvas 的 onDrop →
      // store.addFiles(files, side)）。用真 DragEvent 把整条链跑通，钉住三件只在这条
      // 路上才会发生的事：
      //   ① 落点算出的 side 真的穿到了 placeRec —— 新图进右格、左格那张不被顶掉；
      //   ② 拖入走的仍是**同一条**盘阵关联链（resolve 一次 + /preview-drop 一次）；
      //   ③ 只发 dragover（落位提示）**一个请求都不发** —— 提示是纯本地状态。
      console.log('\n[J] 分屏对比：窗口拖放落右半 → 只进右格，仍走盘阵关联链');
      await drag.installDragKit(page);   // 每次页面导航后都要重装（E 段导航过一次）
      // 这份 jpg 与 E4 那份同名，但**字节数不同**：前端按 name+size 去重，同名同字节
      // 会被认成「列表里已经有了」而走 placeRec，一条新 rec 都不开，这条用例就白写了。
      // jpg 的盘阵反推认的是**文件名**（E2/E4 已证），改尺寸不影响它命中哪个场景。
      const upSplitJpg = path.join(upDir, 'split', WIN + '.jpg');
      fs.mkdirSync(path.dirname(upSplitJpg), { recursive: true });
      makeJpg(upSplitJpg, 500, 250);   // 500 < 800（栅格 1600 的 ÷2）→ 判服务端赢

      const leftBefore = await page.evaluate(() => {
        const r = window.__viewer.activeRec();
        return r ? r.id : null;
      });
      await page.evaluate(() => window.__viewer.setCmpMode('split'));
      const panes0 = await page.evaluate(() => window.__viewer.cmpPanes());
      const cw = await page.evaluate(
        () => document.querySelector('canvas.view-canvas').getBoundingClientRect().width);
      assert(panes0.length === 2, `切分屏后是两格（${panes0.length}）`);
      assert(panes0[0].rect.w + panes0[1].rect.w === Math.round(cw),
        `两格宽之和恰好等于画布宽，无缝无叠（${panes0[0].rect.w}+${panes0[1].rect.w} / ${Math.round(cw)}）`);
      assert(panes0[0].recId === leftBefore && leftBefore !== null,
        `左格接住进入分屏前那张（${panes0[0].recId} / ${leftBefore}）`);
      assert(panes0[1].recId === null, `右格是空的，等着接拖进来的图（${panes0[1].recId}）`);

      const pts = await drag.canvasPoints(page);
      const jSeenBefore = seen.length;
      const over = await drag.dragOverOnly(page,
        { files: [{ path: upSplitJpg }], clientX: pts.right, clientY: pts.midY });
      // 等一拍再数请求：dragover 真要发什么，request 事件要在 Node 这边报出来
      // 才数得到；不等就断，这条断言等于什么都没验。
      await sleep(400);
      assert(over.hint.active === true && over.hint.side === 'B',
        `悬停右半：落位提示指向右格（${JSON.stringify(over.hint)}）`);
      assert(seen.length === jSeenBefore,
        `只发 dragover 不产生任何请求（${seen.length - jSeenBefore} 条）`);

      const jResBefore = countUrl(resolveRe);
      const jDropBefore = countUrl(dropPreviewRe);
      const jBakeBefore = countUrl(previewRe);
      const jjnBefore = await page.evaluate(() => window.__viewer.recs().length);
      const dropped = await drag.dropFiles(page,
        { files: [{ path: upSplitJpg }], clientX: pts.right, clientY: pts.midY });
      assert(dropped.defaultPrevented === true,
        'drop 被处理掉了（preventDefault：浏览器不会导航到拖进来的文件）');
      // 这里**不**断「马上多一条 rec」：.jpg 走的是 openLocalImage，它先
      // `await decodeJpgToCanvas(file)` 才 push rec —— 同一个 evaluate 里同步读到的
      // 条数必然还没变，那不是缺陷。.tif 那条（openOne）是同步 push 的，
      // `test-vue-viewer.js` H 段断的就是那个瞬时 +1；两条路各有各的口径。
      // 落列与否由下面那个 waitFor 判（条件里带 lqPath 与 thumbW，真的等到装载走完）。
      assert(dropped.after >= dropped.before,
        `落图这个动作本身没抛（条数 ${dropped.before} → ${dropped.after}，jpg 异步入列）`);
      try {
        await waitFor(page, (n) => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          return rs.length > n && r.lqPath && r.thumbW > 0;
        }, 30000, '落右半的 jpg 关联上并换成服务端预览', jjnBefore);
      } catch (e) {
        const dbg = await page.evaluate(() => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          const m = document.querySelector('.notice-modal');
          return { n: rs.length, last: r && { name: r.name, route: r.route,
            status: r.status, lqPath: r.lqPath, linkNote: r.linkNote },
            panes: window.__viewer.cmpPanes().map((p) => [p.side, p.recId]),
            modal: m ? m.querySelector('.nm-body').textContent.trim() : null,
            err: document.querySelector('.err-box')?.textContent.trim() ?? null };
        });
        throw new Error(`J 诊断：${JSON.stringify(dbg)}`);
      }
      const jrec = await lastRec();
      assert(jrec.name === WIN + '.jpg', `rec 是拖进来那个文件（${jrec.name}）`);
      assert(jrec.lqPath === WIN_DIR.replace(/\\/g, '/'),
        `关联到盘阵那个场景目录（${jrec.lqPath}）`);
      // 500×250 是本地那份、800×400 是服务端从 1600×800 栅格烤的 ÷2 —— 两边尺寸
      // 特意错开，这一条就是「像素来自哪边」的可执行判据（与 E4 同一套判据，
      // 换到拖放这条路上再验一次）。
      assert(jrec.thumbW === 800 && jrec.thumbH === 400,
        `像素取服务端烤的 800×400，不是拖进来那张 500×250（${jrec.thumbW}×${jrec.thumbH}）`);
      await waitNode(() => countUrl(dropPreviewRe) >= jDropBefore + 1, 15000,
        '落右半这一跳的请求计数');
      const panes1 = await page.evaluate(() => window.__viewer.cmpPanes());
      assert(panes1[1].recId === jrec.id,
        `新图落在**右**格（右格 recId=${panes1[1].recId} / 新 rec=${jrec.id}）`);
      assert(panes1[0].recId === leftBefore,
        `左格那张没被顶掉（${panes1[0].recId} / ${leftBefore}）`);
      assert(panes1[1].active === true && panes1[0].active === false,
        '活动侧跟着落点转到右侧（掩码/云量/状态栏跟着它）');
      const hint1 = await page.evaluate(() => window.__viewer.dragHint());
      assert(hint1.active === false && hint1.side === null,
        `落图之后落位提示已经收掉（${JSON.stringify(hint1)}）`);
      // 计数在这里才断：中间过了几拍页面往返，该到的请求都到了。写成 +N 而不是不断言，
      // 是为了让「重复拖入 / 拖入顺带又取了一次图」这类回归在这里露头。
      assert(countUrl(resolveRe) === jResBefore + 1
        && countUrl(dropPreviewRe) === jDropBefore + 1
        && countUrl(previewRe) === jBakeBefore,
        `拖入链仍是 resolve 一次 + /preview-drop 一次，没碰生产那条 /preview`
        + `（resolve +${countUrl(resolveRe) - jResBefore}`
        + ` / drop +${countUrl(dropPreviewRe) - jDropBefore}`
        + ` / preview +${countUrl(previewRe) - jBakeBefore}）`);
      // 回单幅：分屏是**不动后端**的那一半，收尾把模式还原，免得影响下面 §I 的
      // 错误计数口径（对比模式下画布少一半，任何后续交互都可能落在画布外）。
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      const panes2 = await page.evaluate(() => window.__viewer.cmpPanes());
      assert(panes2.length === 1, `回「关闭」后仍是单幅（${panes2.length}）`);

      /* ---------- I. 全程无错 ---------- */
      console.log('\n[I] 全程无错');
      // D / E / E2 里**刻意**打出来的 404：D 一次（盘阵上没有那个目录）、E 两次
      // （同名不同字节；有日期但目录不存在）、E2 一次（同上，换成 .jpg 拖入）。
      // 刻意打的 400 有两次：E 一次（文件名没有时间戳，后端据此拒绝反推）、
      // H 一次（往盘阵上不存在的清单路径写回）。浏览器对任何非 2xx 响应都会往
      // 控制台写一条，这不算程序缺陷，但也不能睁一只眼闭一只眼：数目必须恰好
      // 等于刻意的那几次，多一条就是有别的资源没取到。
      const deliberate = /status of (404|400)/;
      const notFound = errors.filter((e) => /status of 404/.test(e));
      assert(notFound.length === 4,
        `控制台里的 404 恰好是刻意的那四次（${notFound.length}）`);
      const badRequest = errors.filter((e) => /status of 400/.test(e));
      assert(badRequest.length === 2,
        `控制台里的 400 恰好是刻意的那两次（无时间戳反推 + 不存在的清单，${badRequest.length}）`);
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
