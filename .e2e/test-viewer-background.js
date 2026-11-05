// Offline synthetic images only; no backend or production data.
const assert=require('assert/strict'),fs=require('fs'),path=require('path'),http=require('http');
const {launchBrowser}=require('./launchBrowser');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const dist=path.resolve(__dirname,'../frontend/dist'),out=path.resolve(__dirname,'artifacts/viewer-background');fs.mkdirSync(out,{recursive:true});
 const server=http.createServer((req,res)=>{let file=path.join(dist,new URL(req.url,'http://localhost').pathname);if(!fs.existsSync(file)||fs.statSync(file).isDirectory())file=path.join(dist,'index.html');res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await launchBrowser();
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setRequestInterception(true);page.on('request',r=>new URL(r.url()).pathname.startsWith('/api/')?r.respond({status:200,contentType:r.url().endsWith('/events')?'text/event-stream':'application/json',body:r.url().endsWith('/events')?'':'{}'}):r.continue());
  await page.setViewport({width:1600,height:900});await page.goto(`http://127.0.0.1:${server.address().port}/viewer`);await page.waitForFunction(()=>!!window.__viewer);
  const modes=['grid','dots','studio','graphite'];
  const background=()=>page.$eval('.viewer-backdrop',e=>e.dataset.background);
  assert.equal(await background(),'grid');
  for(const mode of modes){
   await page.evaluate(()=>window.__viewer.setSettingsOpen(true));
   await page.click(`[data-background-option="${mode}"]`);assert.equal(await background(),mode);
   assert.equal(await page.$eval(`[data-background-option="${mode}"]`,e=>e.getAttribute('aria-pressed')),'true');
   if(mode==='grid')await page.screenshot({path:path.join(out,'settings.png')});
   await page.evaluate(()=>window.__viewer.setSettingsOpen(false));await sleep(240);
   await page.screenshot({path:path.join(out,`empty-${mode}.png`)});
  }
  await page.reload();await page.waitForFunction(()=>!!window.__viewer);assert.equal(await background(),'graphite');
  const open=async(w,h,name)=>page.evaluate(async(w,h,name)=>{
   const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d');ctx.fillStyle='#426b69';ctx.fillRect(0,0,w,h);ctx.clearRect(w*.4,h*.4,w*.2,h*.2);
   await window.__viewer.openSceneJpg({name,W:w,H:h,sceneId:name,lqPath:'/synthetic/'+name},await new Promise(r=>c.toBlob(r)));
   // Seed an explicit display-alpha fixture after the normal preview stretch.
   const viewer=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');
   const thumb=viewer.activeRec.thumb,t=thumb.getContext('2d');t.fillStyle='#426b69';t.fillRect(0,0,thumb.width,thumb.height);t.clearRect(thumb.width*.4,thumb.height*.4,thumb.width*.2,thumb.height*.2);
   viewer.renderTick++;viewer.hideModal();
  },w,h,name);
  await open(600,300,'alpha-wide.tif');await sleep(200);
  await page.evaluate(()=>{
   window.__backgroundCanvas=document.querySelector('.view-canvas');window.__backgroundDraws=0;
   const proto=CanvasRenderingContext2D.prototype,draw=proto.drawImage;
   proto.drawImage=function(...args){if(this.canvas===window.__backgroundCanvas)window.__backgroundDraws++;return draw.apply(this,args);};
  });
  const state=()=>page.evaluate(()=>({view:window.__viewer.cmpPanes(),pixels:document.querySelector('.view-canvas').toDataURL(),draws:window.__backgroundDraws}));
  await page.evaluate(()=>window.__viewer.commitRect({x0:20,y0:20,x1:140,y1:100}));await sleep(80);
  const original=await state();
  const overlay=await page.$eval('.draw-canvas',e=>e.toDataURL());
  const hole=await page.$eval('.stage:has(> .view-canvas)',e=>{const b=e.getBoundingClientRect();return {x:Math.round(b.x+b.width/2-10),y:Math.round(b.y+b.height/2-10),width:20,height:20};});
  let matte;
  for(const mode of modes){
   await page.evaluate(mode=>window.__viewer.setCanvasBackground(mode),mode);await sleep(230);
   assert.equal(await page.evaluate(()=>window.__backgroundCanvas===document.querySelector('.view-canvas')),true);
   assert.deepEqual(await state(),original,'CSS preference must not redraw image pixels, resize Canvas, or move Camera');
   assert.equal(await page.$eval('.draw-canvas',e=>e.toDataURL()),overlay,'ROI overlay must stay above the background without repainting');
   const pixels=await page.screenshot({clip:hole});if(!matte)matte=pixels;else assert.deepEqual(pixels,matte,'alpha pixels must retain the original flat matte');
   await page.screenshot({path:path.join(out,`single-${mode}.png`)});
  }
  const fixed=await page.$eval('.backdrop-surface.selected',e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e);return [b.x,b.y,b.width,b.height,s.backgroundPosition,s.backgroundSize];});
  const center=await page.$eval('.stage:has(> .view-canvas)',e=>{const b=e.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};});
  await page.mouse.move(center.x,center.y);await page.mouse.wheel({deltaY:150});await sleep(150);assert.notDeepEqual((await state()).view,original.view);
  await page.mouse.down();await page.mouse.move(center.x+25,center.y+20,{steps:5});await page.mouse.up();await sleep(100);
  assert.deepEqual(await page.$eval('.backdrop-surface.selected',e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e);return [b.x,b.y,b.width,b.height,s.backgroundPosition,s.backgroundSize];}),fixed,'environment must not follow Camera');
  const beforeNav=(await state()).view;await page.click('.navigator canvas');await sleep(100);assert.notDeepEqual((await state()).view,beforeNav);
  await open(300,700,'alpha-tall.tif');await page.evaluate(()=>window.__viewer.setCmpMode('split'));await sleep(200);
  await page.evaluate(async()=>{const viewer=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');await viewer.activate(viewer.recs.find(r=>r.name==='alpha-wide.tif').id,'B');});await sleep(180);
  for(const mode of modes){
   await page.evaluate(mode=>window.__viewer.setCanvasBackground(mode),mode);await sleep(230);
   const panes=await page.evaluate(()=>window.__viewer.cmpPanes());
   const bounds=await page.$$eval('.backdrop-pane',els=>els.map(e=>({x:e.offsetLeft,y:e.offsetTop,w:e.offsetWidth,h:e.offsetHeight})));
   assert.equal(bounds.length,2);bounds.forEach((b,i)=>{assert.equal(b.x,panes[i].rect.x);assert.equal(b.w,panes[i].rect.w);assert.equal(b.h,panes[i].rect.h);});
   assert(await page.$eval('.cmp-divider',e=>{const b=e.getBoundingClientRect();return e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));}));
   await page.screenshot({path:path.join(out,`split-${mode}.png`)});
  }
  const divider=await page.$eval('.cmp-divider',e=>{const b=e.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};});
  await page.mouse.move(divider.x,divider.y);await page.mouse.down();await page.mouse.move(divider.x+40,divider.y,{steps:5});await page.mouse.up();await sleep(100);
  const panes=await page.evaluate(()=>window.__viewer.cmpPanes());assert.notEqual(panes[0].rect.w,panes[1].rect.w);
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);assert(await page.$eval('.backdrop-surface',e=>parseFloat(getComputedStyle(e).transitionDuration)<=.00001));
  assert.deepEqual(errors,[]);console.log('PASS four backgrounds: settings/persistence, empty/single/split, wide/tall alpha matte, unchanged Canvas/pixels/Camera/draw count, fixed texture, zoom/drag/minimap/divider, reduced motion');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exit(1);});
