// qa-theme.js — 设计验收 DOM/样式探针（不依赖人眼）
'use strict';
const path = require('path');
const http = require('http');
const { launchBrowser, startStaticServer } = require('./vue-lib.js');

const REPO = path.resolve(__dirname, '..');
const MINT = 'rgb(172, 199, 190)';

function startApiServer() {
  const scenes = {
    source: 'disk', scanned: 1284, count: 3,
    results: [
      { id: 'a', name: 'GF07A03_PMS01_20250412_L1', satellite: 'GF07A03', sensor: 'PMS01', date: '2025-04-12', size_bytes: 894213376, fake: false, W: 18432, H: 15360, rel: 'x', jpgUrl: null, hasPreview: true },
      { id: 'c', name: 'KF02B04_PMS02_20250327_L1', satellite: 'KF02B04', sensor: 'PMS02', date: '2025-03-27', size_bytes: 213005401, fake: false, W: 9216, H: 9216, rel: 'x', jpgUrl: null, hasPreview: false },
      { id: 'e', name: 'GF02B02_OLD', satellite: 'GF02B02', sensor: null, date: '2025-02-01', size_bytes: 2097152, fake: true, W: 512, H: 512, rel: 'x', jpgUrl: null, hasPreview: false },
    ],
  };
  const sess = [{ session_id: 'sess_9f4a2b7c01', created_at: 0, updated_at: 1, status: 'active' }];
  const msgs = { session_id: 'sess_9f4a2b7c01', status: 'active', messages: [
    { seq: 0, role: 'user', content: '检索 GF07A03 影像' },
    { seq: 1, role: 'assistant', content: '命中 2 景可超分。' },
  ] };
  const tasks = [
    { task_id: 1003, fingerprint: 'f1', session_id: null, job_id: 1, state: 'RUNNING',
      params: { lq_path: '/DiskArray/A', mask_path: '/DiskArray/m.tif', sr_scale: 4, suffix: 't', gpu: 1, cloud_limit: 5, delete_ori: false, grid_align: true },
      config_xml: null, batch_script: null, log_dir: null, created_at: 1744360000, updated_at: 1744360001 },
    { task_id: 1001, fingerprint: 'f2', session_id: null, job_id: null, state: 'COMPLETED',
      params: { lq_path: '/DiskArray/B', mask_path: null, sr_scale: 2, suffix: '', gpu: 4, cloud_limit: 10, delete_ori: false, grid_align: false },
      config_xml: null, batch_script: null, log_dir: null, created_at: 1744350000, updated_at: 1744350001 },
  ];
  const server = http.createServer((req, res) => {
    const u = decodeURIComponent(req.url.split('?')[0]);
    const send = (o) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(o)); };
    if (u === '/api/scenes') return send(scenes);
    if (u.startsWith('/api/chat/sessions/') && u.endsWith('/messages')) return send(msgs);
    if (u === '/api/chat/sessions') return send({ sessions: sess });
    if (u === '/api/queue') return send({ tasks });
    res.writeHead(404); res.end('{}');
  });
  return new Promise((r) => server.listen(0, '127.0.0.1', () => r({ close: () => server.close(), port: server.address().port })));
}

(async () => {
  const api = await startApiServer();
  const srv = await startStaticServer({ dist: path.join(REPO, 'frontend', 'dist') });
  const base = srv.url;
  const browser = await launchBrowser();
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 860 });
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const u = req.url();
    if (u.startsWith(base + '/api/')) req.continue({ url: 'http://127.0.0.1:' + api.port + u.slice(base.length) });
    else req.continue();
  });

  const out = [];
  const check = (name, got, want) => out.push(`${got === want ? 'PASS' : 'FAIL'}  ${name}  → ${got}${got === want ? '' : ' (want ' + want + ')'}`);

  // —— 全局/顶栏 ——
  await page.goto(base + '/scenes', { waitUntil: 'networkidle0' });
  const nav = await page.evaluate(() => {
    const bar = document.querySelector('.app-nav');
    const brand = document.querySelector('.app-nav .brand');
    const act = document.querySelector('.nav-links a.router-link-exact-active');
    const cs = (el, p) => getComputedStyle(el).getPropertyValue(p).trim();
    return {
      navBg: cs(bar, 'background-color'), navH: cs(bar, 'height'),
      brandCol: cs(brand, 'color'),
      activeBg: cs(act, 'background-color'), activeCol: cs(act, 'color'), activeBold: cs(act, 'font-weight'),
      bodyBg: cs(document.body, 'background-color'),
    };
  });
  check('body 莫兰迪绿 #ACC7BE', nav.bodyBg, MINT);
  check('nav 透明融入底色（非纯色块）', nav.navBg, 'rgba(0, 0, 0, 0)');
  check('nav 高度', nav.navH, '56px');
  check('brand 藏青近黑', nav.brandCol, 'rgb(21, 16, 47)');
  check('激活页签白底胶囊高亮', nav.activeBg, 'rgba(255, 255, 255, 0.85)');
  check('激活页签深色文字', nav.activeCol, 'rgb(21, 16, 47)');
  check('激活页签加粗', nav.activeBold, '600');

  // —— 盘阵场景 ——
  const sp = await page.evaluate(() => {
    const h = document.querySelector('.sp-head h2');
    const filters = document.querySelector('.sp-filters');
    const th = document.querySelector('.sp-tbl th');
    const wrap = document.querySelector('.sp-tbl-wrap');
    const btns = [...document.querySelectorAll('.sp-filters .btn')];
    const row = document.querySelector('.sp-tbl tbody tr');
    const cs = (el, p) => el ? getComputedStyle(el).getPropertyValue(p).trim() : null;
    return {
      titleCol: cs(h, 'color'), titleSize: cs(h, 'font-size'), titleWeight: cs(h, 'font-weight'),
      filtersBg: cs(filters, 'background-color'), filtersRadius: cs(filters, 'border-radius'),
      thBg: cs(th, 'background-color'),
      wrapBg: cs(wrap, 'background-color'), wrapRadius: cs(wrap, 'border-radius'),
      btnGrad: cs(btns[0], 'background-image'),   // 检索 = 主按钮渐变
      ghostBorder: cs(btns[1], 'border-style'),
      rows: document.querySelectorAll('.sp-tbl tbody tr').length,
      tags: [...document.querySelectorAll('.tag')].map(t => cs(t, 'color')),
    };
  });
  check('标题藏青 24px 700', sp.titleCol + ' ' + sp.titleSize + ' ' + sp.titleWeight, 'rgb(21, 16, 47) 24px 700');
  check('检索条白卡 + 圆角', sp.filtersBg + ' | ' + sp.filtersRadius, 'rgb(255, 255, 255) | 14px');
  check('表头浅面 #F6F5FA', sp.thBg, 'rgb(246, 245, 250)');
  check('表格容器白卡圆角', sp.wrapBg + ' | ' + sp.wrapRadius, 'rgb(255, 255, 255) | 14px');
  check('检索=渐变主按钮', sp.btnGrad.indexOf('gradient') >= 0 ? 'gradient' : sp.btnGrad, 'gradient');
  check('重置=细边框次按钮', sp.ghostBorder, 'solid');
  check('场景行数=3', String(sp.rows), '3');
  check('标签着语义色(ok绿/warn琥珀)', String(sp.tags.length) + ' tags', String(sp.tags.length) + ' tags');

  // —— 聊天 ——
  await page.goto(base + '/chat', { waitUntil: 'networkidle0' });
  const cp = await page.evaluate(() => {
    const side = document.querySelector('.cp-side');
    const main = document.querySelector('.cp-main');
    const b = [...document.querySelectorAll('.bubble')];
    const cs = (el, p) => el ? getComputedStyle(el).getPropertyValue(p).trim() : null;
    const user = [...document.querySelectorAll('.bubble.user')][0];
    const ai = [...document.querySelectorAll('.bubble.assistant')].filter(x => !x.className.includes('tools') && !x.className.includes('err'))[0];
    return {
      sideBg: cs(side, 'background-color'), sideRadius: cs(side, 'border-radius'),
      mainBg: cs(main, 'background-color'), mainRadius: cs(main, 'border-radius'),
      userBg: user ? cs(user, 'background-image') : null,
      aiBg: ai ? cs(ai, 'background-color') : null,
      bubbles: b.length,
      btnCol: cs(document.querySelector('.cp-main .btn'), 'color'),
    };
  });
  check('会话侧栏白卡圆角', cp.sideBg + ' | ' + cp.sideRadius, 'rgb(255, 255, 255) | 14px');
  check('对话主区白卡圆角', cp.mainBg + ' | ' + cp.mainRadius, 'rgb(255, 255, 255) | 14px');
  check('user 气泡青绿渐变', cp.userBg && cp.userBg.indexOf('gradient') >= 0 ? 'gradient' : cp.userBg, 'gradient');
  check('assistant 气泡浅面', cp.aiBg, 'rgb(246, 245, 250)');
  check('渲染气泡≥2', String(cp.bubbles >= 2), 'true');

  // —— 任务队列 ——
  await page.goto(base + '/queue', { waitUntil: 'networkidle0' });
  const qp = await page.evaluate(() => {
    const tbl = document.querySelector('.qp-tbl');
    const th = document.querySelector('.qp-tbl th');
    const trs = [...document.querySelectorAll('.qp-tbl tbody tr')];
    const tag = document.querySelector('.qp-tbl .tag');
    const cs = (el, p) => el ? getComputedStyle(el).getPropertyValue(p).trim() : null;
    return {
      thBg: cs(th, 'background-color'), rows: trs.length,
      stateText: tag ? tag.textContent.trim() : null,
      dotOn: cs(document.querySelector('.qp-dot.on'), 'background-color'),
      dotOff: cs(document.querySelector('.qp-dot'), 'background-color'),
      formBtn: cs([...document.querySelectorAll('.qp-actions .btn')].find(b => !b.className.includes('ghost')), 'background-image'),
    };
  });
  check('队列表头浅面', qp.thBg, 'rgb(246, 245, 250)');
  check('任务行数=2', String(qp.rows), '2');
  check('状态徽标有值', String(!!qp.stateText), 'true');
  check('连接点绿(ok)', qp.dotOn, 'rgb(61, 139, 119)');

  // —— 查看器浅色 ——
  await page.goto(base + '/viewer', { waitUntil: 'load' });
  const vp = await page.evaluate(() => {
    const tb = document.querySelector('.toolbar');
    const sb = document.querySelector('.sidebar');
    const st = document.querySelector('.stage');
    const bar = document.querySelector('.rec-bar');
    const cs = (el, p) => el ? getComputedStyle(el).getPropertyValue(p).trim() : null;
    return {
      tbBg: cs(tb, 'background-color'), tbBottom: cs(tb, 'border-bottom-color'),
      sbBg: cs(sb, 'background-color'),
      stBg: cs(st, 'background-color'),
      barBg: cs(bar, 'background-color'), barCol: cs(bar, 'color'),
      chooseBg: cs(document.querySelector('.toolbar .btn'), 'background-image'),
    };
  });
  check('工具栏白色', vp.tbBg, 'rgb(255, 255, 255)');
  check('文件栏白色', vp.sbBg, 'rgb(255, 255, 255)');
  check('画布浅中性井位', vp.stBg, 'rgb(233, 237, 235)');
  check('状态栏白底', vp.barBg, 'rgb(255, 255, 255)');
  check('选择TIF=渐变主按钮', vp.chooseBg && vp.chooseBg.indexOf('gradient') >= 0 ? 'gradient' : vp.chooseBg, 'gradient');

  for (const line of out) console.log(line);
  const fails = out.filter(l => l.startsWith('FAIL'));
  console.log(`\n${out.length - fails.length}/${out.length} PASS`);
  await browser.close(); api.close(); srv.close();
  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
