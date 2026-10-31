const assert = require('node:assert/strict');
const { findBrowser } = require('./launchBrowser');
const puppeteer = require('puppeteer-core');
(async () => {
 // Puppeteer's headless default hides scrollbars, masking the original bug.
 const browser = await puppeteer.launch({ executablePath: findBrowser(), headless: true,
  ignoreDefaultArgs: ['--hide-scrollbars'], args: ['--no-sandbox','--disable-features=OverlayScrollbar,FluentOverlayScrollbar'] });
 try {
  const page = await browser.newPage();
  await page.setViewport({width:1366,height:768});
  await page.goto(process.env.SR_UI_URL || 'http://127.0.0.1:4173/viewer');
  await page.waitForSelector('.ft-pill');
  if(process.env.SR_LAYOUT_BASELINE) await page.addStyleTag({content:'.sidebar { scrollbar-gutter: auto !important; }'});
  await page.evaluate(async () => {
   const canvas=document.createElement('canvas'); canvas.width=canvas.height=32;
   canvas.getContext('2d').fillRect(0,0,32,32);
   const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg'));
   const dt=new DataTransfer();
   for(let i=0;i<12;i++)dt.items.add(new File([blob],`layout-${i}.jpg`,{type:'image/jpeg'}));
   const input=document.querySelector('input[type=file]');input.files=dt.files;
   input.dispatchEvent(new Event('change',{bubbles:true}));
  });
  await page.waitForFunction(()=>window.__viewer?.recs().length===12 && !window.__viewer.overlayVisible());
  await new Promise(r=>setTimeout(r,1000));
  await page.$eval('.sidebar',e=>e.scrollTop=0);
  const measure=()=>page.evaluate(()=>{
   const side=document.querySelector('.sidebar'), pill=document.querySelector('.ft-pill');
   const r=pill.getBoundingClientRect();
   return {x:r.x,width:r.width,clientWidth:side.clientWidth,scrolling:side.scrollHeight>side.clientHeight};
  });
  const before=await measure();
  assert.equal(before.scrolling,true);
  await page.$eval('[data-e2e=ft-clear-all]',e=>e.click());
  await page.waitForFunction(()=>window.__viewer.recs().length===0);
  const cleared=await measure();
  assert.equal(cleared.scrolling,false);
  assert.equal(cleared.x,before.x,'clear must not shift capsule');
  assert.equal(cleared.width,before.width);
  assert.equal(cleared.clientWidth,before.clientWidth);
  await page.$eval('[data-e2e=ft-undo]',e=>e.click());
  await page.waitForFunction(()=>window.__viewer.recs().length===12);
  const restored=await measure();
  assert.equal(restored.scrolling,true);
  assert.equal(restored.x,before.x,'undo must not shift capsule');
  assert.equal(restored.width,before.width);
  console.log(JSON.stringify({passed:true,before,cleared,restored}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
