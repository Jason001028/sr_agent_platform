// Real Goal API + SQLite + Worker; Mock model and Fake Executor, no GPU.
// Run after frontend build: node .e2e/test-goal-clarification.js
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert/strict');
const { launchPage } = require('./launchBrowser');
const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(ROOT, 'frontend/dist');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function waitFor(fn) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) { if (await fn()) return; await delay(100); }
  throw new Error('Timed out waiting for server state');
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sr-clarification-'));
  const reserve = http.createServer();
  await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
  const port = reserve.address().port;
  await new Promise(resolve => reserve.close(resolve));
  const logs = [];
  const python = process.env.SR_E2E_PYTHON || path.join(ROOT, '.venv/Scripts/python.exe');
  const child = spawn(python, ['-m', 'uvicorn', 'backend.tests.goal_clarification_fixture:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: ROOT, windowsHide: true,
    env: { ...process.env, SR_AGENT_DB: path.join(tmp, 'goals.db'), SR_LLM_MOCK: '1', SR_SLURM_FAKE: '1',
      SR_GOAL_POLL_SEC: '0.1', SR_SCENES_ROOT: '', PYTHONIOENCODING: 'utf-8' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', d => logs.push(String(d)));
  child.stderr.on('data', d => logs.push(String(d)));
  const api = `http://127.0.0.1:${port}`;
  let browser;
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/api/') || req.url.startsWith('/test/')) {
      const proxy = http.request(api + req.url, { method: req.method, headers: req.headers }, upstream => {
        res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res);
      });
      proxy.on('error', e => {
        if (!res.headersSent) { res.writeHead(502); res.end(String(e)); }
        else res.destroy();
      });
      res.on('close', () => proxy.destroy());
      req.pipe(proxy); return;
    }
    let file = path.resolve(DIST, '.' + decodeURIComponent(req.url.split('?')[0]));
    if (!file.startsWith(DIST + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html');
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.html': 'text/html' };
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  async function get(url) { return (await fetch(api + url)).json(); }
  async function post(url, body) {
    const r = await fetch(api + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    assert.equal(r.ok, true); return r.json();
  }
  try {
    await waitFor(async () => { try { return (await get('/api/health')).ok; } catch { return false; } });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const goal = await post('/api/goals', { title: '浏览器选项澄清', checkpoint: { acceptance: { required_tools: ['fixture_finish'] } } });
    await waitFor(async () => (await get('/api/goals/' + goal.goal_id)).status === 'WAITING_USER');
    const launched = await launchPage(); browser = launched.browser;
    const page = launched.page;
    await page.setViewport({ width: 1280, height: 850 });
    await page.goto(origin + '/chat', { waitUntil: 'networkidle2' });
    await page.waitForSelector('.clarification-card[data-status="PENDING"]');
    await page.screenshot({ path: path.join(tmp, 'clarification-pending.png') });
    assert.match(await page.$eval('.clarification-question', el => el.textContent), /请选择/);
    const before = await get('/test/clarification-state');
    await page.reload({ waitUntil: 'networkidle2' });
    await page.waitForSelector('.clarification-card[data-status="PENDING"]');
    assert.deepEqual(await get('/test/clarification-state'), before, 'reload must not call model');
    await page.evaluate(() => {
      const button = document.querySelector('.clarification-options button');
      button.click(); button.click();
    });
    await page.waitForSelector('.clarification-card[data-status="ANSWERED"]');
    await waitFor(async () => (await get('/api/goals/' + goal.goal_id)).status === 'COMPLETED');
    assert.equal((await get('/test/clarification-state')).executions, 1);
    const first = await get(`/api/goals/${goal.goal_id}/clarifications`);
    assert.equal(first[0].answer.selected_option_id, 'north');
    assert.equal((await get(`/api/goals/${goal.goal_id}/events`)).events.filter(e => e.event_type === 'clarification_answered').length, 1);
    await page.reload({ waitUntil: 'networkidle2' });
    await page.waitForSelector('.clarification-card[data-status="ANSWERED"]');
    assert.equal(await page.$('.clarification-options button'), null);
    console.log('PASS option -> reload while waiting -> double submit -> same Goal COMPLETED -> reload answered');

    // New session isolates the free-text case from the first task's context.
    const second = await post('/api/goals', { title: '浏览器自由文本澄清', checkpoint: { acceptance: { required_tools: ['fixture_finish'] } } });
    await waitFor(async () => (await get('/api/goals/' + second.goal_id)).status === 'WAITING_USER');
    await page.goto(origin + '/chat', { waitUntil: 'networkidle2' });
    await page.waitForSelector('.clarification-card[data-status="PENDING"]');
    await page.type('.clarification-form textarea', '只处理东北角的小区域');
    await page.click('.clarification-form button');
    await page.waitForSelector('.clarification-card[data-status="ANSWERED"]');
    await waitFor(async () => (await get('/api/goals/' + second.goal_id)).status === 'COMPLETED');
    await page.waitForFunction(() => document.body.textContent.includes('已按补充信息完成任务'));
    assert.equal((await get('/test/clarification-state')).executions, 2);
    assert.equal((await get(`/api/goals/${second.goal_id}/clarifications`))[0].answer.answer_text, '只处理东北角的小区域');
    await page.screenshot({ path: path.join(tmp, 'clarification-answered.png') });
    assert.equal(launched.errors.filter(e => e.startsWith('[pageerror]')).length, 0);
    console.log('PASS free text -> committed answer -> worker completes same Goal; no browser exceptions');

    const cancelled = await post('/api/goals', { title: '浏览器取消待回答问题' });
    await waitFor(async () => (await get('/api/goals/' + cancelled.goal_id)).status === 'WAITING_USER');
    await page.goto(origin + '/chat', { waitUntil: 'networkidle2' });
    await page.waitForSelector('.clarification-card[data-status="PENDING"]');
    await post(`/api/goals/${cancelled.goal_id}/cancel`, {});
    await page.waitForSelector('.clarification-card[data-status="CANCELLED"]');
    assert.equal(await page.$('.clarification-options button'), null);
    console.log('PASS cancellation -> expired card without actionable buttons');

    const lost = await post('/api/goals', { title: '浏览器丢失提交响应', checkpoint: { acceptance: { required_tools: ['fixture_finish'] } } });
    await waitFor(async () => (await get('/api/goals/' + lost.goal_id)).status === 'WAITING_USER');
    await page.goto(origin + '/chat', { waitUntil: 'networkidle2' });
    await page.waitForSelector('.clarification-card[data-status="PENDING"]');
    await page.evaluate(() => {
      const original = window.fetch;
      let drop = true;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).endsWith('/respond') && drop) { drop = false; throw new Error('injected lost response after commit'); }
        return response;
      };
    });
    await page.click('.clarification-options button');
    await page.waitForSelector('.clarification-card[data-status="ANSWERED"]');
    await waitFor(async () => (await get('/api/goals/' + lost.goal_id)).status === 'COMPLETED');
    assert.equal((await get('/test/clarification-state')).executions, 3);
    assert.equal((await get(`/api/goals/${lost.goal_id}/events`)).events.filter(e => e.event_type === 'clarification_answered').length, 1);
    console.log('PASS lost response after commit -> reload server state -> exactly one answer/execution');
    console.log(JSON.stringify({ goals: [goal.goal_id, second.goal_id, cancelled.goal_id, lost.goal_id], ...(await get('/test/clarification-state')), screenshot: path.join(tmp, 'clarification-answered.png') }));
  } catch (e) {
    console.error(logs.slice(-20).join('')); throw e;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
    child.kill();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
