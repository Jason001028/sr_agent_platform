// Quiet Mission Control 浏览器回归：合成数据，不运行 SR / GPU。
// ------------------------------------------------------------------
// 跑 frontend/dist + 静态假服务，验证：
//   A. 工具栏 / Agent 双 Tab；URL 指定批次可直接关联
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
let reportAvailable = true;
let noGoals = false;
let cancelCalls = 0;
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

function runningSnapshot() {
  return {
    contract_version: 'batch_snapshot.v1',
    goal: {
      goal_id: GOAL, session_id: null, title: '50 景修复批次（Fake SR）',
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
    report: { available: reportAvailable, updated_at: 1760000500 },
    server_time: 1760000500,
  };
}

function snapshot(id = GOAL) {
  const s = runningSnapshot();
  if (id === GOAL) return s;
  s.goal.goal_id = id; s.goal.version = 20;
  s.producer.current = null; s.producer.detecting_count = 0; s.producer.preparing_count = 0;
  s.active_jobs = []; s.recent_jobs = [];
  s.counts = Object.fromEntries(Object.keys(s.counts).map(k => [k, 0]));
  s.counts.total = s.counts.eligible = 27;
  s.goal.status = id === 'g27' ? 'BLOCKED' : 'COMPLETED';
  s.goal.phase = id === 'g27' ? 'blocked' : 'done';
  s.goal.title = id === 'g27' ? '27 景预检异常' : '27 景已完成';
  s.goal.monitoring_done = true; s.goal.last_seq = 0;
  s.progress = { detection: {done:0,total:27,known:true,applicable:true}, submission: {done:0,total:0,known:true,applicable:false}, verification: {done:0,total:0,known:true,applicable:false} };
  if (id === 'g27') {
    s.counts.failed = 27;
    s.recent_jobs = Array.from({length:27},(_,i)=>job(i+1,'FAILED',{job_id:null,sr_status:null,error:'pair_invalid: PAN/NOSR 配对核验失败 · 原始记录 '+(i+1)}));
  } else {
    Object.assign(s.counts,{detected:27,submitted:27,verified:27,verified_demo:27,converged:27});
    for (const t of Object.values(s.progress)) Object.assign(t,{done:27,total:27,known:true,applicable:true});
  }
  return s;
}
function records(id) {
  const snap = snapshot(id), rows = new Map();
  for (const row of [...snap.recent_jobs,...snap.active_jobs,snap.producer.current].filter(Boolean)) rows.set(row.item_id,row);
  return [...rows.values()].sort((a,b)=>a.ordinal-b.ordinal).map(row=>({ ...row, version:row.item_version, external_job_id:row.job_id,
    checkpoint:{target_image:{path:row.image_path},attempt:row.attempt}, error:row.error }));
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
        assert.equal(input.strategy, 'automatic');
        assert.equal(input.mode, 'production');
        assert.ok(input.sources.every(row => row.path.endsWith('/PAN.tif')));
        sendJson(res, { manifest_ref: 'frozen-mock', rejected: false,
          counts: { items: input.sources.length, errors: 0, warnings: 0 }, diagnostics: [],
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
    const goalId = u.split('/')[3];
    if (/^\/api\/goals\/(g50|g27|gdone)\/items$/.test(u)) {
      const statuses = (url.searchParams.get('status') || '').split(',').filter(Boolean);
      const after = Number(url.searchParams.get('after_ordinal') || 0);
      const items = records(goalId).filter(row=>row.ordinal>after && (!statuses.length || statuses.includes(row.status))).slice(0,20);
      sendJson(res,{items,next_after_ordinal:items.at(-1)?.ordinal || after}); return;
    }
    if (u === '/api/goals' && url.searchParams.get('kind') === 'batch') {
      sendJson(res, noGoals ? [] : [GOAL,'g27','gdone'].map(id=>({ ...snapshot(id).goal,kind:'batch' }))); return;
    }
    if (/^\/api\/goals\/(g50|g27|gdone)\/batch-snapshot$/.test(u)) {
      sendJson(res, snapshot(goalId));
      return;
    }
    if (/^\/api\/goals\/(g50|g27|gdone)\/events$/.test(u)) {
      if (goalId !== GOAL) { sendJson(res,{goal_id:goalId,events:[],last_seq:0,has_more:false,has_earlier:false}); return; }
      sendJson(res, eventsPage());
      return;
    }
    if (/^\/api\/goals\/(g50|g27|gdone)\/events\/stream$/.test(u)) {
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
    if (/^\/api\/goals\/(g50|g27|gdone)\/batch-report$/.test(u)) {
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
    if (u === `/api/goals/${GOAL}/resume` || u === `/api/goals/${GOAL}/cancel`) {
      revision++; status = u.endsWith('/cancel') ? 'CANCELLED' : 'RUNNING';
      if (status === 'CANCELLED') cancelCalls++;
      sendJson(res,{goal:{goal_id:GOAL,status,version:19},changed:true,control_revision:revision,monitoring_done:status==='CANCELLED'}); return;
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

    const stores = () => document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;
    async function openMenu() { if (await page.$eval('[data-e2e=batch-settings]',e=>e.getAttribute('aria-expanded')) !== 'true') await page.click('[data-e2e=batch-settings]'); }
    async function closeMenu() { if (await page.$eval('[data-e2e=batch-settings]',e=>e.getAttribute('aria-expanded')) === 'true') await page.click('[data-e2e=batch-settings]'); }
    async function selectGoal(id) {
      await openMenu(); await page.select('.bp-select',id);
      await page.waitForFunction(id=>document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('batchGoal').snapshot?.goal.goal_id===id,{},id);
      await closeMenu();
    }
    // Empty/selection: no dormant progress/current/queue components, no auto submit.
    await page.click('.ctx-tab'); await page.click('.qc-smart');
    await page.waitForSelector('[data-e2e=batch-draft]',{visible:true});
    assert.equal(await page.$('[data-e2e=batch-tracks]'),null);
    assert.equal(await page.$('[data-e2e=mission-queue]'),null);
    assert.equal(createCalls,0);
    assert.equal(await page.$$eval('.ctx-tabs .ctx-tab',es=>es.length),2);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('batch-task-open')));
    await page.waitForSelector('[data-e2e=batch-status]');
    // One meaningful hero + independently denominated stage nodes, one current row.
    const overview = await page.$eval('[data-e2e=batch-status]',e=>e.textContent);
    assert.match(overview,/检测进度/); assert.match(overview,/40 \/ 50/); assert.match(overview,/DEMO \/ MOCK/);
    const stages=await page.$$eval('[data-stage]',es=>es.map(e=>e.textContent));
    assert.match(stages[0],/检测.*40 \/ 50/); assert.match(stages[1],/SR 提交.*30 \/ 40/); assert.match(stages[2],/验收.*10 \/ 30/);
    assert.equal(await page.$$eval('.mission-item',es=>es.length),1);
    assert.equal(await page.$('[data-e2e=mission-statistics]'),null);
    assert.equal(await page.$('[data-e2e=batch-events]'),null);
    assert.equal(await page.$('[data-e2e=batch-items]'),null);
    assert.doesNotMatch(await page.$eval('.mission-body',e=>e.textContent),/后台 SR 队列|完整执行队列|D\/A|S\/R|V\/R/);
    await page.screenshot({path:path.join(__dirname,'artifacts/batch-mission-running.png')});
    // Pause/409 and resume live in a single settings menu, without auto retries.
    await openMenu(); await page.click('[data-e2e=batch-pause]');
    await page.waitForSelector('.bc-error'); assert.match(await page.$eval('.bc-error',e=>e.textContent),/控制版本已变化/);
    assert.equal(pauseCalls,1); await page.click('[data-e2e=batch-pause]');
    await page.waitForSelector('[data-e2e=batch-resume]'); assert.equal(pauseCalls,2);
    await page.click('[data-e2e=batch-resume]'); await page.waitForSelector('[data-e2e=batch-pause]');
    // View settings never create or cancel work. Follow keeps its existing user-intent exit.
    await page.click('[data-e2e=mission-statistics-toggle]');
    await page.waitForSelector('[data-e2e=mission-statistics]'); assert.equal(createCalls,0);assert.equal(cancelCalls,0);
    assert.equal(await page.$eval('[data-e2e=batch-follow]',e=>e.checked),false);
    await page.click('[data-e2e=batch-follow]');
    await page.evaluate(()=>document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').noteUserIntent());
    await page.waitForFunction(()=>!document.querySelector('[data-e2e=batch-follow]').checked);
    await page.click('[data-e2e=mission-statistics-toggle]'); await closeMenu();
    // Collapse is one short header; keep the live association and reopen without reset.
    await page.click('[data-e2e=batch-task-expand]');
    assert.ok(await page.$eval('[data-e2e=batch-task-dock]',e=>e.getBoundingClientRect().height)<80);
    await page.click('[data-e2e=batch-task-expand]');
    // All pre-job failures: never fabricate SR state or job IDs; only first three by default.
    await selectGoal('g27');
    assert.match(await page.$eval('.hero-number',e=>e.textContent),/27.*景异常/);
    assert.match(await page.$eval('.mission-notice',e=>e.textContent),/尚无已提交的 SR 作业/);
    assert.equal(await page.$$eval('.mission-item',es=>es.length),3);
    assert.equal(await page.$$eval('[role=progressbar]',es=>es.length),1);
    assert.doesNotMatch(await page.$eval('.mission-body',e=>e.textContent),/SR 执行失败|Job 0|undefined/);
    for (const n of [0,1]) await (await page.$$('.item-content'))[n].click();
    assert.match(await page.$eval('[data-item=item-1] .item-error',e=>e.textContent),/原始记录 1/);
    assert.match(await page.$eval('[data-item=item-2] .item-error',e=>e.textContent),/原始记录 2/);
    await page.evaluate(()=>{const b=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('batchGoal'); window.savedOpen=b.openItem; window.located=[]; b.openItem=async row=>{located.push(row.item_id);return '';};});
    await (await page.$$('.locate'))[0].click(); await (await page.$$('.locate'))[1].click();
    assert.deepEqual(await page.evaluate(()=>located),['item-1','item-2']);
    await page.evaluate(()=>{document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('batchGoal').openItem=savedOpen;});
    for (const n of [0,1]) await (await page.$$('.item-content'))[n].click();
    await page.screenshot({path:path.join(__dirname,'artifacts/batch-mission-failed.png')});
    await openMenu();
    await page.evaluate(()=>[...document.querySelectorAll('.mission-menu .menu-link')].find(e=>e.textContent.includes('交给 Agent')).click());
    await page.waitForFunction(()=>document.querySelector('.ctx-tab:nth-child(2)').classList.contains('on'));
    assert.match(await page.$eval('.at-input',e=>e.value),/g27/);
    await page.click('.ctx-tab:first-child');
    // One queue: the expanded list replaces compact focus, pages all 27 failures.
    await page.click('.mission-queue header .link'); await page.waitForSelector('[data-e2e=batch-items]');
    await page.waitForFunction(()=>document.querySelectorAll('.mission-item').length===20);
    assert.equal(await page.$('[data-e2e=batch-jobs]'),null);
    await page.click('[data-e2e=batch-items] footer button:last-child');
    await page.waitForFunction(()=>document.querySelectorAll('.mission-item').length===7);
    await page.click('.mission-queue header .link');
    // Correct empty tracks and no stale focus after completion.
    await selectGoal('gdone');
    assert.match(await page.$eval('[data-e2e=batch-status]',e=>e.textContent),/已验收/);
    assert.equal(await page.$('[data-e2e=batch-tracks]'),null); assert.equal(await page.$('[data-e2e=mission-queue]'),null);
    await page.click('[data-e2e=batch-report]'); await page.waitForSelector('.notice-modal');
    assert.match(await page.$eval('.notice-modal .nm-body',e=>e.textContent),/27/);
    await page.click('.notice-modal .nm-ok');
    await page.screenshot({path:path.join(__dirname,'artifacts/batch-mission-completed.png')});
    // Debug history is discoverable only in the established developer mode.
    await selectGoal(GOAL);
    await page.evaluate(()=>localStorage.setItem('sr.developerMode.local','1'));
    await openMenu(); await page.click('[data-e2e=mission-events-toggle]'); await closeMenu();
    await page.waitForSelector('[data-e2e=batch-events]');
    await page.click('[data-e2e=batch-events] > summary');
    await page.waitForFunction(()=>document.querySelectorAll('.be-row').length===10);
    await page.click('[data-e2e=batch-events] .be-raw summary');
    assert.match(await page.$eval('[data-e2e=batch-events] .be-raw pre',e=>e.textContent),/pause/);
    await openMenu(); await page.click('[data-e2e=mission-events-toggle]'); await closeMenu();
    // Offline/online recovery keeps the same compact item and no duplicate rows.
    await page.setOfflineMode(true); await page.waitForFunction(()=>document.querySelector('.connection-note')?.textContent.includes('连接中断'));
    await page.setOfflineMode(false); await page.waitForFunction(()=>!document.querySelector('.connection-note'));
    assert.equal(await page.$$eval('.mission-item',es=>es.length),1);
    // Responsive: one scroll owner; reduced-motion disables transitions.
    await page.setViewport({width:1100,height:820});
    for (const width of [328,400]) {
      await page.$eval('.ctx-rail',(e,width)=>e.style.width=width+'px',width);
      const overflow=await page.$eval('[data-e2e=batch-task-dock]',e=>e.scrollWidth>e.clientWidth);
      assert.equal(overflow,false);
      const nested=await page.$$eval('.mission-body *',es=>es.filter(e=>e.clientHeight>0&&e.scrollHeight>e.clientHeight+1&&['auto','scroll'].includes(getComputedStyle(e).overflowY)).length);
      assert.equal(nested,0);
      await page.screenshot({path:path.join(__dirname,`artifacts/batch-mission-${width}.png`)});
    }
    assert.equal(await page.$eval('.phase-node',e=>getComputedStyle(e).transitionDuration),'0s');
    // Start directly from numbered Viewer groups; local HTTP without randomUUID.
    await page.evaluate(()=>{
      const v=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
      for(const [name,kind] of [['scene-a','nosr'],['scene-a','product'],['scene-b','nosr']]) v.insertSceneCard({name:name+'-'+kind,W:128,H:128,sceneId:name+kind,sceneDir:'/mock/'+name,lqPath:null,stageKind:kind},{id:name+kind,name,W:128,H:128,size_bytes:0,lq_path:null,hasPreview:false});
    });
    await page.click('.qc-smart'); await page.waitForFunction(()=>document.querySelector('[data-e2e=batch-draft] .primary')?.textContent.includes('已选 2 组'));
    assert.equal(createCalls,0);
    await page.click('.selection button:last-child');assert.equal(await page.$eval('.primary',e=>e.disabled),true);
    await page.click('.selection button:first-of-type');
    await page.screenshot({path:path.join(__dirname,'artifacts/batch-mission-draft.png')});
    await page.evaluate(()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}));
    await page.click('[data-e2e=batch-draft] .primary');await page.waitForSelector('[data-e2e=batch-status]');assert.equal(createCalls,1);
    await openMenu();await page.click('[data-e2e=batch-cancel]');assert.equal(cancelCalls,0);
    await page.click('[data-e2e=batch-cancel-confirm]');await page.waitForFunction(()=>document.querySelector('[data-e2e=batch-status]')?.textContent.includes('已取消'));assert.equal(cancelCalls,1);
    assert.deepEqual(errors, []);
    console.log('Quiet Mission browser checks passed: two tabs, fixed summary, numbered sequence selection and single-click frozen submission, refresh recovery, 50-scene snapshot tracks/current/queue, collapsed event timeline with raw payload, control 409 then success notice, controlled report modal, follow exits on user intent');
  } finally {
    for (const res of openStreams) {
      try { res.destroy(); } catch { /* already closed */ }
    }
    if (browser) await browser.close();
    server.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
