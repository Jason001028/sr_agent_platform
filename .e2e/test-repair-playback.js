// Built UI, real Viewer camera/SVG, synthetic read-only batch endpoints.
const assert = require('assert/strict');
const http = require('http');
const fs = require('fs');
const path = require('path');
const { launchBrowser } = require('./launchBrowser');
const dist = path.resolve(__dirname, '../frontend/dist');
const artifacts = path.join(__dirname, 'artifacts');
const calls = [], forbidden = [];
const rows = [1,2,3].map(ordinal=>({ item_id:`i${ordinal}`,ordinal,name:`Synthetic PAN ${ordinal}`,
  status:'MASK_READY',version:1,checkpoint:{target_image:{path:`/${ordinal}.tif`},
    detection:{candidates:[{}],provenance:'real'},mask:{}} }));
const server = http.createServer((req,res)=>{
  const u = new URL(req.url,'http://localhost');
  res.setHeader('Content-Type','application/json');
  if(u.pathname==='/api/goals/playback/items') {
    const after=Number(u.searchParams.get('after_ordinal'));
    calls.push(after);res.end(JSON.stringify({items:rows.filter(r=>r.ordinal>after),next_after_ordinal:0}));return;
  }
  const overlay=u.pathname.match(/^\/api\/goals\/playback\/items\/(i\d)\/overlay$/);
  if(overlay){
    res.end(JSON.stringify({contract_version:'overlay.v1',goal_id:'playback',item_id:overlay[1],
      provenance:'real',source:{image_key:`fixture-${overlay[1]}`,revision:'fixture',width:1000,height:1000},
      candidates:[{candidate_id:'r1',kind:'polygon',points:[[100,100],[245,100],[245,245],[100,245]]},
        {candidate_id:'r2',kind:'polygon',points:[[650,600],[795,600],[795,745],[650,745]]}],
      overlay:{url:'/overlay.png',width:1000,height:1000}}));return;
  }
  if(u.pathname.startsWith('/api/')) {
    // Path resolution is read-only despite using POST. Writes/submissions are forbidden.
    if(req.method!=='GET' && u.pathname!=='/api/scenes/resolve')forbidden.push(req.method+' '+u.pathname);
    res.statusCode=404;res.end('{}');return;
  }
  let file=path.join(dist,decodeURIComponent(u.pathname));
  if(!fs.existsSync(file)||fs.statSync(file).isDirectory())file=path.join(dist,'index.html');
  res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');
  res.end(fs.readFileSync(file));
});
let browser;
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  try {
    browser=await launchBrowser();const page=await browser.newPage();const errors=[];
    page.on('pageerror',e=>errors.push(e.stack));await page.setViewport({width:1440,height:1000});
    await page.goto(`http://127.0.0.1:${server.address().port}/viewer`);
    await page.waitForSelector('[data-e2e=diff-open]');
    await page.evaluate(async()=>{
      const pinia=document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
      const v=pinia._s.get('viewer'),b=pinia._s.get('batchGoal'),p=pinia._s.get('batchPlayback'),s=pinia._s.get('scenes');
      window.stores={v,b,p,s};window.opens=[];window.preloads=[];window.failures=0;
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1000;
      const ctx=canvas.getContext('2d');ctx.fillStyle='#536b65';ctx.fillRect(0,0,1000,1000);
      ctx.strokeStyle='#809b8c';ctx.lineWidth=2;
      for(let x=0;x<1000;x+=40){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,1000);ctx.stroke();ctx.beginPath();ctx.moveTo(0,x);ctx.lineTo(1000,x);ctx.stroke();}
      const blob=await new Promise(r=>canvas.toBlob(r,'image/jpeg'));
      v.addFiles([1,2,3].map(i=>new File([blob],`Synthetic PAN ${i}.jpg`)));
    });
    await page.waitForFunction(()=>stores.v.recs.length===3&&stores.v.recs.every(r=>r.thumb&&r.src));
    await page.evaluate(()=>{
      const {v,b,s}=stores;
      v.hideModal();
      v.recs.forEach(r=>{r.spatialIdentity=`fixture-i${r.name.match(/PAN (\d)/)[1]}`;r.spatialIdentityToken=r.token;});
      s.preloadQuiet=async path=>{preloads.push(path);};
      s.openQuiet=async(path,_origin,opts)=>{
        opens.push(path);
        if(path==='/2.tif'&&++failures<=2)return 'Synthetic missing image';
        if(opts.stillWanted())await v.activate(v.recs.find(r=>r.name.includes(`PAN ${path[1]}.`)).id,undefined,'follow');
        v.hideModal();
        return '';
      };
      b.goalId='playback';b.setFollow(true);
    });
    await page.waitForSelector('.repair-vectors polygon.tracing');
    await page.waitForFunction(()=>parseFloat(getComputedStyle(document.querySelector('.repair-vectors polygon.tracing')).strokeDashoffset)<.3);
    assert.equal(await page.evaluate(()=>stores.p.ordinal),1);
    await fs.promises.mkdir(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,'repair-playback-drawing.png')});
    await page.evaluate(()=>stores.b.clearOverlay());
    await page.waitForFunction(()=>!stores.p.running);
    assert.equal(await page.evaluate(()=>stores.p.doc),null,'close clears playback geometry');
    assert.equal(await page.$('.repair-vectors'),null,'close hides active animation');
    assert.equal(await page.evaluate(()=>stores.p.completed),0,'interrupted scene is not consumed');
    await page.click('.repair-toggle');
    try { await page.waitForFunction(()=>stores.p.completed===3,{timeout:25000}); }
    catch(e) { console.error(await page.evaluate(()=>({phase:stores.p.phase,note:stores.p.note,follow:stores.b.follow,opens,preloads,completed:stores.p.completed,errors:stores.p.running}))); throw e; }
    await page.evaluate(()=>stores.b.setFollow(false));
    await page.evaluate(()=>stores.b.clearOverlay());
    assert.equal(await page.$('.repair-vectors'),null,'close also hides paused result contours');
    assert.deepEqual(await page.evaluate(()=>opens),['/1.tif','/1.tif','/2.tif','/2.tif','/3.tif']);
    assert.ok(await page.evaluate(()=>preloads.includes('/2.tif')&&preloads.includes('/3.tif')));
    assert.ok(calls.includes(1)&&calls.includes(2),'cursor advances only after presentation');
    assert.equal(await page.evaluate(()=>stores.v.recs.some(r=>r.maskRois?.length)),false,'playback never writes editable masks');
    assert.deepEqual(errors,[]);assert.deepEqual(forbidden,[]);
    const report={opens:await page.evaluate(()=>opens),preloads:await page.evaluate(()=>preloads),calls,errors,forbidden};
    await fs.promises.writeFile(path.join(artifacts,'repair-playback-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {if(browser)await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
