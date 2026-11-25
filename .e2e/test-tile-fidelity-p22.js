// Real Tile API and production-built Viewer fixture. See docs/status/tile-fidelity-p22.md.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { launchBrowser } = require('./launchBrowser');

async function main() {
  const api = process.env.AUDIT_API || 'http://127.0.0.1:8766';
  const fixture = process.env.AUDIT_FIXTURE || 'http://127.0.0.1:5179/tests/image-open.html?tileDebug=1';
  const out = process.argv[2] || 'docs/status/tile-fidelity-p22';
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setViewport({ width:1440, height:880, deviceScaleFactor:1 });
    await page.goto(fixture);
    await page.waitForFunction('!!window.imageOpenTest');
    const rows = await page.evaluate(async api => (await fetch(api+'/fixture/tile-scenes')).json(), api);
    const ready = (level = 0) => page.waitForFunction(l => {
      const state = JSON.parse(document.querySelector('.view-canvas').dataset.tilePresentation || '[]')[0];
      return state?.readyLOD === l && state.visible === state.drawn && state.visible > 0;
    }, { timeout:60000 }, level);
    await page.evaluate(async row => {
      const f = window.imageOpenTest;
      window.p22 = { start:performance.now(), held:0, released:0, frames:[] };
      const error = await f.openScene(row);
      if (error) throw Error(error);
      const s=f.store, rec=s.activeRec;
      if (!rec.tileSource.info.displayProfiles) throw Error('Missing display profiles');
      s.zoomNative();
      const p=s.panes[0], v=p.view;
      s.setPaneView('A',{scale:v.scale,ox:p.rect.w/2-(24576+512)*rec.thumb.width/rec.W*v.scale,
        oy:p.rect.h/2-(24576+490)*rec.thumb.height/rec.H*v.scale});
      window.p22.firstNativeStart=performance.now();
    }, rows[0]);
    await ready();
    const first = await page.evaluate(()=>({
      openToNativeMs:performance.now()-window.p22.start,
      zoomToNativeMs:performance.now()-window.p22.firstNativeStart,
    }));
    await fs.mkdir(out,{recursive:true});
    await page.screenshot({path:path.join(out,'viewer-before-default.png')});
    await page.evaluate(() => {
      const original=window.fetch;
      window.fetch=async (url,opts)=>{
        const isOld=String(url).includes('window%3A2.16000000000');
        if(!isOld) return original(url,opts);
        // Intentionally allow a genuinely stale response to finish after cancellation.
        window.p22.held++;
        const response=await original(url,{...opts,signal:undefined});
        await new Promise(r=>setTimeout(r,900));
        window.p22.released++;
        return response;
      };
      window.imageOpenTest.store.setStretchBounds(21600,21800);
    });
    await page.waitForFunction('window.p22.held>0');
    const beforeManual=await page.evaluate(() => {
      window.imageOpenTest.store.setStretchBounds(21700,21706);
      return performance.now();
    });
    await ready();
    await page.waitForFunction('window.p22.released>0');
    await ready();
    const native = await page.evaluate(start=>{
      const cv=document.querySelector('.view-canvas');
      const {rect}=window.imageOpenTest.store.panes[0];
      const pixels=cv.getContext('2d').getImageData(Math.round(rect.w/2)-3,Math.round(rect.h/2),7,1).data;
      return {gray:Array.from(pixels).filter((_,i)=>i%4===0),
        alpha:Array.from(pixels).filter((_,i)=>i%4===3), manualStableMs:performance.now()-start,
        staleReleased:window.p22.released};
    },beforeManual);
    assert.deepEqual(native.gray,[0,42,85,128,170,212,255]);
    assert.ok(native.alpha.every(value=>value===255));
    await page.screenshot({path:path.join(out,'viewer-after-manual.png')});
    await page.evaluate(async()=>{
      const s=window.imageOpenTest.store;
      for(let i=0;i<4;i++){
        const r=s.panes[0].rect;
        s.onWheel(r.w/2,r.h/2,i%2 ? 2 : .5);
        await new Promise(requestAnimationFrame);
      }
      s.zoomNative();
      let previous=performance.now();
      for(let i=0;i<120;i++){
        const at=await new Promise(requestAnimationFrame);
        window.p22.frames.push(at-previous);
        previous=at;
        s.onPan(i<60 ? -17 : 17,0);
      }
    });
    await ready();
    await page.evaluate(async other=>{
      const f=window.imageOpenTest,s=f.store, a=s.activeRec;
      await f.openAsset(other.id);
      const b=s.activeRec;
      // Synthetic fixture explicitly represents the same calibrated DN domain.
      a.sceneDir=b.sceneDir='/synthetic/p22';
      await s.openPreviewPair(a.id,b.id);
      s.setActiveSide('A');
      s.setStretchLink('shared');
      s.setStretchBounds(21700,21706);
    },rows[1]);
    await page.waitForFunction(() => {
      const s=window.imageOpenTest.store;
      return s.panes.length===2&&s.panes.every(p=>p.rec?.tileDisplay?.includes('2.17060000000e+4'));
    });
    const result=await page.evaluate(()=>{
      const cv=document.querySelector('.view-canvas');
      const frames=[...window.p22.frames].sort((a,b)=>a-b);
      return {panRafP95:frames[Math.ceil(.95*frames.length)-1],panFrames:frames.length,
        peakTileBytes:Number(cv.dataset.tilePeakBytes),sharedManual:true};
    });
    const report={browser:await browser.version(),...first,...native,...result};
    await fs.writeFile(path.join(out,'browser.json'),JSON.stringify(report,null,2)+'\n');
    console.log(report);
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
