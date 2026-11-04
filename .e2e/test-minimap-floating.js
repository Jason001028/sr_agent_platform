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
 const out = path.resolve(__dirname,'artifacts/submission-feedback'); fs.mkdirSync(out,{recursive:true});

 
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
 assert.deepEqual(errors,[]);console.log('PASS minimap aspect ratio, floating controls, settings isolation, collapse persistence, reduced motion');
 await browser.close();server.close();
})().catch(e=>{console.error(e);process.exit(1)});

