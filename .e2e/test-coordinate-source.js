const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
 const http = require('http');
 const dist = path.resolve(__dirname, '../frontend/dist');
 const server = http.createServer((req,res)=>{
  const pathname = new URL(req.url,'http://localhost').pathname;
  let file = path.join(dist,pathname);
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file=path.join(dist,'index.html');
  const ext=path.extname(file);res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.png':'image/png','.svg':'image/svg+xml'})[ext]||'application/octet-stream');
  res.end(fs.readFileSync(file));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base = `http://127.0.0.1:${server.address().port}`;
 const browser = await launchBrowser();
 const page = await browser.newPage();
 let mode = 'new', posts = 0, tasks = [], viewerCompletion = false;
 const errors = []; page.on('pageerror', e => errors.push(e.message));
 await page.setRequestInterception(true);
 page.on('request', async r => {
  if (!new URL(r.url()).pathname.startsWith('/api/')) return r.continue();
  if (r.method() === 'POST') {
   posts++; await sleep(500);
   if (mode === 'unknown') return r.abort('connectionfailed');
   if (mode === 'error') return r.respond({status:422, contentType:'application/json', body:JSON.stringify({detail:'模拟掩码无效'})});
   const body = JSON.parse(r.postData());
   tasks = [{task_id:42,job_id:77,state:'PENDING',fingerprint:'mock-submission',params:body,created_at:1760000000}];
   return r.respond({status:201, contentType:'application/json', body:JSON.stringify({task_id:42,job_id:77,status:mode === 'existing' ? 'RESUMED_ACTIVE' : 'SUBMITTED',state:'PENDING'})});
  }
  return r.respond({status:200,contentType:r.url().endsWith('/events')?'text/event-stream':'application/json',body:r.url().endsWith('/events') ? (viewerCompletion && tasks.length ? 'data: '+JSON.stringify({type:'job_update',task_id:42,job_id:77,state:'COMPLETED'})+'\n\n' : '') : r.url().endsWith('/queue')?JSON.stringify({tasks}):'{}'});
 });
 const out = path.resolve(__dirname,'artifacts/submission-feedback'); fs.mkdirSync(out,{recursive:true});
 async function setup(width) {
  await page.setViewport({width,height:900}); await page.goto(base+'/queue');
  await page.waitForSelector('.qp-actions').catch(async e=>{console.error('PAGE',await page.content(),errors);throw e});
  await page.evaluate(() => [...document.querySelectorAll('.qp-actions button')].find(e=>e.textContent.includes('提交 SR')).click());
  await page.type('input[list="qp-lq-cands"]','/mock/scene');
 }

 await page.setViewport({width:1366,height:900}); await page.goto(base+'/viewer');
 await page.waitForFunction(()=>!!window.__viewer);
 assert.equal(await page.$eval('.coordinate-control input',e=>e.value),'');
 const name='JL1KF02B02_PMS07_20260917122028_200538707_101_0006_001_L1';
 async function scene(dir) { await page.evaluate(async dir=>{
  const c=document.createElement('canvas');c.width=16;c.height=16;
  const blob=await new Promise(r=>c.toBlob(r));
  await window.__viewer.openSceneJpg({name:dir+'.tif',W:160,H:160,sceneId:dir,lqPath:'/mock/'+dir},blob);
 },dir); }
 await scene(name);
 await page.evaluate(name=>window.__viewer.qcImport('qc.txt', name+',\t产品存在伪影 (问题类型:产品存在伪影 行列号:12,34 影像类型:pan )\t测试'),name);
 await page.waitForFunction(()=>document.querySelector('.coordinate-control input').value==='12,34');
 assert.equal(await page.$eval('.coordinate-badge',e=>e.textContent),'已导入');
 await sleep(240);
 assert(await page.$eval('.coordinate-card',e=>{const b=e.getBoundingClientRect();return e.contains(document.elementFromPoint(b.x+b.width/2,b.bottom-3));}));
 assert.equal(await page.$('.coordinate-detail'),null);
 await page.screenshot({path:path.join(out,'coordinate-imported.png')});
 assert((await page.$eval('.coordinate-lockin img',e=>e.src)).includes('animated.svg'));
 await page.click('.coordinate-suppress input');
 assert.equal(await page.evaluate(()=>localStorage.getItem('sr.viewer.coordinateHelp.hidden')),'1');
 await sleep(850);assert((await page.$eval('.coordinate-lockin img',e=>e.src)).includes('static.svg'));

 await page.$eval('.coordinate-control input',e=>{e.value='56,78';e.dispatchEvent(new Event('input',{bubbles:true}));});
 await page.waitForFunction(()=>document.querySelector('.coordinate-badge').textContent==='手动');
 await scene('unmatched');
 await page.waitForFunction(()=>document.querySelector('.coordinate-control input').value==='');
 assert.equal(await page.$eval('.coordinate-badge',e=>e.textContent),'未提供');
 await sleep(4700); assert.equal(await page.$('.coordinate-card'),null);
 await page.screenshot({path:path.join(out,'coordinate-empty.png')});
 assert.deepEqual(errors,[]); console.log('PASS coordinate source, manual, scene reset, timeout');
 await browser.close(); server.close();
})().catch(e=>{console.error(e);process.exit(1)});

