// Browser -> real FastAPI/SQLite/Batch worker -> independent Fake SR ledger.
// Run after building frontend: node .e2e/test-batch-real.js
const assert = require('assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { spawn, spawnSync } = require('child_process');
const { createHash } = require('crypto');
const { launchBrowser } = require('./launchBrowser');
const repo = path.resolve(__dirname, '..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'batch-browser-'));
const artifacts = path.join(__dirname, 'artifacts');
const pause = ms => new Promise(r => setTimeout(r, ms));
const ledger = [];
let child, browser, page, base, names;
const evidence = { root, scheduler: 'independent Fake SR; wall-clock lifecycle; survives API restart', checks: [] };
evidence.frontendIndexSha256 = createHash('sha256').update(fs.readFileSync(path.join(repo, 'frontend/dist/index.html'))).digest('hex');
const scheduler = http.createServer(async (req, res) => {
  const send = body => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); };
  if (req.url === '/submit') {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const { script } = JSON.parse(raw);
    ledger.push({ job_id: ledger.length + 1, script, accepted: Date.now() });
    return send({ job_id: ledger.length });
  }
  const job = ledger[Number(req.url.split('/').pop()) - 1];
  if (!job) { res.statusCode = 404; return send({ error: 'unknown job' }); }
  const elapsed = Date.now() - job.accepted;
  send({ job_id: job.job_id, active: elapsed < 6500,
    state: elapsed < 500 ? 'PENDING' : elapsed < 6500 ? 'RUNNING' : 'COMPLETED', exit_code: elapsed < 6500 ? null : '0:0' });
});
async function json(url, body) {
  const r = await fetch(base + url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.ok(r.ok, `${url}: ${r.status} ${r.ok ? '' : await r.text()}`); return r.json();
}
async function until(fn, timeout = 180000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await fn(); if (value) return value; await pause(100); }
  throw Error('Timed out: ' + fn.toString());
}
async function start(port) {
  const python = fs.existsSync(path.join(repo, '.venv/Scripts/python.exe')) ? path.join(repo, '.venv/Scripts/python.exe') : 'python';
  child = spawn(python, ['-m', 'backend.tests.batch_browser_fixture', '--root', root, '--port', String(port), '--scheduler', `http://127.0.0.1:${scheduler.address().port}`], { cwd: repo, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', b => fs.appendFileSync(path.join(root, 'api.log'), b));
  child.stderr.on('data', b => fs.appendFileSync(path.join(root, 'api.log'), b));
  await until(async () => { try { return (await fetch(base + '/api/health')).ok; } catch { return false; } }, 30000);
}
async function stop() {
  if (!child || child.exitCode !== null) return;
  const exited = new Promise(r => child.once('exit', r));
  if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
  else child.kill('SIGKILL');
  await exited;
}
async function store(expression) {
  return page.evaluate(async expr => {
    const p = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
    const value = await Function('p', `return (${expr})`)(p);
    return value === undefined ? null : JSON.parse(JSON.stringify(value));
  }, expression);
}
async function clickText(selector, text) {
  await page.evaluate((sel, text) => {
    const e = [...document.querySelectorAll(sel)].find(e => e.textContent.includes(text));
    if (!e || e.disabled) throw Error('Unavailable button: ' + text); e.click();
  }, selector, text);
}
async function openMonitor() {
  await page.waitForSelector('[data-e2e=batch-task-expand]');
  await page.evaluate(() => document.querySelector('.ctx-tab').click());
  if (await page.$eval('[data-e2e=batch-task-expand]', e => e.getAttribute('aria-expanded') !== 'true')) await page.click('[data-e2e=batch-task-expand]');
  await clickText('.task-detail nav button', '执行监控');
}
async function create(count, offset, title, invalid = false) {
  await page.evaluate(() => document.querySelector('.ctx-tab').click());
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('batch-task-open', { detail: { section: 'draft' } })));
  const text = names.slice(offset, offset + count).map(n => `${n}\t模糊 问题类型:模糊 影像类型:pan 行列号:12,14\t验收`).join('\n') + (invalid ? '\ninvalid QC line' : '');
  await page.$eval('[data-e2e=batch-draft] textarea', (e, v) => { e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); }, text);
  await page.$eval('[aria-label=批次名称]', (e, v) => { e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); }, title);
  const before = (await json('/api/goals?kind=batch')).length;
  await clickText('[data-e2e=batch-draft] button', '核验并');
  await page.waitForSelector('.frozen');
  assert.equal((await json('/api/goals?kind=batch')).length, before, 'Import never executes');
  assert.equal(await page.$eval('.frozen button', e => e.disabled), true);
  if (invalid) await page.click('.frozen p label input');
  await page.click('.frozen > label input');
  // Emulate a lost response after the real server commits. The UI retries with the same key.
  let intercepted = false;
  await page.setRequestInterception(true);
  const intercept = async req => {
    if (!intercepted && req.method() === 'POST' && new URL(req.url()).pathname === '/api/goals') {
      intercepted = true;
      const result = await json('/api/goals', JSON.parse(req.postData()));
      evidence.checks.push({ lostCreateResponse: result.goal_id });
      await req.abort('failed');
    } else await req.continue();
  };
  page.on('request', intercept);
  await clickText('.frozen button', '创建 Batch Goal');
  await page.waitForFunction(() => document.querySelector('[data-e2e=batch-draft] [role=alert]'));
  page.off('request', intercept); await page.setRequestInterception(false);
  await clickText('.frozen button', '创建 Batch Goal');
  await until(async () => (await json('/api/goals?kind=batch')).length === before + 1);
  await page.waitForFunction(() => document.querySelector('.task-detail nav button:nth-child(2)').classList.contains('on'));
  const gid = await store("p._s.get('batchGoal').goalId");
  assert.equal(new URL(page.url()).searchParams.get('batch_goal'), gid);
  return gid;
}
async function control(action) {
  await page.evaluate(action => document.querySelector(`[data-e2e=batch-${action}]`).click(), action);
  if (action === 'cancel') await clickText('[data-e2e=batch-controls] button', '确认取消');
  await until(async () => !await store("p._s.get('batchGoal').controlBusy"));
  assert.equal(await store("p._s.get('batchGoal').controlError"), '');
}
(async () => {
  await new Promise(r => scheduler.listen(0, '127.0.0.1', r));
  const socket = http.createServer(); await new Promise(r => socket.listen(0, '127.0.0.1', r));
  const port = socket.address().port; await new Promise(r => socket.close(r)); base = `http://127.0.0.1:${port}`;
  try {
    await start(port); names = JSON.parse(fs.readFileSync(path.join(root, 'fixtures.json'))).names;
    browser = await launchBrowser(); page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 1000 });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + '/viewer'); await page.waitForSelector('[data-e2e=batch-draft]');
    const small = await create(5, 50, '小批次控制验收', true);
    console.log('Created small batch', small);
    await until(async () => (await json(`/api/goals/${small}/batch-snapshot`)).counts.submitted > 0);
    await control('pause');
    const paused = await json(`/api/goals/${small}/batch-snapshot`);
    await pause(7200);
    const monitored = await json(`/api/goals/${small}/batch-snapshot`);
    assert.equal(monitored.goal.status, 'PAUSED'); assert.equal(monitored.counts.submitted, paused.counts.submitted);
    assert.ok(monitored.counts.verified > paused.counts.verified, 'Monitor runs while paused');
    await control('resume');
    await until(async () => (await json(`/api/goals/${small}/batch-snapshot`)).counts.submitted > paused.counts.submitted);
    await control('cancel');
    const cancelled = await until(async () => { const s = await json(`/api/goals/${small}/batch-snapshot`); return s.goal.monitoring_done && s; });
    assert.equal(cancelled.goal.status, 'CANCELLED'); assert.ok(cancelled.counts.cancelled > 0);
    evidence.small = cancelled;
    console.log('Small control acceptance passed');
    const startCount = ledger.length;
    const gid = await create(50, 0, '50 景实际后端 Fake SR 联合验收'); evidence.goalId = gid;
    console.log('Created 50-scene batch', gid);
    const overlap = await until(async () => { const s = await json(`/api/goals/${gid}/batch-snapshot`); return s.active_jobs.length && s.producer.current && s.producer.current.ordinal > s.active_jobs[0].ordinal && s; });
    await until(async () => { const s = await store("p._s.get('batchGoal').snapshot"); return s?.goal.goal_id === gid && s.active_jobs.length && s.producer.current; });
    evidence.overlap = overlap;
    console.log('Concurrent Producer and SR observed');
    const imageError = await store("p._s.get('batchGoal').openItem(p._s.get('batchGoal').snapshot.active_jobs[0])");
    assert.equal(imageError, '');
    await until(async () => await store("!!p._s.get('viewer').activeRec?.thumb"));
    await page.waitForSelector('.batch-pane');
    evidence.checks.push('Real backend image and identity-bound Overlay displayed');
    console.log('Real image and Overlay loaded');
    // Hold a real resolve response in the browser transport, then perform actual wheel navigation.
    await page.setRequestInterception(true);
    let held;
    const hold = req => { if (!held && req.url().includes('/api/scenes/resolve')) held = req; else req.continue(); };
    page.on('request', hold);
    await until(async () => await store("!!p._s.get('batchGoal').snapshot?.producer.current?.image_path"), 30000);
    await page.evaluate(() => document.querySelector('[data-e2e=batch-follow]').click());
    await until(() => held, 15000);
    const beforeIntent = await store("p._s.get('viewer').userIntentSeq");
    await page.hover('.view-canvas'); await page.mouse.wheel({ deltaY: 100 });
    await until(async () => (await store("p._s.get('viewer').userIntentSeq")) > beforeIntent, 5000);
    const active = await store("p._s.get('viewer').activeId");
    await held.continue(); page.off('request', hold); await page.setRequestInterception(false);
    await pause(500);
    assert.equal(await store("p._s.get('batchGoal').follow"), false);
    assert.equal(await store("p._s.get('viewer').activeId"), active);
    evidence.checks.push('Late follow response after wheel cannot navigate');
    console.log('Late follow isolated');
    await page.reload({ waitUntil: 'domcontentloaded' }); await openMonitor();
    console.log('Refresh association restored');
    assert.equal(await store("p._s.get('batchGoal').goalId"), gid);
    // A real network disconnect ends the SSE; background execution is independent.
    await page.setOfflineMode(true); await pause(900); await page.setOfflineMode(false);
    await until(async () => ['live', 'done'].includes(await store("p._s.get('batchGoal').syncState")), 15000);
    console.log('Offline stream reconnected');
    const closedAt = await json(`/api/goals/${gid}/batch-snapshot`);
    assert.notEqual(closedAt.goal.status, 'COMPLETED', 'Close while Batch still runs');
    await page.close();
    const withoutPage = await until(async () => { const s = await json(`/api/goals/${gid}/batch-snapshot`); return s.goal.last_seq > closedAt.goal.last_seq && s; }, 15000);
    assert.ok(withoutPage.goal.last_seq > closedAt.goal.last_seq);
    page = await browser.newPage(); await page.setViewport({ width: 1440, height: 1000 });
    await page.goto(base + '/viewer?batch_goal=' + gid); await openMonitor();
    const restartCount = ledger.length;
    console.log('Closing page and reopening passed; restarting API', restartCount);
    await stop(); await start(port);
    evidence.checks.push({ backendRestartWhileJobsActive: restartCount });
    const final = await until(async () => { const s = await json(`/api/goals/${gid}/batch-snapshot`); return s.goal.status === 'COMPLETED' && s.goal.monitoring_done && s; });
    await until(async () => { const s = await store("p._s.get('batchGoal') && ({snapshot:p._s.get('batchGoal').snapshot, consumed:p._s.get('batchGoal').consumedSeq})"); return s?.snapshot?.goal.last_seq === final.goal.last_seq && s.consumed === final.goal.last_seq; });
    const ui = await store("({counts:p._s.get('batchGoal').snapshot.counts, consumed:p._s.get('batchGoal').consumedSeq})");
    assert.deepEqual(ui.counts, final.counts);
    assert.equal(final.counts.verified, 50); assert.equal(final.counts.verified_demo, 50); assert.equal(final.counts.verified_production, 0);
    assert.equal(ledger.length - startCount, 50);
    const submissions = ledger.slice(startCount);
    assert.equal(new Set(submissions.map(j => j.script)).size, 50, 'No physical resubmission');
    const report = await json(`/api/goals/${gid}/batch-report`);
    assert.equal(report.counts.SUCCEEDED, 50);
    assert.equal(report.provenance, 'mock'); assert.equal(report.success_scope, 'demo_contract');
    assert.equal(report.items.length, 50);
    assert.ok(report.items.every(i => i.verification.ok === true && i.verification.provenance === 'mock' && i.verification.success_scope === 'demo_contract'));
    await clickText('.task-detail nav button', '结果报告');
    await page.waitForSelector('[data-e2e=batch-report]');
    await page.evaluate(() => document.querySelector('[data-e2e=batch-report]').click());
    await page.waitForSelector('.notice-modal');
    await page.click('.notice-modal .nm-raw summary');
    assert.deepEqual(JSON.parse(await page.$eval('.nm-raw pre', e => e.textContent)), report);
    await page.click('.nm-ok');
    await clickText('.task-detail nav button', '执行监控');
    await page.evaluate(() => document.querySelector('[data-e2e=batch-events] > summary').click());
    const cursors = [];
    for (let i = 0; i <= Math.ceil(final.goal.last_seq / 20); i++) {
      const has = await store("p._s.get('batchGoal').hasEarlier"); if (!has) break;
      await page.evaluate(() => document.querySelector('[data-e2e=batch-events] .be-earlier').click());
      await page.waitForFunction(() => !document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('batchGoal').loadingEarlier);
      const seqs = await store("p._s.get('batchGoal').historyPage.map(e=>e.seq)");
      if (seqs.length) cursors.push(Math.min(...seqs));
    }
    assert.ok(final.goal.last_seq > 500); assert.equal(cursors.at(-1), 1);
    assert.ok(cursors.every((c, i) => !i || c < cursors[i-1]));
    evidence.final = final; evidence.report = report; evidence.physicalSubmissions = submissions;
    evidence.eventRecovery = { consumed: ui.consumed, watermark: final.goal.last_seq, historicalCursors: cursors };
    evidence.checks.push('Import/explicit exclusion/create retry; pause/resume/cancel; overlap; refresh; offline reconnect; close/reopen; API crash/restart; report equality; >500 history to seq 1');
    assert.deepEqual(errors, []);
    fs.mkdirSync(artifacts, { recursive: true });
    await page.screenshot({ path: path.join(artifacts, 'batch-real.png'), fullPage: true });
    evidence.result = 'passed';
    console.log(`PASS real Batch browser: ${gid}; 50 physical submissions; ${final.goal.last_seq} events fully consumed; history reaches seq 1`);
  } finally {
    fs.mkdirSync(artifacts, { recursive: true });
    if (page && !page.isClosed()) await page.screenshot({ path: path.join(artifacts, 'batch-real-last.png'), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(artifacts, 'batch-real-evidence.json'), JSON.stringify({ ...evidence, ledger }, null, 2));
    if (browser) await browser.close(); await stop(); scheduler.close();
  }
})().catch(e => { console.error(e); console.error('Fixture logs:', root); process.exitCode = 1; });
