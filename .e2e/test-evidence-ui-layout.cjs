// Build: cd frontend; npx vite build --config vite.evidence-ui.config.ts
// Serve work/evidence-ui-preview on 127.0.0.1:5180, then run this script from the repo root.
const {launchPage}=require('./launchBrowser');
const assert=require('assert');
(async()=>{const {browser,page,errors}=await launchPage();try{await page.setViewport({width:1440,height:1000});await page.goto('http://127.0.0.1:5180/tests/evidence-ui-preview.html');await page.waitForSelector('article');assert.equal(await page.$$eval('article',n=>n.length),5);assert(await page.$eval('.evidence-report',e=>e.scrollWidth<=e.clientWidth));await page.click('button[aria-label="下一页"]');await page.waitForFunction(()=>document.querySelector('.pager').textContent.includes('06'));await page.$eval('.expand',e=>e.click());await page.waitForSelector('.is-wide');assert(await page.$eval('.is-wide .pager',e=>e.textContent.includes('06')));assert(await page.$eval('.is-wide',e=>e.scrollWidth<=e.clientWidth));await page.screenshot({path:'.e2e/evidence-ui-wide.png'});await page.$eval('.is-wide .expand',e=>e.click());assert(await page.$eval('.pager',e=>e.textContent.includes('06')));await page.setViewport({width:400,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'.e2e/evidence-ui-compact.png'});assert.deepEqual(errors,[]);console.log('PASS 5 rows, narrow overflow, paging, expand/collapse state');}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});



