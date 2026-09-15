// shot-theme.js — 主题截图：mock /api/*，逐页渲染新莫兰迪浅色主题，出 PNG 供人眼验收
'use strict';
const path = require('path');
const fs = require('fs');
const http = require('http');
const { launchBrowser, startStaticServer, waitFor } = require('./vue-lib.js');

const REPO = path.resolve(__dirname, '..');
const FIX = path.join(REPO, 'test-tifs', 'e2e-vue');
const OUT = path.join(REPO, '.e2e', 'shots');
const VIEWPORT = { width: 1440, height: 860 };

// 伪装 /api/* 的本地 JSON 服务器（静态服务不认得这些路径，会 SPA fallback 成 html）
function startApiServer() {
  const scenes = {
    source: 'disk', scanned: 1284, count: 8,
    results: [
      { id: 'a', name: 'GF07A03_PMS01_20250412_L1', satellite: 'GF07A03', sensor: 'PMS01', date: '2025-04-12', size_bytes: 894213376, fake: false, W: 18432, H: 15360, rel: 'GF07A03/…', jpgUrl: null, hasPreview: true },
      { id: 'b', name: 'KF02B04_PMS02_20250403_L1', satellite: 'KF02B04', sensor: 'PMS02', date: '2025-04-03', size_bytes: 1248777984, fake: false, W: 12288, H: 12288, rel: 'KF02B04/…', jpgUrl: null, hasPreview: true },
      { id: 'c', name: 'GF07A03_PMS01_20250327_L1', satellite: 'GF07A03', sensor: 'PMS01', date: '2025-03-27', size_bytes: 213005401, fake: false, W: 9216, H: 9216, rel: 'GF07A03/…', jpgUrl: null, hasPreview: false },
      { id: 'd', name: 'KF02B04_PMS02_20250318_L1', satellite: 'KF02B04', sensor: 'PMS02', date: '2025-03-18', size_bytes: 641012224, fake: false, W: 15360, H: 15360, rel: 'KF02B04/…', jpgUrl: null, hasPreview: false },
      { id: 'e', name: 'GF02B02_OLD_20250201_L1', satellite: 'GF02B02', sensor: null, date: '2025-02-01', size_bytes: 2097152, fake: true, W: 512, H: 512, rel: 'legacy/…', jpgUrl: null, hasPreview: false },
      { id: 'f', name: 'KF01B02_PMS03_20250401_L1', satellite: 'KF01B02', sensor: 'PMS03', date: '2025-04-01', size_bytes: 482341248, fake: false, W: 10240, H: 9216, rel: 'KF01B02/…', jpgUrl: null, hasPreview: true },
    ],
  };
  const sess = [
    { session_id: 'sess_9f4a2b7c01', created_at: 1744351200, updated_at: 1744365000, status: 'active' },
    { session_id: 'sess_1b3d08ee22', created_at: 1744200000, updated_at: 1744210000, status: 'active' },
  ];
  const msgs = {
    session_id: 'sess_9f4a2b7c01', status: 'active',
    messages: [
      { seq: 0, role: 'user', content: '检索 2025-04 之后 GF07A03 的 PMS01 影像，看有多少景可用超分。' },
      { seq: 1, role: 'tool', tool_name: 'search_scenes', ok: true, tool_call_id: 'c1', content: null, detail: 'GF07A03 / PMS01 · 命中 2 景' },
      { seq: 2, role: 'assistant', tool_calls: [{ name: 'search_scenes', arguments: { satellite: 'GF07A03', sensor: 'PMS01' } }] },
      { seq: 3, role: 'tool', tool_name: 'search_scenes', ok: true, tool_call_id: 'c1', detail: 'satellite=GF07A03 sensor=PMS01 date_from=2025-04-01 → 2 景' },
      { seq: 4, role: 'assistant', content: '4 月后共命中 2 景可超分影像：GF07A03_PMS01_20250412_L1 与 KF02B04_PMS02_20250403_L1。需要我打开某景绘制掩码后提交 SR 吗？' },
    ],
  };
  const tasks = [
    { task_id: 1003, fingerprint: 'sha256:41f7…c90a', session_id: 'sess_9f4a2b7c01', job_id: 8871, state: 'RUNNING',
      params: { lq_path: '/DiskArray/GF07A03_xxx_L1_PAN', mask_path: '/DiskArray/masks/GF07A03_xxx_L1_PAN_roi.tif', sr_scale: 4, suffix: 't', gpu: 1, cloud_limit: 5, delete_ori: false, grid_align: true },
      config_xml: null, batch_script: null, log_dir: '/DiskArray/logs/1003', created_at: 1744360000, updated_at: 1744361000 },
    { task_id: 1002, fingerprint: 'sha256:7be1…0d2c', session_id: null, job_id: null, state: 'PENDING',
      params: { lq_path: '/DiskArray/KF02B04_xxx_L1_PAN', mask_path: null, sr_scale: 2, suffix: '', gpu: 4, cloud_limit: 10, delete_ori: false, grid_align: false },
      config_xml: null, batch_script: null, log_dir: null, created_at: 1744359000, updated_at: 1744359000 },
    { task_id: 1001, fingerprint: 'sha256:0b4c…9f12', session_id: null, job_id: 8804, state: 'COMPLETED',
      params: { lq_path: '/DiskArray/GF02B02_legacy', mask_path: '/DiskArray/masks/legacy_roi.tif', sr_scale: 4, suffix: 't', gpu: 1, cloud_limit: 0, delete_ori: true, grid_align: false },
      config_xml: null, batch_script: null, log_dir: '/DiskArray/logs/1001', created_at: 1744350000, updated_at: 1744352000 },
    { task_id: 1000, fingerprint: 'sha256:dead…beef', session_id: null, job_id: null, state: 'FAILED',
      params: { lq_path: '/DiskArray/bad_input', mask_path: null, sr_scale: 4, suffix: 't', gpu: 1, cloud_limit: 0, delete_ori: false, grid_align: false },
      config_xml: null, batch_script: null, log_dir: null, created_at: 1744340000, updated_at: 1744341000 },
  ];

  const server = http.createServer((req, res) => {
    const u = decodeURIComponent(req.url.split('?')[0]);
    const send = (obj) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
    if (u === '/api/scenes') return send(scenes);
    if (u.startsWith('/api/chat/sessions/') && u.endsWith('/messages')) return send(msgs);
    if (u === '/api/chat/sessions') return send({ sessions: sess });
    if (u === '/api/queue') return send({ tasks });
    res.writeHead(404); res.end('{}');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ close: () => server.close(), port: server.address().port }));
  });
}

async function snap(page, name) {
  await new Promise((r) => setTimeout(r, 600));
  await page.screenshot({ path: path.join(OUT, name + '.png') });
  console.log('  ✓', name + '.png');
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const api = await startApiServer();
  // 把 /api 交给 mock 服务器，其余交给静态服务
  const srv = await startStaticServer({ dist: path.join(REPO, 'frontend', 'dist') });
  const browser = await launchBrowser();
  const page = await browser.newPage();
  await page.setViewport(VIEWPORT);

  // 主服务器顶替：把对 srv 的 /api/* 请求重写到 api 服务器
  const base = srv.url;
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const u = req.url();
    if (u.startsWith(base + '/api/')) {
      req.continue({ url: 'http://127.0.0.1:' + api.port + u.slice(base.length) });
    } else {
      req.continue();
    }
  });

  const pages = [
    { route: '/scenes', name: '01-scenes' },
    { route: '/chat', name: '02-chat' },
    { route: '/queue', name: '03-queue' },
    { route: '/viewer', name: '04-viewer' },
  ];
  for (const p of pages) {
    console.log('route', p.route);
    await page.goto(base + p.route, { waitUntil: 'networkidle0', timeout: 30000 }).catch(() => {});
    await snap(page, p.name);
  }

  // viewer 再补一张「已载入影像」态：上传 rgb8 fixture，进入绘制掩码模式看一眼浮层
  console.log('route /viewer + upload + drawmode');
  await page.goto(base + '/viewer', { waitUntil: 'load' }).catch(() => {});
  await new Promise((r) => setTimeout(r, 400));
  const tif = path.join(FIX, 'rgb8.tif');
  const input = await page.$('input[type=file]');
  if (input) {
    await input.uploadFile(tif);
    await new Promise((r) => setTimeout(r, 2500));
  }
  try {
    await page.evaluate(() => window.__viewer && window.__viewer.enterDraw && window.__viewer.enterDraw());
    await new Promise((r) => setTimeout(r, 400));
  } catch (e) { /* noop */ }
  await snap(page, '05-viewer-image');

  await browser.close();
  api.close();
  srv.close();
  console.log('done →', OUT);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
