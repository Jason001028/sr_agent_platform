// Local JPEG + queue SSE fixtures only. No TIFF, disk array or SR execution.
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { launchBrowser } = require('./launchBrowser');
const root = path.resolve(__dirname, '..');
const dist = path.join(root, 'frontend/dist');
const artifacts = path.join(__dirname, 'artifacts/image-refresh');
fs.mkdirSync(artifacts, { recursive: true });
function jpeg(rev, color) {
  const script = `import io,base64\nfrom PIL import Image\nfrom backend.services.preview_jpg import rule_stamp\nb=io.BytesIO(); Image.new('L',(320,160),${color}).save(b,format='JPEG',comment=rule_stamp(div=4,source_rev='${rev}'));print(base64.b64encode(b.getvalue()).decode())`;
  return Buffer.from(execFileSync('python', ['-c', script], { cwd: root }).toString().trim(), 'base64');
}
const images = { 0: jpeg('abcdef123456-100-0', 70), 1: jpeg('abcdef123456-100-1', 130), 2: jpeg('abcdef123456-100-2', 170) };
let phase = 'running', version = 1, previewGets = 0, sceneBakes = 0;
const streams = new Set();
function tasks() { return [1, 2].map(id => ({ task_id: id, fingerprint: 'fake-'+id, job_id: 100+id, state: phase === 'running' ? 'RUNNING' : 'COMPLETED',
  params: { lq_path: '/synthetic/sequence', suffix: 'sr', mask_path: null, sr_scale: 2 }, created_at: 1, updated_at: version + 10,
  started_at: 1, finished_at: phase === 'running' ? null : version + 10, preview_state: phase === 'ready' ? 'done' : 'running' })); }
function result(id) {
  const srcRev = `abcdef123456-100-${version}`;
  return { task_id: id, state: phase === 'ready' ? 'ready' : 'preparing', note: 'fixture', group: '/synthetic/sequence', kind: id === 1 ? 'input' : 'product',
    version: `result-${id}|${srcRev}`, aliases: id === 1 ? ['library-input'] : [], finished_at: version + 10,
    row: { id: `result-${id}`, name: id === 1 ? 'PAN.tif' : 'NOSR.tif', srcRev, W: 1024, H: 512, previewDiv: 4, hasPreview: phase === 'ready',
      satellite: null, sensor: null, date: null, fake: false, size_bytes: 100, rel: null, jpgUrl: null, lq_path: '/synthetic/sequence', thumbnail_url: null } };
}
function json(res, data) { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); }
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://fixture');
  if (u.pathname === '/api/queue') return json(res, { tasks: tasks() });
  if (u.pathname === '/api/queue/events') { res.writeHead(200, { 'Content-Type': 'text/event-stream', Connection: 'keep-alive' }); res.write(': connected\n\n'); streams.add(res); req.on('close', () => streams.delete(res)); return; }
  if (u.pathname === '/test/phase') {
    phase = u.searchParams.get('phase'); version = Number(u.searchParams.get('version') || version);
    for (const stream of streams) for (const task of tasks()) {
      stream.write('data: ' + JSON.stringify({ type: 'job_update', task_id: task.task_id, state: task.state, updated_at: task.updated_at, finished_at: task.finished_at }) + '\n\n');
      stream.write('data: ' + JSON.stringify({ type: 'preview_update', task_id: task.task_id, state: task.preview_state }) + '\n\n');
    }
    return json(res, { ok: true });
  }
  const match = u.pathname.match(/^\/api\/queue\/(\d+)\/image-refresh(\/preview)?$/);
  if (match) {
    if (!match[2]) return json(res, result(Number(match[1])));
    previewGets++; const bytes = images[version];
    res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Content-Length': bytes.length }); res.end(bytes); return;
  }
  if (u.pathname === '/fixture.jpg') { res.setHeader('Content-Type', 'image/jpeg'); res.end(images[0]); return; }
  if (/\/preview$/.test(u.pathname)) { sceneBakes++; res.writeHead(409); return json(res, { detail: 'must not bake during refresh' }); }
  if (u.pathname === '/api/goals') return json(res, []);
  if (u.pathname.startsWith('/api/')) return json(res, {});
  let file = path.join(dist, u.pathname);
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
  res.setHeader('Content-Type', ({ '.html':'text/html', '.js':'application/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.webp':'image/webp' })[path.extname(file)] || 'application/octet-stream');
  res.end(fs.readFileSync(file));
});
async function seed(page) {
  await page.evaluate(async () => {
    const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
    const viewer = pinia._s.get('viewer');
    const blob = await (await fetch('/fixture.jpg')).blob();
    await viewer.openSceneJpg({ name:'PAN.tif', sceneId:'library-input', W:1024, H:512, lqPath:'/synthetic/sequence', sceneDir:'/synthetic/sequence', srcRev:'abcdef123456-100-0' }, blob);
    viewer.activeRec.maskRois = [[[1,1],[10,1],[10,10]]];
    viewer.setPaneView('A', { scale:2, ox:11, oy:17 });
  });
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await launchBrowser(); const page = await browser.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.setViewport({ width:1600, height:1000 });
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base + '/viewer'); await page.waitForSelector('.stage'); await seed(page);
    await page.evaluate(() => fetch('/test/phase?phase=preparing'));
    await page.waitForFunction(() => document.querySelector('.sidebar')?.textContent.includes('成果准备中'));
    assert.equal(await page.$$eval('[data-e2e=image-refresh-badge]', es => es.length), 0);
    await page.evaluate(() => fetch('/test/phase?phase=ready'));
    await page.waitForFunction(() => document.querySelectorAll('[data-e2e=image-refresh-badge]').length === 2);
    await page.waitForFunction(() => document.querySelectorAll('.sr-refresh-card--sweep').length === 1);
    assert.match(await page.$eval('.refresh-group-count', e => e.textContent), /2 张新图/);
    assert.equal(await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').activeRec.srcRev), 'abcdef123456-100-0');
    await page.screenshot({ path:path.join(artifacts,'unread.png') });
    await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').setPaneView('A', {scale:2,ox:11,oy:17}));
    await page.click('[data-rec-id="1"] .refresh-open');
    await page.waitForFunction(() => !document.querySelector('[data-rec-id="1"] [data-e2e=image-refresh-badge]'));
    const loaded = await page.evaluate(() => { const v = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer'); return { rev:v.activeRec.srcRev, view:{scale:v.view.scale,ox:v.view.ox,oy:v.view.oy}, rois:JSON.parse(JSON.stringify(v.activeRec.maskRois)) }; });
    assert.equal(loaded.rev, 'abcdef123456-100-1'); assert.deepEqual(loaded.view, { scale:2,ox:11,oy:17 }); assert.equal(loaded.rois.length,1);
    assert.match(await page.$eval('.refresh-group-count', e => e.textContent), /1 张新图/);
    await page.evaluate(() => fetch('/test/phase?phase=ready&version=2'));
    await page.waitForFunction(() => document.querySelectorAll('[data-e2e=image-refresh-badge]').length === 2);
    assert.equal(await page.$$eval('.sidebar [data-rec-id]', es => es.length), 2);
    assert.equal(await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').activeRec.srcRev), 'abcdef123456-100-1');
    await page.emulateMediaFeatures([{ name:'prefers-reduced-motion', value:'reduce' }]);
    await page.waitForFunction(() => document.querySelectorAll('.sr-refresh-card--sweep').length === 0);
    assert.equal(await page.$$eval('.sidebar img[src*="new-animated"]', es => es.length),0);
    await page.click('[data-rec-id="1"] .refresh-open');
    await page.waitForFunction(() => !document.querySelector('[data-rec-id="1"] [data-e2e=image-refresh-badge]'));
    await page.reload(); await page.waitForSelector('.stage'); await seed(page);
    await page.waitForFunction(() => document.querySelectorAll('[data-e2e=image-refresh-badge]').length === 1);
    assert.equal(sceneBakes,0); assert.ok(previewGets <= 5);
    assert.deepEqual(errors,[]);
    console.log('Image Refresh: preparing/ready, shared SSE, bounded warmup, camera/ROI preservation, same-name revisions, read persistence, group counts and reduced motion passed.');
  } finally { for (const s of streams) s.destroy(); if (browser) await browser.close(); server.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
