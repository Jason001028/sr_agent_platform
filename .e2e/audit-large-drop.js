// Real Chromium file handles, injected into the existing DOM drop handler.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const puppeteer = require('puppeteer-core');

async function main() {
  const [mapped, unmapped] = process.argv.slice(2);
  assert.ok(mapped && unmapped, 'Pass the mapped TIFF and a differently named local TIFF');
  const browser = await puppeteer.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage();
    await page.setViewport({width:1440,height:880});
    await page.goto('http://127.0.0.1:5179/tests/image-open.html?tileDebug=1');
    await page.waitForFunction(()=>window.imageOpenTest);
    await page.evaluate(()=>{
      const input=document.createElement('input');input.type='file';input.id='audit-file';
      document.body.append(input);
      const original=Blob.prototype.arrayBuffer;
      window.auditReads=[];
      Blob.prototype.arrayBuffer=function(){window.auditReads.push(this.size);return original.call(this);};
    });
    const input=await page.$('#audit-file');
    async function drop(file) {
      await input.uploadFile(file);
      return page.evaluate(()=>{
        const input=document.querySelector('#audit-file'), transfer=new DataTransfer();
        transfer.items.add(input.files[0]);
        document.querySelector('.stage').dispatchEvent(new DragEvent('drop',{
          dataTransfer:transfer,bubbles:true,cancelable:true,clientX:650,clientY:400,
        }));
        return performance.now();
      });
    }
    async function state(start) {
      return page.evaluate(start=>{
        const s=window.imageOpenTest.store, r=s.activeRec;
        return { ms:performance.now()-start,name:r?.name,thumb:r?.thumb&&[r.thumb.width,r.thumb.height],
          tile:!!r?.tileSource,verified:!!r?.serverSourceVerified,route:r?.route,
          uploadBytes:window.imageOpenTest.requests.filter(r=>r.method==='PUT').reduce((n,r)=>n+r.bytes,0),
          largestMainArrayBuffer:Math.max(0,...window.auditReads) };
      },start);
    }
    const serverStart=await drop(mapped);
    await page.waitForFunction(()=>window.imageOpenTest.store.activeRec?.tileSource,{timeout:60000});
    const server=await state(serverStart);
    assert.equal(server.verified,true);assert.equal(server.uploadBytes,0);

    const localStart=await drop(unmapped);
    await page.waitForFunction(()=>{
      const r=window.imageOpenTest.store.activeRec;
      return r?.progressivePreview || (r?.thumb && !r?.tileSource);
    },{timeout:90000});
    const firstLocal=await state(localStart);
    await page.waitForFunction(()=>{
      const s=window.imageOpenTest.store;
      return s.activeRec?.thumb && !s.activeRec.tileSource && !s.imageLoad.active;
    },{timeout:90000});
    const local=await state(localStart);
    assert.equal(local.tile,false);assert.equal(local.verified,false);assert.equal(local.uploadBytes,0);
    assert.ok(Math.max(...local.thumb)<=4096);assert.ok(local.largestMainArrayBuffer<1024**2);

    await page.evaluate(()=>window.imageOpenTest.store.clearRecs());
    await drop(unmapped);
    await page.waitForFunction(()=>window.imageOpenTest.store.imageLoad.active);
    await page.evaluate(()=>window.imageOpenTest.store.stopImageLoad());
    await drop(mapped);
    await page.waitForFunction(()=>{
      const s=window.imageOpenTest.store;
      return !!s.activeRec?.tileSource && !s.imageLoad.active;
    },{timeout:60000});
    const cancelled=await page.evaluate(()=>window.imageOpenTest.store.recs.filter(r=>!r.tileSource)
      .map(r=>({published:!!r.thumb,status:r.status})));
    assert.ok(cancelled.every(r=>!r.published),JSON.stringify(cancelled));

    await page.evaluate(()=>window.imageOpenTest.store.clearRecs());
    await drop(unmapped);
    await page.waitForFunction(()=>window.imageOpenTest.store.imageLoad.active);
    await drop(mapped);
    await page.waitForFunction(()=>{
      const s=window.imageOpenTest.store;
      return !!s.activeRec?.tileSource && !s.imageLoad.active;
    },{timeout:60000});
    const replaced=await page.evaluate(()=>{
      const s=window.imageOpenTest.store;
      return {active:s.activeRec.name, old:s.recs.filter(r=>!r.tileSource)
        .map(r=>({published:!!r.thumb,status:r.status}))};
    });
    assert.ok(replaced.old.length && replaced.old.every(r=>!r.published),JSON.stringify(replaced));
    const report={browser:await browser.version(),server,firstLocal,local,cancelled,replaced};
    if(process.env.AUDIT_DROP_RESULT)await fs.writeFile(process.env.AUDIT_DROP_RESULT,JSON.stringify(report,null,2)+'\n');
    console.log(report);
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
