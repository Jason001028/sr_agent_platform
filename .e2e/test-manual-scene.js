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
//      命中（同名同字节）→ 不做本地解码，直接换成服务端生成 JPG（拖入那条端点
//      /preview-drop；产物落**生产场景目录** `<编号>_preview.jpg`，不落临时缓存），
//      rec 升级成 route='jpg' + lqPath + sceneId，提交按钮转可用；
//      同名但字节数不同 / 目录不存在 → 报错（写进 rec.linkNote）且按钮仍禁用，
//      退回本地解码；文件名里没有日期 → 只提示手填，一个 resolve 请求都不发；
//   E2. 拖盘阵上的 `<编号>.jpg`（与同名 .tif 同目录）：名字对得上就关联成同一场景，
//      **像素用拖进来那张原图**（不调 /preview）；另外后台静默生成一跳 /preview-drop
//      （落 `<编号>_preview.jpg` 进场景目录，不遮罩、不改像素）；
//      关联不上 → 弹窗给后端原因（`.notice-modal`），状态栏不卡在「正在关联…」；
//   E3. 拖**纯 RC** 目录（里面只有 `PAN.tif`）那份 `<编号>.jpg`：也要关联上。
//      jpg 名比的是**场景目录名**，不是栅格输入的 stem —— 比后者的话，纯 RC
//      场景恒 404，而它恰恰是 SR 真要跑的场景（真机「极少出现盘阵小标」的根因）；
//   E4. 盘阵上那份显示件**不够清晰**时（栅格 1600×800 vs 同名 jpg 320×160，
//      ÷2 生成出 800 > 320）→ 改用服务端从栅格生成的那份：走 /preview-drop、面板文案
//      回到默认那句。E2 是它的对照组（盘阵上没有同名 jpg；就算有，800×400 在 ÷2
//      下也判 jpg 赢 —— 判据是**严格大于**）。
//   F. PAN.tif（RC）场景：掩码名取**输入名的 stem**（`PAN_mask.tif`），不取目录名 ——
//      这正是"写出去的掩码与提交时去找的那份不一致"的陷阱（§6）。
//   G. 粘**单个 .tif 文件路径**（不在场景目录里）：照样能看，且预览按 1/2 生成进源图
//      自己的目录；但它不能提交 SR（lqPath 为 null → 按钮禁用）；
//   H. 《待修复清单》写回盘阵（POST /api/qclist/write）：导入一份 **GBK** 清单
//      （Node 编不出 GBK，用 Python 转码落盘）→ 粘 `W:\…\待修复清单.txt` → 同步 →
//      从磁盘按字节读回：未改动时与原文**完全一致**（GBK 没被转成 UTF-8）、标了终态
//      之后上半部分仍逐字不动；粘一个不存在的路径 → 后端原话进错误条、不新建文件。
//   J. 图像对比的**窗口拖放**（A–H 走的都是 input.uploadFile，那是另一条路）：真
//      DragEvent（.e2e/lib/drag.js）拖盘阵那份 `<编号>.jpg` 到**右半** → 新图只进右格、
//      左格那张不动、活动侧转右；仍走同一条盘阵关联链（resolve +1、/preview-drop +1、
//      生产那条 /preview +0）；只发 dragover（落位提示）**一个请求都不发**。
//   L. 本轮新增的两件事（J 已覆盖「从外面拖文件进来」，L 覆盖「拖左栏卡片」与
//      「拖中间产物」）：
//      L1–L3 左栏卡片自己带票（`application/x-sr-rec`）拖进画布 → 活动侧换图；
//            分屏下按落点进 B 格；对比模式拖到画布外沿用那句 toast，活动图不动。
//            （卡片没带 `draggable`、画布只认 `Files` 时这条链根本不存在。）
//      L2b   刚进分屏、右格空着时把**左格那张自己**拖到右半：左格那张仍在（旧规则的
//            「互换」在空目标格上等于把左格挖空 —— 2026-09-22 用户报的「左图变为空」）。
//      L4    拖中间产物 `<编号>_sr.jpg`（同级有 `<编号>_sr.tif`）：认出 kind=product、
//            卡片上「盘阵+序号+SR」三标齐全（序号与本体那张**同号**）、只读小标、
//            三颗修复按钮置灰；W/H 取产物自己的栅格（3200×1600）而像素仍是拖进来的
//            原图（1600×800）；后台静默生成 `<编号>_sr_preview.jpg`（1600×800 —— 与
//            本体那份 800×400 差一倍，据此分清生成的是哪一份栅格）。
//      L5    产物上三道修复入口逐条打一遍：绘制掩码被拒且不进绘制态、保存掩码返回
//            false 且**本体那份掩码的 mtime 一个字节没动**（本次唯一的破坏性风险）、
//            提交 SR 不跳队列页且队列一条不多。
//      L6–L7 本轮（2026-09-24）新增的「未超分那份」：L6 拖入一景的**本体显示件** →
//            平台去同一场景目录找 `<目录名>_NOSR.tif` 并同时降采样到 jpg（只对那一份发
//            一次 /preview，落点尺寸按当前档位）；L7 把生成的那份 jpg 拖回来 → 卡片上
//            「盘阵 + 同序号 + NOSR」三标齐全（缺它时那颗标只是文案侥幸对、配色属于
//            产物那族）、只读小标在、修复入口同样堵死。
//            现造一景（SC2）而不是复用前面的：预热按**场景目录**记账，同一景这一会话
//            里只做一次，复用前面那景只能验到「第二次不再生成」。
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

/** 后端子进程的输出尾巴 —— **失败时打出来**。
 *
 *  原来这里是 `() => {}`，后端的日志直接进黑洞。而 `GET /api/queue` 上一条
 *  「CORS policy: No 'Access-Control-Allow-Origin'」的浏览器报错，正是一个**后端 500
 *  的形状**：Starlette 的 500 由 `ServerErrorMiddleware` 发出，而它在 `CORSMiddleware`
 *  **外面**，于是那份响应天然不带 CORS 头，浏览器只报 CORS、不报 500。
 *  见不到 traceback 就只能靠猜 —— 猜一次就是整轮重跑（几分钟）。
 *  只留尾部若干行：跑通时一行都不打，不刷屏。 */
const backendLog = [];
const BACKEND_LOG_KEEP = 40;
function noteBackend(chunk) {
  for (const line of String(chunk).split(/\r?\n/)) {
    if (!line.trim()) continue;
    backendLog.push(line);
    if (backendLog.length > BACKEND_LOG_KEEP) backendLog.shift();
  }
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
# 会把它整幅留下（400×200），新规则（各边 1/2）生成出 200×100 —— 尺寸断言因此
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

/* ---------------- K 段要用的「SR 产物」替身 ---------------- */
// 与 FIXTURE_PY 里那个 tif() 同一套写法（uint16 灰度 + 无压缩），保证后端读
// 尺寸走的是与其它夹具完全一样的路径。产物在真机上由 SR 跑出来，夹具里没有，
// 而 K 段要验的正是「盘上已有一份现成预览时预取会去取它」—— 没有这份就没有
// 可预取的对象，那几条断言等于没写。
const TIF_PY = `
import sys
import numpy as np, tifffile
w, h = int(sys.argv[2]), int(sys.argv[3])
a = (np.arange(w * h, dtype=np.uint32).reshape(h, w) % 4096).astype(np.uint16)
tifffile.imwrite(sys.argv[1], a, photometric="minisblack")
`;

function makeTif(file, w, h) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const r = spawnSync('python', ['-c', TIF_PY, file, String(w), String(h)],
    { encoding: 'utf-8' });
  if (r.status !== 0) throw new Error('TIFF 生成失败: ' + (r.stderr || r.stdout));
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
  // 640×320 是本地那份，「服务端从栅格生成的」是 1600×800 ÷2 = 800×400。
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
      // 「拖进来的图生成在了临时缓存里，没往生产数据目录撒文件」。
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
  child.stdout.on('data', noteBackend);
  child.stderr.on('data', noteBackend);

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
      // 再去解码生成预览字节，只看条数会撞进「推了但还没装好」那一瞬（route 还是 null）。
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
      assert(row.mask === '掩码 ' + SC + '_mask.tif',
        `任务行显示实际用的掩码（「${row.mask}」）`);

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

      /* ---------- E. 拖本地文件 → 双指纹反推 → 走服务端生成 JPG ---------- */
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
      // 2026-09-22 起预览只有一个名字 `<栅格 stem>_preview.jpg`：平台那份（A 段粘路径
      // 打开时走 /preview 生成的）与拖入链的落点**是同一个文件**。这条钉「打开时就已经
      // 有一份」，也是下面「场景目录里只有一份预览」的前提。
      const platJpg = path.join(SC_DIR, SC + '_preview.jpg');
      assert(fs.existsSync(platJpg),
        'A 段粘路径打开时已落下 <编号>_preview.jpg（平台自己那份）');
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
      // 命中之后**不再做本地解码**：像素来自服务端生成 JPG（各边 1/2），
      // 尺寸是元数据的 1600×800、缩略图 800×400 —— 本地解码这张 2.5MB 的
      // uint16 也出得来缩略图，所以判据取 route 与字节来源。
      assert(linked.route === 'jpg',
        `命中即升级成盘阵 JPG 路由（route=${linked.route}）`);
      assert(linked.W === 1600 && linked.H === 800,
        `尺寸取影像头 1600×800（${linked.W}×${linked.H}）`);
      assert(linked.thumbW === 800 && linked.thumbH === 400,
        `像素来自服务端 1/2 预览 JPG（缩略图 ${linked.thumbW}×${linked.thumbH}）`);
      assert(!!linked.sceneId, `升级后带上场景 id（${String(linked.sceneId).slice(0, 12)}…）`);
      assert(await srEnabled(), '关联成功后「提交 SR」由灰转可用');
      assert(countUrl(dropPreviewRe) === dropBefore + 1
        && countUrl(previewRe) === previewBefore,
        '取的是拖入专用端点 /preview-drop，没碰生产那条 /preview');
      // 产物落在**生产场景目录**里（`<编号>_preview.jpg`）：生成一次长期可用，
      // 而不是每天第一次拖入都重新生成一遍 —— 这是这次改动的要点。落点与打开时那份
      // 同名，所以场景目录里不会躺着两个几乎同名的文件（用户报的就是这个）。
      const dropJpg = path.join(SC_DIR, SC + '_preview.jpg');
      assert(fs.existsSync(dropJpg),
        `拖入的预览写进生产场景目录（${path.basename(dropJpg)}）`);
      assert(fs.readdirSync(SC_DIR).filter((n) => /preview/i.test(n)).length === 1,
        '场景目录里只有一份预览名（改名前的点号那份没有残留）');
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
      // 改名前的点号那份不再产出（旧名只会在各条链处理到那份栅格时被同时删掉）
      assert(!fs.existsSync(path.join(PROD_DIR, PROD + '.preview.jpg')),
        '点号那份不再产出');

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
      // 失败也放行，好把后端那句话 / 弹窗原样带进断言消息里（E4 同一套做法）：
      // 只报「超时」看不出是关联被拒了、还是解码那一步卡住了。
      try {
        await waitFor(page, () => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          return r && r.route === 'jpg';
        }, 20000, '拖入的 jpg 关联上场景目录');
      } catch (e) {
        const dbg = await page.evaluate(() => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          const m = document.querySelector('.notice-modal');
          return { n: rs.length, last: r && { name: r.name, route: r.route,
            status: r.status, statusCls: r.statusCls, linkNote: r.linkNote,
            lqPath: r.lqPath },
            modal: m ? m.querySelector('.nm-body').textContent.trim() : null,
            err: document.querySelector('.err-box')?.textContent.trim() ?? null };
        });
        throw new Error(`E2 诊断：${JSON.stringify(dbg)}`);
      }
      const jpgRec = await lastRec();
      assert(jpgRec.name === SC + '.jpg',
        `rec 还是用户拖进来的那个文件（${jpgRec.name}）`);
      assert(jpgRec.lqPath === SC_DIR.replace(/\\/g, '/'),
        `关联到同一场景目录（${jpgRec.lqPath}）`);
      assert(!!jpgRec.sceneId, `升级后带上场景 id（${String(jpgRec.sceneId).slice(0, 12)}…）`);
      assert(jpgRec.W === 1600 && jpgRec.H === 800,
        `W/H 取影像头 1600×800（${jpgRec.W}×${jpgRec.H}）—— 掩码换算回原图就靠它`);
      // 本次改动的要点：像素用**拖进来那张 jpg 自己**的，不去服务端生成一份预览。
      // 早先只有 .tif 才试关联，jpg 一律 route='img'；改成两条路合并后，若照搬
      // tif 那条（调 /preview-drop）就会拿服务端缩图顶掉用户自己拖的图。
      //
      // 像素不走服务端，但**请求仍要发一次**（2026-09-21 起）：拖 jpg 进来之后后台
      // 静默生成一份 `<这份影像的 stem>_preview.jpg` 进场景目录 —— 用户口径是「盘阵上
      // 得留下这一份」，而像素仍用他拖进来的原图。所以要钉的是两件事分开：像素
      // 没被顶掉（上面两条），以及那一跳**恰好生成的是这一环节自己的栅格**
      // （rec.sceneId = 本体那份 `<编号>.tif` 的场景 id，不是别的目录）。
      const dropPreviewUrl = (sid) =>
        `${apiBase}/api/scenes/${encodeURIComponent(sid)}/preview-drop?div=2`;
      await waitNode(() => countUrl(dropPreviewRe) >= dropBakeBefore + 1, 15000,
        '拖 jpg 之后的后台静默生成');
      const bakeUrls = seen.filter((u) => dropPreviewRe.test(u)).slice(dropBakeBefore);
      assert(bakeUrls.length === 1 && bakeUrls[0] === dropPreviewUrl(jpgRec.sceneId),
        `后台静默生成的是这一环节自己的栅格（${bakeUrls[0]}）`);
      assert(countUrl(previewRe) === bakeBefore,
        `生产那条 /preview 一次没碰（+${countUrl(previewRe) - bakeBefore}）`);
      assert(jpegSize(path.join(SC_DIR, SC + '_preview.jpg')) !== null,
        `场景目录里那份 ${SC}_preview.jpg 在（拖入链的落点，长期留存）`);
      assert(jpgRec.thumbW === 800 && jpgRec.thumbH === 400,
        `缩略图就是拖进来那张 jpg 的像素（${jpgRec.thumbW}×${jpgRec.thumbH}）`);
      assert(jpgRec.layout.includes('拖入的原图'),
        `布局按实际来源写，不谎称是盘阵生成的那份（${jpgRec.layout}）`);
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

      /* ---------- E4. 盘阵那份显示件不够清晰 → 改用服务端从栅格生成的那份 ---------- */
      // 真机上这件事就是用户报的那句话：目录里那份预生成的显示件（PAN.jpg 之类）
      // 分辨率不够，实际预览得改成服务端从配套 .tif 下采样。判据只有一条：
      //   round(max(栅格长边)/div) > max(显示件长边)
      // 这边栅格 1600×800、显示件 320×160、档位钉在 ÷2（本脚本开头）→ 800 > 320
      // 成立，于是像素改用服务端那份。E2 是**对照组**：那边盘阵上没有同名 jpg，
      // 就算补一个 800×400 的，÷2 生成出 800 也不严格大于 800 → 判 jpg 赢，一字不动。
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
      // 走的是**拖入那条**端点：落点在场景目录里（`<编号>_preview.jpg`）。
      // 名字与平台自己那份相同（2026-09-22 起落点统一），差别只剩由哪条链触发。
      // 计数断言要**等**：页面那边的状态与 Node 这边的 `request` 回调是两条独立
      // 的投递（同一条 CDP 连接，但 evaluate 的响应可能抢在那条 request 事件前面
      // 回到 Node），加载已完成而计数还没涨是常态、不是缺陷。所以先等它涨上来。
      await waitNode(() => countUrl(dropPreviewRe) >= winDropBefore + 1, 15000,
        '拖入预览这一跳的请求计数');
      // 面板文案回到默认那句：走服务端时前端不传 layout，由 openSceneJpg 自己写
      // （默认那句 = 「盘阵 JPG（N 尺度 + 直方图均衡）」，不再带「服务端已生成」）。
      assert(winRec.layout.includes('盘阵 JPG') && !winRec.layout.includes('服务端已生成'),
        `面板文案回到默认那句、不赘述生成预览方（${winRec.layout}）`);
      assert(winRec.layout.includes('1/2'),
        `并写明是哪一档（${winRec.layout}）`);
      // 像素来自**服务端从栅格生成的** 800×400，不是本地那份 640×320：
      // 两边的尺寸是特意错开的，这一条就是「谁赢」的可执行判据。
      assert(winRec.thumbW === 800 && winRec.thumbH === 400,
        `像素取服务端从 1600×800 栅格生成的 800×400（${winRec.thumbW}×${winRec.thumbH}）`);
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
      const looseJpg = path.join(LOOSE_DIR, LOOSE + '_preview.jpg');
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

      // 1/2 尺度的落盘证据：缓存落在源同目录、名字是 `<源 stem>_preview.jpg`；像素恰好
      // 一半。400×200 → 200×100；旧规则（长边 8192 封顶）会留下整幅 400×200。
      await waitNode(() => !!jpegSize(looseJpg), 30000, '裸 TIF 的预览落盘');
      const sz = jpegSize(looseJpg);
      assert(sz.w === 200 && sz.h === 100,
        `预览 JPG 各边为源图 1/2（400×200 → ${sz.w}×${sz.h}）`);
      assert(fs.existsSync(looseJpg)
        && path.dirname(looseJpg) === LOOSE_DIR,
        `预览生成在源图**自己的目录**里（${path.basename(looseJpg)}）`);

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

      /* ---------- H2. 清单上的名字**缺产品段** → 按那行的影像类型补回来再打开 ---------- */
      // 2026-09-27 用户报的 bug：质检部门的清单第一列**约定俗成省掉产品段**（写
      // `…_101_0005_001_L1`，而盘阵上景级目录叫 `…_101_0005_001_L1_PAN`，第二列里
      // 写着「影像类型:pan」）。名字少这一段，反推出来的**景级与段级目录名会一起**
      // 少一段（段级名就是从景级名去掉景号得来的），两个日期候选全落空 → 404，整批
      // 图一行都打不开。补哪一段由那一行的影像类型定，这一列没写就 `_PAN`（用户口径）。
      //
      // 这条从**面板上那颗「打开」按钮**走：cleanName → imgType → openByName →
      // POST /api/scenes/resolve {name, product} → 后端补段反推。验的是用户报的那条路
      // 本身，而不是某一段的中间产物。
      console.log('\n[H2] 清单名字缺产品段：按影像类型补 _PAN / _MSS 再打开盘阵场景');
      // 盘阵上**只有带产品段的那两个目录名**。两行的影像类型故意一个 pan 一个 MSS：
      // 「一律补 _PAN」的实现能过第一条、过不了第二条（MSS 那条补 _PAN 必然 404）。
      const PD_PAN = 'A_B_' + ymd + '142500_200536960_101_0005_001_L1_PAN';
      const PD_MSS = 'A_B_' + ymd + '142600_200536960_101_0007_001_L1_MSS';
      const pdPanDir = path.join(ARRAY, ...treeOf(PD_PAN));
      const pdMssDir = path.join(ARRAY, ...treeOf(PD_MSS));
      const PD_META = '<?xml version="1.0" encoding="UTF-8"?>'
        + '<SolarAzimuth>181.79</SolarAzimuth>';
      for (const [dir, nm] of [[pdPanDir, PD_PAN], [pdMssDir, PD_MSS]]) {
        makeTif(path.join(dir, nm + '.tif'), 800, 400);
        fs.writeFileSync(path.join(dir, nm + '_meta.xml'), PD_META);
      }
      // 清单第一列 = 去掉产品段的那份原文（约定俗成的写法）
      const pdPanBare = PD_PAN.slice(0, -'_PAN'.length);
      const pdMssBare = PD_MSS.slice(0, -'_MSS'.length);
      const QC2 = path.join(tmp, '待修复清单_缺产品段.txt');
      writeGbk(
        pdPanBare + ',\t产品存在伪影 (问题类型:产品存在伪影 行列号:7300.26,1737.98 影像类型:pan )\t李佳峻\n'
        + pdMssBare + ',\t产品存在伪影 (问题类型:产品存在伪影 行列号:120.5,88.25 影像类型:MSS )\t李佳峻\n',
        QC2);
      await (await page.$('.qc-h input[type=file]')).uploadFile(QC2);
      await waitFor(page, () => window.__viewer.qcState().loaded, 10000, '缺产品段清单导入');
      const qs3 = await page.evaluate(() => window.__viewer.qcState());
      assert(qs3.total === 2,
        `这一份清单解析出 2 行（${qs3.total} —— 与 H 那份不是同一份）`);
      // 行上的影像类型是补哪一段的**唯一**依据，它得真的从描述里抽出来了（点下去
      // 之后传什么全看它），所以先在这儿钉一下，别让后面的断言替它背锅。
      const qcRowText = await page.evaluate(
        () => [...document.querySelectorAll('.qc-row')]
          .map((li) => li.textContent.replace(/\s+/g, ' ').trim()));
      assert(qcRowText.length === 2
        && qcRowText[0].indexOf('pan') >= 0 && qcRowText[1].indexOf('MSS') >= 0,
        `两行都从描述里抽出了影像类型（${qcRowText.join(' | ')}）`);

      /** 点清单里某一行（按第一列的名字找）的「打开」按钮 —— 真实 DOM 点击，不走钩子。 */
      const clickRowOpen = (bare) => page.evaluate((n) => {
        const row = [...document.querySelectorAll('.qc-row')].find((li) => {
          const el = li.querySelector('.qc-name');
          return el && el.textContent.trim() === n;
        });
        if (!row) return { ok: false, why: '清单里没有这一行' };
        const btn = row.querySelector('button.qc-mini');
        if (!btn) return { ok: false, why: '这一行没有「打开」按钮' };
        if (btn.disabled) return { ok: false, why: '「打开」按钮还是灰的' };
        btn.click();
        return { ok: true };
      }, bare);

      /** 点开这一行，等查看器真的装上**那一景**（看 lqPath + rec 名，不看条数）。 */
      const openQcRow = async (bare, dir, nm, label) => {
        const hit = await clickRowOpen(bare);
        assert(hit.ok, `${label}：行上有可点的「打开」按钮（${JSON.stringify(hit)}）`);
        let rec;
        try {
          rec = await waitFor(page, (want) => {
            const a = window.__viewer.activeRec();
            return a && a.lqPath === want.dir && a.name === want.name
              ? { name: a.name, lqPath: a.lqPath, route: a.route, W: a.W, H: a.H }
              : null;
          }, 30000, label, { dir: dir.replace(/\\/g, '/'), name: nm });
        } catch (e) {
          const dbg = await page.evaluate(() => {
            const a = window.__viewer.activeRec();
            const err = document.querySelector('.err-box');
            return {
              active: a ? { name: a.name, lqPath: a.lqPath } : null,
              err: err ? err.textContent.replace(/\s+/g, ' ').trim() : null,
            };
          });
          throw new Error(`${label} 诊断：${JSON.stringify(dbg)}`);
        }
        // 开出来了就不许再弹「打开失败」：面板拿 openByName 的返回值当判据，
        // 返回值非空即喂给 viewer 的错误条。这一条是回归那半个 bug —— 图开了、
        // 红条却说失败（早先 openByName 成功后还兜底返回了个错误串）。
        const errTxt = await page.evaluate(
          () => document.querySelector('.err-box')?.textContent.replace(/\s+/g, ' ').trim() ?? '');
        assert(errTxt.indexOf('打开「') < 0,
          `${label}：开出来了就没弹「打开失败」的红条（${errTxt ? errTxt.slice(0, 50) : '错误条为空'}）`);
        return rec;
      };

      // ① 影像类型 pan：盘阵上只有 `…_PAN`，名字原样试必然 404 —— 能开出来就说明产品
      //    段真的补上去了。同时钉住「补段是后端的事」：前端一次 resolve 都没多花。
      const h2ResBefore = countUrl(resolveRe);
      const panRec2 = await openQcRow(pdPanBare, pdPanDir, PD_PAN, 'pan 那行开 _PAN 场景');
      assert(countUrl(resolveRe) - h2ResBefore === 1,
        `前端按名字只发一次 resolve，补哪几段由后端定（${countUrl(resolveRe) - h2ResBefore} 次）`);
      assert(panRec2.route === 'jpg' && panRec2.W === 800 && panRec2.H === 400,
        `开出来的是盘阵那一景的栅格，尺寸取影像头（route=${panRec2.route} / ${panRec2.W}×${panRec2.H}）`);

      // ② 影像类型 MSS：这一条**只有真按那行补 _MSS 才开得出来**（默认补 _PAN 会 404）。
      const mssRec2 = await openQcRow(pdMssBare, pdMssDir, PD_MSS, 'MSS 那行开 _MSS 场景');
      assert(mssRec2.lqPath !== panRec2.lqPath,
        `两行落在两个不同的场景目录（${mssRec2.lqPath} / ${panRec2.lqPath}）`);

      // ③ 影像类型只定**顺序**，不是「不递就打不开」：MSS 那行不递提示照样开得出来
      //    （后端两条补法都试），区别在谁排前面、以及 404 时怎么解释。返回值必须是
      //    空的 —— 非空就会被面板当成失败弹红条（上面那条断言的反面）。
      const mssNoHint = await page.evaluate((n) => window.__viewer.qcOpenByName(n), pdMssBare);
      assert(mssNoHint === '',
        `不递影像类型也开得出来（后端两条补法都试）：返回 ${
          mssNoHint === '' ? "''（成功）" : JSON.stringify(mssNoHint)}`);

      // ④ 反推全落空时，404 要自己说清「补过哪几段」：候选里凭空多出两条自己拼的
      //    目录名，不说清用户只会更懵 —— 这正是用户看到的那句话。
      const pdGone = 'A_B_' + ymd + '143500_200536960_101_9999_001_L1';
      const goneErr = await page.evaluate((n) => window.__viewer.qcOpenByName(n), pdGone);
      assert(typeof goneErr === 'string' && goneErr.indexOf('没有产品段') >= 0
        && goneErr.indexOf(pdGone + '_PAN') >= 0 && goneErr.indexOf(pdGone + '_MSS') >= 0,
        `盘上两条补法都没有时，404 点明补过哪几段（…${goneErr.slice(-70)}）`);

      /* ---------- J. 分屏对比：窗口拖放落右半 ---------- */
      // A–H2 走的都是 `input.uploadFile`（文件选择框那条路）。真实用户是把图**拖**进
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
      // 500×250 是本地那份、800×400 是服务端从 1600×800 栅格生成的 ÷2 —— 两边尺寸
      // 特意错开，这一条就是「像素来自哪边」的可执行判据（与 E4 同一套判据，
      // 换到拖放这条路上再验一次）。
      assert(jrec.thumbW === 800 && jrec.thumbH === 400,
        `像素取服务端生成的 800×400，不是拖进来那张 500×250（${jrec.thumbW}×${jrec.thumbH}）`);
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
      // 是为了让「重复拖入 / 拖入同时又取了一次图」这类回归在这里露头。
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

      /* ---------- K. 场景芯片去重 + 对比模式后台预取 ---------- */
      // 两件事都属于「别为同一份字节跑第二趟」：
      //   ① `openSceneSibling` 命中**已经开着**的那张就别再要像素（`/preview` 一次
      //      可能几 MB）；第二次点同一枚芯片只该多一次 `/siblings`（要问才知道它
      //      对应哪条 rec）。
      //   ② 对比模式下的后台预取（用户开关，**默认关**）只取「服务端已有一份现成
      //      预览、且档位对得上」的那几类。真机上「我什么都没点，盘阵却在读大图」
      //      是这条最该钉住的边界，所以同一个场景验两遍：先把那份现成预览**按住
      //      不给**（预取必须一个字节都不取），再把它生成上（预取必须恰好取它一份）。
      console.log('\n[K] 场景芯片：已开的图不再要像素；预取只取现成预览');
      const sibRe = new RegExp(`^${apiBase}/api/scenes/[^/]+/siblings(\\?|$)`);
      // Node 侧直接问后端。**不经过页面** —— page.on('request') 看不到它，所以
      // 它不会污染下面那些「页面发了几个请求」的增量。
      const sibOf = async (sid) => {
        const r = await fetch(`${apiBase}/api/scenes/${encodeURIComponent(sid)}/siblings`);
        if (!r.ok) throw new Error(`siblings HTTP ${r.status}（${sid}）`);
        return r.json();
      };
      // 与前端 `scenePreviewUrl` 逐字同形（场景 id 是 base64url + `~` 前缀，URL 安全，
      // encodeURIComponent 后原样落地）。下面用它算「这一份该被取」的期望 URL。
      const prevUrlOf = (id) => `${apiBase}/api/scenes/${encodeURIComponent(id)}/preview?div=2`;
      const byKind = (sib, kind) => sib.items.find((it) => it.kind === kind);

      const kActive0 = await page.evaluate(() => window.__viewer.activeRec());
      assert(!!(kActive0 && kActive0.sceneId),
        `J 收尾时活动图带盘阵关联（${kActive0 && kActive0.name}）`);
      const kSid = kActive0.sceneId;
      const sib0 = await sibOf(kSid);
      const prod0 = byKind(sib0, 'product');
      const nosr0 = byKind(sib0, 'nosr');
      assert(!!(prod0 && prod0.name) && !!(nosr0 && nosr0.name),
        `后端拼得出两类产物名（suffix=${sib0.suffix} 来自 ${sib0.suffixFrom}，`
        + `候选 ${JSON.stringify(sib0.productCandidates)}）`);
      // 名字一律取**后端自己拼的候选名**：本脚本不重复实现那套命名规则（`[:-4]`
      // 切片 + suffix），名字改了这里跟着改，不会两边各说各话。尺寸特意与输入影像
      // （1600×800）错开，缩略图尺寸因此能分清端上来的是哪一份。
      const prodPath = path.join(sib0.lqPath, prod0.name);
      const nosrPath = path.join(sib0.lqPath, nosr0.name);
      makeTif(prodPath, 800, 400);
      makeTif(nosrPath, 640, 320);
      const sib1 = await sibOf(kSid);
      const prod1 = byKind(sib1, 'product');
      const nosr1 = byKind(sib1, 'nosr');
      assert(prod1.exists && prod1.W === 800 && prod1.H === 400,
        `造出来的「本轮超分产物」被认成 800×400（exists=${prod1.exists} / `
        + `${prod1.W}×${prod1.H}）`);
      assert(nosr1.exists && nosr1.W === 640 && nosr1.H === 320,
        `造出来的「NOSR」被认成 640×320（exists=${nosr1.exists} / `
        + `${nosr1.W}×${nosr1.H}）`);
      assert(!prod1.hasPreview && !nosr1.hasPreview,
        '两份都还没有预览 —— 也就是说，此刻盘上没有任何"现成的那份"');
      // 只生成「本轮超分产物」那一份（K1 要点的芯片）。「NOSR」先按住不生成：K2 前半
      // 段断言的就是「盘上没有现成预览 → 预取一个字节都不取」。
      const bakeProd = await fetch(prevUrlOf(prod1.id));
      assert(bakeProd.ok, `服务端生成「本轮超分产物」的 ÷2 预览（HTTP ${bakeProd.status}）`);
      await bakeProd.arrayBuffer();          // 必须读完：不读会吊着这条连接
      const prod2 = byKind(await sibOf(kSid), 'product');
      assert(prod2.hasPreview === true && prod2.previewDiv === 2,
        `盘上那份预览带 ÷2 的档位戳（hasPreview=${prod2.hasPreview} / `
        + `previewDiv=${prod2.previewDiv}）`);

      // ---- K1. 芯片：第一次点取像素，第二次点只问 id ----
      // 芯片条默认收起（工具条上那颗按钮开的），这里先展开 —— 这一段点的是真按钮。
      await page.evaluate(() => window.__viewer.setCmpStripOpen(true));
      const chipReady = async (label) => {
        await waitFor(page, () => {
          const b = document.querySelector('[data-e2e="cmp-sib-product"]');
          return !!b && !b.disabled;
        }, 15000, label);
      };
      await chipReady('「本轮超分产物」芯片可用');
      const k1Sib = countUrl(sibRe);
      const k1Prev = countUrl(previewRe);
      const k1Recs = await page.evaluate(() => window.__viewer.recs().length);
      await page.click('[data-e2e="cmp-sib-product"]');
      await waitFor(page, (sid) => window.__viewer.recs().some((r) => r.sceneId === sid),
        20000, '「本轮超分产物」开出来了', prod2.id);
      await waitNode(() => countUrl(sibRe) === k1Sib + 1, 15000, '芯片那一跳的 /siblings');
      const k1RecsAfter = await page.evaluate(() => window.__viewer.recs().length);
      assert(k1RecsAfter === k1Recs + 1,
        `芯片开出恰好一条 rec（${k1Recs} → ${k1RecsAfter}）`);
      assert(countUrl(previewRe) === k1Prev + 1,
        `第一次点芯片：/preview 恰好 +1（取那份 jpg；实际 +${countUrl(previewRe) - k1Prev}）`);
      const kProdRec = await page.evaluate(
        (sid) => window.__viewer.recs().find((r) => r.sceneId === sid), prod2.id);
      assert(kProdRec.thumbW === 400 && kProdRec.thumbH === 200,
        `缩略图就是那份 ÷2 预览的尺寸 400×200（${kProdRec.thumbW}×${kProdRec.thumbH}）`);
      assert(kProdRec.lqPath === sib0.lqPath.replace(/\\/g, '/'),
        `产物的 rec 也拿到盘阵关联，掩码写回才有落点（${kProdRec.lqPath}）`);

      await chipReady('芯片回到可用（第一次那跳收尾）');
      const k2Sib = countUrl(sibRe);
      const k2Prev = countUrl(previewRe);
      const k2Recs = await page.evaluate(() => window.__viewer.recs().length);
      const k2Active = await page.evaluate(() => {
        const r = window.__viewer.activeRec();
        return r ? r.id : null;
      });
      await page.click('[data-e2e="cmp-sib-product"]');
      await waitNode(() => countUrl(sibRe) === k2Sib + 1, 15000, '第二次点芯片的 /siblings');
      await sleep(500);            // 真要发 /preview，这半秒足够它冒出来
      assert(countUrl(previewRe) === k2Prev,
        `第二次点同一枚芯片：/preview 一个都没发（实际 +${countUrl(previewRe) - k2Prev}）`);
      const k2State = await page.evaluate(() => {
        const r = window.__viewer.activeRec();
        return { n: window.__viewer.recs().length, id: r ? r.id : null };
      });
      assert(k2State.n === k2Recs && k2State.id === k2Active,
        `rec 条数与活动图都没动（${k2Recs}→${k2State.n} / ${k2Active}→${k2State.id}）`);

      // ---- K2. 预取：开关关 → 零请求；开关开 → 只取「现成的那份」 ----
      // 合格项的判据与 store 里那套逐条对齐：在盘上（exists）、有尺寸、**已有现成
      // 预览且档位对得上**、并且没有哪条 rec 正开着它。写成 Node 侧算一遍而不是
      // 写死「应该取哪一份」，断言钉的才是**过滤语义**，不是夹具的巧合。
      const eligibleNow = async (sid) => {
        const open = await page.evaluate(
          () => window.__viewer.recs().map((r) => r.sceneId).filter(Boolean));
        const sib = await sibOf(sid);
        return sib.items.filter((it) => it.exists && it.id && it.name
          && it.W != null && it.H != null && it.hasPreview && it.previewDiv === 2
          && !open.includes(it.id));
      };
      const kSid2 = await page.evaluate(() => {
        const r = window.__viewer.activeRec();
        return r ? r.sceneId : null;
      });
      assert(!!kSid2, `K1 之后活动图仍带场景（${kSid2}）`);
      const brief = (its) => JSON.stringify(its.map((it) => [it.kind, it.name]));

      // K2a：开关**关着**（默认态）进对比模式 —— 两个端点都不该被碰。
      await page.evaluate(() => window.__viewer.setCmpPrefetch(false));
      assert(await page.evaluate(() => window.__viewer.cmpPrefetchOn()) === false,
        '预取开关是关的（默认态）');
      let sibMark = countUrl(sibRe);
      let prevMark = countUrl(previewRe);
      await page.evaluate(() => window.__viewer.setCmpMode('click'));
      await sleep(700);
      assert(countUrl(sibRe) === sibMark && countUrl(previewRe) === prevMark,
        `开关关着进对比模式：一个请求都不发（/siblings +${countUrl(sibRe) - sibMark}`
        + ` / /preview +${countUrl(previewRe) - prevMark}）`);

      // K2b：开关开着 —— 但盘上那份「NOSR」还没生成过，合格项是**空集**。
      // 空集也要如实判一次：接下来「一个字节都不取」才有可解释的理由（钉的是
      // 「预取绝不触发生成预览」）。
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      await page.evaluate(() => window.__viewer.setCmpPrefetch(true));
      await sleep(500);
      assert(countUrl(sibRe) === sibMark && countUrl(previewRe) === prevMark,
        '只打开开关、还没进对比模式：也不发请求（预取不是"点开就取"）');
      const eligB = await eligibleNow(kSid2);
      assert(eligB.length === 0,
        `此刻没有可预取的项（「NOSR」在盘上但还没预览：${brief(eligB)}）`);
      sibMark = countUrl(sibRe);
      prevMark = countUrl(previewRe);
      const recsB = await page.evaluate(() => window.__viewer.recs().length);
      await page.evaluate(() => window.__viewer.setCmpMode('click'));
      await waitNode(() => countUrl(sibRe) === sibMark + 1, 15000,
        '开关开时进对比模式的那次 /siblings');
      await sleep(900);            // 预取是顺序 await 的；给"多发一次"留够露头的时间
      assert(countUrl(previewRe) === prevMark,
        `盘上没有现成预览 → 预取一个字节都不取，绝不触发生成预览`
        + `（/preview +${countUrl(previewRe) - prevMark}）`);
      assert(await page.evaluate(() => window.__viewer.recs().length) === recsB,
        '预取不建 rec');
      // K2b-注：这一次合格项是空集，缓存行会如实停在原处（0 项 / 别的数字）—— 用户
      // 无从分辨「没东西可预取」与「预取坏了」。所以预取必须**把结果说出来**。
      await page.click('[data-e2e="set-open"]');
      await sleep(250);
      const noteB = await page.evaluate(() => {
        const el = document.querySelector('[data-e2e="set-prefetch-note"]');
        return el ? el.textContent.trim() : null;
      });
      assert(!!noteB && noteB.includes('没有可预取'),
        `空集也要如实说明「没有可预取的」（"${noteB}"）`);
      await page.keyboard.press('Escape');
      await sleep(200);

      // K2c：把那份「NOSR」的预览生成上（**在页面之外**生成的，不算页面发的
      // 请求），再进一次对比模式：这次合格项恰好是它一项。
      const nosr2 = byKind(await sibOf(kSid2), 'nosr');
      const bakeNosr = await fetch(prevUrlOf(nosr2.id));
      assert(bakeNosr.ok, `服务端生成「NOSR」的 ÷2 预览（HTTP ${bakeNosr.status}）`);
      await bakeNosr.arrayBuffer();
      const nosrN = byKind(await sibOf(kSid2), 'nosr');
      assert(nosrN.hasPreview === true && nosrN.previewDiv === 2,
        `盘上那份预览带 ÷2 的档位戳（previewDiv=${nosrN.previewDiv}）`);
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      const eligC = await eligibleNow(kSid2);
      assert(eligC.length === 1 && eligC[0].kind === 'nosr',
        `合格项恰好是「NOSR」一项（${brief(eligC)}）`);
      const wantUrl = prevUrlOf(eligC[0].id);
      sibMark = countUrl(sibRe);
      prevMark = countUrl(previewRe);
      const recsC = await page.evaluate(() => window.__viewer.recs().length);
      const activeC = await page.evaluate(() => {
        const r = window.__viewer.activeRec();
        return r ? r.id : null;
      });
      // 面板**先开着别关**：那一行的数字要在预取落地时自己变 —— 它是这份缓存唯一的
      // 显示面，而改这份缓存的按钮（预取开关）就长在它上面。只在打开时读一次快照的话，
      // 用户点开开关盯着紧挨着的那行数字，数字永远停在打开时那一次（多半是 0 项），
      // 只能得出「预取没生效」（2026-09-22 用户报的就是这一条）。
      await page.click('[data-e2e="set-open"]');
      await sleep(250);
      const lineCount = async () => {
        const t = await page.evaluate(() => {
          const el = document.querySelector('[data-e2e="set-cache-line"]');
          return el ? el.textContent.trim() : null;
        });
        const m = /^(\d+) 项/.exec(t || '');
        return { text: t, n: m ? Number(m[1]) : -1 };
      };
      const lineBefore = await lineCount();
      assert(lineBefore.n >= 0, `开预取前缓存行可读（"${lineBefore.text}"）`);
      await page.evaluate(() => window.__viewer.setCmpMode('click'));
      await waitNode(() => countUrl(previewRe) >= prevMark + 1, 20000,
        '预取把那一份取回来');
      await waitNode(() => countUrl(sibRe) === sibMark + 1, 15000, '预取那次 /siblings');
      const fetched = seen.filter((u) => previewRe.test(u)).slice(prevMark);
      assert(fetched.length === 1 && fetched[0] === wantUrl,
        `预取只取了那"现成的一份"（${JSON.stringify(fetched)}）`);
      const afterC = await page.evaluate(() => {
        const r = window.__viewer.activeRec();
        return {
          n: window.__viewer.recs().length,
          id: r ? r.id : null,
          mask: !!document.querySelector('.decode-mask'),
        };
      });
      assert(afterC.n === recsC, `预取不建 rec（${recsC} → ${afterC.n}）`);
      assert(afterC.id === activeC, `预取不换活动图（${activeC} → ${afterC.id}）`);
      assert(afterC.mask === false,
        '预取不占用遮罩 —— 用户此刻在看别的图，不该冒出"正在加载"');
      assert((await page.evaluate(() => window.__viewer.cmpList())).length === recsC,
        '点选清单里的还是原来那些（预取不进清单）');

      // 面板那一行跟着预取**实时**更新：面板一直开着，预取落地（上面那两条 waitNode
      // 已经等到）之后，行里的项数该涨，且与 `previewCacheStats()` 的真值逐字对上。
      const lineAfter = await lineCount();
      const truth = await page.evaluate(() => window.__viewer.previewCacheStats());
      const truthText = `${truth.count} 项 / ${(truth.bytes / (1024 * 1024)).toFixed(1)} MB`;
      assert(lineAfter.n === lineBefore.n + 1,
        `预取落地后缓存行的项数 +1（${lineBefore.text} → ${lineAfter.text}）`);
      assert(lineAfter.text === truthText,
        `缓存行与真值逐字一致（"${lineAfter.text}" vs "${truthText}"）`);
      const noteC = await page.evaluate(() => {
        const el = document.querySelector('[data-e2e="set-prefetch-note"]');
        return el ? el.textContent.trim() : null;
      });
      assert(!!noteC && noteC.includes('已预取'),
        `取完了也如实说（"${noteC}"）`);
      await page.keyboard.press('Escape');
      await sleep(200);

      // 收尾：复位成默认（开关关、回单幅），免得影响 §I 的错误计数口径。
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      await page.evaluate(() => window.__viewer.setCmpPrefetch(false));
      assert(await page.evaluate(() => window.__viewer.cmpPrefetchOn()) === false,
        '预取开关复位成默认关');

      /* ---------- L. 左栏卡片拖进画布；中间产物 jpg 只能对比 ---------- */
      // 2026-09-21 用户报的两个 bug，各验一条**用户手上那一下**：
      //   ① 左栏的图拖不进中间画布（只能与系统文件夹交互）—— 早先卡片没带
      //      `draggable`，画布的落图门又只认 `dataTransfer.types` 里的 `Files`，
      //      这条链从头到尾不存在；
      //   ② 拖进来的若是**中间产物**（`<编号>_sr.jpg`），要能关联到盘阵、卡片上
      //      带「盘阵 + 同一景序号 + 环节」三颗小标，**修复入口全部堵死**（掩码与
      //      SR 都建在本体影像的网格上），并在盘阵上留下它自己那份 `_preview.jpg`。
      console.log('\n[L] 左栏卡片拖进画布；中间产物 jpg 关联后只读');
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      const ptsL = await drag.canvasPoints(page);
      const midX = ptsL.rect.left + ptsL.rect.width / 2;
      const idOfCard = (n) => page.evaluate((name) => {
        const r = window.__viewer.recs().find((x) => x.name === name);
        return r ? r.id : null;
      }, n);
      const panCardId = await idOfCard(PAN + '.jpg');
      const scCardId = await idOfCard(SC + '.jpg');
      assert(!!panCardId && !!scCardId && panCardId !== scCardId,
        `左栏有可拖的卡片（${PAN}.jpg=${panCardId} / ${SC}.jpg=${scCardId}）`);

      // L1：单幅下把卡片拖进画布 —— 换活动图（拖另一张再换回去，两个方向都验）。
      const dragL1 = await drag.dragCard(page,
        { cardName: PAN + '.jpg', clientX: midX, clientY: ptsL.midY });
      assert(dragL1.payload === String(panCardId),
        `卡片把票放进了 DataTransfer（${dragL1.payload}）—— 不是我们替它 setData 的`);
      assert(dragL1.defaultPrevented === true,
        'drop 被画布处理掉了（preventDefault：浏览器不会拿这张图去导航）');
      await waitFor(page, (id) => {
        const r = window.__viewer.activeRec();
        return !!r && r.id === id;
      }, 10000, '拖卡片换活动图', panCardId);
      const dragL2 = await drag.dragCard(page,
        { cardName: SC + '.jpg', clientX: midX, clientY: ptsL.midY });
      const lActiveName = await waitFor(page, (id) => {
        const r = window.__viewer.activeRec();
        return r && r.id === id ? r.name : null;
      }, 10000, '再拖一张换回来', scCardId);
      assert(dragL2.payload === String(scCardId) && lActiveName === SC + '.jpg',
        `再拖另一张，活动图跟着换（${lActiveName}）`);

      // L2：分屏下落进光标所在那一格（落点算出的 side 真的穿到了 activate）。
      await page.evaluate(() => window.__viewer.setCmpMode('split'));
      const panesL0 = await page.evaluate(() => window.__viewer.cmpPanes());
      const dragL3 = await drag.dragCard(page,
        { cardName: PAN + '.jpg', clientX: ptsL.right, clientY: ptsL.midY });
      assert(dragL3.hint.active === true && dragL3.hint.side === 'B',
        `悬停右半：落位提示指向右格（${JSON.stringify(dragL3.hint)}）`);
      await waitFor(page, (id) => {
        const p = window.__viewer.cmpPanes();
        return p[1] && p[1].recId === id;
      }, 10000, '卡片落在右格', panCardId);
      const panesL1 = await page.evaluate(() => window.__viewer.cmpPanes());
      assert(panesL1[0].recId === panesL0[0].recId,
        `左格那张没被顶掉（${panesL1[0].recId} / ${panesL0[0].recId}）`);
      assert(panesL1[1].active === true,
        '活动侧跟着落点转到右侧（掩码/云量/状态栏跟着它）');

      // L2b（2026-09-22 用户报）：刚进分屏、右格还空着时，把**左格那张自己**拖到右半
      // —— 旧规则的「互换」在目标格空着时没有「原来那张」可接，等于把左格挖空，
      // 用户看到的是「左图变成空的（只剩占位框）」。判据已改成「空目标格照常落图、
      // 另一格不动」。这里走真拖动：卡片自己填票 → 落点算出的 side = 'B'。
      // 先绕一圈 off 再进分屏：分屏只在**进的那一下**播种（paneA = 活动图、paneB 空），
      // 已经在分屏里再调一次 split 是空操作，右格会留着上一段那张。
      await page.evaluate(() => window.__viewer.setCmpMode('off'));
      await page.evaluate(() => window.__viewer.setCmpMode('split'));
      const panesB0 = await page.evaluate(() => window.__viewer.cmpPanes());
      assert(panesB0[0].recId === panCardId && panesB0[1].recId === null,
        `回到分屏：左格是当时那张、右格空着（${panesB0[0].recId} / ${panesB0[1].recId}）`);
      await drag.dragCard(page,
        { cardName: PAN + '.jpg', clientX: ptsL.right, clientY: ptsL.midY });
      const panesB1 = await page.evaluate(() => window.__viewer.cmpPanes());
      assert(panesB1[0].recId === panCardId,
        `左格那张还在（${panesB1[0].recId}）—— 空目标格不再把另一格挖空`);
      assert(panesB1[1].recId === panCardId && panesB1[1].active === true,
        `右格摆上它、活动侧跟着过去（${panesB1[1].recId} / active=${panesB1[1].active}）`);

      // L3：对比模式下拖到画布外 —— 给一句人话，别默默什么都没发生。
      await page.evaluate(() => window.__viewer.setCmpMode('click'));
      const lActiveBefore = await page.evaluate(() => window.__viewer.activeRec().id);
      await drag.dragCard(page,
        { cardName: PAN + '.jpg', clientX: ptsL.outside, clientY: ptsL.outsideY });
      const lToast = await waitFor(page, () => {
        const t = document.querySelector('.toast');
        return t ? t.textContent.trim() : null;
      }, 10000, '画布外拖卡片的那句 toast');
      assert(lToast.includes('只能把影像拖到画布上'),
        `画布外那一下给一句人话（${lToast}）`);
      assert(await page.evaluate(() => window.__viewer.activeRec().id) === lActiveBefore,
        '画布外那一下不改活动图');
      await page.evaluate(() => window.__viewer.setCmpMode('off'));

      // L4：拖**中间产物** jpg 进来。产物在真机上由 SR 跑出来（各边 2×），显示件
      // `<编号>_sr.jpg` 是它自己那份。夹具里没有现成的，两条都现造：栅格 3200×1600
      // （÷2 生成出 1600×800，与本体 1600×800 ÷2 = 800×400 差着一倍，断言因此能分清
      // 「生成的是哪一份栅格」），本地那份拖进来的 jpg 1600×800（盘阵上没有同名 jpg
      // → 判本地赢，像素用拖进来那张）。
      const SR = SC + '_sr';
      const srRaster = path.join(SC_DIR, SR + '.tif');
      makeTif(srRaster, 3200, 1600);
      const upSrJpg = path.join(upDir, SR + '.jpg');
      makeJpg(upSrJpg, 1600, 800);
      const lResBefore = countUrl(resolveRe);
      const lDropBefore = countUrl(dropPreviewRe);
      const lRecsBefore = await page.evaluate(() => window.__viewer.recs().length);
      await input.uploadFile(upSrJpg);
      try {
        await waitFor(page, (n) => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          return rs.length > n && r.route === 'jpg' && r.stageKind === 'product';
        }, 30000, '产物 jpg 关联上盘阵场景', lRecsBefore);
      } catch (e) {
        const dbg = await page.evaluate(() => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          const m = document.querySelector('.notice-modal');
          return { n: rs.length, last: r && { name: r.name, route: r.route,
            status: r.status, statusCls: r.statusCls, linkNote: r.linkNote },
            modal: m ? m.querySelector('.nm-body').textContent.trim() : null,
            err: document.querySelector('.err-box')?.textContent.trim() ?? null };
        });
        throw new Error(`L4 诊断：${JSON.stringify(dbg)}`);
      }
      const srRec = await lastRec();
      assert(srRec.name === SR + '.jpg',
        `rec 还是拖进来那个文件（${srRec.name}）`);
      assert(srRec.stageKind === 'product' && srRec.stageLabel === 'SR',
        `后端认出它是本轮超分产物（kind=${srRec.stageKind} / 标签 ${srRec.stageLabel}）`);
      assert(srRec.lqPath === null,
        `关联上了但 lqPath 为空 —— 服务端拒绝把中间产物当可提交场景（${JSON.stringify(srRec.lqPath)}）`);
      assert(srRec.sceneDir === SC_DIR.replace(/\\/g, '/'),
        `仍属这一景（场景目录 ${srRec.sceneDir}）`);
      assert(srRec.W === 3200 && srRec.H === 1600,
        `W/H 取**产物自己**那份栅格 3200×1600，不是本体的 1600×800（${srRec.W}×${srRec.H}）`);
      assert(srRec.thumbW === 1600 && srRec.thumbH === 800
        && srRec.layout.includes('拖入的原图'),
        `像素仍用拖进来那张原图（${srRec.thumbW}×${srRec.thumbH} / ${srRec.layout}）`);
      const lTag = await waitFor(page, () => {
        const t = document.querySelector('.toast');
        return t && t.textContent.includes('仅用于对比') ? t.textContent.trim() : null;
      }, 10000, '中间产物那句 toast');
      assert(lTag.includes('修复请打开本体'),
        `关联成功的提示如实说清它不能修（${lTag.slice(0, 60)}…）`);
      // 三颗小标必须落到 DOM 上（store 里对、页面不更新是踩过的坑）：序号与本体
      // 那张**同一个号** —— 这就是「同一景的图归在一组」那件事。
      const cardChips = (n) => page.evaluate((name) => {
        const item = [...document.querySelectorAll('.file-item')].find((el) => {
          const nm = el.querySelector('.name');
          return nm && nm.textContent.includes(name);
        });
        if (!item) return null;
        const q = (s) => {
          const e = item.querySelector(s);
          return e ? e.textContent.trim() : null;
        };
        return { scn: q('.name .scn'), ord: q('.name .ord'), stage: q('.name .stage'),
          ro: q('.tags .ro-tag'), name: item.querySelector('.name').textContent };
      }, n);
      const srChips = await cardChips(SR + '.jpg');
      const scChips = await cardChips(SC + '.jpg');
      assert(srChips.scn === '盘阵' && srChips.stage === 'SR',
        `产物卡片上「盘阵 + SR」齐全（${JSON.stringify(srChips)}）`);
      assert(Number(srChips.ord) > 0 && srChips.ord === scChips.ord,
        `同一景共用同一个序号（产物 ${srChips.ord} / 本体 ${scChips.ord}）`);
      assert(srChips.ro === '仅对比，不作修复',
        `产物卡片多一颗只读小标（${srChips.ro}）`);
      assert(scChips.ro === null,
        `本体卡片没有那颗只读小标（${JSON.stringify(scChips.ro)}）`);
      // 修复入口三处全堵：工具栏两颗按钮置灰（可见的那道），store 里那三道门是
      // 键盘快捷键与程序化调用那条路（下面 L5 逐条打一遍）。
      const srBtns = await toolbarState(page);
      const lDrawBtn = await page.evaluate(() => {
        const b = [...document.querySelectorAll('.toolbar button')]
          .find((x) => x.textContent.trim() === '绘制掩码');
        return b ? { disabled: b.disabled, title: b.title } : null;
      });
      assert(srBtns.sr === false && srBtns.bake === false,
        `「提交 SR」「保存掩码到盘阵」都置灰（sr=${srBtns.sr} / bake=${srBtns.bake}）`);
      assert(lDrawBtn && lDrawBtn.disabled === true
        && lDrawBtn.title.includes('中间产物'),
        `「绘制掩码」也置灰并写明理由（${lDrawBtn && lDrawBtn.title}）`);
      // 落盘：后台静默生成的那一份是**产物自己**的栅格（3200×1600 ÷2 = 1600×800）。
      // 本体那份是 800×400，差一倍 —— 这一条就是「生成的是哪一份」的可执行判据。
      const srDropJpg = path.join(SC_DIR, SR + '_preview.jpg');
      await waitNode(() => jpegSize(srDropJpg) !== null, 30000, '产物的 _preview.jpg 落盘');
      const srSz = jpegSize(srDropJpg);
      assert(srSz.w === 1600 && srSz.h === 800,
        `场景目录里落下 ${SR}_preview.jpg，生成的是产物自己的栅格（${srSz.w}×${srSz.h}）`);
      // 这一跳走的是拖入链那条端点，产物**不回**生产过程那条 /preview（两条落点不同）。
      assert(countUrl(resolveRe) === lResBefore + 1
        && countUrl(dropPreviewRe) === lDropBefore + 1,
        `拖入链仍是 resolve 一次 + /preview-drop 一次`
        + `（resolve +${countUrl(resolveRe) - lResBefore}`
        + ` / drop +${countUrl(dropPreviewRe) - lDropBefore}）`);

      // L5：三道门逐条打一遍。掩码这一条是本次唯一的破坏性风险 —— 产物尺寸
      // （3200×1600）的掩码配本体的 lq_path 写出去，本体那份掩码就被静默盖掉了，
      // 所以判据取**盘上那份掩码的 mtime 一动不动**，不是「有没有报错」。
      // 三处共用同一句话（`lib/stage.ts` 的 stageRefusal），文案只在第一处验一次
      // —— 后两处再验同一个串的话，读到的是不是本次写进去的就说不清了（错误条
      // 六秒才自清）；后两处改为各验各的**效果**。
      const srMaskPath = path.join(SC_DIR, SC + '_mask.tif');
      const srMaskMtime = fs.existsSync(srMaskPath) ? fs.statSync(srMaskPath).mtimeMs : null;
      assert(srMaskMtime !== null, 'E2 段写下的本体掩码还在（对照组）');
      assert(srRec.serverMaskPath === null,
        `服务端一个掩码路径都没给产物（${JSON.stringify(srRec.serverMaskPath)}）—— 第一道锁`);
      const lRoisBefore = await page.evaluate(() => window.__viewer.getRois().length);
      await page.evaluate(() => window.__viewer.enterDraw());
      const lDrawErr = await waitFor(page, () => {
        const el = document.querySelector('.err-box');
        return el && el.textContent.includes('不作修复')
          ? el.textContent.replace(/\s+/g, ' ').trim() : null;
      }, 10000, '「绘制掩码」的拒绝语');
      assert(lDrawErr.includes('中间产物') && lDrawErr.includes('请先打开本体'),
        `「绘制掩码」给出人话拒绝（${lDrawErr.slice(0, 48)}…）`);
      assert(await page.evaluate(() => window.__viewer.getRois().length) === lRoisBefore,
        `也没进绘制态（ROI 仍是 ${lRoisBefore} 个）`);
      const lBakeOk = await page.evaluate(() => window.__viewer.bakeMaskToServer());
      await sleep(300);
      assert(lBakeOk === false, `「保存掩码到盘阵」直接返回 false（${lBakeOk}）`);
      assert(fs.statSync(srMaskPath).mtimeMs === srMaskMtime,
        '本体那份掩码一个字节都没被动过（产物写回本体是这次唯一要防的事）');
      const lTasksBefore = await page.evaluate(async (ab) => {
        const r = await fetch(ab + '/api/queue');
        return (await r.json()).tasks.length;
      }, apiBase);
      await page.evaluate(() => window.__viewer.submitSr());
      await sleep(400);
      assert(!(await page.evaluate(() => location.pathname)).endsWith('/queue'),
        '没有跳去队列页（拒绝发生在取数之前，不是"提交了才报错"）');
      const lTasksAfter = await page.evaluate(async (ab) => {
        const r = await fetch(ab + '/api/queue');
        return (await r.json()).tasks.length;
      }, apiBase);
      assert(lTasksAfter === lTasksBefore,
        `队列里一条都没多（${lTasksBefore} → ${lTasksAfter}）`);

      /* ---------- L6–L7. 「未超分那份」：拖显示件时同时生成；生成的那份拖回来带 NOSR 标 ---------- */
      // 2026-09-24 用户口径：拖入 `<目录名>.jpg`（本体显示件）或产物显示件时，平台去
      // **同一个场景目录**里找 `<输入影像 stem>_NOSR.tif`（未超分那份）并降采样到它自己的
      // `<stem>_preview.jpg`；把这份 jpg 拖回来时，卡片上要带「NOSR」标。
      //
      // 为什么要现造一景：这份预热按**场景目录**记账（同一景这一会话只做一次），前面
      // 那些景在 K 段造出 NOSR 栅格**之前**就已经被拖过一次了 —— 复用它们只能验到
      // 「第二次不再生成」，验不到「拖一下就生成」。
      console.log('\n[L6] 拖入显示件同时生成「未超分那份」');
      const SC2 = 'A_B_' + ymd + '141200_200536960_101_0005_001';
      const sc2Dir = path.join(ARRAY, ...treeOf(SC2));
      const sc2Nosr = SC2 + '_NOSR.tif';           // 用户口径的名字：输入 stem + _NOSR
      const sc2Baked = SC2 + '_NOSR_preview.jpg';  // 生成的的落点
      makeTif(path.join(sc2Dir, SC2 + '.tif'), 1600, 800);
      makeTif(path.join(sc2Dir, sc2Nosr), 640, 320);
      fs.writeFileSync(path.join(sc2Dir, SC2 + '_meta.xml'),
        '<?xml version="1.0" encoding="UTF-8"?><SolarAzimuth>181.79</SolarAzimuth>');
      // 本地那份显示件（用户在浏览器里选/拖的那一份，盘阵上没有同名 jpg）
      const upSc2Jpg = path.join(upDir, SC2 + '.jpg');
      makeJpg(upSc2Jpg, 800, 400);
      assert(!fs.existsSync(path.join(sc2Dir, sc2Baked)),
        '开跑时场景目录里还没有那份生成的的 jpg（对照）');

      const l6PrevBefore = countUrl(previewRe);
      const l6RecsBefore = await page.evaluate(() => window.__viewer.recs().length);
      await input.uploadFile(upSc2Jpg);
      const l6Rec = await waitFor(page, (n) => {
        const rs = window.__viewer.recs();
        const r = rs[rs.length - 1];
        return rs.length > n && r.route === 'jpg' && r.stageKind === 'input'
          ? { id: r.id, sceneId: r.sceneId, lqPath: r.lqPath } : null;
      }, 30000, '本体显示件关联上盘阵场景', l6RecsBefore);
      assert(l6Rec.lqPath === sc2Dir.replace(/\\/g, '/'),
        `命中这一景（lqPath=${l6Rec.lqPath}）`);
      // 后台那次「同时生成」：文件落盘要等它读完那张栅格，所以等文件而不是等计数。
      await waitNode(() => jpegSize(path.join(sc2Dir, sc2Baked)) !== null, 30000,
        '未超分那份的 _preview.jpg 落盘');
      const l6Sz = jpegSize(path.join(sc2Dir, sc2Baked));
      assert(l6Sz.w === 320 && l6Sz.h === 160,
        `${sc2Baked} 落盘，生成的是那一份栅格（÷2：640×320 → ${l6Sz.w}×${l6Sz.h}）`);
      // 只该对**那一份**发一次 /preview：请求的 URL 就是它的场景 id。
      const l6Seen = seen.filter((u) => previewRe.test(u)).slice(l6PrevBefore);
      const nosrL6 = byKind(await sibOf(l6Rec.sceneId), 'nosr');
      assert(l6Seen.length === 1 && l6Seen[0] === prevUrlOf(nosrL6.id),
        `这一次拖入只对「未超分那份」发了 /preview（${l6Seen.join(' ') || '一个都没发'}）`);

      console.log('\n[L7] 把生成的那份 jpg 拖回来：带 NOSR 标、同序号、不可修复');
      const l7RecsBefore = await page.evaluate(() => window.__viewer.recs().length);
      await input.uploadFile(path.join(sc2Dir, sc2Baked));
      try {
        await waitFor(page, (n) => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          return rs.length > n && r.route === 'jpg' && r.stageKind === 'nosr';
        }, 30000, '未超分那份的 jpg 关联上盘阵场景', l7RecsBefore);
      } catch (e) {
        const dbg = await page.evaluate(() => {
          const rs = window.__viewer.recs();
          const r = rs[rs.length - 1];
          const m = document.querySelector('.notice-modal');
          return { n: rs.length, last: r && { name: r.name, route: r.route,
            stageKind: r.stageKind, status: r.status, linkNote: r.linkNote },
            modal: m ? m.querySelector('.nm-body').textContent.trim() : null };
        });
        throw new Error(`L7 诊断：${JSON.stringify(dbg)}`);
      }
      const nosrRec = await lastRec();
      assert(nosrRec.stageKind === 'nosr' && nosrRec.stageLabel === 'NOSR',
        `后端认出它是未超分那份（kind=${nosrRec.stageKind} / 标签 ${nosrRec.stageLabel}）`);
      assert(nosrRec.lqPath === null,
        `lqPath 为空 —— 服务端拒绝把未超分那份当可提交场景（${JSON.stringify(nosrRec.lqPath)}）`);
      assert(nosrRec.sceneDir === sc2Dir.replace(/\\/g, '/'),
        `仍属这一景（场景目录 ${nosrRec.sceneDir}）`);
      const nosrChips = await cardChips(sc2Baked);
      const sc2Chips = await cardChips(SC2 + '.jpg');
      assert(nosrChips.scn === '盘阵' && nosrChips.stage === 'NOSR',
        `这份卡片上「盘阵 + NOSR」齐全（${JSON.stringify(nosrChips)}）`);
      assert(Number(nosrChips.ord) > 0 && nosrChips.ord === sc2Chips.ord,
        `与本体共用同一个序号（未超分那份 ${nosrChips.ord} / 本体 ${sc2Chips.ord}）`);
      assert(nosrChips.ro === '仅对比，不作修复' && sc2Chips.ro === null,
        `只读小标只在这份上（NOSR=${nosrChips.ro} / 本体=${sc2Chips.ro}）`);
      const nosrBtns = await toolbarState(page);
      assert(nosrBtns.sr === false && nosrBtns.bake === false,
        `「提交 SR」「保存掩码到盘阵」都置灰（sr=${nosrBtns.sr} / bake=${nosrBtns.bake}）`);
      // 卡片上那颗环节标的 CSS 类跟着环节走：`.stage.nosr` 是琥珀族，与 `.stage.input`
      // （本体，青绿）、`.stage.product`（产物，蓝）三者互不相同。文案侥幸相同、
      // 配色与工具提示属于产物那族，是这条链最初的样子（判据漏了「裸 _NOSR」那一支）。
      const nosrCls = await page.evaluate((name) => {
        const item = [...document.querySelectorAll('.file-item')].find((el) => {
          const nm = el.querySelector('.name');
          return nm && nm.textContent.includes(name);
        });
        const st = item && item.querySelector('.name .stage');
        return st ? { cls: st.className, title: st.title } : null;
      }, sc2Baked);
      assert(nosrCls && nosrCls.cls.includes('nosr'),
        `那颗标的类是 nosr（${JSON.stringify(nosrCls)}）`);

      /* ---------- M. 一键解析：清单 → 逐景生成两份 jpg → 按序号入列 → 点亮联动 ---------- */
      // 用户口径（2026-09-27）：拖入 .txt 之后，面板上多一颗**醒目橘色**「一键解析」；
      // 点一下先清空左侧暂存区，再**严格按清单行序**逐景把「本体 jpg + NOSR jpg」两份
      // 都生成到盘上、每景两张卡按同一个序号依次入列；此后点右侧清单里任意一行，左侧
      // 对应的卡要橘色高亮 + 上下滚过去。
      //
      // 这一节刻意**另造新景**，不复用 H2 那两景：那两景在 H2 已经被打开过，它们的
      // 预览字节已经躺在浏览器的本地 blob 缓存里（键 = `场景 id|档位|jpg`），批量再取
      // 就是本地命中、一个 HTTP 都不发 —— 那条路上「每个 id 恰好生成一次」根本没被验到。
      //
      // 四行清单一次把四种情形都摆出来：
      //   ①② 盘上有这一景、也有 NOSR → 每景两张卡（共用序号）；
      //   ③  盘上有这一景、**没有** NOSR → 只落一张卡 + 一条中性的「缺 NOSR」提示；
      //   ④  盘上压根没有这一景 → 标红记账，**后面的景照跑**（前三行都已跑完）。
      // 四行的第一列都**缺产品段**（写 `…_L1`，盘阵上叫 `…_L1_PAN`），desc 里写影像
      // 类型 —— 批量这条路吃的正是 H2 那条「按影像类型补段」的解析，不能只认全名。
      console.log('\n[M] 一键解析：按清单行序逐景生成两份 jpg、共用序号入列、点亮联动');
      const MB_META = '<?xml version="1.0" encoding="UTF-8"?>'
        + '<SolarAzimuth>181.79</SolarAzimuth>';
      // 尺寸各不相同：落在盘上的那五份 jpg 各是 ÷2 档（1200×600→600×300 等），
      // 数字对不上就说明端上来的不是这一景的图。
      const mbScenes = [
        { name: 'A_B_' + ymd + '151000_200536960_101_0021_001_L1_PAN', w: 1200, h: 600,
          nosr: { w: 400, h: 200 } },
        { name: 'A_B_' + ymd + '151100_200536960_101_0023_001_L1_MSS', w: 1000, h: 500,
          nosr: { w: 320, h: 160 } },
        // 有本体、**没有**那份未超分 —— 三条候选名一条都不在盘上
        { name: 'A_B_' + ymd + '151200_200536960_101_0025_001_L1_PAN', w: 800, h: 400,
          nosr: null },
      ];
      // 盘上不存在的那个名字（第 ④ 行）：有完整的成像时刻与日期，两条产品段候选全落空
      const MB_GONE = 'A_B_' + ymd + '151300_200536960_101_0099_001_L1';
      for (const s of mbScenes) {
        s.bare = s.name.slice(0, -4);            // 清单第一列的写法：去掉末段产品段
        s.dir = path.join(ARRAY, ...treeOf(s.name));
        makeTif(path.join(s.dir, s.name + '.tif'), s.w, s.h);
        fs.writeFileSync(path.join(s.dir, s.name + '_meta.xml'), MB_META);
        if (s.nosr) makeTif(path.join(s.dir, s.name + '_NOSR.tif'), s.nosr.w, s.nosr.h);
        assert(!fs.existsSync(path.join(s.dir, s.name + '_preview.jpg')),
          `${s.name} 开跑前盘上还没有预览（对照）`);
      }
      const QC3 = path.join(tmp, '待修复清单_一键解析.txt');
      const mbRow = (bare, type) => bare
        + ',\t产品存在伪影 (问题类型:产品存在伪影 行列号:120.5,88.25 影像类型:'
        + type + ' )\t李佳峻\n';
      writeGbk(
        mbRow(mbScenes[0].bare, 'pan') + mbRow(mbScenes[1].bare, 'MSS')
        + mbRow(mbScenes[2].bare, 'pan') + mbRow(MB_GONE, 'pan'),
        QC3);
      await (await page.$('.qc-h input[type=file]')).uploadFile(QC3);
      await waitFor(page, () => window.__viewer.qcState().loaded, 10000, '一键解析清单导入');
      const qsM = await page.evaluate(() => window.__viewer.qcState());
      assert(qsM.total === 4, `这一份清单解析出 4 行（${qsM.total} —— 与 H2 那份不是同一份）`);
      // 换了一份清单：上一份的跑批账（键是行名）必须跟着作废，不能带着上一份的名字
      // 显示在当前这一份上。
      assert(Object.keys(qsM.statuses).length === 0 && qsM.done === 0,
        `刚导入时零终态、零跑批账（statuses=${JSON.stringify(qsM.statuses)}）`);

      /* M1. 那颗橘色按钮 */
      const mGo = await page.evaluate(() => {
        const b = document.querySelector('.qc-go');
        if (!b) return null;
        const cs = getComputedStyle(b);
        const txt = document.querySelector('.qc-go-txt');
        return { tag: b.tagName, text: b.textContent.trim(), cls: b.className,
          disabled: b.disabled, title: b.title, bg: cs.backgroundColor, fg: cs.color,
          line: txt ? txt.textContent.trim() : null };
      });
      // class 里除了 qc-go 还挂着状态名（`:class="qc.bakeState"`）：待命态是 idle，
      // 跑着是 running。**独立一个类**是给 e2e 按类找按钮用的（.qc-ob 是隔壁那颗）。
      assert(mGo && mGo.tag === 'BUTTON' && mGo.text === '一键解析'
        && mGo.cls.split(/\s+/).includes('qc-go') && mGo.cls.includes('idle'),
        `导入之后面板上多一颗「一键解析」按钮（${JSON.stringify(mGo)}）`);
      // 「醒目橘色」是用户那条要求本身，就按**渲染出来的颜色**验：.qc-go 用的是
      // --notice-job（#C2743A），**刻意不是主题色** —— 主题的青绿已经被
      // .file-item.active 与「盘阵」那颗小标占了，再借它就没有「这是一颗特别的
      // 按钮」的意思了。
      assert(mGo.bg === 'rgb(194, 116, 58)',
        `按钮底色是那颗橘色令牌（bg=${mGo.bg}）`);
      assert(mGo.disabled === false, '待命态可点（不是跑到一半留下的禁用态）');
      assert(mGo.title.includes('逐景') && mGo.title.includes('随时可停'),
        `提示把丑话说全（${mGo.title.slice(0, 32)}…）`);
      assert(mGo.line && mGo.line.indexOf('两份 jpg') >= 0,
        `旁边那行字说清它要干什么（${mGo.line}）`);

      /* M2. 点一下：先清空，再逐景入列；全程不盖遮罩 */
      const mRecsBefore = await recCount(page);
      assert(mRecsBefore > 0,
        `点之前左侧是有内容的（${mRecsBefore} 张）—— 否则「先清空」验不出什么`);
      // 请求计数的起点：这一趟批量该发几个 /preview、几个 /siblings（见 M4）
      const mPrevBefore = countUrl(previewRe);
      const mSibBefore = countUrl(sibRe);
      // 全程盯着遮罩：批量在真机上是几十景 × 每景几十秒，**绝不能**盖 modal 遮罩把
      // 用户正在看的图挡住、把工具栏锁死。
      //
      // 采样间隔取 5ms 而不是「看上去够快」的 40ms：这里的 fixture 是 1200×600 的小图、
      // 盘阵就在本机 temp，**整批只跑一百多毫秒**（12 个 HTTP 往返），40ms 那档一共才采
      // 到 4 个点 —— 而下面那条 `samples >= 8` 是**非无进展的底**（防「采样器压根没跑起来
      // 于是 covered 恒为 0」这种误报通过），不是时长目标。5ms 一采，整批至少落十几个点，
      // 任何持续 ≥5ms 的遮罩都躲不掉。
      await page.evaluate(() => {
        window.__mOverlay = [];
        window.__mTimer = setInterval(
          () => window.__mOverlay.push(window.__viewer.overlayVisible() ? 1 : 0), 5);
      });
      // 真实 DOM 点击（el.click() 派发的就是真的 MouseEvent）。**必须与读结果同一个
      // evaluate**：bakeAll 的同步前缀（清空 + 置 running）在 click 返回之前就跑完了，
      // 分成两次 evaluate 再读就成了赌「第一个 resolve 还没回来」—— 那是时序不是断言。
      // （按钮上的字这里**不读**：同步读到的还是 click 之前那次渲染的 DOM，Vue 的
      // 刷新在微任务里。字面那半条留给 M8 —— 那里读之前先 await 过一个宏任务。）
      const mKick = await page.evaluate(() => {
        const b = document.querySelector('.qc-go');
        const before = window.__viewer.recs().length;
        b.click();
        return { before, after: window.__viewer.recs().length,
          st: window.__viewer.qcBakeState().state };
      });
      assert(mKick.before > 0 && mKick.after === 0,
        `点下去先把暂存区清空（${mKick.before} → ${mKick.after} 张）`);
      assert(mKick.st === 'running', `按钮一点就跑起来（state=${mKick.st}）`);
      try {
        await waitFor(page,
          () => ['done', 'stopped'].includes(window.__viewer.qcBakeState().state),
          180000, '跑批结束');
      } catch (e) {
        const dbg = await page.evaluate(() => window.__viewer.qcBakeState());
        throw new Error(`跑批没结束：${JSON.stringify(dbg)}`);
      }
      const mBake = await page.evaluate(() => {
        clearInterval(window.__mTimer);
        const st = window.__viewer.qcBakeState();
        return { ...st, samples: window.__mOverlay.length,
          covered: window.__mOverlay.reduce((a, b) => a + b, 0) };
      });
      assert(mBake.state === 'done', `四行跑完、没被停（state=${mBake.state}）`);
      assert(mBake.covered === 0 && mBake.samples >= 8,
        `全程 ${mBake.samples} 次采样里遮罩一次都没盖（批量不弹 modal）`);

      /* M3. 入列顺序 = .txt 行序，每景两张卡共用一个序号 */
      const mCards = await page.evaluate(() => window.__viewer.recs().map((r) => ({
        id: r.id, name: r.name, sceneId: r.sceneId, sceneDir: r.sceneDir,
        stageKind: r.stageKind, stageLabel: r.stageLabel, route: r.route,
        W: r.W, H: r.H, thumbW: r.thumbW, thumbH: r.thumbH,
        hasCard: r.hasCard, thumb: r.thumb, lqPath: r.lqPath,
      })));
      const mWantNames = [mbScenes[0].name, mbScenes[0].name + '_NOSR',
        mbScenes[1].name, mbScenes[1].name + '_NOSR', mbScenes[2].name];
      assert(mCards.length === 5 && mCards.map((r) => r.name).join('|') === mWantNames.join('|'),
        `五张卡、名字与顺序严格按清单行序（${mCards.map((r) => r.name.slice(-9)).join(' / ')}）`);
      // 「生下来不带像素」：入列时 thumb 是 null，生成完（jpg 已落盘）**仍然**没有像素
      // —— 像素是点开那一张时才取的。÷2 档一张 1200×600 的场景卡装着约 8MB 位图，
      // 几十景一次装进内存必爆（浏览器单次分配 ~2GB），所以批量只负责把 jpg 生成到盘上。
      assert(mCards.every((r) => r.hasCard && r.thumb === null),
        `五张卡都还是空卡（有身份、没像素：${mCards.filter((r) => r.hasCard && r.thumb === null).length}/5）`);
      assert(mCards.every((r) => r.route === 'jpg' && !!r.sceneDir && !!r.sceneId),
        '五张卡都走盘阵 JPG 路由、都带场景目录与场景 id');
      // 序号是**按场景目录分组**发的（sceneOrdinalOf），先清空再按序入列 ⇒ 天然就是
      // 1,1,2,2,3。DOM 上那颗「盘阵 / 序号 / 环节」也一并验，别只在 store 里对。
      const mChips = await page.evaluate(() => [...document.querySelectorAll('.file-item')]
        .map((el) => ({
          scn: el.querySelector('.name .scn') ? el.querySelector('.name .scn').textContent.trim() : null,
          ord: el.querySelector('.name .ord') ? el.querySelector('.name .ord').textContent.trim() : null,
          stage: el.querySelector('.name .stage') ? el.querySelector('.name .stage').textContent.trim() : null,
        })));
      assert(mChips.map((c) => c.ord).join(',') === '1,1,2,2,3',
        `DOM 上按序号归纳依次展示（${mChips.map((c) => c.ord).join(',')}）`);
      assert(mChips.map((c) => c.stage).join(',') === '本体,NOSR,本体,NOSR,本体'
        && mChips.every((c) => c.scn === '盘阵'),
        `每景本体在前、NOSR 紧挨着在后（${mChips.map((c) => c.stage).join(',')}）`);
      assert(!await page.evaluate(() => !!window.__viewer.activeRec()),
        '批量入列不激活任何一张、不抢焦点（点开另有其路）');

      /* M4. 五份 jpg 真落盘，且每个场景 id 恰好生成一次 */
      const mJpgs = [
        [mbScenes[0].dir, mbScenes[0].name + '_preview.jpg', 600, 300],
        [mbScenes[0].dir, mbScenes[0].name + '_NOSR_preview.jpg', 200, 100],
        [mbScenes[1].dir, mbScenes[1].name + '_preview.jpg', 500, 250],
        [mbScenes[1].dir, mbScenes[1].name + '_NOSR_preview.jpg', 160, 80],
        [mbScenes[2].dir, mbScenes[2].name + '_preview.jpg', 400, 200],
      ];
      for (const [dir, f, w, h] of mJpgs) {
        const sz = jpegSize(path.join(dir, f));
        assert(sz && sz.w === w && sz.h === h,
          `${f} 落盘且是 ÷2 那一档（${sz ? sz.w + '×' + sz.h : '没生成'}，期望 ${w}×${h}）`);
      }
      const mPrevSeen = seen.filter((u) => previewRe.test(u)).slice(mPrevBefore);
      const mSibSeen = seen.filter((u) => sibRe.test(u)).slice(mSibBefore);
      const mCountOf = (id) => mPrevSeen.filter(
        (u) => u === `${apiBase}/api/scenes/${encodeURIComponent(id)}/preview?div=2`).length;
      const mPerId = mCards.map((r) => mCountOf(r.sceneId));
      const mDupIds = mCards.map((r) => r.sceneId).filter((id, i, a) => a.indexOf(id) !== i);
      assert(mDupIds.length === 0,
        `五张卡对应五个不同的场景 id（重复 ${mDupIds.length} 个 —— 一景不该出现两张本体）`);
      assert(mPerId.every((n) => n === 1),
        `每个场景 id 恰好生成一次、没有重复读盘（${mPerId.join(',')}）`);
      assert(mPrevSeen.length === 5,
        `这一趟总共只发 5 次 /preview（本体 3 + NOSR 2，实发 ${mPrevSeen.length} 次）`);
      assert(mSibSeen.length === 3,
        `每景问一次 /siblings（含没有那份的那一景，实发 ${mSibSeen.length} 次）`);

      /* M5. 失败账：盘上没有的那行标红记原因，且**不污染写回文档** */
      const mState = await page.evaluate(() => window.__viewer.qcBakeState());
      // 两张账的**键都是清单行名**（= .txt 第一列那个缺产品段的裸名），不是盘上的场景名
      // —— 驱动器 `runSceneBake` 拿 `it.name` 记账，而 `it` 是清单行。第 ③ 行也
      // 只有用它的裸名才找得到那条「缺 NOSR」。
      assert(Object.keys(mState.fails).length === 1 && !!mState.fails[MB_GONE],
        `只有盘上没有的那一行记了失败（${Object.keys(mState.fails).join(',') || '一条都没有'}）`);
      // 先摊平成字符串再断言：`.reason` 是后端 detail 原文（含「试过哪些候选」），
      // 直接对它取字段/切片会把「没记上原因」变成一条 TypeError 而不是一条断言失败。
      const mFail = JSON.stringify(mState.fails[MB_GONE] || {});
      assert(mFail.indexOf('resolve') >= 0
        && mFail.indexOf(MB_GONE + '_PAN') >= 0 && mFail.indexOf(MB_GONE + '_MSS') >= 0,
        `原因是后端原话、点明补过哪几段候选、且记在「解析场景」这一步（${mFail.slice(0, 120)}）`);
      const mNoteName = mbScenes[2].bare;
      assert(Object.keys(mState.notes).length === 1 && !!mState.notes[mNoteName],
        `「盘上没有未超分那份」进 notes 不进 fails（${JSON.stringify(Object.keys(mState.notes))}）`);
      assert(String(mState.notes[mNoteName] || '').indexOf('NOSR') >= 0,
        `中性提示如实说清缺的是哪一份（${mState.notes[mNoteName]}）`);
      assert(mState.line.includes('共 4 景') && mState.line.includes('成功 3')
        && mState.line.includes('失败 1') && mState.line.includes('缺 NOSR 1'),
        `收尾汇总把成败与缺失分开数（${mState.line}）`);
      assert(mState.head === '一键解析', `跑完按钮变回「一键解析」（${mState.head}）`);
      // 失败账**绝不能漏进 .txt**：statuses 是质检结论（会被 buildQcDoc 写回盘阵、
      // 还驱动 counts.done 那颗计数），生成图失败是平台自己的事，两码事。
      const mQcAfter = await page.evaluate(() => window.__viewer.qcState());
      assert(Object.keys(mQcAfter.statuses).length === 0 && mQcAfter.done === 0,
        `失败账没漏进清单（statuses=${JSON.stringify(mQcAfter.statuses)} / 终态 ${mQcAfter.done} 行）`);
      const mRowDom = await page.evaluate((n) => {
        const nameOf = (li) => {
          const el = li.querySelector('.qc-name');
          return el ? el.textContent.trim() : '';
        };
        const row = [...document.querySelectorAll('.qc-row')].find((li) => nameOf(li) === n);
        const fails = document.querySelector('.qc-fails');
        return { red: row ? row.className.includes('bake-fail') : null,
          reds: document.querySelectorAll('.qc-row.bake-fail').length,
          txt: fails ? fails.textContent.replace(/\s+/g, ' ').trim() : null };
      }, MB_GONE);
      assert(mRowDom.red === true && mRowDom.reds === 1,
        `盘上没有的那一行是唯一标红的一行（${mRowDom.reds} 行标红）`);
      assert(mRowDom.txt && mRowDom.txt.includes(MB_GONE)
        && mRowDom.txt.includes('解析场景') && mRowDom.txt.includes(MB_GONE + '_MSS'),
        `面板下方逐条摊开「名字 + 第几步 + 后端原话」（${(mRowDom.txt || '').slice(0, 46)}…）`);

      /* M6. 点清单一行 → 左侧两张卡橘色点亮 + 上下滚过去（活动图毫发无损） */
      const mRowClick = (n) => page.evaluate((name) => {
        const row = [...document.querySelectorAll('.qc-row')].find((li) => {
          const el = li.querySelector('.qc-name');
          return el && el.textContent.trim() === name;
        });
        if (!row) return false;
        row.click();
        return true;
      }, n);
      // 先把第 1 景那张**本体**点开（真 DOM 点击）：既把活动图定住好验「点亮不改它」，
      // 又同时把「空卡点开才取图」这条懒路走通。
      await page.evaluate(() => document.querySelectorAll('.file-item')[0].click());
      // 等的是**像素真到位**（thumbW > 0），不是「它成了活动图」：`activate` 是同步把
      // activeId 挂上的，取图在后面几拍才回来 —— 只等 id 会在像素到之前就返回，
      // 读出来就是 0×0。同 §E/§K 那两处 waiting 的判据。
      await waitFor(page, (want) => {
        const a = window.__viewer.activeRec();
        return a && a.id === want && a.thumbW > 0 ? { id: a.id, thumbW: a.thumbW,
          thumbH: a.thumbH, route: a.route, hasCard: a.hasCard } : null;
      }, 30000, '第 1 景本体那张空卡点开', mCards[0].id);
      const mOpened = await page.evaluate(() => window.__viewer.activeRec());
      assert(mOpened.thumbW === 600 && mOpened.thumbH === 300,
        `点开空卡才去取图，补的是这一景的 ÷2 预览（缩略图 ${mOpened.thumbW}×${mOpened.thumbH}）`);
      assert(mOpened.hasCard === false,
        '取过像素之后就不再是「待取图」的空卡了（再点不会重取）');
      // 视口压矮：5 张卡在 600px 高的侧栏里未必溢得出来，溢不出来 scrollIntoView 就
      // 无从观察 —— 「上下滑动」这条要求就变成了空断言。
      const mViewport = page.viewport() || { width: 800, height: 600 };
      await page.setViewport({ width: mViewport.width, height: 300 });
      // 侧栏滚动位置：顶部是几，**读出来**，不假设它是 0。基线取错的话下面那条
      // 「滚下去了」随时可能是「它本来就在那儿」。
      const mSideTop = () => page.evaluate(() => document.querySelector('.sidebar').scrollTop);
      // 平滑滚动（behavior: 'smooth'）没有「完成事件」，只能靠**连续两次采样同值**
      // 判断它停住了。等「小于某个中途值」那种写法是自证的同义反复（waitFor 的条件
      // 就是断言本身），所以两条方向都等停稳再判。
      const mSettle = async () => {
        // 先等一小会儿再开始采：点击到 scrollIntoView 之间隔着 Vue 的一次刷新，
        // 一上来就连采到两个相等的「还没动」会当成停稳，等于用旧值去判「滚回去了」。
        await sleep(120);
        let prev = null;
        for (let i = 0; i < 40; i++) {
          const t = await mSideTop();
          if (prev !== null && t === prev) return t;
          prev = t;
          await sleep(60);
        }
        return prev;
      };
      const mScroll0 = await mSideTop();
      assert(await mRowClick(mbScenes[1].bare), '清单里有第 2 景那一行（点得到）');
      const mDown = await waitFor(page, (prev) => {
        const t = document.querySelector('.sidebar').scrollTop;
        return t > prev ? { top: t } : null;
      }, 8000, '侧栏滚到第 2 景', mScroll0);
      const mLit = await page.evaluate(() => ({
        ids: window.__viewer.litIds(), tick: window.__viewer.litTick(),
        domLit: document.querySelectorAll('.file-item.lit').length,
        activeId: window.__viewer.activeRec() ? window.__viewer.activeRec().id : null,
        cls: [...document.querySelectorAll('.file-item.lit')].map((el) => el.className),
      }));
      const mWant2 = [mCards[2].id, mCards[3].id].sort().join(',');
      assert(mLit.ids.slice().sort().join(',') === mWant2,
        `点亮的是第 2 景那两张卡（${mLit.ids.join(',')}）`);
      assert(mLit.domLit === 2, `DOM 上恰好两张卡带 .lit（${mLit.domLit} 张）`);
      assert(mLit.activeId === mCards[0].id,
        `点亮**不动**活动图（active 还是 ${mLit.activeId}，起手的 ${mCards[0].id}）`);
      assert(mDown.top > mScroll0, `第 2 景在下面 → 侧栏真的滚下去了（${mScroll0} → ${mDown.top}）`);
      // 描边颜色要等动画走完再读：那 0.5s 里 outline-color 从半透明橘补间到实色。
      // 这一觉同时让上面那次平滑滚动走完，于是下面那个「回来了」的基线是**停稳值**。
      await sleep(600);
      const mDownEnd = await mSideTop();
      assert(mDownEnd > mScroll0, `滚停在第 2 景那儿（scrollTop=${mDownEnd}）`);
      const mOutline = await page.evaluate(() => {
        const el = document.querySelector('.file-item.lit');
        const cs = getComputedStyle(el);
        return { color: cs.outlineColor, style: cs.outlineStyle, width: cs.outlineWidth };
      });
      assert(mOutline.style === 'solid' && mOutline.color === 'rgb(194, 116, 58)',
        `点亮就是那颗橘色描边（${mOutline.width} ${mOutline.style} ${mOutline.color}）`);
      assert(mLit.cls.length === 2 && mLit.cls.every((c) => /(^|\s)lit(\s|$)/.test(c)),
        `两张卡都带 lit 这个持久的类（${mLit.cls.join(' | ')}）`);
      // 往回点第 1 景那行：滚回上面去（用户要的是「上下」两个方向都跟得上）
      assert(await mRowClick(mbScenes[0].bare), '清单里有第 1 景那一行（点得到）');
      const mUpTop = await mSettle();
      const mLit2 = await page.evaluate(() => ({
        ids: window.__viewer.litIds(), tick: window.__viewer.litTick(),
        domLit: document.querySelectorAll('.file-item.lit').length,
        cls: [...document.querySelectorAll('.file-item.lit')].map((el) => el.className),
      }));
      assert(mLit2.tick === mLit.tick + 1,
        `再点一次（哪怕点回同一景）点亮次数也 +1（${mLit.tick} → ${mLit2.tick}）`);
      assert(mLit2.ids.slice().sort().join(',') === [mCards[0].id, mCards[1].id].sort().join(',')
        && mLit2.domLit === 2, `换成第 1 景那两张（${mLit2.ids.join(',')}）`);
      assert(mUpTop < mDownEnd, `往回滚回上面（${mDownEnd} → ${mUpTop}）`);
      // 动画是**奇偶两个同名 keyframes 交替挂**：同一批卡连着被点亮两次时，只有类名
      // 真的换了一个才会重放（不重建元素、不强制 reflow）。
      const mAnim = await page.evaluate(() => [...document.querySelectorAll('.file-item.lit')]
        .map((el) => (el.className.match(/lit-[ab]/) || [''])[0]));
      assert(mAnim.length === 2 && mAnim.every((c) => /^lit-[ab]$/.test(c)),
        `点亮类在两套动画之间交替，连着点也会重放（${mAnim.join(',')}）`);
      await page.setViewport(mViewport);

      /* M7. 点开 NOSR 那张空卡：像素按需补齐，修复入口三处全堵 */
      const mNosrIdx = mCards.findIndex((r) => r.stageKind === 'nosr');
      assert(mNosrIdx === 1, `第 1 景的 NOSR 卡在列表第 2 位（${mNosrIdx}）`);
      await page.evaluate((i) => document.querySelectorAll('.file-item')[i].click(), mNosrIdx);
      await waitFor(page, (want) => {
        const a = window.__viewer.activeRec();
        return a && a.id === want && a.thumbW > 0 ? { id: a.id } : null;
      }, 30000, 'NOSR 那张空卡点开', mCards[mNosrIdx].id);
      const mNosrRec = await page.evaluate(() => window.__viewer.activeRec());
      assert(mNosrRec.thumbW === 200 && mNosrRec.thumbH === 100,
        `NOSR 那份的像素也是点开才取（缩略图 ${mNosrRec.thumbW}×${mNosrRec.thumbH} = ÷2 的 400×200）`);
      assert(mNosrRec.stageKind === 'nosr' && mNosrRec.stageLabel === 'NOSR',
        `环节还是 NOSR（${mNosrRec.stageKind} / ${mNosrRec.stageLabel}）`);
      // lqPath 取**场景目录**而不是 null —— 与「场景芯片」那条入口（openSceneSibling）
      // 逐字对齐，否则同一种图两条入口进来会长得不一样。「不能修复」由 stageKind 管
      // （isIntermediateStage 那三处门），不看 lqPath。
      assert(mNosrRec.lqPath === mCards[mNosrIdx].sceneDir,
        `NOSR 卡的 lqPath 就是这一景的场景目录（${mNosrRec.lqPath}）`);
      const mNosrChips = await cardChips(mCards[mNosrIdx].name);
      assert(mNosrChips && mNosrChips.ro === '仅对比，不作修复',
        `卡片上那颗只读小标在（${mNosrChips && mNosrChips.ro}）`);
      const mNosrBtns = await toolbarState(page);
      assert(mNosrBtns.sr === false && mNosrBtns.bake === false,
        `「提交 SR」「保存掩码到盘阵」都置灰（sr=${mNosrBtns.sr} / bake=${mNosrBtns.bake}）`);
      const mDrawBtn = await page.evaluate(() => {
        const b = [...document.querySelectorAll('.toolbar button')]
          .find((x) => x.textContent.trim() === '绘制掩码');
        return b ? { disabled: b.disabled } : null;
      });
      assert(mDrawBtn && mDrawBtn.disabled === true, '「绘制掩码」也置灰（三处门都关着）');

      /* M8. 跑到一半按「停止」：收手、已生成的卡留下、且不记一堆假失败 */
      // 「停止」最容易写错的地方：在飞的 resolve 会以 AbortError 被拒，若把它当成
      // 「这一景解析失败」，用户一按停止就凭空多出一条红账。这条断言盯的就是它。
      const mStop = await page.evaluate(async () => {
        const b = document.querySelector('.qc-go');
        b.click();                                   // 再跑一次（前面的卡会被清掉）
        const t0 = Date.now();
        while (Date.now() - t0 < 60000 && !window.__viewer.recs().length) {
          // 循环体里 await 一次宏任务：既让批量往前走，也让 Vue 的刷新跑完 ——
          // 于是下面读到的 head 已经是「跑起来了」之后的那一份 DOM。
          await new Promise((r) => setTimeout(r, 5));
        }
        const n = window.__viewer.recs().length;
        const st = window.__viewer.qcBakeState().state;
        const head = b.textContent.trim();
        const disabled = b.disabled;
        b.click();                                   // 这颗按钮此时等效「停止」
        return { n, st, head, disabled, after: window.__viewer.qcBakeState().state };
      });
      assert(mStop.n > 0 && mStop.st === 'running' && mStop.head === '停止',
        `第一张卡一落地就按停止（此刻 ${mStop.n} 张、state=${mStop.st}、按钮「${mStop.head}」）`);
      assert(mStop.disabled === false, '跑着的时候这颗按钮可点（点它就是停止）');
      assert(mStop.after === 'stopping',
        `按下去先进「正在停止」（那一景的取图停不下来，如实置灰，${mStop.after}）`);
      await waitFor(page, () => ['stopped', 'done'].includes(window.__viewer.qcBakeState().state),
        60000, '收手完成');
      const mStopEnd = await page.evaluate(() => {
        const st = window.__viewer.qcBakeState();
        return { ...st, recs: window.__viewer.recs().map((r) => ({ name: r.name, hasCard: r.hasCard })) };
      });
      assert(mStopEnd.state === 'stopped', `停下来了（state=${mStopEnd.state}）`);
      // done 是**竞态**的，所以只钉住「跑了一部分就收手」这件事，不钉死那个 1：
      // 点「停止」那一下至少第 1 景的解析已经成了（第一张卡刚落地），所以 done ≥ 1；
      // 而掐断只能再放行**在飞的那一景**一步，所以 done ≤ 2 < 4 —— 两边都是可证的。
      // 钉 `done === 1` 就成了赌「第 2 景的 resolve 还没回来」，那是时序不是断言。
      assert(mStopEnd.done >= 1 && mStopEnd.done < mStopEnd.total,
        `跑了一部分就收手（done=${mStopEnd.done}/${mStopEnd.total}）`);
      assert(mStopEnd.total === 4, `总数仍是清单那 4 行（${mStopEnd.total}）`);
      assert(Object.keys(mStopEnd.fails).length === 0 && Object.keys(mStopEnd.notes).length === 0,
        `取消不当失败记（fails=${JSON.stringify(mStopEnd.fails)}）`);
      // 收尾那行也按 done 现算，不写死数字（同上）。
      assert(mStopEnd.line.indexOf('已停止：跑完 ' + mStopEnd.done + '/' + mStopEnd.total + ' 景') >= 0
        && mStopEnd.line.indexOf('失败') < 0,
        `收尾如实报「已停止」，不编一条失败出来（${mStopEnd.line}）`);
      assert(mStopEnd.recs.length >= 1 && mStopEnd.recs.length <= 2
        && mStopEnd.recs.every((r) => r.hasCard),
        `已经落地的卡原样留下（${mStopEnd.recs.map((r) => r.name.slice(-9)).join(' / ')}）`);
      assert(mStopEnd.head === '一键解析', `按钮变回「一键解析」（${mStopEnd.head}）`);

      /* ---------- I. 全程无错 ---------- */
      console.log('\n[I] 全程无错');
      // D / E / E2 / H2 / M 里**刻意**打出来的 404：D 一次（盘阵上没有那个目录）、E 两次
      // （同名不同字节；有日期但目录不存在）、E2 一次（同上，换成 .jpg 拖入）、
      // H2 一次（清单上那一景盘阵是没有的，且两种产品段补法都试过）、
      // M 一次（清单第 ④ 行同一个情形，只是这次是**一键解析**批量打出来的 ——
      // 按「停止」收手的那次不算：被掐掉的请求是 ERR_ABORTED，不是 404）。
      // 刻意打的 400 有两次：E 一次（文件名没有时间戳，后端据此拒绝反推）、
      // H 一次（往盘阵上不存在的清单路径写回）。浏览器对任何非 2xx 响应都会往
      // 控制台写一条，这不算程序缺陷，但也不能睁一只眼闭一只眼：数目必须恰好
      // 等于刻意的那几次，多一条就是有别的资源没取到。
      const deliberate = /status of (404|400)/;
      const notFound = errors.filter((e) => /status of 404/.test(e));
      assert(notFound.length === 6,
        `控制台里的 404 恰好是刻意的那六次（${notFound.length}）`);
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
  // 失败时把后端最后几十行摊出来：页面里看到的「CORS / 500 / 一条怪报错」常常只有
  // 后端这边说得清（上面 noteBackend 的注释写了为什么）。
  if (backendLog.length) {
    console.error(`[test-manual-scene] —— 后端最后 ${backendLog.length} 行 ——`);
    for (const line of backendLog) console.error('  | ' + line);
  }
  process.exit(1);
});
