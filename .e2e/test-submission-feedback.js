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
   return r.respond({status:201, contentType:'application/json', body:JSON.stringify({task_id:42,job_id:77,status:mode === 'existing' ? 'RESUMED_ACTIVE' : 'SUBMITTED',state:'PENDING',notice:'Backend write details. '.repeat(30)})});
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
 for (const width of [1366,1600,1920]) {
  mode='new'; await setup(width);
  const frames=[];
  await page.click('.sr-submit');
  assert.equal(await page.$eval('.sr-submit',e=>e.getAttribute('aria-busy')),'true');
  assert.equal(await page.$('.sr-receipt'),null);
  await page.click('.sr-submit');
  for(let i=0;i<17;i++){frames.push((await page.screenshot({encoding:'base64'})));await sleep(80);}
  await page.waitForSelector('.sr-receipt');
  const bounds=await page.$eval('.sr-receipt',e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,bottom:r.bottom,text:e.textContent};});
  assert(bounds.left>=0 && bounds.right<=width && bounds.bottom<=900); assert(bounds.text.includes('42'));
  await page.screenshot({path:path.join(out,`${width}.png`)});
  if(width===1366)fs.writeFileSync(path.join(out,'recording.html'),`<meta charset="utf-8"><title>真实浏览器提交反馈录屏</title><img style="width:100%"><script>const frames=${JSON.stringify(frames)};let i=0;setInterval(()=>{document.querySelector('img').src='data:image/png;base64,'+frames[i++%frames.length]},120)</script>`);
  await page.hover('.sr-receipt'); await sleep(6200); assert(await page.$('.sr-receipt'));
  await page.click('.sr-close'); await sleep(260); assert.equal(await page.$('.sr-receipt'),null);
 }
 assert.equal(posts,3);
 mode='existing'; await setup(1366); await page.click('.sr-submit'); await page.waitForSelector('.sr-receipt');
 assert((await page.$eval('.sr-receipt',e=>e.textContent)).includes('已关联现有任务'));
 assert.equal(await page.$('.sr-new-task'),null);
 assert.equal(await page.$('.sr-receipt details'),null);
 assert((await page.$eval('.sr-receipt',e=>e.textContent)).length<80);
 mode='error'; await setup(1366); await page.click('.sr-submit'); await page.waitForFunction(()=>document.querySelector('.sr-status').textContent.includes('模拟掩码无效'));
 assert.equal(await page.$eval('.sr-submit',e=>e.disabled),false); assert.equal(await page.$('.sr-receipt'),null);
 mode='unknown'; await setup(1366); await page.click('.sr-submit'); await page.waitForFunction(()=>document.querySelector('.sr-status').textContent.includes('待确认'));
 assert.equal(await page.$eval('.sr-submit',e=>e.disabled),true); assert.equal(await page.$('.sr-receipt'),null);
 await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
 mode='new'; await setup(1366); await page.click('.sr-submit'); await page.waitForSelector('.sr-receipt');
 assert.equal(await page.$eval('.sr-receipt',e=>getComputedStyle(e).transform),'none');
 await page.click('a[href="/chat"]'); await sleep(300); assert.equal(await page.$('.sr-receipt'),null);

 mode='new'; tasks=[]; await page.evaluate(()=>{localStorage.setItem('sr.submit.automatic','0');localStorage.removeItem('sr.submit.automatic.v2');}); await page.goto(base+'/viewer');
 await page.waitForFunction(()=>!!window.__viewer);
 async function openMockScene() {
  await page.evaluate(async()=>{
   const cv=document.createElement('canvas');cv.width=16;cv.height=16;
   const ctx=cv.getContext('2d');ctx.fillStyle='#808080';ctx.fillRect(0,0,16,16);
   const blob=await new Promise(r=>cv.toBlob(r,'image/png'));
   await window.__viewer.openSceneJpg({name:'PAN.tif',W:160,H:160,sceneId:'mock',lqPath:'/mock/scene'},blob);
  });
 }
 await openMockScene();
 await page.click('.set-btn');
 assert.equal(await page.$eval('[data-e2e="set-auto-submission"]', e=>e.getAttribute('aria-checked')),'true');
 await page.keyboard.press('Escape');
 const metrics=()=>page.$eval('.toolbar .sr-submit',e=>{const s=getComputedStyle(e);return [s.width,s.height,s.borderRadius,s.fontWeight,s.backgroundColor,s.color];});
 const original=await metrics();
 assert(await page.$('.toolbar .sr-submit .app-icon'));
 const before=posts; viewerCompletion=true;
 await page.click('.toolbar .sr-submit'); await page.click('.toolbar .sr-submit');
 await page.waitForSelector('.sr-receipt');
 assert.equal(posts,before+1); assert.equal(new URL(page.url()).pathname,'/viewer');
 await page.waitForSelector('[data-e2e="job-notice-ok"]',{timeout:15000});
 await sleep(1700);assert.deepEqual(await metrics(),original);
 assert(await page.$('.toolbar .sr-submit .app-icon'));
 await page.screenshot({path:path.join(out,'viewer-automatic.png')});
 await page.click('.sr-close'); await page.click('.set-btn');
 await page.click('[data-e2e="set-auto-submission"]'); await page.keyboard.press('Escape');
 await page.reload(); await page.waitForFunction(()=>!!window.__viewer); await openMockScene();
 assert.deepEqual(await metrics(),original);
 assert(await page.$('.toolbar .sr-submit .app-icon'));
 await page.click('.toolbar .sr-submit');
 await page.waitForSelector('.qp-actions');
 assert.equal(posts,before+1); assert.equal(await page.$eval('input[list="qp-lq-cands"]',e=>e.value),'/mock/scene');
 assert.deepEqual(errors,[]); console.log('PASS: widths, pending/double click, receipt identity, hover, dedup, rejection, unknown, reduced motion, unmount; recording saved');
 await browser.close(); server.close();
})().catch(e=>{console.error(e);process.exit(1)});

