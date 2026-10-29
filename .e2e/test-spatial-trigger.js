const http=require('http'),fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {spawn}=require('child_process'),{launchBrowser}=require('./launchBrowser');
(async()=>{
 const backend=spawn('python',[path.join(__dirname,'spatial_mock_server.py')],{cwd:path.join(__dirname,'..'),stdio:['ignore','pipe','pipe']});let logs='';backend.stderr.on('data',b=>logs+=b);
 let browser,server;
 try{
  const port=await new Promise((resolve,reject)=>{backend.stdout.once('data',b=>resolve(Number(String(b).trim())));backend.once('exit',()=>reject(Error(logs)));});
  for(let i=0;i<100;i++){try{await fetch(`http://127.0.0.1:${port}/api/chat/sessions`);break;}catch{await new Promise(r=>setTimeout(r,100));}}
  const dist=path.join(__dirname,'../frontend/dist');
  server=http.createServer((req,res)=>{
   const u=new URL(req.url,'http://local').pathname;
   if(u.startsWith('/api/')){const proxy=http.request({hostname:'127.0.0.1',port,path:req.url,method:req.method,headers:req.headers},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});proxy.on('error',e=>{if(!res.headersSent)res.writeHead(502);res.end(String(e));});req.pipe(proxy);return;}
   let f=path.join(dist,u==='/'?'index.html':u);if(!fs.existsSync(f)||fs.statSync(f).isDirectory())f=path.join(dist,'index.html');
   res.setHeader('Content-Type',({'.js':'application/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'})[path.extname(f)]||'application/octet-stream');res.end(fs.readFileSync(f));
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await launchBrowser();const page=await browser.newPage();await page.setViewport({width:1366,height:900});await page.goto(`http://127.0.0.1:${server.address().port}/viewer`);await page.waitForSelector('.stage');
  await page.evaluate(async()=>{const cv=document.createElement('canvas');cv.width=800;cv.height=600;const blob=await new Promise(r=>cv.toBlob(r,'image/jpeg')),dt=new DataTransfer();dt.items.add(new File([blob],'mock-only.jpg',{type:'image/jpeg'}));document.querySelector('.stage').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));});
  await page.waitForFunction(()=>document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').activeRec?.thumb);
  await page.waitForNetworkIdle({idleTime:300});
  await page.evaluate(()=>document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('viewer').hideModal());
  await page.click('.image-pill');await page.waitForFunction(()=>document.querySelectorAll('.annotation-marker').length===3,{timeout:10000}).catch(async e=>{console.log(await page.evaluate(()=>document.querySelector('.at').innerText));console.log(logs);throw e;});
  assert.equal((await page.$$('.spatial-references button')).length,3);assert.equal((await page.$$('.annotation-geometry polygon, .annotation-geometry .annotation-bbox')).length,0);
  await page.click('.spatial-references button:nth-of-type(2)');assert.equal((await page.$$('.annotation-geometry polygon, .annotation-geometry .annotation-bbox')).length,1);
  await page.click('.annotation-close');assert.equal((await page.$$('.annotation-geometry polygon, .annotation-geometry .annotation-bbox')).length,0);
  await page.click('.annotation-marker');await page.waitForSelector('.annotation-popover');
  await page.click('.spatial-references button');assert.equal((await page.$$('.annotation-popover')).length,1);
  await page.evaluate(async()=>{const c=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat');await c.loadHistory(c.sessionId);});
  assert.equal((await page.$$('.spatial-references button')).length,3);assert.equal((await page.$$('.annotation-marker')).length,3);
  await page.evaluate(async()=>{const c=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat');await c.newSession();});
  await page.type('.at-input','TEXT_ONLY');await page.click('.image-pill');
  await page.waitForFunction(()=>{const c=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat');return !c.sending&&c.messages.some(m=>m.role==='assistant'&&m.content?.includes('Mock text only'));});
  assert.equal((await page.$$('.annotation-marker')).length,0,'Text-only result creates no invented locations');
  assert.equal((await page.$$('.spatial-references button')).length,0);
  console.log('PASS: analyze button -> real Chat API/upload -> Loop/tool validation -> SQLite -> SSE -> Vue geometry/references -> history. Isolated Mock only.');
 }finally{if(browser)await browser.close();if(server)server.closeAllConnections();if(server)server.close();backend.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
