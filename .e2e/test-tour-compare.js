// Offline synthetic Scene evidence + image previews. Never calls SR/GPU.
const assert=require('assert/strict'),fs=require('fs'),path=require('path'),http=require('http');
const {launchBrowser}=require('./launchBrowser');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const dist=path.resolve(__dirname,'../frontend/dist'),out=path.resolve(__dirname,process.env.SR_E2E_ARTIFACT_DIR||'artifacts/tour-compare');fs.mkdirSync(out,{recursive:true});
 const server=http.createServer((req,res)=>{let file=path.join(dist,new URL(req.url,'http://localhost').pathname);if(!fs.existsSync(file)||fs.statSync(file).isDirectory())file=path.join(dist,'index.html');res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.html':'text/html'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await launchBrowser();
 try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let images={},fetches=0,insufficient=true,oldQueries=0;
 const evidence={sequence:'synthetic-sequence',width:24000,height:16000,registration:{reference:'geotiff:EPSG:32650:PixelIsArea',crs:'EPSG:32650',extent:[0,0,24000,16000],orientation:[1,0,0,-1]}};
 // NOSR 用 1.96 倍的非整数网格（生产实测）：逐像素乘积不相等，但宽高比一致，
 // 自动巡览必须仍能把它和 PAN 本体排成一对。预览仍按 1/2 缩放，便于断言视口换算。
 const items=[['a','PAN.tif','pan',24000,16000],['b','PAN_NOSR.tif','nosr',12245,8163],['c','PAN_ori.tif','ori',24000,16000],['d','PAN_old.tif','old',24000,16000]].map(([id,name,role,width,height])=>({id,name,kind:role==='pan'?'input':role==='nosr'?'nosr':'product',exists:true,W:width,H:height,srcRev:id,sizeBytes:1,mtime:1,rel:name,hasPreview:true,previewDiv:4,jpgUrl:`/synthetic/${id}.png`,tourIdentity:{...evidence,role,width,height,sourceRevision:id}}));
 await page.setRequestInterception(true);
 page.on('request',r=>{const u=new URL(r.url());
  if(u.pathname.startsWith('/synthetic/')){fetches++;return r.respond({status:200,contentType:'image/png',body:images[u.pathname.split('/').pop()[0]]});}
  if(!u.pathname.startsWith('/api/'))return r.continue();
  if(r.method()!=='GET')return r.respond({status:403,body:'Synthetic tests forbid mutations'});
  const old=u.searchParams.get('suffix')==='old';if(old)oldQueries++;
  const body=u.pathname.endsWith('/siblings')?JSON.stringify({sceneId:'a',lqPath:'/synthetic/sequence',items:old?[items[3]]:[],suffix:old?'old':null,suffixFrom:old?'query':null,productCandidates:old?['PAN_old.tif']:[],nosrCandidates:[],tourItems:items.map(i=>insufficient?{...i,tourIdentity:{...i.tourIdentity,registration:undefined}}:i)}):u.pathname.endsWith('/queue')?'{"tasks":[]}':'{}';
  return r.respond({status:200,contentType:u.pathname.endsWith('/events')?'text/event-stream':'application/json',body:u.pathname.endsWith('/events')?'':body});
 });
 await page.setViewport({width:1600,height:900});await page.goto(`http://127.0.0.1:${server.address().port}/viewer`);await page.waitForFunction(()=>!!window.__viewer);
 const urls=await page.evaluate(async()=>{
  const urls={};for(const [id,w,h,brightness] of [['a',2400,1600,70],['b',1200,800,100],['c',2400,1600,130],['d',2400,1600,150]]){
   const c=document.createElement('canvas');c.width=w;c.height=h;const ctx=c.getContext('2d');ctx.fillStyle=`rgb(${brightness},${brightness},${brightness})`;ctx.fillRect(0,0,w,h);ctx.fillStyle='#eee';for(let x=0;x<w;x+=w/12)ctx.fillRect(x,0,w/100,h);ctx.fillRect(0,h/2,w,h/100);urls[id]=c.toDataURL();
  }
  const blob=await(await fetch(urls.a)).blob();await window.__viewer.openSceneJpg({name:'PAN.tif',W:24000,H:16000,sceneId:'a',lqPath:'/synthetic/sequence',sceneDir:'/synthetic/sequence',srcRev:'a'},blob);return urls;
 });
 for(const id in urls)images[id]=Buffer.from(urls[id].split(',')[1],'base64');
 const view=()=>page.evaluate(()=>{const p=window.__viewer.cmpPanes()[0],v=p.view;return {scale:Math.round(v.scale*(p.recId===2?.5:1)*1e8)/1e8,ox:Math.round(v.ox*1e8)/1e8,oy:Math.round(v.oy*1e8)/1e8};}),tour=()=>page.evaluate(()=>window.__viewer.autoTour());
 const choose=async index=>{await page.select('[aria-label="影像配对"]',String(index));await page.evaluate(()=>[...document.querySelectorAll('.pair-actions button')].find(b=>b.textContent==='载入配对').click());await page.waitForFunction(()=>window.__viewer.flicker().ready);await sleep(150);};
 await page.click('.tour-button');await page.click('.tour-popover input[type=checkbox]');await page.waitForFunction(()=>document.querySelector('[aria-label="影像配对"]').options.length===4);
 assert.equal(await page.$eval('[aria-label="影像配对"]',e=>e.value),'-1');await choose(0);
 const before=await view(),requests=fetches;await page.evaluate(()=>[...document.querySelectorAll('.pair-actions button')].find(b=>b.textContent==='手动切换').click());
 const alt=await view();assert.equal(before.scale,alt.scale);assert.equal(before.ox,alt.ox);assert.equal(before.oy,alt.oy);assert.equal(fetches,requests);
 await page.select('[aria-label="切换间隔"]','600');
 for(const width of [1280,1366,1600]){
  await page.setViewport({width,height:width===1600?900:720});await sleep(100);
  const panel=await page.$eval('.tour-popover',e=>{const b=e.getBoundingClientRect();return {width:b.width,right:b.right,bottom:b.bottom,overflow:e.scrollWidth>e.clientWidth};});
  assert.equal(panel.width,328);assert(panel.right<=width&&panel.bottom<=(width===1600?900:720));assert(!panel.overflow);
  assert(await page.$eval('.tour-popover',e=>{const b=e.getBoundingClientRect();return e.contains(document.elementFromPoint(b.left+b.width/2,b.top+20));}),'panel heading must remain above the comparison capsule');
  await page.screenshot({path:path.join(out,`panel-compare-${width}.png`)});
 }
 await page.click('.tour-start');await page.waitForSelector('.tour-capsule');await sleep(1450);
 const stopped=await view();await sleep(650);assert.deepEqual(await view(),stopped);assert((await tour()).coverageB>0);assert((await tour()).intersection>0);
 await page.screenshot({path:path.join(out,'stop.png')});await sleep(2300);assert.notDeepEqual(await view(),stopped);assert.equal(fetches,requests);
 await page.click('.tour-capsule button:first-of-type');const paused=await view();await sleep(800);assert.deepEqual(await view(),paused);assert.equal(await page.$('.tour-animated'),null);
 for(const background of ['dots','grid','studio','graphite']){
  const canvas=await page.evaluate(()=>{window.__tourCanvas=document.querySelector('.view-canvas');return window.__viewer.autoTour();});
  await page.evaluate(background=>window.__viewer.setCanvasBackground(background),background);await sleep(220);
  assert.deepEqual(await view(),paused);assert.equal((await tour()).status,canvas.status);assert.equal(fetches,requests);
  assert(await page.evaluate(()=>window.__tourCanvas===document.querySelector('.view-canvas')));
  await page.screenshot({path:path.join(out,`background-${background}.png`)});
 }
 await page.click('.tour-capsule button:last-child');await page.click('.tour-button');
 await page.select('[aria-label="交替模式"]','move');await page.click('.tour-start');await sleep(1500);const moving=await view();await sleep(700);assert.notDeepEqual(await view(),moving);await page.screenshot({path:path.join(out,'move.png')});
 for(const background of ['dots','grid','studio','graphite']){
  const before=await view();await page.evaluate(background=>window.__viewer.setCanvasBackground(background),background);await sleep(220);
  assert.equal((await tour()).status,'playing');assert.notDeepEqual(await view(),before);assert.equal(fetches,requests);
 }
 await page.mouse.move(800,400);await page.mouse.wheel({deltaY:-100});await sleep(150);assert.equal((await tour()).status,'paused');const manual=await view();await sleep(700);assert.deepEqual(await view(),manual);
 await page.click('.tour-capsule button:first-of-type');await sleep(200);await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});assert.equal((await tour()).status,'paused');
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});await page.click('.tour-capsule button:last-child');
 // PAN/ori uses the same path, then evidence loss is visibly refused.
 await page.click('.tour-button');await page.waitForFunction(()=>document.querySelector('[aria-label="影像配对"]').options.length===4);await choose(1);await page.click('.tour-start');await sleep(1800);assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('PAN_ori.tif')|| (await page.evaluate(()=>window.__viewer.flicker().pairName)).includes('ori'));
 await page.click('.tour-capsule button:last-child');await page.click('.tour-button');await page.waitForFunction(()=>!document.querySelector('[aria-label="影像配对"]').disabled);await choose(2);await page.click('.tour-start');await sleep(1800);assert((await page.$eval('.tour-capsule',e=>e.textContent)).includes('old'));await page.screenshot({path:path.join(out,'old.png')});
 await page.click('.tour-capsule button:last-child');await page.evaluate(()=>window.__viewer.setCmpStripOpen(true));await page.waitForSelector('[data-e2e="cmp-sib-old"]');
 assert.equal(await page.$eval('[data-e2e="cmp-sib-old"]',e=>e.className),await page.$eval('[data-e2e="cmp-sib-nosr"]',e=>e.className));
 const cachedFetches=fetches;await page.click('[data-e2e="cmp-sib-old"]');await page.waitForFunction(()=>window.__viewer.activeRec().name.includes('old'));assert(oldQueries>0);assert.equal(fetches,cachedFetches);
 await page.screenshot({path:path.join(out,'old-button.png')});
 await page.click('.tour-button');await page.waitForFunction(()=>document.querySelector('[aria-label="影像配对"]').options.length===4);
 const candidates=await page.$$eval('[aria-label="影像配对"] option',options=>options.slice(1).map(o=>({disabled:o.disabled,text:o.textContent})));assert.equal(candidates.length,3);assert(candidates.every(o=>!o.disabled));assert(candidates.some(o=>o.text.includes('old')));
 assert(!(await page.$eval('.tour-popover',e=>e.textContent)).includes('缺少共同的可靠配准'));
 await page.screenshot({path:path.join(out,'untagged-candidates.png')});await choose(0);await page.click('.tour-start');await page.waitForSelector('.tour-capsule');
 await page.click('.tour-capsule button:last-child');await sleep(400);
 const beforeHistory=await view();
 await page.mouse.move(800,400);await page.mouse.wheel({deltaY:-100});await sleep(400);
 assert.notDeepEqual(await view(),beforeHistory);
 await page.click('.viewer-history button:first-child');await sleep(350);assert.deepEqual(await view(),beforeHistory);
 await page.click('a[href="/chat"]');await sleep(200);assert.equal((await tour()).status,'idle');assert.deepEqual(errors,[]);
 console.log('PASS untagged production sibling selection, cached manual switch, shared viewport, stop x2 rounds, move, coverage intersection, pause, takeover, visibility, ori/old, unmount');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exit(1);});
