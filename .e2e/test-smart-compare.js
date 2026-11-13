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
  const serveOnly=process.argv.includes('--serve');
  await new Promise(r=>server.listen(serveOnly ? 8768 : 0,'127.0.0.1',r));
  if(serveOnly){console.log('Synthetic LTD Viewer: http://127.0.0.1:8768/viewer');return;}
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
    await page.waitForFunction(()=>document.querySelector('.diff-summary')?.textContent.includes('LTD'),{timeout:30000});
    // response.json() in the Node listener can finish after Vue renders summary.
    for(let i=0;i<100&&!responses.length;i++) await new Promise(r=>setTimeout(r,50));
    assert(responses[0]?.source==='source-grid',JSON.stringify(responses));
    assert(responses[0].regions.length>0,'no computed candidate');
    const before=requests;
    await page.$eval('input[aria-label="异常显示阈值"]',x=>{x.value='.3';x.dispatchEvent(new Event('input',{bubbles:true}));});
    await page.$eval('input[aria-label="热力叠加不透明度"]',x=>{x.value='.4';x.dispatchEvent(new Event('input',{bubbles:true}));});
    assert.equal(requests,before,'display control reran detector');
    await page.click('.diff-close');
    await page.waitForSelector('.diff-marker');await page.click('.diff-marker');
    await page.waitForSelector('.diff-popover-card');
    assert((await page.$eval('.dc-evidence',n=>n.textContent)).includes('未验证'),'missing scene evidence should be explicit');
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
    await page.waitForSelector('.approval-actions button', {timeout:15000});
    assert.equal(await page.evaluate(()=>window.__viewer.getRois().length),0,'unreviewed masks must stay staged');
    await page.screenshot({path:path.resolve(__dirname,'artifacts/auto-mask-review.png'),fullPage:false});
    for (let i=0; i<200; i++) {
      const approve = await page.$('.approval-actions button:first-child:not([aria-pressed="true"])');
      if (!approve) break;
      await approve.click();
      await new Promise(r=>setTimeout(r,50));
    }
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
    await page.waitForFunction(()=>document.body.textContent.includes('LTD 独有候选像素'),{timeout:30000});
    assert(responses.some(r=>r.comparison?.summary.length===3));
    await page.$eval('.compare-row',n=>n.closest('details').open=true);
    await page.click('[data-e2e="diff-three-open"]');
    await page.waitForSelector('[data-e2e="diff-three-views"] canvas');
    await page.waitForFunction(()=>!document.querySelector('[data-e2e="diff-three-views"]')?.textContent.includes('正在解码'));
    assert.equal(await page.$$eval('.three-views canvas',xs=>xs.length),3);
    const initialTransform=await page.$eval('.three-views canvas',x=>x.dataset.viewTransform);
    const middle=await page.$('.three-views .method-pane:nth-child(2) canvas');const middleBox=await middle.boundingBox();
    await page.mouse.move(middleBox.x+middleBox.width/2,middleBox.y+middleBox.height/2);await page.mouse.wheel({deltaY:-200});
    await page.waitForFunction(before=>document.querySelector('.three-views canvas').dataset.viewTransform!==before,{},initialTransform);
    let transforms=await page.$$eval('.three-views canvas',xs=>xs.map(x=>x.dataset.viewTransform));assert.equal(new Set(transforms).size,1,'three zoom views diverged');
    const zoomTransform=transforms[0];
    await page.mouse.down();await page.mouse.move(middleBox.x+middleBox.width/2+35,middleBox.y+middleBox.height/2+20,{steps:4});await page.mouse.up();
    transforms=await page.$$eval('.three-views canvas',xs=>xs.map(x=>x.dataset.viewTransform));assert.equal(new Set(transforms).size,1);assert.notEqual(transforms[0],zoomTransform,'pan did not update shared camera');
    assert.equal(requests,3,'three-view gestures reran analysis');
    fs.mkdirSync(path.resolve(__dirname,'../test-tifs/smart-compare'),{recursive:true});
    await page.screenshot({path:path.resolve(__dirname,'../test-tifs/smart-compare/three-views.png'),fullPage:true});
    await page.click('.three-views header button:last-child');
    await page.click('[data-e2e="diff-open"]');await page.$eval('.compare-row',n=>n.closest('details').open=true);
    await page.$eval('details.diff-advanced',n=>n.open=true);
    await page.click('.compare-row button');
    await page.waitForFunction(()=>document.querySelector('.diff-summary')?.textContent.includes('像素差分'));
    assert.equal(requests,3,'switching cached comparison reran detector');
    const comparisonButtons=await page.$$('.compare-row button');await comparisonButtons[2].click();
    await page.waitForFunction(()=>document.querySelector('.diff-summary')?.textContent.includes('LTD'));
    // Native supplement is a score overview and must say so in the actual UI.
    const buttons=await page.$$('details.diff-advanced button');
    for(const button of buttons)if((await button.evaluate(n=>n.textContent)).includes('原始尺度补扫'))await button.click();
    await page.waitForFunction(()=>document.querySelector('.diff-summary')?.textContent.includes('原始尺度补扫概览'),{timeout:30000});
    assert(responses.some(r=>r.source==='source-tiled' && r.regions.every(x=>!x.maskEligible)));
    const shot=path.resolve(__dirname,'../test-tifs/smart-compare');fs.mkdirSync(shot,{recursive:true});
    await page.screenshot({path:path.join(shot,'viewer.png'),fullPage:true});
    assert.equal(errors.length,0,errors.join('\n'));
    console.log(JSON.stringify({source:'real-http-synthetic-tiff',requests,gridRegions:responses[0].regions.length,roi:true,maskTransaction:true,maskUndo:true,comparison:true,threeViewsSharedZoomAndPan:true,nativeScanOverview:true,unverifiedSceneEvidence:true,displayWithoutReanalysis:true}));
  }finally{await browser.close();server.closeAllConnections();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
