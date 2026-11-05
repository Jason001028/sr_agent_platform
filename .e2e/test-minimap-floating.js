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
  const ext=path.extname(file);res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.png':'image/png'})[ext]||'application/octet-stream');
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
 const out = path.resolve(__dirname,process.env.SR_E2E_ARTIFACT_DIR || 'artifacts/minimap-opacity'); fs.mkdirSync(out,{recursive:true});

 
 await page.setViewport({width:1600,height:900}); await page.goto(base+'/viewer');
 await page.waitForFunction(()=>!!window.__viewer);
 await page.evaluate(async()=>{
  const c=document.createElement('canvas');c.width=320;c.height=160;
  const ctx=c.getContext('2d');const g=ctx.createLinearGradient(0,0,320,160);g.addColorStop(0,'#111');g.addColorStop(1,'#eee');ctx.fillStyle=g;ctx.fillRect(0,0,320,160);
  const blob=await new Promise(r=>c.toBlob(r));
  await window.__viewer.openSceneJpg({name:'minimap.tif',W:320,H:160,sceneId:'minimap',lqPath:'/mock/minimap'},blob);
 });
 await page.waitForSelector('.navigator canvas',{visible:true});
 const canvas='.navigator canvas';
 const before=await page.$eval(canvas,e=>({w:e.width,h:e.height}));assert.equal(before.w/before.h,2);
 await page.hover('.navigator');await sleep(220);
 await page.screenshot({path:path.join(out,'minimap-floating.png')});
 await page.click('.settings-toggle'); await page.waitForSelector('.navigator .settings');
 assert.deepEqual(await page.$eval(canvas,e=>({w:e.width,h:e.height})),before);
 const opacityInput='[aria-label="鹰眼不透明度"]';
 assert.equal(await page.$eval(opacityInput,e=>e.min),'0');
 const clip=await page.$eval('.navigator .maps',e=>{const b=e.getBoundingClientRect();return {x:b.x+5,y:b.bottom-40,width:b.width-10,height:30};});
 for(const value of [0,50,100]){
  await page.$eval(opacityInput,(e,value)=>{e.value=String(value);e.dispatchEvent(new Event('input',{bubbles:true}));},value);
  await sleep(50);
  const styles=await page.$eval('.navigator',e=>({surface:getComputedStyle(e.querySelector('.navigator-surface')).opacity,map:getComputedStyle(e.querySelector('.maps')).opacity,background:getComputedStyle(e).backgroundColor,controls:getComputedStyle(e.querySelector('.settings')).opacity}));
  assert.equal(Number(styles.surface),value/100);assert.equal(Number(styles.map),1,'opacity must be applied once to the composited surface');
  assert.equal(styles.background,'rgba(0, 0, 0, 0)');assert.equal(styles.controls,'1');
  if(value===0){
   const transparent=await page.screenshot({clip});
   await page.$eval('.navigator',e=>e.style.visibility='hidden');
   const absent=await page.screenshot({clip});assert.deepEqual(transparent,absent,'0% must reveal the viewer without a white backing');
   await page.$eval('.navigator',e=>e.style.visibility='');
   await page.screenshot({path:path.join(out,'opacity-zero.png')});
  }
 }
 assert.deepEqual(await page.$eval(canvas,e=>({w:e.width,h:e.height})),before);
 await page.click('.settings-toggle');
 const handle=await page.$eval('.move-handle',e=>{const b=e.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};});
 await page.mouse.move(handle.x,handle.y);await page.mouse.down();await page.mouse.move(handle.x-60,handle.y-40,{steps:5});await page.mouse.up();
 const nav=await page.$eval('.navigator',e=>({left:e.offsetLeft,top:e.offsetTop}));
 await page.click('.navigator-tools button:last-child');await sleep(250);
 assert.equal(await page.$eval('.reopen',e=>e.offsetWidth),42);
 await page.reload();await page.waitForFunction(()=>!!window.__viewer);
 await page.evaluate(async()=>{
  const c=document.createElement('canvas');c.width=320;c.height=160;
  const ctx=c.getContext('2d');const g=ctx.createLinearGradient(0,0,320,160);g.addColorStop(0,'#111');g.addColorStop(1,'#eee');ctx.fillStyle=g;ctx.fillRect(0,0,320,160);
  const blob=await new Promise(r=>c.toBlob(r));
  await window.__viewer.openSceneJpg({name:'minimap.tif',W:320,H:160,sceneId:'minimap',lqPath:'/mock/minimap'},blob);
 });
 await page.waitForSelector('.reopen',{visible:true});
 assert.equal(await page.evaluate(()=>localStorage.getItem('sr.viewer.minimap.visible')),'0');
 await page.click('.reopen');await sleep(250);
 assert.equal(await page.evaluate(()=>localStorage.getItem('sr.viewer.minimap.visible')),'1');
 await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
 assert(await page.$eval('.navigator-tools',e=>parseFloat(getComputedStyle(e).transitionDuration)<=.00001));
 assert.deepEqual(errors,[]);console.log('PASS minimap 0/50/100% opacity, zero-opacity pixel equivalence, aspect ratio, floating controls, settings isolation, collapse persistence, reduced motion');
 await browser.close();server.close();
})().catch(e=>{console.error(e);process.exit(1)});

