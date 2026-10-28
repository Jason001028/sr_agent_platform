const assert = require('assert/strict');
const fs=require('fs'),path=require('path'),http=require('http');
const { launchBrowser } = require('./launchBrowser');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const dist=path.resolve(__dirname,'../frontend/dist');
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://local').pathname;
  if(url.startsWith('/api/')) {res.writeHead(404,{'Content-Type':'application/json'});res.end('{}');return;}
  let file=path.join(dist,url==='/'?'index.html':url);
  if(!file.startsWith(dist)){res.writeHead(403);res.end();return;}
  if(!fs.existsSync(file)||fs.statSync(file).isDirectory())file=path.join(dist,'index.html');
  res.setHeader('Content-Type',({'.js':'application/javascript','.css':'text/css','.png':'image/png','.webp':'image/webp','.svg':'image/svg+xml','.html':'text/html'})[path.extname(file)]||'application/octet-stream');
  res.end(fs.readFileSync(file));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base=`http://127.0.0.1:${server.address().port}`;
 let browser;
 try {
  browser=await launchBrowser();const page=await browser.newPage();const errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/chat/'))requests.push(r.url());});
  await page.setViewport({width:1600,height:900});await page.goto(base+'/viewer');
  await page.waitForSelector('.at-welcome');
  assert.ok(await page.$eval('.ctx-tab.on',e=>e.textContent.includes('Agent')));
  assert.equal(await page.$eval('.ctx-rail',e=>e.classList.contains('collapsed')),false);
  assert.equal(await page.$eval('.at-input',e=>e.disabled),true);
  assert.equal(await page.$eval('.at-input-lock',e=>getComputedStyle(e).backdropFilter),'blur(8px)');
  assert.ok(await page.evaluate(()=>{const a=document.querySelector('.at-input-lock').getBoundingClientRect(),b=document.querySelector('.at-scroll').getBoundingClientRect();return a.top>=b.bottom-1;}));
  await page.evaluate(()=>{const stores=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s;window.v=stores.get('viewer');window.c=stores.get('chat');window.sendCount=0;window.c.send=async()=>{window.sendCount++};});
  assert.equal(await page.evaluate(()=>window.c.messages.length),0);
  assert.deepEqual(requests,[]);
  await page.evaluate(()=>document.querySelector('.at-input').dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',bubbles:true})));
  assert.equal(await page.evaluate(()=>window.sendCount),0);
  await page.screenshot({path:path.join(__dirname,'shots/viewer-agent-welcome-locked.png')});
  await page.evaluate(()=>{window.c.sessionId='ui-test';window.c.messages=[{seq:1,role:'assistant',content:'Existing conversation'}];});
  await page.waitForFunction(()=>document.querySelector('.at-scroll').textContent.includes('Existing conversation'));
  assert.equal(await page.$('.at-welcome'),null);
  await page.evaluate(()=>{const cv=document.createElement('canvas');cv.width=cv.height=8;const b=atob(cv.toDataURL('image/jpeg').split(',')[1]);window.__viewer.openLocalImage(new File([Uint8Array.from(b,c=>c.charCodeAt(0))],'local.jpg',{type:'image/jpeg'}));});
  await page.waitForFunction(()=>window.v.activeRec?.thumb);
  assert.equal(await page.$eval('.at-input',e=>e.disabled),true);
  await page.waitForFunction(()=>document.querySelector('.at-reason').textContent.includes('\u672a\u5173\u8054'));
  await page.evaluate(()=>{window.v.activeRec.sceneId='production-scene';});
  await page.waitForFunction(()=>!document.querySelector('.at-input').disabled);
  assert.equal(await page.$('.at-input-lock'),null);
  assert.equal(await page.evaluate(()=>window.c.sessionId),'ui-test');
  await page.type('.at-input','test');await page.focus('.at-input');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>window.sendCount===1);
  await page.evaluate(()=>{window.v.activeRec.sceneId=null;});await page.waitForSelector('.at-input-lock');
  assert.equal(await page.evaluate(()=>window.c.sessionId),'ui-test');
  assert.ok(await page.$eval('.at-scroll',e=>e.textContent.includes('Existing conversation')));
  await page.evaluate(()=>{const input=document.querySelector('.at-input');input.value='blocked';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',bubbles:true}));});
  assert.equal(await page.evaluate(()=>window.sendCount),1);
  await page.evaluate(()=>document.querySelector('.ctx-tab').click());await page.waitForFunction(()=>localStorage.getItem('sr.viewer.ctxTab')==='roi');await page.reload();await page.waitForSelector('.ctx-tab.on');
  assert.equal(await page.$eval('.ctx-tab.on',e=>e.textContent.trim()),'\u5de5\u5177\u680f');
  await page.evaluate(()=>document.querySelectorAll('.ctx-tab')[1].click());
  await page.evaluate(()=>{window.v=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');window.v.setCompareMode('split');});
  await page.waitForFunction(()=>document.querySelector('.ctx-rail').classList.contains('collapsed'));
  await page.evaluate(()=>window.v.setCompareMode('off'));
  await page.waitForFunction(()=>!document.querySelector('.ctx-rail').classList.contains('collapsed'));
  await page.click('.ctx-toggle');await page.reload();await page.waitForSelector('.ctx-rail.collapsed');
  assert.ok(await page.$eval('.ctx-tab.on',e=>e.textContent.includes('Agent')));
  assert.deepEqual(errors,[]);console.log('PASS: default Agent/open, frontend-only greeting, footer-only glass lock, local restriction, scene unlock/relock preserves session/history, Enter guard, persisted tabs/collapse and split restoration.');
 }finally{if(browser)await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
