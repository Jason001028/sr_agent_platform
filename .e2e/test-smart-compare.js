/* Real HTTP + synthetic TIFF + built Viewer. Start smart-compare-server.py first. */
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('assert');
const {launchPage}=require('./launchBrowser');
const root=path.resolve(__dirname,'../frontend/dist');
const backend=Number(process.env.SMART_TEST_PORT||8767);
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/api/')||req.url==='/_smart_fixture'){
    const proxy=http.request({host:'127.0.0.1',port:backend,path:req.url,method:req.method,headers:req.headers},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});
    proxy.on('error',e=>{if(!res.headersSent)res.writeHead(502);res.end(String(e));});res.on('close',()=>proxy.destroy());req.pipe(proxy);return;
  }
  const pathname=decodeURIComponent(new URL(req.url,'http://local').pathname);
  let file=path.resolve(root,'.'+pathname);if(!file.startsWith(root+path.sep)&&file!==root){res.writeHead(403);res.end();return;}
  if(!fs.existsSync(file)||fs.statSync(file).isDirectory())file=path.join(root,'index.html');
  res.setHeader('content-type',({'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.html':'text/html'})[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const {browser,page,errors}=await launchPage();let requests=0;
  const responses=[];
  page.on('request',r=>{if(r.url().includes('/api/diff/analyze'))requests++;});
  page.on('response',async r=>{if(r.url().includes('/api/diff/analyze'))try{responses.push(await r.json());}catch{}});
  try{
    await page.setViewport({width:1440,height:1000});
    const base='http://127.0.0.1:'+server.address().port;
    await page.goto(base+'/viewer',{waitUntil:'networkidle0'});
    await page.waitForFunction(()=>!!window.__viewer);
    await page.evaluate(async()=>{
      const fixtures=await(await fetch('/_smart_fixture')).json();
      for(const f of fixtures)await window.__viewer.openSceneJpg(f.meta,await(await fetch(f.preview)).blob());
      window.__viewer.hideModal();
      localStorage.setItem('sr.developerMode.local','1');
    });
    await page.waitForFunction(()=>!window.__viewer.overlayVisible());
    await page.click('[data-e2e="diff-open"]');
    await page.click('[data-e2e="diff-smart"]');
    // Explicitly choose PAN as target A, NOSR as reference B.
    const values=await page.$$eval('select[aria-label="影像 A"] option',xs=>xs.map(x=>({value:x.value,text:x.textContent})));
    const a=values.find(x=>x.text.includes('PAN.tif')),b=values.find(x=>x.text.includes('NOSR'));
    assert(a&&b,'pair options unavailable');
    await page.select('select[aria-label="影像 A"]',a.value);
    await page.select('select[aria-label="影像 B"]',b.value);
    await page.click('[data-e2e="diff-analyze"]');
    await page.waitForFunction(()=>document.querySelector('.diff-summary')?.textContent.includes('智能对比'),{timeout:30000});
    assert(responses[0]?.source==='source-grid',JSON.stringify(responses));
    assert(responses[0].regions.length>0,'no computed candidate');
    const before=requests;
    await page.$eval('input[aria-label="异常显示阈值"]',x=>{x.value='.3';x.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.$eval('input[aria-label="热力叠加不透明度"]',x=>{x.value='.4';x.dispatchEvent(new Event('input',{bubbles:true}));});
    assert.equal(requests,before,'display control reran detector');
    await page.click('.diff-close');
    await page.waitForSelector('.diff-marker');await page.click('.diff-marker');
    await page.waitForSelector('.diff-popover-card');
    const refine=await page.$$('.diff-popover-card button');
    let clicked=false;
    for(const x of refine){const t=await x.evaluate(n=>n.textContent);if(t.includes('精算')){await x.click();clicked=true;break;}}
    assert(clicked,'refine action missing');
    await page.waitForFunction(()=>document.querySelector('.diff-popover-card')===null,{timeout:30000});
    await page.waitForFunction(()=>document.body.textContent.includes('当前是原始 ROI')||document.querySelectorAll('.diff-marker').length>0,{timeout:30000});
    // Wait for the actual HTTP result rather than inferring completion from markers.
    await page.waitForFunction(()=>!document.body.textContent.includes('分析中…'),{timeout:30000});
    await new Promise(r=>setTimeout(r,300));
    assert(responses.some(r=>r.source==='source-roi'),JSON.stringify(responses.map(r=>r.detail||r.source)));
    await page.waitForSelector('.diff-marker');await page.click('.diff-marker');
    await page.waitForSelector('.diff-popover-card');
    const maskButtons=await page.$$('.diff-popover-card button');
    let maskClicked=false;
    for(const button of maskButtons)if((await button.evaluate(n=>n.textContent)).includes('加入修复掩码')){
      assert.equal(await button.evaluate(n=>n.disabled),false,'refined candidate is not eligible');await button.click();maskClicked=true;break;
    }
    assert(maskClicked,'mask action unavailable');
    try { await page.waitForFunction(()=>window.__viewer.getRois().length>0,{timeout:15000}); }
    catch(e){ console.error(await page.evaluate(()=>({errors:[...document.querySelectorAll('.dc-err,.diff-error,.dc-notice,.diff-notice')].map(x=>x.textContent),modal:window.__viewer.modal()})));throw e; }
    const maskCount=await page.evaluate(()=>window.__viewer.getRois().length);
    assert(maskCount>0,'candidate was not committed to editable Mask');
    await page.evaluate(()=>window.__viewer.undoRoi());
    assert.equal(await page.evaluate(()=>window.__viewer.getRois().length),0,'Mask transaction did not undo');
    await page.click('[data-e2e="diff-open"]');
    await page.$eval('details.diff-advanced',x=>x.open=true);
    await page.$eval('input[aria-label="三算法对照"]',x=>{x.checked=true;x.dispatchEvent(new Event('change',{bubbles:true}));});
    await page.click('[data-e2e="diff-analyze"]');
    await page.waitForFunction(()=>document.body.textContent.includes('Smart 独有候选像素'),{timeout:30000});
    assert(responses.some(r=>r.comparison?.summary.length===3));
    const shot=path.resolve(__dirname,'../test-tifs/smart-compare');fs.mkdirSync(shot,{recursive:true});
    await page.screenshot({path:path.join(shot,'viewer.png'),fullPage:true});
    assert.equal(errors.length,0,errors.join('\n'));
    console.log(JSON.stringify({source:'real-http-synthetic-tiff',requests,gridRegions:responses[0].regions.length,roi:true,maskTransaction:true,maskUndo:true,comparison:true,displayWithoutReanalysis:true}));
  }finally{await browser.close();server.closeAllConnections();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
