const assert = require('assert');
const {launchPage}=require('./launchBrowser');
const base=process.env.SR_EVIDENCE_E2E_URL||'http://127.0.0.1:8797';
const report=process.env.SR_EVIDENCE_REPORT_ID;
(async()=>{
 if(!report)throw new Error('SR_EVIDENCE_REPORT_ID required');
 const {browser,page,errors}=await launchPage();
 try{
  await page.goto(`${base}/repair-reports/${report}`);
  await page.waitForSelector('article');
  assert((await page.$$('article')).length<=30);
  const labels=await page.$$eval('article:first-of-type figcaption',nodes=>nodes.map(n=>n.textContent));
  assert.deepStrictEqual(labels,['NOSR','修复产物']);
  await page.$$eval('article:first-of-type button',buttons=>buttons.find(b=>b.textContent.includes('展开')).click());
  await page.waitForFunction(()=>document.body.textContent.includes('修复前 PAN'));
  await page.reload();await page.waitForSelector('article');
  await page.setViewport({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'.e2e/repair-evidence-preview.png',fullPage:true});
  assert.deepStrictEqual(errors,[]);
  console.log('PASS evidence rows, role order, before expansion, refresh, narrow layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
