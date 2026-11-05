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
 const out = path.resolve(__dirname,process.env.SR_E2E_ARTIFACT_DIR || 'artifacts/submission-feedback'); fs.mkdirSync(out,{recursive:true});

 
 await page.setViewport({width:1600,height:900});await page.goto(base+'/viewer');await page.waitForFunction(()=>!!window.__viewer);
 await page.evaluate(async()=>{
  const c=document.createElement('canvas');c.width=2400;c.height=1600;
  const ctx=c.getContext('2d');ctx.fillStyle='#666';ctx.fillRect(0,0,c.width,c.height);
  for(let i=0;i<2400;i+=100){ctx.fillStyle=i%200?'#999':'#333';ctx.fillRect(i,0,50,1600);}
  const blob=await new Promise(r=>c.toBlob(r));
  await window.__viewer.openSceneJpg({name:'tour.tif',W:24000,H:16000,sceneId:'tour',lqPath:'/mock/tour'},blob);
 });
 const view=()=>page.evaluate(()=>window.__viewer.cmpPanes()[0].view);
 const initial=await view();
 await page.click('.tour-button');await sleep(200);assert.deepEqual(await view(),initial);
 assert.equal(await page.$eval('[data-route="rows"]',e=>e.getAttribute('aria-checked')),'true');
 for(const route of ['columns','inward','rows']){
  const beforeSelection=await view();
  await page.click(`[data-route="${route}"]`);assert.deepEqual(await view(),beforeSelection);
  await page.click('.tour-start');await page.waitForSelector('.tour-capsule');
  const state=await page.evaluate(()=>window.__viewer.autoTour());assert.equal(state.route,route);
  assert(state.routeSteps.length>1);
  if(route==='columns')assert.equal(state.routeSteps[0].x,state.routeSteps[1].x);
  else assert.equal(state.routeSteps[0].y,state.routeSteps[1].y);
  await page.keyboard.press('Escape');await sleep(230);await page.click('.tour-button');
 }
 await page.focus('[data-route="rows"]');await page.keyboard.press('ArrowRight');
 assert.equal(await page.$eval('[data-route="columns"]',e=>e.getAttribute('aria-checked')),'true');
 await page.keyboard.press('Home');
 for(const width of [1280,1366,1600]){
  await page.setViewport({width,height:width===1600?900:720});await sleep(100);
  const panel=await page.$eval('.tour-popover',e=>{const b=e.getBoundingClientRect();return {width:b.width,right:b.right,bottom:b.bottom,height:b.height,overflow:e.scrollWidth>e.clientWidth};});
  assert.equal(panel.width,328);assert(panel.right<=width&&panel.bottom<=(width===1600?900:720));assert(!panel.overflow);
  await page.screenshot({path:path.join(out,`tour-panel-${width}.png`)});
 }
 await page.click('.tour-start');await page.waitForSelector('.tour-capsule');await sleep(1700);
 assert.notDeepEqual(await view(),initial);
 await page.screenshot({path:path.join(out,'auto-tour.png')});
 await page.waitForSelector('.tour-animated.ready');
 const bounds=await page.$eval('.tour-button',e=>{const b=e.getBoundingClientRect();return [b.x,b.y,b.width,b.height];});
 const icon=await page.$('.tour-icon');
 const frame1=await icon.screenshot({encoding:'base64'});await sleep(130);const frame2=await icon.screenshot({encoding:'base64'});assert.notEqual(frame1,frame2);
 for(let i=0;i<20;i++){
  await page.click('.tour-capsule button:first-child');assert.equal(await page.$('.tour-animated'),null);
  assert.equal(await page.$eval('.tour-static',e=>getComputedStyle(e).opacity),'1');
  await page.click('.tour-capsule button:first-child');await page.waitForSelector('.tour-animated.ready');
  assert.deepEqual(await page.$eval('.tour-button',e=>{const b=e.getBoundingClientRect();return [b.x,b.y,b.width,b.height];}),bounds);
 }
 await page.click('.tour-capsule button:first-child');await sleep(100);const paused=await view();await sleep(300);assert.deepEqual(await view(),paused);
 await page.click('.tour-capsule button:first-child');await sleep(300);
 await page.mouse.move(800,400);await page.mouse.wheel({deltaY:-100});await sleep(100);
 assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('手动操作'));
 const manual=await view();await sleep(250);assert.deepEqual(await view(),manual);
 await page.click('.tour-capsule button:first-child');await sleep(100);
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
 assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('页面隐藏'));
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
 assert.equal(await page.$('.tour-animated'),null);
 const hidden=await view();await sleep(150);assert.deepEqual(await view(),hidden);
 await page.setViewport({width:1500,height:900});await sleep(200);
 assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('暂停'));
 await page.click('.tour-button');await page.click('.tour-start');await sleep(100);
 await page.evaluate(()=>window.__viewer.enterDraw());await sleep(100);
 assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('暂停'));
 await page.evaluate(()=>window.__viewer.exitDraw());
 await page.evaluate(()=>window.__viewer.setCmpMode('split'));await sleep(150);
 assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('暂停'));
 await page.keyboard.press('Escape');await sleep(230);assert.equal(await page.$('.tour-capsule'),null);
 await page.evaluate(()=>window.__viewer.setCmpMode('off'));await sleep(200);
 await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
 await page.click('.tour-button');await page.click('.tour-start');assert.equal(await page.$('.tour-capsule'),null);assert.equal(await page.$('.tour-animated'),null);
 assert((await page.$eval('.tour-popover',e=>e.textContent)).includes('减少动效'));
 await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'no-preference'}]);await page.click('.tour-start');await page.waitForSelector('.tour-capsule');
 await page.click('a[href="/chat"]');await sleep(250);assert.equal(await page.$('.tour-capsule'),null);
 assert.deepEqual(errors,[]);console.log('PASS tour entry, camera movement, pause/resume, manual takeover, hidden, split, escape, reduced motion, unmount');
 await browser.close();server.close();
})().catch(e=>{console.error(e);process.exit(1)});
