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
  res.setHeader('Content-Type',({'.js':'application/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.html':'text/html'})[path.extname(file)]||'application/octet-stream');
  res.end(fs.readFileSync(file));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const base=`http://127.0.0.1:${server.address().port}`;
 let browser;
 try {
  browser=await launchBrowser();const page=await browser.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.evaluateOnNewDocument(()=>{
   const request=window.requestAnimationFrame.bind(window),cancel=window.cancelAnimationFrame.bind(window);
   window.pendingAnimationFrames=new Set();
   window.requestAnimationFrame=cb=>{const id=request(t=>{window.pendingAnimationFrames.delete(id);cb(t);});window.pendingAnimationFrames.add(id);return id;};
   window.cancelAnimationFrame=id=>{window.pendingAnimationFrames.delete(id);cancel(id);};
  });
  await page.setViewport({width:1366,height:900});await page.goto(base+'/viewer');
  await page.waitForSelector('.logo-intro[data-playing="true"]');
  assert.equal(await page.$('.logo-intro img'),null);
  const sample=()=>page.evaluate(()=>{
   const cv=document.querySelector('.ascii-logo-canvas'),ctx=cv.getContext('2d'),pixels=ctx.getImageData(0,0,cv.width,cv.height).data;
   let occupied=0,hash=0,white=0;for(let i=0;i<pixels.length;i+=4){if(pixels[i+3]){occupied++;hash=(hash*31+pixels[i+3])>>>0;if(pixels[i]>240&&pixels[i+1]>240&&pixels[i+2]>240)white++;}}
   return {occupied,hash,white,corner:ctx.getImageData(0,0,20,20).data.some(v=>v!==0)};
  });
  // Record the actual on-screen canvas over the stage color, for >2 x 4.8s cycles.
  await page.evaluate(()=>{
   const source=document.querySelector('.ascii-logo-canvas'),out=document.createElement('canvas');out.width=out.height=400;
   const ctx=out.getContext('2d'),bg=getComputedStyle(document.querySelector('.stage')).backgroundColor;
   const stream=out.captureStream(15),chunks=[];const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});
   const timer=setInterval(()=>{ctx.fillStyle=bg;ctx.fillRect(0,0,400,400);ctx.drawImage(source,0,0,400,400);},1000/15);
   window.finishRecording=()=>new Promise(resolve=>{
    recorder.onstop=()=>{clearInterval(timer);stream.getTracks().forEach(t=>t.stop());const blob=new Blob(chunks,{type:'video/webm'});window.recordedBlob=blob;const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(blob);};recorder.stop();
   });recorder.ondataavailable=e=>chunks.push(e.data);recorder.start();
  });
  const samples=[];for(let n=0;n<9;n++){samples.push(await sample());await sleep(1200);}
  const video=await page.evaluate(()=>window.finishRecording());
  fs.writeFileSync(path.join(__dirname,'shots/viewer-ascii-flow.webm'),Buffer.from(video,'base64'));
  const filmstrip=await page.evaluate(async()=>{
   const video=document.createElement('video');video.src=URL.createObjectURL(window.recordedBlob);video.muted=true;
   await new Promise(r=>video.onloadedmetadata=r);
   const cv=document.createElement('canvas');cv.width=1000;cv.height=222;const ctx=cv.getContext('2d');
   const times=[.5,2.5,4.9,7.3,9.7];
   for(let i=0;i<times.length;i++){video.currentTime=times[i];await new Promise(r=>video.onseeked=r);ctx.drawImage(video,i*200,0,200,200);ctx.fillStyle='#244b40';ctx.font='12px monospace';ctx.fillText(times[i]+'s',i*200+8,215);}
   URL.revokeObjectURL(video.src);return cv.toDataURL('image/png').split(',')[1];
  });
  fs.writeFileSync(path.join(__dirname,'shots/viewer-ascii-flow-filmstrip.png'),Buffer.from(filmstrip,'base64'));
  assert.ok(new Set(samples.map(s=>s.hash)).size>5,'Characters must keep changing across two loops');
  assert.ok(samples.every(s=>s.occupied>1000&&!s.white&&!s.corner),'Logo stays visible with transparent margins and no white rectangle');
  assert.ok(Math.max(...samples.map(s=>s.occupied))/Math.min(...samples.map(s=>s.occupied))<1.7,'Stable silhouette/density');
  for(const [width,height] of [[1366,900],[1600,900],[1920,900],[768,540]]){
   await page.setViewport({width,height});await page.screenshot({path:path.join(__dirname,`shots/viewer-ascii-flow-${width}.png`)});
   assert.ok(await page.evaluate(()=>{const a=document.querySelector('.stage').getBoundingClientRect(),b=document.querySelector('.logo-intro').getBoundingClientRect();return Math.abs(a.x+a.width/2-b.x-b.width/2)<2&&Math.abs(b.width-b.height)<1&&b.width<=400;}));
  }
  await page.setViewport({width:1366,height:900});
  await page.evaluate(()=>{window.testViewer=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer');window.testViewer.busy=true;});
  await page.waitForFunction(()=>!document.querySelector('.logo-intro'));
  assert.equal(await page.evaluate(()=>window.pendingAnimationFrames.size),0);
  await page.evaluate(()=>window.testViewer.busy=false);await page.waitForSelector('.logo-intro[data-playing="true"]');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await page.$eval('.logo-intro',e=>e.dataset.playing),'false');
  assert.equal(await page.evaluate(()=>window.pendingAnimationFrames.size),0);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForSelector('.logo-intro[data-playing="true"]');
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
  await page.waitForSelector('.logo-intro[data-playing="false"]');const frozen=await sample();await sleep(250);assert.deepEqual(await sample(),frozen);
  assert.equal(await page.evaluate(()=>window.pendingAnimationFrames.size),0);
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'no-preference'}]);await page.waitForSelector('.logo-intro[data-playing="true"]');
  await page.evaluate(()=>window.testViewer.setCompareMode('split'));await page.waitForFunction(()=>!document.querySelector('.logo-intro'));
  await page.evaluate(()=>window.testViewer.setCompareMode('off'));await page.waitForSelector('.logo-intro[data-playing="true"]');
  await page.evaluate(()=>{const dt=new DataTransfer(),cv=document.createElement('canvas');cv.width=cv.height=8;const b=atob(cv.toDataURL('image/jpeg').split(',')[1]);dt.items.add(new File([Uint8Array.from(b,c=>c.charCodeAt(0))],'drop.jpg',{type:'image/jpeg'}));const event=new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true});document.querySelector('.view-canvas').dispatchEvent(event);if(!event.defaultPrevented)throw Error('Drop navigation not prevented');});
  await page.waitForFunction(()=>window.testViewer.activeRec?.thumb);assert.equal(await page.$('.logo-intro'),null);
  await sleep(300);assert.equal(await page.evaluate(()=>window.pendingAnimationFrames.size),0);
  await page.evaluate(()=>window.testViewer.removeAll());await page.waitForSelector('.logo-intro[data-playing="true"]');
  await page.evaluate(()=>document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/plugins'));await page.waitForFunction(()=>!document.querySelector('.logo-intro'));await sleep(100);
  assert.equal(await page.evaluate(()=>window.pendingAnimationFrames.size),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: recorded 10.8s (>2 cycles), evolving characters, stable transparent silhouette, 4 viewport sizes, load/hidden/reduced-motion/unmount cleanup, resume, split and JPG drop.');
  console.log(JSON.stringify(samples));
 }finally{if(browser)await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
