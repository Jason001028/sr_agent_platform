// Batch Progress Panel 浏览器验收（PR F）
// ------------------------------------------------------------------
// 跑 frontend/dist + 静态假服务，验证：
//   A. 只有存在可关联批次时才出现「批次」Tab；URL 指定批次可直接关联
//   B. 50 景快照：三维进度、当前检测（含在途数量）、后台 SR 队列都来自快照
//   C. 事件时间线默认折叠，展开后按 seq 最新在前，原始 payload 按需展开
//   D. 控制带 revision：409 时提示并重读快照，重试成功给出外部作业不终止的说明
//   E. 报告走受控入口，Modal 显示摘要与可展开的原始 JSON
//   F. 跟随默认关闭；用户视图操作立即退出跟随
// 用法: node test-batch-panel.js   （先 cd frontend && npm run build）
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');

const DIST = path.resolve(__dirname, '../frontend/dist');
const GOAL = 'g50';
const TOTAL = 50;

let revision = 2;
let status = 'RUNNING';
let pauseCalls = 0;
let createCalls = 0;
const openStreams = new Set();

function counts() {
  return {
    total: TOTAL, eligible: TOTAL, detected: 40, detection_pending: 10,
    needs_repair: 40, submitted: 30, verified: 10,
    verified_demo: 10, verified_production: 0, verified_other: 0,
    converged: 0, no_issue: 0, failed: 2, blocked: 1, excluded: 0,
    skipped_runtime: 0, cancelled: 0, capacity_skipped: 0,
    unknown_jobs: 1, detecting: 3, submitting: 1, waiting_sr: 5,
    verifying: 1, in_flight_sr: 6,
  };
}

function job(ordinal, jobStatus, extra = {}) {
  return {
    item_id: `item-${ordinal}`, ordinal, name: `scene-${ordinal}`,
    status: jobStatus, item_version: 1, attempt: 0,
    job_id: 9000 + ordinal, sr_status: jobStatus === 'WAITING_SR' ? 'RUNNING' : null,
    task_status: jobStatus === 'WAITING_SR' ? 'RUNNING' : null,
    unknown: false,
    submitted_at: 1760000000 + ordinal, updated_at: 1760000100 + ordinal,
    error: null, image_path: `/disk-array/2026/01/01/${ordinal}/PAN.tif`,
    has_mask: true, overlay_ready: true, verification_ok: null,
    ...extra,
  };
}

function jobs() {
  const rows = [
    job(41, 'WAITING_SR', { sr_status: 'UNKNOWN', unknown: true }),
    job(42, 'FAILED', { sr_status: 'FAILED', error: 'sr_verify: quality gate exited 3' }),
    job(43, 'BLOCKED', { error: '外部调度器拒绝提交：队列已满' }),
    job(44, 'VERIFYING'),
    job(45, 'WAITING_SR'),
    job(46, 'WAITING_SR'),
  ];
  for (let ordinal = 1; ordinal <= 24; ordinal += 1) {
    rows.push(job(ordinal, 'SUCCEEDED', { verification_ok: true, sr_status: 'COMPLETED' }));
  }
  return rows;
}

function snapshot() {
  return {
    contract_version: 'batch_snapshot.v1',
    goal: {
      goal_id: GOAL, session_id: 'session-1', title: '50 景修复批次（Fake SR）',
      kind: 'batch', status, phase: status === 'PAUSED' ? 'paused' : 'monitoring',
      mode: 'demo', version: 17, control_revision: revision,
      monitoring_done: false, monitor_state: 'running', last_seq: 50,
      created_at: 1760000000, updated_at: 1760000500, finished_at: null,
      batch_control: { pause_requested: status === 'PAUSED' },
    },
    counts: counts(),
    progress: {
      detection: { done: 40, total: 50, applicable: true, known: true },
      submission: { done: 30, total: 40, applicable: true, known: true },
      verification: { done: 10, total: 30, applicable: true, known: true },
    },
    producer: {
      current: {
        item_id: 'item-41', ordinal: 41, name: 'scene-41', status: 'DETECTING',
        item_version: 1, attempt: 1, updated_at: 1760000500,
        image_path: '/disk-array/2026/01/01/410001/PAN.tif',
        has_mask: false, overlay_ready: false, candidate_count: 3,
        job_id: null, sr_status: null, error: null,
      },
      detecting_count: 3, preparing_count: 3, prepared_count: 12,
    },
    active_jobs: jobs().slice(0, 6),
    recent_jobs: jobs(),
    report: { available: true, updated_at: 1760000500 },
    server_time: 1760000500,
  };
}

function eventsPage() {
  const rows = [];
  for (let seq = 31; seq <= 50; seq += 1) {
    let type = 'item_state_changed';
    let payload = { from: 'WAITING_SR', to: 'VERIFYING' };
    if (seq === 50) {
      type = 'batch_user_control';
      payload = { action: 'pause', revision: 1 };
    } else if (seq === 47) {
      type = 'external_job_terminal';
      payload = { state: 'FAILED', job_id: 9042 };
    } else if (seq === 45) {
      type = 'item_verified';
      payload = { ok: false };
    }
    rows.push({
      goal_id: GOAL, seq, event_type: type, payload, created_at: 1760000000 + seq,
      step_ordinal: seq === 50 ? null : 10 + (seq % 30),
    });
  }
  return {
    goal_id: GOAL, events: rows.reverse(), last_seq: 50,
    next_after: null, has_more: false, next_before: 31, has_earlier: true,
  };
}

function sendJson(res, body, code = 200) {
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}

(async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://local');
    const u = url.pathname;

    if (u === '/api/goals/batch-imports') {
      let body = ''; req.on('data', chunk => body += chunk); req.on('end', () => {
        const input = JSON.parse(body);
        assert.equal(input.strategy, 'review');
        sendJson(res, { manifest_ref: 'frozen-mock', rejected: false,
          counts: { items: 2, errors: 0, warnings: 0 }, diagnostics: [],
          items: [1, 2].map(n => ({ item_id: 'candidate-' + n, ordinal: n, name: 'selected-scene-' + n, scene_ref: '/mock/scene-' + n, source_lines: [n] })) });
      }); return;
    }
    if (u === '/api/goals' && req.method === 'POST') {
      let body = ''; req.on('data', chunk => body += chunk); req.on('end', () => {
        const input = JSON.parse(body);
        assert.equal(input.manifest_ref, 'frozen-mock');
        assert.ok(input.request_key);
        createCalls++;
        sendJson(res, { goal_id: GOAL, kind: 'batch', status: 'QUEUED', version: 1 });
      }); return;
    }
    if (u === `/api/goals/${GOAL}/items`) { sendJson(res, { items: [], next_after_ordinal: 0 }); return; }
    if (u === '/api/goals' && url.searchParams.get('kind') === 'batch') {
      sendJson(res, [{
        goal_id: GOAL, session_id: 'session-1', status: 'RUNNING',
        title: '50 景修复批次（Fake SR）', kind: 'batch', version: 17,
      }]);
      return;
    }
    if (u === `/api/goals/${GOAL}/batch-snapshot`) {
      sendJson(res, snapshot());
      return;
    }
    if (u === `/api/goals/${GOAL}/events`) {
      sendJson(res, eventsPage());
      return;
    }
    if (u === `/api/goals/${GOAL}/events/stream`) {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      });
      res.write(': connected\n\n');
      openStreams.add(res);
      req.on('close', () => openStreams.delete(res));
      return;
    }
    if (u === `/api/goals/${GOAL}/batch-report`) {
      sendJson(res, {
        goal_id: GOAL, title: '50 景修复批次（Fake SR）',
        summary: { total: TOTAL, verified: 10, failed: 2, blocked: 1 },
        generated_at: '2026-11-03T10:00:00+08:00',
      });
      return;
    }
    if (u === `/api/goals/${GOAL}/pause`) {
      pauseCalls += 1;
      if (pauseCalls === 1) {
        sendJson(res, { detail: {
          message: 'control revision changed', reason: 'stale_revision',
          control_revision: revision,
        } }, 409);
        return;
      }
      revision += 1;
      status = 'PAUSED';
      sendJson(res, {
        goal: { goal_id: GOAL, status, version: 18 },
        changed: true, control_revision: revision, monitoring_done: false,
      });
      return;
    }
    if (u.startsWith('/api/')) {
      sendJson(res, {}, 404);
      return;
    }

    let f = path.join(DIST, u === '/' ? 'index.html' : u);
    if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(DIST, 'index.html');
    const mime = {
      '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html',
      '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2',
    }[path.extname(f)];
    res.setHeader('Content-Type', mime || 'application/octet-stream');
    res.end(fs.readFileSync(f));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));

  if (process.env.BATCH_MOCK_SERVER_ONLY) { console.log('MOCK_URL=http://127.0.0.1:' + server.address().port + '/viewer?batch_goal=' + GOAL); return; }
  let browser;
  try {
    browser = await launchBrowser();
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.setViewport({ width: 1440, height: 980, deviceScaleFactor: 1 });
    await page.goto(`http://127.0.0.1:${server.address().port}/viewer?batch_goal=${GOAL}`);
    await page.waitForSelector('.stage');

    // A. URL 指定批次 → 快照加载后出现「批次」Tab。
    await page.waitForSelector('[data-e2e=batch-task-dock]');
    assert.equal(await page.$$eval('.ctx-tabs .ctx-tab', es => es.length), 2);
    await page.click('.ctx-tab');
    await page.click('[data-e2e=batch-task-expand]');
    await page.waitForSelector('[data-e2e=batch-panel]');
    await page.waitForSelector('[data-e2e=batch-overview]');
    const summaryTop = await page.$eval('[data-e2e=batch-task-dock]', e => e.getBoundingClientRect().top);
    await page.$eval('.tools-scroll', e => { e.scrollTop = 300; });
    assert.equal(await page.$eval('[data-e2e=batch-task-dock]', e => e.getBoundingClientRect().top), summaryTop);
    await page.$eval('.tools-scroll', e => { e.scrollTop = 0; });

    // B. 50 景快照的三个事实来源都在首屏。
    const overview = await page.$eval('[data-e2e=batch-overview]', (e) => e.textContent);
    assert.match(overview, /50 景修复批次/);
    assert.match(overview, /DEMO \/ MOCK/);
    assert.match(overview, /总景数\s*50/);

    const tracks = await page.$eval('[data-e2e=batch-tracks]', (e) => e.textContent);
    assert.match(tracks, /检测[\s\S]*40 \/ 50/);
    assert.match(tracks, /提交 SR[\s\S]*30 \/ 40/);
    assert.match(tracks, /验证[\s\S]*10 \/ 30/);

    const current = await page.$eval('[data-e2e=batch-current]', (e) => e.textContent);
    assert.match(current, /#41 scene-41/);
    assert.match(current, /检测中/);
    assert.match(current, /另有 2 景同时检测中（在途共 3 景）/);

    const queue = await page.$eval('[data-e2e=batch-jobs]', (e) => e.textContent);
    assert.match(queue, /在途 6/);
    assert.match(queue, /3 异常/);
    const allRows = await page.$$eval('[data-e2e=batch-jobs] .bj-row', (a) => a.length);
    assert.ok(allRows >= 25, `队列应展示最近 Job 尾部，实际 ${allRows}`);
    assert.match(await page.$eval('[data-e2e=batch-jobs] .bj-error', (e) => e.textContent),
      /quality gate exited 3/);
    await page.click('[data-e2e=batch-only-issues]');
    await page.waitForFunction(
      "document.querySelectorAll('[data-e2e=batch-jobs] .bj-row').length === 3");
    await page.click('[data-e2e=batch-only-issues]');

    // C. 事件默认折叠；展开后最新在前（seq 50 的用户控制）。
    const eventsOpen = await page.$eval('[data-e2e=batch-events]', (e) => e.open);
    assert.equal(eventsOpen, false, '事件时间线默认必须折叠');
    const summary = await page.$eval('[data-e2e=batch-events] summary', (e) => e.textContent);
    assert.match(summary, /用户暂停批次/);
    await page.click('[data-e2e=batch-events] > summary');
    await page.waitForFunction(
      "document.querySelectorAll('[data-e2e=batch-events] .be-row').length === 10");
    const first = await page.$eval('[data-e2e=batch-events] .be-row', (e) => e.textContent);
    assert.match(first, /用户暂停批次/);
    await page.click('[data-e2e=batch-events] .be-row .be-raw summary');
    const raw = await page.$eval('[data-e2e=batch-events] .be-row .be-raw pre',
      (e) => e.textContent);
    assert.match(raw, /"action": "pause"/);

    // D. 409 → 重新读快照并提示；重试成功说明外部作业不会被终止。
    await page.click('[data-e2e=batch-pause]');
    await page.waitForSelector('.bc-error');
    assert.match(await page.$eval('.bc-error', (e) => e.textContent), /控制版本已变化/);
    await page.click('[data-e2e=batch-pause]');
    await page.waitForFunction(
      "document.querySelector('[data-e2e=batch-controls] .bc-notice')?.textContent.includes('外部调度器')");
    assert.equal(pauseCalls, 2);

    // E. 报告：受控入口 + 摘要 + 按需展开的原始 JSON。
    await page.click('[data-e2e=batch-report]');
    await page.waitForSelector('.notice-modal .nm-title');
    assert.match(await page.$eval('.notice-modal .nm-title', (e) => e.textContent), /批次报告/);
    assert.match(await page.$eval('.notice-modal .nm-body', (e) => e.textContent), /检测 40 \/ 50/);
    await page.click('.notice-modal .nm-raw summary');
    const reportRaw = await page.$eval('.notice-modal .nm-raw pre', (e) => e.textContent);
    assert.match(reportRaw, /"summary"/);
    await page.click('.notice-modal .nm-ok');

    // F. 跟随默认关闭；用户视图操作立即退出。
    const followBefore = await page.$eval('[data-e2e=batch-follow]', (e) => e.checked);
    assert.equal(followBefore, false);
    await page.click('[data-e2e=batch-follow]');
    await page.waitForFunction(
      "document.querySelector('[data-e2e=batch-follow]').checked === true");
    await page.evaluate(() => {
      const pinia = document.querySelector('#app').__vue_app__
        .config.globalProperties.$pinia;
      pinia._s.get('viewer').noteUserIntent();
    });
    await page.waitForFunction(
      "document.querySelector('[data-e2e=batch-follow]').checked === false");
    assert.match(await page.$eval('[data-e2e=batch-panel]', (e) => e.textContent), /退出跟随/);

    // G. Scope freezing and explicit review authorization precede Goal creation.
    await page.click('.task-detail nav button');
    await page.type('[data-e2e=batch-draft] textarea', 'explicit fake QC selection');
    await page.select('[data-e2e=batch-draft] label select', 'review');
    await page.evaluate(() => [...document.querySelectorAll('[data-e2e=batch-draft] button')].find(b => b.textContent.includes('核验并冻结')).click());
    await page.waitForSelector('.frozen');
    assert.equal(createCalls, 0);
    const createDisabled = await page.$eval('.frozen button', e => e.disabled);
    assert.equal(createDisabled, true);
    await page.click('.frozen input[type=checkbox]');
    await page.click('.frozen button');
    await page.waitForFunction(() => document.querySelector('.task-detail nav button:nth-child(2)').classList.contains('on'));
    assert.equal(createCalls, 1);
    // Explicitly switching tabs preserves store association and snapshot.
    await page.click('.ctx-tab:nth-child(2)');
    await page.click('.ctx-tab:first-child');
    assert.match(await page.$eval('[data-e2e=batch-task-dock]', e => e.textContent), /50 景/);
    await page.reload();
    await page.waitForSelector('[data-e2e=batch-task-expand]');
    assert.equal(await page.$eval('[data-e2e=batch-task-expand]', e => e.getAttribute('aria-expanded')), 'false');
    assert.match(await page.$eval('[data-e2e=batch-task-dock]', e => e.textContent), /40 \/ 50/);
    assert.equal(await page.$$eval('.ctx-tabs .ctx-tab', es => es.length), 2);
    assert.deepEqual(errors, []);
    console.log('Batch Panel browser checks passed: two tabs, fixed summary, frozen draft authorization, refresh recovery, 50-scene snapshot tracks/current/queue, collapsed event timeline with raw payload, control 409 then success notice, controlled report modal, follow exits on user intent');
  } finally {
    for (const res of openStreams) {
      try { res.destroy(); } catch { /* already closed */ }
    }
    if (browser) await browser.close();
    server.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
