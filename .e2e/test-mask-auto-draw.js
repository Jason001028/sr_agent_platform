// Built Vue UI + ACTUAL Python diff/geometry service on synthetic preview PNGs.
// Scene/file endpoints are fixtures; no production files, mask output or SR calls.
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { launchBrowser } = require('./launchBrowser');
const root = path.resolve(__dirname, '..'), dist = path.join(root, 'frontend/dist');
const venvPython = path.join(root, '.venv/Scripts/python.exe');
const python = process.env.SR_TEST_PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python');
const artifacts = path.join(__dirname, 'artifacts');
const mime = { '.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml' };
const forbidden = [], metrics = [];
let calls = 0, legacy = false;
function analyze(body) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-c',
      'import sys,json; from backend.services.image_diff import analyze_payload; print(json.dumps(analyze_payload(json.load(sys.stdin)),allow_nan=False))'],
      { cwd:root, windowsHide:true, env:{...process.env, PYTHONUTF8:'1'} });
    let out = '', err = '';
    child.stdout.on('data', d => out += d); child.stderr.on('data', d => err += d);
    child.on('error', reject);
    child.on('close', code => code ? reject(new Error(err)) : resolve(JSON.parse(out)));
    child.stdin.end(JSON.stringify(body));
  });
}
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === '/api/diff/analyze') {
      calls++; let raw = ''; for await (const chunk of req) raw += chunk;
      const result = await analyze(JSON.parse(raw));
      if (result.autoMask) metrics.push({ elapsed_ms:result.autoMask.elapsed_ms, polygons:result.autoMask.polygons.map(p => ({
        rank:p.rank, vertices:p.vertex_count, coverage:p.coverage_ratio, sourceCoverage:p.source_polygons[0]?.coverage_ratio,
        ratio:p.area_ratio, fallback:p.fallback, needs_review:p.needs_review })) });
      if (legacy) delete result.autoMask;
      res.end(JSON.stringify(result)); return;
    }
    if (url.pathname === '/api/scenes/resolve') {
      let raw=''; for await (const chunk of req) raw+=chunk;
      const input=JSON.parse(raw);
      res.end(JSON.stringify({row:{id:input.name,name:input.name,W:256,H:256,rasterPreview:null},
        resolved:{dir:'/synthetic/scene',sr_capable:false,kind:input.name.includes('NOSR')?'nosr':'input'}})); return;
    }
    if (url.pathname === '/api/queue') { res.end('{"tasks":[]}'); return; }
    if (url.pathname.endsWith('/siblings')) {
      res.end(JSON.stringify({items:['PAN.jpg','PAN_NOSR.jpg'].map((id,i)=>({id,exists:true,
        srcRev:'fixture-v1',W:256,H:256,kind:i?'nosr':'input',tourIdentity:{sequence:'fixture',role:i?'nosr':'pan'}}))})); return;
    }
    if (url.pathname.startsWith('/api/')) {
      if (req.method !== 'GET') forbidden.push(req.method+' '+url.pathname);
      res.statusCode=404; res.end('{}'); return;
    }
    let file = path.join(dist, decodeURIComponent(url.pathname));
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  } catch (e) { res.statusCode=500; res.end(JSON.stringify({detail:e.message})); }
});
let browser;
(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  try {
    browser = await launchBrowser(); const page = await browser.newPage();
    const errors=[]; page.on('pageerror', e => errors.push(e.stack));
    await page.setViewport({width:1440,height:1000});
    await page.goto('http://127.0.0.1:'+server.address().port+'/viewer');
    await page.waitForSelector('[data-e2e=diff-open]');
    await page.evaluate(async () => {
      const pinia=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
      window.stores={v:pinia._s.get('viewer'),d:pinia._s.get('diff')};
      const canvas=document.createElement('canvas'); canvas.width=canvas.height=256;
      const ctx=canvas.getContext('2d');
      for(let x=0;x<256;x++){ctx.fillStyle=`rgb(${50+x/3},${50+x/3},${50+x/3})`;ctx.fillRect(x,0,1,256);}
      const base=await new Promise(r=>canvas.toBlob(r,'image/jpeg',1));
      ctx.fillStyle='white'; ctx.fillRect(30,35,65,55); ctx.fillRect(105,35,60,55); // 10px gap
      ctx.beginPath();ctx.arc(165,165,35,0,2*Math.PI);ctx.fill();
      const changed=await new Promise(r=>canvas.toBlob(r,'image/jpeg',1));
      stores.v.addFiles([new File([base],'PAN.jpg'),new File([changed],'PAN_NOSR.jpg')]);
    });
    await page.waitForFunction(() => stores.v.recs.length===2 && stores.v.recs.every(r=>r.src&&r.thumb&&r.route==='jpg'));
    await page.evaluate(() => {
      const {v,d}=stores;
      v.recs.sort((a,b)=>Number(a.name.includes('NOSR'))-Number(b.name.includes('NOSR')));
      v.recs.forEach((r,i)=>{r.srcRev='fixture-v1';r.sceneDir='/synthetic/scene';r.stageKind=i?'nosr':'input';
        // Normal open/drop paths have confirmed kind/revision but no tour certificate.
        delete r.tourIdentity;});
      d.aId=v.recs[0].id;d.bId=v.recs[1].id;d.advanced.blur=false;d.advanced.openKernel=1;d.advanced.closeKernel=1;
    });
    assert.equal(await page.evaluate(()=>stores.d.analyze()),true);
    assert.equal(await page.$eval('[data-e2e=auto-mask-draw]',e=>e.disabled),false);
    assert.equal(await page.$eval('[data-e2e=auto-mask-draw]',e=>Math.round(e.getBoundingClientRect().height)),28);
    await page.evaluate(async()=>{
      const {v,d}=stores; await v.activate(v.recs[0].id);
      v.addMaskPolygon(v.activeId,[[5,5],[15,5],[15,15]]);
      window.manual=JSON.stringify(v.activeRec.maskRois);d.resultVisible=false;d.display='regions';
      window.renderBefore=v.renderTick;window.panId=v.activeId;
    });
    await page.click('[data-e2e=auto-mask-draw]');
    await page.waitForFunction(()=>!!stores.d.autoDrawing || !!stores.d.error);
    assert.equal(await page.evaluate(()=>stores.d.error),'', JSON.stringify(await page.evaluate(()=>({
      error:stores.d.error, reason:stores.d.autoDrawReason, recs:stores.v.recs.map(r=>({id:r.sceneId,rev:r.srcRev,W:r.W,H:r.H,kind:r.stageKind,identity:r.tourIdentity}))
    }))));
    await page.waitForSelector('.auto-mask-pane polygon');
    assert.equal(await page.evaluate(()=>stores.v.activeRec.maskRois.length),1,'no partial writes');
    // Initial camera focus legitimately repaints the canvas. Measure SVG-only
    // drawing after the short camera transition has settled.
    await page.evaluate(async()=>{await new Promise(r=>setTimeout(r,300));window.renderBefore=stores.v.renderTick;});
    await page.waitForFunction(()=>{
      const p=document.querySelector('.auto-mask-pane polygon');
      return p && parseFloat(getComputedStyle(p).strokeDashoffset)===0;
    });
    await fs.promises.mkdir(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,'mask-auto-draw-animation.png')});
    // CSS SVG animation must not repaint the TIFF canvas once per frame.
    assert.ok(await page.evaluate(()=>stores.v.renderTick-window.renderBefore)<4);
    await page.waitForFunction(()=>!stores.d.autoDrawing);
    const count=await page.evaluate(()=>stores.v.activeRec.maskRois.length);
    assert.equal(count,4,'three independent regions + existing hand mask');
    await page.screenshot({path:path.join(artifacts,'mask-auto-draw-complete.png')});
    await page.evaluate(()=>stores.v.undoRoi());
    assert.equal(await page.evaluate(()=>JSON.stringify(stores.v.activeRec.maskRois)),await page.evaluate(()=>manual));
    await page.evaluate(()=>stores.v.redoRoi());
    assert.equal(await page.evaluate(()=>stores.v.activeRec.maskRois.length),count);
    await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
    assert.equal(await page.evaluate(()=>stores.d.autoDraw()),true);
    assert.equal(await page.evaluate(()=>stores.v.activeRec.maskRois.length),count,'repeat replaces batch');
    assert.equal(await page.evaluate(()=>stores.d.autoDrawing),null);
    await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'no-preference'}]);
    await page.evaluate(()=>{window.pendingDraw=stores.d.autoDraw();});
    await page.waitForSelector('.auto-mask-pane polygon');
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>pendingDraw),false);
    assert.equal(await page.evaluate(()=>stores.v.activeRec.maskRois.length),count);
    await page.evaluate(()=>{window.pendingDraw=stores.d.autoDraw();});
    await page.evaluate(async()=>{await stores.v.activate(stores.v.recs[1].id);});
    assert.equal(await page.evaluate(()=>pendingDraw),false);
    assert.equal(await page.evaluate(()=>stores.v.recs[0].maskRois.length),count);
    assert.equal(await page.evaluate(()=>stores.v.recs[1].maskRois?.length||0),0);
    await page.evaluate(()=>{stores.v.recs[0].srcRev='changed';});
    assert.equal(await page.$eval('[data-e2e=auto-mask-draw]',e=>e.disabled),true);
    await page.evaluate(()=>{stores.v.recs[0].srcRev='fixture-v1';stores.d.clearResults();});
    assert.equal(await page.evaluate(()=>stores.v.recs[0].maskRois.length),count,'clear analysis preserves mask');
    legacy=true;
    assert.equal(await page.evaluate(()=>stores.d.analyze()),true);
    assert.equal(await page.$('[data-e2e=auto-mask-draw]'),null,'legacy API hides entry');
    assert.deepEqual(errors,[]);assert.deepEqual(forbidden,[]);
    const report={analysisCalls:calls,polygons:count-1,metrics,errors,forbidden};
    await fs.promises.writeFile(path.join(artifacts,'mask-auto-draw-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally { if(browser) await browser.close(); server.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
