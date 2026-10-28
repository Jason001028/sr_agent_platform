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
  browser=await launchBrowser();const page=await browser.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
  await page.setViewport({width:1600,height:900});await page.goto(base+'/viewer');
  await page.waitForSelector('.demo-list');
  await page.waitForFunction(()=>[...document.querySelectorAll('.demo-list img')].every(img=>img.complete&&img.naturalWidth===512));
  assert.equal(await page.$$('.demo-card').then(a=>a.length),2);
  assert.equal(await page.$('.demo-card button'),null);
  assert.ok(await page.$$eval('.demo-card',els=>els.every(e=>e.inert&&e.draggable===false&&getComputedStyle(e).opacity==='0.85')));
  assert.deepEqual(await page.$$eval('.demo-card .fname',els=>els.map(e=>e.textContent)),['demo_pan.webp','demo_nosr.webp']);
  await page.evaluate(()=>{window.v=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');});
  assert.equal(await page.evaluate(()=>window.v.recs.length),0);
  await page.evaluate(()=>document.querySelector('.demo-card').dispatchEvent(new MouseEvent('click',{bubbles:true})));
  assert.equal(await page.evaluate(()=>window.v.activeId),null);
  await page.screenshot({path:path.join(__dirname,'shots/viewer-demo-cards.png')});
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'no-preference'}]);
  await page.waitForFunction(()=>document.querySelector('.demo-list').getAnimations({subtree:true}).length===3);
  const timeline=await page.evaluate(async()=>{const a=document.querySelector('.demo-list').getAnimations({subtree:true});await Promise.all(a.map(x=>x.ready));return a.map(x=>({start:x.startTime,duration:x.effect.getTiming().duration}));});
  assert.ok(timeline.every(a=>a.duration===12000));
  assert.ok(Math.max(...timeline.map(a=>a.start))-Math.min(...timeline.map(a=>a.start))<.1,'Shared document-timeline start');
  const phase=async time=>page.evaluate(t=>{
   const root=document.querySelector('.demo-list');root.getAnimations({subtree:true}).forEach(a=>{a.pause();a.currentTime=t;});
   return {cards:[...root.querySelectorAll('.demo-card')].map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {x:r.x,y:r.y,w:r.width,h:r.height,opacity:Number(s.opacity),transform:s.transform};}),dot:getComputedStyle(root.querySelector('.demo-dot')).transform,dotOpacity:Number(getComputedStyle(root.querySelector('.demo-dot')).opacity)};
  },time);
  const pan=await phase(1000), flowStart=await phase(4100), flowEnd=await phase(4900),nosr=await phase(6000),rest=await phase(9500),repeat=await phase(13000);
  assert.equal(pan.cards[0].opacity,1);assert.equal(pan.cards[1].opacity,.85);
  assert.equal(nosr.cards[0].opacity,.85);assert.equal(nosr.cards[1].opacity,1);
  assert.ok(rest.cards.every(c=>c.opacity===.85));assert.deepEqual(repeat,pan);
  const geometry=state=>state.cards.map(({opacity,...g})=>g);
  for(const state of [flowStart,flowEnd,nosr,rest,repeat])assert.deepEqual(geometry(state),geometry(pan));
  assert.notEqual(flowStart.dot,flowEnd.dot);
  // Sample every specified plateau and the midpoint of each 0.5s fade.
  for(const [time,expected] of [[0,[.85,.85]],[250,[.85,.85]],[500,[.85,.85]],[750,[.925,.85]],[1000,[1,.85]],[3500,[1,.85]],[3750,[.925,.85]],[4000,[.85,.85]],[4500,[.85,.85]],[5000,[.85,.85]],[5250,[.85,.925]],[5500,[.85,1]],[8000,[.85,1]],[8250,[.85,.925]],[8500,[.85,.85]],[10000,[.85,.85]],[11999,[.85,.85]],[12000,[.85,.85]]]) {
   const state=await phase(time);
   state.cards.forEach((c,i)=>assert.ok(Math.abs(c.opacity-expected[i])<.0001,`Timing at ${time}ms: ${c.opacity}`));
   assert.ok(state.cards.filter(c=>c.opacity>.8501).length<=1,'Never focus both cards');
   if(time<=4000||time>=5000)assert.ok(state.dotOpacity<.000001,'Dot only visible during transfer');
   assert.deepEqual(geometry(state),geometry(pan));
  }
  for(let time=0;time<12000;time+=100){const state=await phase(time);assert.ok(state.cards.filter(c=>c.opacity>.8501).length<=1);}
  await phase(1000);await page.screenshot({path:path.join(__dirname,'shots/viewer-demo-pan-focus.png')});
  await phase(6000);await page.screenshot({path:path.join(__dirname,'shots/viewer-demo-nosr-focus.png')});
  // Recreate CSS-owned animations after using WAAPI to inspect exact phases.
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
  await page.waitForFunction(()=>document.querySelector('.demo-list').getAnimations({subtree:true}).length===0);
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'no-preference'}]);
  await page.waitForFunction(()=>document.querySelector('.demo-list').getAnimations({subtree:true}).length===3);
  // Let untouched CSS animations actually run across two full cycles.
  await page.mouse.move(800,600);
  for(const [target,focus] of [[1500,'pan'],[6000,'nosr'],[13500,'pan'],[18000,'nosr'],[24500,'rest']]) {
   await page.waitForFunction(t=>document.querySelector('.demo-pan').getAnimations()[0].currentTime>=t, {timeout:16000}, target);
   const state=await page.evaluate(()=>({time:document.querySelector('.demo-pan').getAnimations()[0].currentTime,pan:Number(getComputedStyle(document.querySelector('.demo-pan')).opacity),nosr:Number(getComputedStyle(document.querySelector('.demo-nosr')).opacity),iterations:document.querySelector('.demo-pan').getAnimations()[0].effect.getComputedTiming().currentIteration}));
   assert.ok(Math.abs(state.pan-(focus==='pan'?1:.85))<.001);
   assert.ok(Math.abs(state.nosr-(focus==='nosr'?1:.85))<.001);
   if(target>=12000)assert.ok(state.iterations>=1,'Actual next cycle must run');
   if(target===13500||target===18000)await page.screenshot({path:path.join(__dirname,`shots/viewer-demo-live-${focus}-cycle2.png`)});
  }
  await page.hover('.demo-tip');
  assert.equal(await page.$eval('.demo-pan',e=>getComputedStyle(e).animationPlayState),'running','Hint hover must not freeze the loop');
  await page.hover('.demo-list');await sleep(100);
  assert.equal(await page.$eval('.demo-pan',e=>getComputedStyle(e).animationPlayState),'paused');
  const held=await page.$eval('.demo-pan',e=>e.getAnimations()[0].currentTime);await sleep(250);
  assert.equal(await page.$eval('.demo-pan',e=>e.getAnimations()[0].currentTime),held);
  await page.mouse.move(800,600);
  assert.equal(await page.$eval('.demo-pan',e=>getComputedStyle(e).animationPlayState),'running');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForSelector('.demo-paused');assert.equal(await page.$eval('.demo-nosr',e=>getComputedStyle(e).animationPlayState),'paused');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForFunction(()=>!document.querySelector('.demo-paused'));
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
  assert.equal(await page.$eval('.demo-pan',e=>getComputedStyle(e).animationName),'none');
  assert.equal(await page.$eval('.demo-guide',e=>getComputedStyle(e).display),'none');
  await page.evaluate(()=>window.v.busy=true);await page.waitForFunction(()=>!document.querySelector('.demo-list'));
  await page.evaluate(()=>window.v.busy=false);await page.waitForSelector('.demo-list');
  await page.click('.side-toggle');await page.waitForSelector('.sidebar.collapsed');
  await page.click('.side-toggle');await page.waitForFunction(()=>!document.querySelector('.sidebar').classList.contains('collapsed'));
  // A real native JPG drop must remove examples and use the same card structure.
  await page.evaluate(()=>{const dt=new DataTransfer(),cv=document.createElement('canvas');cv.width=cv.height=8;const b=atob(cv.toDataURL('image/jpeg').split(',')[1]);dt.items.add(new File([Uint8Array.from(b,c=>c.charCodeAt(0))],'real.jpg',{type:'image/jpeg'}));const e=new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true});document.querySelector('.view-canvas').dispatchEvent(e);if(!e.defaultPrevented)throw Error('Drop navigation was not prevented');});
  await page.waitForFunction(()=>!document.querySelector('.demo-list'));
  await page.waitForFunction(()=>window.v.activeRec?.thumb);
  assert.equal(await page.$$('.file-item[data-rec-id]').then(a=>a.length),1);
  assert.ok(await page.$('.file-item[data-rec-id] > .card-body > .meta'));
  assert.ok(await page.$('.file-item[data-rec-id] .ft canvas'));
  assert.equal(await page.$eval('.file-item[data-rec-id]',e=>e.draggable),true);
  await page.evaluate(()=>document.querySelector('.file-item[data-rec-id] .close').click());
  await page.waitForSelector('.demo-list');assert.equal(await page.evaluate(()=>window.v.recs.length),0);
  assert.deepEqual(errors,[]);console.log('PASS: 24.5s actual continuous two-cycle playback, hint-hover keeps running, exact 12s phase boundaries/fade midpoints, shared timeline/no overlap, CSS focus/flow/repeat, invariant card geometry, hover/hidden/reduced-motion pause, paired local WebP previews, shared formal card structure, inert/store-free examples, busy/loading suppression, sidebar collapse, native JPG drop, formal thumbnail and remove action.');
 }finally{if(browser)await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
