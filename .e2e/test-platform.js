// 阶段5 验收：平台 REST/SSE 全链路（dev-machine + 假 LLM + 假调度器）
// ------------------------------------------------------------------
// 前置：`cd frontend && npm run build`（本测试读 frontend/dist）。
// 拓扑：uvicorn（SR_LLM_MOCK=1 SR_SLURM_FAKE=1 + 临时 db + 临时盘阵根）+
//       dist 静态服务；页面经 evaluateOnNewDocument 注入 window.__SR_CFG__.apiBase
//       （默认同源 nginx 反代 → 跨端口直连后端，验证异源 base 注入路径）。
// 覆盖 api-contract.md §5.3：
//   A. 聊天：/chat 新建会话 → 发一条 → SSE 全事件归并 → 工具行 + 最终回复渲染；
//      切走再回来 → GET messages 恢复历史（刷新可恢复）。
//   B. 队列：/queue 手填 lq_path 提交 → 假调度器推进 → SSE job_update 推到 COMPLETED。
//   C. 掩码→SR：/viewer 打开盘阵场景（合成 JPG + 画 ROI）→ 点「提交 SR」→
//      POST /api/masks 落盘 → 自动跳 /queue 表单预填（不自动提交）→ 用户确认提交。
// 用法：cd .e2e && node test-platform.js
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn } = require('child_process');
const { launchPage } = require('./launchBrowser');

const REPO = path.resolve(__dirname, '..');
const DIST = path.join(REPO, 'frontend', 'dist');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
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

function startStaticServer() {
  const server = http.createServer((req, res) => {
    let urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (urlPath === '/') urlPath = '/index.html';
    let filePath = path.join(DIST, urlPath);
    if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(DIST, 'index.html');   // createWebHistory 回退
    }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(500); res.end(String(err)); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(filePath)] || 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function waitHealth(url, ms = 20000) {
  const t0 = Date.now();
  for (;;) {
    try { const r = await fetch(url); if (r.ok) return; } catch { /* 未起 */ }
    if (Date.now() - t0 > ms) throw new Error('后端 /api/health 超时: ' + url);
    await sleep(200);
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

// 等某段文本出现在整个文档里（消息区/状态徽标通用）；text 经 evaluate 实参传入
const waitText = (page, text, timeoutMs) =>
  waitFor(page, (t) => document.body && document.body.textContent.includes(t),
    timeoutMs, `text:${text}`, text);

async function clickByText(page, text) {
  const ok = await page.evaluate((t) => {
    const b = [...document.querySelectorAll('button')]
      .find((x) => x.textContent.trim() === t && !x.disabled);
    if (!b) return false;
    b.click();
    return true;
  }, text);
  if (!ok) throw new Error(`按钮未找到或已禁用: ${text}`);
  return ok;
}

// 按 label>span 文本定位 .qp-form 字段并写入（placeholder 是示例值，不能当选择器）
async function setField(page, fieldLabel, value) {
  await waitFor(page, (label, v) => {
    const lab = [...document.querySelectorAll('.qp-form label')]
      .find((l) => { const s = l.querySelector('span'); return s && s.textContent.includes(label); });
    if (!lab) return false;
    const inp = lab.querySelector('input');
    if (!inp) return false;
    inp.value = v;
    inp.dispatchEvent(new Event('input', { bubbles: true }));   // 驱动 v-model
    return true;
  }, 10000, `填 ${fieldLabel}`, fieldLabel, value);
}

// 读 .qp-form 某字段当前值（返回 label 存在与否；value 可为 null）
async function readField(page, fieldLabel) {
  return page.evaluate((label) => {
    const lab = [...document.querySelectorAll('.qp-form label')]
      .find((l) => { const s = l.querySelector('span'); return s && s.textContent.includes(label); });
    const inp = lab && lab.querySelector('input');
    return inp ? { ok: true, value: inp.value } : { ok: false, value: null };
  }, fieldLabel);
}

function firstRowTags(page) {
  return page.evaluate(() =>
    [...document.querySelectorAll('tbody tr td .tag')].map((x) => x.textContent.trim()));
}

function isIgnorableConsole(msg) {
  return /AbortError|ERR_ABORTED|queue\/events|net::ERR/i.test(msg);
}

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sr-e2e-p5-'));
  const scenesRoot = path.join(tmp, 'scenes');
  // 场景 = 一个目录（真机是「年/月/日/生产编号」），里面躺着 <目录名>.tif 与
  // <目录名>_meta.xml —— scene_search 的判据。lq_path 就是它（不是盘阵根）。
  const SCENE = 'GF07A03_PMS01_20260722125045';
  const sceneDir = path.join(scenesRoot, SCENE);
  fs.mkdirSync(sceneDir, { recursive: true });
  fs.writeFileSync(path.join(sceneDir, SCENE + '.tif'), Buffer.alloc(0));
  fs.writeFileSync(path.join(sceneDir, SCENE + '_meta.xml'),
    '<?xml version="1.0" encoding="UTF-8"?><SolarAzimuth>181.79</SolarAzimuth>');
  // §4.3 起：POST /api/queue 不带 mask_path 时按 <lq_path>/<目录名>_mask.tif 推导，
  // 文件不存在直接 400（不许静默全图超分）。所以提交场景必须自带掩膜 —— 后端只
  // 校验存在性，内容不读。
  fs.writeFileSync(path.join(sceneDir, SCENE + '_mask.tif'), Buffer.alloc(0));
  const workDir = path.join(tmp, 'work');
  fs.mkdirSync(workDir, { recursive: true });

  const apiPort = await freePort();
  const apiBase = `http://127.0.0.1:${apiPort}`;

  console.log(`[test-platform] 临时: db=${path.join(tmp, 'db.sqlite')} scenes=${scenesRoot}`);
  const child = spawn('python', ['-m', 'backend.api'], {
    cwd: REPO,
    env: {
      ...process.env,
      SR_AGENT_DB: path.join(tmp, 'db.sqlite'),
      SR_SCENES_ROOT: scenesRoot,
      SR_SLURM_WORK_DIR: workDir,
      SR_LLM_MOCK: '1',
      SR_SLURM_FAKE: '1',
      SR_SLURM_FAKE_T_MS: '250',
      SR_QUEUE_POLL_SEC: '0.3',
      SR_API_HOST: '127.0.0.1',
      SR_API_PORT: String(apiPort),
      // 提交侧（normalize_submit_path）要求 lq_path 落在盘阵前缀白名单内，并且
      // 会把路径归一化：开发机的临时目录带盘符，不补一条"该盘符映射到自身"的
      // 规则，本机绝对路径会因「未知盘符」被 400。与 backend/tests/__init__.py::
      // allowed_roots_env 同一套（Linux 上 Path(r).drive 为空，这条例外不生效）。
      SR_ALLOWED_ROOTS: scenesRoot,
      SR_DRIVE_MAP: (() => {
        const drv = path.parse(scenesRoot).root.replace(/[\\/]+$/, '');
        return `${drv}=${drv}`;
      })(),
    },
  });
  child.stdout.on('data', () => {});
  child.stderr.on('data', () => {});
  try {
    await waitHealth(apiBase + '/api/health');

    const server = await startStaticServer();
    const base = `http://127.0.0.1:${server.address().port}`;
    console.log(`[test-platform] 静态 ${base} ← dist；API ${apiBase}`);

    const { browser, page, errors } = await launchPage();
    const external = [];
    page.on('request', (req) => {
      const u = req.url();
      if (!u.startsWith(base) && !u.startsWith(apiBase) && !u.startsWith('data:')) {
        external.push(u);
      }
    });
    await page.evaluateOnNewDocument((cfg) => { window.__SR_CFG__ = cfg; },
      { apiBase, staticBase: '' });

    try {
      /* ---------- A. 聊天（SSE 回合渲染 + 历史恢复） ---------- */
      console.log('\n[A] 聊天（mock LLM：固定 search_scenes → 总结）');
      await page.goto(base + '/chat', { waitUntil: 'networkidle2', timeout: 30000 });
      // 自动新建会话：输入框从禁用变可用
      await waitFor(page, () => {
        const i = document.querySelector('input.cp-input');
        return i && !i.disabled;
      }, 10000, 'chat 会话就绪');
      await page.type('input.cp-input', '看看盘阵上有什么场景');
      await page.keyboard.press('Enter');
      await waitText(page, '已检索盘阵场景', 20000);
      await waitText(page, 'mock 模型', 20000);
      const toolLine = await page.evaluate(() => {
        const el = document.querySelector('.tool-trace');
        return el ? el.textContent : '';
      });
      assert(toolLine.indexOf('search_scenes') >= 0, `工具行 search_scenes ✓ (${toolLine.trim().slice(0, 40)})`);

      // 历史恢复：切 /queue 再回 /chat，GET messages 重建同一条最终回复
      await page.goto(base + '/queue', { waitUntil: 'networkidle2', timeout: 30000 });
      await page.goto(base + '/chat', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitText(page, 'mock 模型', 15000);
      assert(true, '切走再回来：历史消息经 GET messages 恢复渲染');

      /* ---------- B. 队列（假调度器推到 COMPLETED） ---------- */
      console.log('\n[B] 共享任务队列（SR_SLURM_FAKE 状态机）');
      await page.goto(base + '/queue', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitText(page, 'SSE 已连接', 10000);
      await clickByText(page, '提交 SR 作业');
      const lqPath = sceneDir;
      await setField(page, 'lq_path', lqPath);
      await clickByText(page, '提交 SR');
      // SSE job_update（+list 快照）驱动：SUB…→PENDING→RUNNING→COMPLETED
      await waitFor(page, () => {
        const t = document.querySelector('tbody tr td .tag');
        return t && t.textContent.trim() === '完成';
      }, 25000, '队列 COMPLETED');
      const tags = await firstRowTags(page);
      assert(tags[0] === '完成', `提交后假调度器跑完 → 首行徽标「完成」 (${tags.join(',')})`);

      // 行级信息 + 「以这行参数再提交」：耗时列 / 目录下拉候选 / 只填不提交
      const rowInfo = await page.evaluate(() => {
        const tr = document.querySelector('.qp-tbl tbody tr');
        const tds = tr ? [...tr.querySelectorAll('td')] : [];
        const lqInput = [...document.querySelectorAll('.qp-form label')]
          .filter((l) => {
            const s = l.querySelector('span');
            return s && s.textContent.trim().startsWith('lq_path');
          })
          .map((l) => l.querySelector('input'))[0];
        return {
          elapsed: tds[6] ? tds[6].textContent.trim() : '',
          ops: tds[7] ? [...tds[7].querySelectorAll('button')].map((b) => b.textContent.trim()) : [],
          lqList: lqInput
            ? { list: lqInput.getAttribute('list'), readonly: lqInput.readOnly } : null,
          cands: (() => {
            const dl = document.getElementById('qp-lq-cands');
            return dl ? [...dl.querySelectorAll('option')].map((o) => o.value) : [];
          })(),
        };
      });
      assert(/^\d+ 秒$/.test(rowInfo.elapsed), `耗时列给出终态耗时（${rowInfo.elapsed}）`);
      // 终态耗时的真值 = 后端 updated_at − created_at（假调度器下恒 < 60 秒，所以
      // 只会是「N 秒」，不会走到「N 分 N 秒」分支）。页内那份 updated_at 若停在上
      // 一次 GET 的快照 —— 提交刚落库时 updated_at == created_at —— 任务一完成耗
      // 时就掉成「0 秒」，点一下刷新才露出真值。这里直接与接口读数对齐。
      const apiTasks = (await (await fetch(apiBase + '/api/queue')).json()).tasks;
      const truth = Math.round(apiTasks[0].updated_at - apiTasks[0].created_at) + ' 秒';
      assert(rowInfo.elapsed === truth,
        `耗时列 = 接口真值（页内 ${rowInfo.elapsed} / 接口 ${truth}）`);
      assert(rowInfo.ops.indexOf('再提交') >= 0, `每行给出「再提交」（${rowInfo.ops.join('/')}）`);
      assert(rowInfo.lqList && rowInfo.lqList.readonly === false
        && rowInfo.lqList.list === 'qp-lq-cands',
        `lq_path 可手改且挂了历史候选 datalist（list=${rowInfo.lqList && rowInfo.lqList.list}）`);
      assert(rowInfo.cands.length === 1 && path.resolve(rowInfo.cands[0]) === path.resolve(lqPath),
        `目录下拉候选 = 队列里出现过的目录（${rowInfo.cands.join(',')}）`);

      await clickByText(page, '再提交');
      await waitFor(page, () => {
        const t = document.querySelector('.qp-form .qp-draft-tip');
        return t && t.textContent.indexOf('已带入任务 #') >= 0;
      }, 10000, '「再提交」预填提示');
      const back = await page.evaluate(() => {
        const v = (l) => {
          const lab = [...document.querySelectorAll('.qp-form label')]
            .find((x) => { const s = x.querySelector('span'); return s && s.textContent.includes(l); });
          const inp = lab && lab.querySelector('input');
          return inp ? inp.value : null;
        };
        return { lq: v('lq_path'), scale: v('SR 倍率') };
      });
      assert(path.resolve(back.lq) === path.resolve(lqPath) && back.scale === '2',
        `「再提交」把该行参数填回表单（lq_path=${back.lq} scale=${back.scale}）`);
      assert((await firstRowTags(page)).length === 1,
        '「再提交」只填表单、不自动提交（队列仍 1 行）');

      /* ---------- C. 场景→SR（查看器 → /queue 只读预填 → 用户确认提交） ---------- */
      console.log('\n[C] 查看器场景→SR（route=jpg + lqPath 带出 + 表单预填）');
      await page.goto(base + '/viewer', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitFor(page, () => !!window.__viewer, 10000, '__viewer 钩子');

      // 后端 /api/scenes 首个真实行 → sceneId + lq_path（合成 JPG 打开 route='jpg' rec）
      // lq_path 必带：§4.3 起「提交 SR」要的是场景的原图目录，没有它就无从提交（按钮禁用）。
      const scene = await page.evaluate(async (ab) => {
        const r = await fetch(ab + '/api/scenes');
        const body = await r.json();
        if (!body.results || !body.results.length) throw new Error('后端 scenes 空');
        return { id: body.results[0].id, name: body.results[0].name,
                 lqPath: body.results[0].lq_path };
      }, apiBase);
      assert(scene.name.indexOf('GF07A03') === 0, `后端扫到盘阵场景 ${scene.name}`);

      await page.evaluate(async (meta) => {
        const cv = document.createElement('canvas');
        cv.width = 16; cv.height = 16;
        const ctx = cv.getContext('2d');
        ctx.fillStyle = '#808080'; ctx.fillRect(0, 0, 16, 16);
        const blob = await new Promise((res) => cv.toBlob(res, 'image/png'));
        await window.__viewer.openSceneJpg(
          { name: meta.name, W: 1600, H: 800, sceneId: meta.id, lqPath: meta.lqPath }, blob);
      }, scene);
      await waitFor(page, () => {
        const r = window.__viewer.activeRec();
        return r && r.route === 'jpg';
      }, 15000, 'route=jpg 打开');
      await page.evaluate(() => {
        window.__viewer.commitRect({ x0: 3, y0: 3, x1: 13, y1: 13 });   // 缩略图坐标 ROI
      });
      const rois = await page.evaluate(() => window.__viewer.getRois().length);
      assert(rois === 1, `画了 1 个掩码区域 (getRois=${rois})`);

      // 点工具栏「提交 SR」→ 服务端烘焙 → setDraft + 跳 /queue（不自动提交）
      await clickByText(page, '提交 SR');
      await waitFor(page, () => location.pathname.endsWith('/queue'), 15000, '跳转 /queue');
      await waitFor(page, () => !!document.querySelector('.qp-form .qp-draft-tip'), 10000, '表单预填提示');
      const lqField = await readField(page, 'lq_path');
      const maskField = await readField(page, 'mask');
      const mask = maskField.ok ? maskField.value : '';
      const lq = lqField.ok ? lqField.value : '';
      assert(mask.indexOf('_mask.tif') >= 0, `掩码已预填 ${path.basename(mask || '')}`);
      assert(path.resolve(lq) === path.resolve(sceneDir), `lq_path 预填原图目录 ${lq}`);
      let tagsBefore = (await firstRowTags(page)).length;
      assert(tagsBefore === 1, `未自动提交：此时队列仍只有上一任务 (${tagsBefore})`);
      // 先原样确认一次：与上一任务**参数完全相同**（同 lq_path + 同默认值，§4.3 起
      // 前端恒传 mask_path=null）→ 幂等层命中 RESUMED_COMPLETED，队列**不该**多出一行。
      // 后缀两次都留空：指纹里那个值由后端现读 SR 配置文件得到（2026-09-16 起），
      // 两次解析同源，所以"原样再提交"仍然成立；这段不是"两次都传 'sr'"的巧合。
      await clickByText(page, '提交 SR');
      await waitFor(page, () => !document.querySelector('.qp-form .qp-draft-tip'),
        15000, '提交往返完成（预填被清）');
      await sleep(400);
      assert((await firstRowTags(page)).length === 1,
        '同参数再提交 → 幂等层复用原任务，队列没有第二行');

      // 改一个参数（操作员的真实动作）→ 指纹不同 → 这才该新建任务并跑完
      await setField(page, 'SR 倍率', '4');
      await clickByText(page, '提交 SR');   // 用户确认，才真正落库提交
      await waitFor(page, () => {
        const tags2 = [...document.querySelectorAll('tbody tr td .tag')].map((x) => x.textContent.trim());
        return tags2.length === 2 && tags2[0] === '完成';
      }, 25000, '改参后的任务 COMPLETED');
      assert(true, '确认提交后队列出现第 2 个任务并跑完');

      /* ---------- D. 布局：三页同宽（--page-w）+ 耗时列不换行 ---------- */
      console.log('\n[D] 布局（页宽令牌 / 耗时列 nowrap）');
      const pageRoots = [['/scenes', '.scenes-page'], ['/queue', '.queue-page'],
                         ['/chat', '.chat-page']];
      const maxW = {};
      for (const [route, sel] of pageRoots) {
        await page.goto(base + route, { waitUntil: 'networkidle2', timeout: 30000 });
        maxW[sel] = await page.evaluate((s) => {
          const el = document.querySelector(s);
          return el ? getComputedStyle(el).maxWidth : null;
        }, sel);
      }
      // 改前三页各写各的：场景库 1360、队列/聊天 1240 —— 宽度就是这么漂开的。
      assert(maxW['.scenes-page'] && maxW['.scenes-page'] === maxW['.queue-page']
        && maxW['.queue-page'] === maxW['.chat-page'],
        `场景库 / 队列 / 聊天 三页同宽（${pageRoots.map(([, s]) => maxW[s]).join(' / ')}）`);

      await page.goto(base + '/queue', { waitUntil: 'networkidle2', timeout: 30000 });
      await waitFor(page, () => !!document.querySelector('.qp-tbl tbody td.elapsed'),
        10000, '耗时列在场');
      const elapsedStyle = await page.evaluate(() =>
        getComputedStyle(document.querySelector('.qp-tbl tbody td.elapsed')).whiteSpace);
      // 改前没有 nowrap：「已运行 3 分 20 秒」中间的三个空格就是断行点。
      assert(elapsedStyle === 'nowrap', `耗时列不换行（white-space=${elapsedStyle}）`);

      const appErrors = errors.filter((e) => !isIgnorableConsole(e));
      assert(appErrors.length === 0, `无浏览器错误 (${JSON.stringify(appErrors.slice(0, 4))})`);
      assert(external.length === 0, `无外部网络请求 (${external.slice(0, 3).join(', ')})`);

      console.log(`\n[test-platform] ✅ 全部通过 (${pass} 项断言)`);
    } finally {
      await browser.close();
      server.close();
    }
  } finally {
    child.kill();
    // Windows 下 sqlite 句柄在进程退出后瞬时释放：稍候 + 容忍 EBUSY
    await sleep(600);
    for (let i = 0; i < 3; i++) {
      try { fs.rmSync(tmp, { recursive: true, force: true }); break; }
      catch (e) { if (i === 2) console.warn('[cleanup] 临时目录未删净:', e.message); await sleep(300); }
    }
  }
}

main().catch((err) => {
  console.error('\n[test-platform] ❌ 失败:', err.message);
  process.exit(1);
});
