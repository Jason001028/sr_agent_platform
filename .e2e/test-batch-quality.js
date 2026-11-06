// Actual built UI + FastAPI + SQLite + shared geometry + MaskBuilder + repair
// contract. The CPU fixture seeds two completed synthetic SR jobs; NO real SR.
const assert=require('assert/strict'),fs=require('fs'),path=require('path'),os=require('os'),net=require('net');
const {spawn}=require('child_process');const {launchBrowser}=require('./launchBrowser');
const repo=path.resolve(__dirname,'..'),root=fs.mkdtempSync(path.join(os.tmpdir(),'sr-quality-e2e-'));
const artifacts=path.join(__dirname,'artifacts');let child,browser,logs='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function port(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
async function start(p){
  child=spawn(path.join(repo,'.venv/Scripts/python.exe'),['-m','backend.tests.quality_browser_fixture','--root',root,'--port',String(p)],
    {cwd:repo,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
  child.stdout.on('data',d=>logs+=d);child.stderr.on('data',d=>logs+=d);
  for(let i=0;i<100;i++){if(child.exitCode!=null)throw new Error(logs);try{if((await fetch(`http://127.0.0.1:${p}/api/health`)).ok)return;}catch{}await sleep(100);}
  throw new Error('fixture startup timeout: '+logs);
}
async function stop(){if(child&&child.exitCode==null){const c=child;await new Promise(r=>{c.once('exit',r);c.kill();});}child=null;}
(async()=>{
  const p=await port(),base=`http://127.0.0.1:${p}`;await start(p);
  const fixture=JSON.parse(fs.readFileSync(path.join(root,'fixtures.json'),'utf8'));
  const api=async route=>{const r=await fetch(base+route);return{status:r.status,data:await r.json()};};
  try{
    browser=await launchBrowser();const page=await browser.newPage(),errors=[],posts=[];
    page.on('pageerror',e=>errors.push(e.stack));
    page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/quality-review'))posts.push(JSON.parse(r.postData()));});
    await page.setViewport({width:1600,height:1050});
    async function open(){
      await page.goto(base+'/viewer?batch_goal='+fixture.goal_id);
      await page.waitForSelector('[data-e2e=batch-task-dock]');
      await page.evaluate(async gid=>{
        const pinia=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
        window.qstores={batch:pinia._s.get('batchGoal'),viewer:pinia._s.get('viewer'),quality:pinia._s.get('batchQuality')};
        await qstores.batch.selectGoal(gid);
        window.dispatchEvent(new CustomEvent('batch-task-open'));
      },fixture.goal_id);
      await page.waitForSelector('[data-e2e=batch-quality-panel] .quality-tasks button');
    }
    async function select(index){
      const buttons=await page.$$('[data-e2e=batch-quality-panel] .quality-tasks button');await buttons[index].click();
      await page.waitForFunction(()=>qstores.quality.comparisonReady||!!qstores.quality.error);
      assert.equal(await page.evaluate(()=>qstores.quality.error),'');
      assert.equal(await page.evaluate(()=>qstores.quality.comparisonReady),true);
    }
    async function button(text){const handle=await page.evaluateHandle(text=>[...document.querySelectorAll('[data-e2e=batch-quality-panel] button')].find(b=>b.textContent.trim()===text),text);return handle.asElement();}
    await open();await select(0);
    assert.equal(await page.evaluate(()=>qstores.quality.reviews.length),2);
    assert.equal(await page.evaluate(()=>qstores.quality.doc.regions.length),2);
    assert.equal(await page.evaluate(()=>qstores.viewer.splitSynchronized),true);
    assert.equal(await page.$$eval('[data-e2e=batch-quality-overlay] svg',e=>e.length),2);
    assert.equal(await page.evaluate(()=>qstores.quality.doc.job_id),'8101');
    const scales=()=>page.evaluate(()=>qstores.viewer.panes.map(p=>p.view.scale*p.rec.thumb.width));
    let s=await scales();assert.ok(Math.abs(s[0]-s[1])<1e-6);
    await page.evaluate(()=>qstores.viewer.onPan(13,-7));
    s=await scales();assert.ok(Math.abs(s[0]-s[1])<1e-6);
    await fs.promises.mkdir(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,'batch-quality-review.png')});
    await(await button('闪烁对比')).click();
    await page.waitForFunction(()=>qstores.viewer.compareMode==='flicker'&&qstores.viewer.flickerPlaying);
    assert.equal(await page.evaluate(()=>qstores.quality.comparisonReady),true);
    assert.equal(await page.$$eval('[data-e2e=batch-quality-overlay] svg',e=>e.length),1);
    await page.type('[aria-label=审核人]','合成测试审核人');await page.type('[aria-label=审核原因]','区域边界和纹理检查通过');
    assert.equal(await(await button('整景确认通过')).evaluate(e=>e.disabled),true);
    await(await button('本区域通过')).click();
    await page.waitForFunction(()=>!qstores.quality.busy&&qstores.quality.doc.version===1);
    const firstReview=await page.evaluate(()=>qstores.quality.doc.review_id);
    await stop();await start(p); // hard process restart; same SQLite and artifact versions
    await open();await select(0);
    assert.equal(await page.evaluate(()=>qstores.quality.doc.review_id),firstReview);
    assert.equal(await page.evaluate(()=>qstores.quality.doc.regions.filter(r=>r.decision==='approved').length),1);
    const next=await page.evaluate(()=>qstores.quality.doc.regions.find(r=>r.decision==='pending').region_id);
    await page.select('[aria-label=选择待验收区域]',next);
    await page.type('[aria-label=审核人]','合成测试审核人');
    await(await button('本区域通过')).click();
    await page.waitForFunction(()=>!qstores.quality.busy&&qstores.quality.doc.regions.every(r=>r.decision==='approved'));
    await(await button('整景确认通过')).click();
    await page.waitForFunction(()=>qstores.batch.snapshot?.counts.verified===1);
    assert.notEqual(await page.evaluate(()=>qstores.batch.snapshot.goal.status),'COMPLETED');
    assert.equal(await page.evaluate(()=>qstores.batch.snapshot.quality.waiting_scenes),1);
    await select(1);assert.equal(await page.evaluate(()=>qstores.quality.doc.job_id),'8102');
    assert.equal(await page.evaluate(()=>qstores.quality.doc.regions.filter(r=>r.decision==='approved').length),0);
    await page.type('[aria-label=审核原因]','纹理仍需人工处理，不自动重提 SR');
    await(await button('驳回')).click();await page.waitForFunction(()=>!qstores.quality.busy&&qstores.quality.doc.version===1);
    await(await button('整景驳回 · 转人工处理')).click();
    await page.waitForFunction(()=>qstores.batch.snapshot?.goal.status==='BLOCKED');
    const first=(await api(`/api/goals/${fixture.goal_id}/items/${fixture.items[0]}/quality-review`)).data;
    assert.equal(first.audit.length,3);
    const original=first.binding.output.path,tmp=original+'.replacement';fs.writeFileSync(tmp,fs.readFileSync(original));fs.renameSync(tmp,original);
    assert.equal((await api(`/api/goals/${fixture.goal_id}/items/${fixture.items[0]}/quality-review`)).status,409);
    const stale=(await api(`/api/goals/${fixture.goal_id}/quality-reviews`)).data.reviews;
    assert.equal(stale.find(r=>r.item_id===fixture.items[0]).current,false);
    assert.deepEqual(errors,[]);assert.equal(posts.length,5);
    assert.equal(new Set(posts.map(p=>p.request_key)).size,5);
    assert.ok(!logs.includes('quality test MUST NOT submit SR'));
    const report={seeded_jobs:2,new_sr_submissions:0,regions_per_scene:2,review_posts:posts.length,restart_persisted:true,
      stale_output_rejected:true,goal_status:'BLOCKED',errors,root};
    fs.writeFileSync(path.join(artifacts,'batch-quality-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  }finally{if(browser)await browser.close();await stop();fs.writeFileSync(path.join(artifacts,'batch-quality-server.log'),logs);}
})().catch(e=>{console.error(e);process.exitCode=1;});
