// Uses the normal Viewer and P1 API. Run browsers serially for comparable timings.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const { setTimeout: delay } = require('node:timers/promises');
const { execFileSync } = require('node:child_process');
const puppeteer = require('puppeteer-core');

const api = process.env.AUDIT_API || 'http://127.0.0.1:8766';
const fixture = process.env.AUDIT_FIXTURE || 'http://127.0.0.1:5179/tests/image-open.html?tileDebug=1';
const out = process.env.AUDIT_OUT || '.e2e/fixtures/raster-audit-results';
const layouts = (process.env.AUDIT_LAYOUTS || 'strip').split(',');
const modes = (process.env.AUDIT_MODES || 'native').split(',').filter(Boolean);
const seconds = Number(process.env.AUDIT_SECONDS || 10);
const rounds = Number(process.env.AUDIT_ROUNDS || 1);
const dpr = Number(process.env.TILE_DPR || 1);
const tag = process.env.AUDIT_TAG || 'audit';
const sweep = Number(process.env.AUDIT_SWEEP_PX || 130);

function p(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(fraction * sorted.length) - 1)] ?? null;
}

function browserMemory(processes) {
  if (!process.env.AUDIT_PYTHON) return null;
  return JSON.parse(execFileSync(process.env.AUDIT_PYTHON, ['-c',
    'import json,psutil,sys; rows=json.loads(sys.argv[1]); live=[r for r in rows if psutil.pid_exists(r["id"])]; print(json.dumps({kind:sum(psutil.Process(r["id"]).memory_info().rss for r in live if r["type"]==kind) for kind in {r["type"] for r in live}}))',
    JSON.stringify(processes)], { encoding: 'utf8' }));
}

async function instrument(page) {
  await page.evaluate(() => {
    const trace = window.rasterAudit = { requests: [], frames: [], decode: [], draw: [], long: [], coverage: [],
      marks: {}, lastMove: null, pointerPaint: [], bitmapLive: 0, bitmapPeak: 0 };
    const active = ctx => ctx.canvas.classList?.contains('view-canvas');
    const fetchOrig = window.fetch;
    window.fetch = async (url, init = {}) => {
      const str = String(url), start = performance.now();
      const tile = /\/tiles\/(\d+)\/(\d+)\/(\d+)/.exec(str);
      if (/\/preview\?/.test(str)) trace.marks.T2 ??= start;
      if (tile) {
        if (window.imageOpenTest.store.activeRec?.thumb) trace.marks.T4 ??= start;
        else trace.marks.bootstrap ??= start;
      }
      const sample = { tile: tile ? tile.slice(1).map(Number) : null, start, bytes: 0,
        preview: /\/preview\?/.test(str) };
      trace.requests.push(sample);
      init.signal?.addEventListener('abort', () => { if (!sample.end) sample.aborted = true; }, { once: true });
      const response = await fetchOrig(url, init);
      sample.end = performance.now();
      sample.status = response.status;
      sample.bytes = Number(response.headers.get('content-length')) || 0;
      if (str.endsWith('/assets')) trace.marks.T1 = sample.end;
      return response;
    };
    const make = window.createImageBitmap, close = ImageBitmap.prototype.close;
    const bytes = new WeakMap();
    window.createImageBitmap = async (...args) => {
      const t = performance.now(), bmp = await make(...args);
      const n = bmp.width * bmp.height * 4;
      bytes.set(bmp, n);
      trace.bitmapLive += n;
      trace.bitmapPeak = Math.max(trace.bitmapPeak, trace.bitmapLive);
      trace.decode.push({ at: t, ms: performance.now() - t, w: bmp.width, h: bmp.height });
      return bmp;
    };
    ImageBitmap.prototype.close = function () {
      trace.bitmapLive -= bytes.get(this) || 0;
      bytes.delete(this);
      return close.call(this);
    };
    const clear = CanvasRenderingContext2D.prototype.clearRect;
    const draw = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.clearRect = function (...args) {
      if (active(this)) {
        const t = performance.now();
        trace.frames.push(t);
        const layers = JSON.parse(this.canvas.dataset.tiles || '[]').filter(Boolean);
        trace.coverage.push({ at: t, loaded: layers.reduce((n,l)=>n+l.ready,0), total: layers.reduce((n,l)=>n+l.total,0) });
        if (trace.lastMove !== null) {
          trace.pointerPaint.push({ at: t, ms: t - trace.lastMove });
          trace.lastMove = null;
        }
      }
      return clear.apply(this, args);
    };
    CanvasRenderingContext2D.prototype.drawImage = function (...args) {
      if (!active(this)) return draw.apply(this, args);
      const t = performance.now();
      const result = draw.apply(this, args);
      trace.draw.push({ at: t, ms: performance.now() - t, bitmap: args[0] instanceof ImageBitmap });
      if (this.globalAlpha > 0) {
        trace.marks.T3 ??= t;
        if (args[0] instanceof ImageBitmap) trace.marks.T5 ??= t;
      }
      return result;
    };
    window.addEventListener('mousemove', event => {
      if (event.buttons & 1) trace.lastMove = event.timeStamp;
    }, true);
    new PerformanceObserver(list => {
      trace.long.push(...list.getEntries().map(e => ({ at: e.startTime, ms: e.duration })));
    }).observe({ entryTypes: ['longtask'] });
  });
}

async function verifyNative(page) {
  const target = 24576;
  const start = await page.evaluate(target => {
    const s = window.imageOpenTest.store;
    const start = performance.now();
    s.zoomNative();
    const pane = s.panes[0], r = pane.rec, v = pane.view;
    s.setPaneView('A', {scale:v.scale, ox:pane.rect.w/2-target*r.thumb.width/r.W*v.scale,
      oy:pane.rect.h/2-target*r.thumb.height/r.H*v.scale});
    return start;
  }, target);
  const first = await page.waitForFunction(() => {
    const tile = JSON.parse(document.querySelector('.view-canvas').dataset.tiles)[0];
    return tile?.level === 0 && tile.ready > 0 && performance.now();
  }, {timeout:60000}).then(handle=>handle.jsonValue());
  await waitTiles(page, 0);
  const result = await page.evaluate(async ({api,target,start,first}) => {
    const completeMs = performance.now()-start;
    const s = window.imageOpenTest.store, c = document.querySelector('.view-canvas');
    const panel = s.panes[0];
    const native = JSON.parse(c.dataset.tiles)[0];
    const dpr = Math.min(2,devicePixelRatio);
    const px = Math.round(panel.rect.w*dpr/2)-32, py = Math.round(panel.rect.h*dpr/2)-32;
    const image = c.getContext('2d').getImageData(px,py,64,64).data;
    const scratch = document.createElement('canvas');scratch.width=scratch.height=64;
    const ctx = scratch.getContext('2d');
    const center = target/512, ident=s.activeRec.previewAsset.id;
    for (let y=0;y<2;y++) for(let x=0;x<2;x++){
      const response=await fetch(`${api}/api/image-open/assets/${ident}/tiles/0/${center+x-1}/${center+y-1}`);
      const bmp=await createImageBitmap(await response.blob());
      ctx.drawImage(bmp,x===0?480:0,y===0?480:0,32,32,x*32,y*32,32,32);
      bmp.close();
    }
    const expected=ctx.getImageData(0,0,64,64).data;
    let different=0, sum=0;
    for(let i=0;i<image.length;i+=4){
      if(image[i]!==expected[i]||image[i+3]!==255) different++;
      sum+=Math.abs(image[i]-expected[i]);
    }
    return { native, firstReadyMs:first-start, completeMs, checked:4096, different, mae:sum/4096 };
  }, {api,target,start,first});
  assert.equal(result.native.level,0,JSON.stringify(result));
  assert.equal(result.different,0,JSON.stringify(result));
  return result;
}

const waitTiles = (page, expectedLevel = null) => page.waitForFunction(expectedLevel => {
  const cv = document.querySelector('.view-canvas');
  const layers = JSON.parse(cv.dataset.tiles || '[]').filter(Boolean);
  return layers.length && (expectedLevel === null || layers[0].level === expectedLevel)
    && layers.every(t => t.total > 0 && t.ready === t.total) && cv.dataset.tileAnimating === 'false';
}, { timeout: 60000 }, expectedLevel);

async function open(page, row, label) {
  await page.evaluate(async row => {
    const trace = window.rasterAudit;
    trace.marks = { T0: performance.now() };
    const error = await window.imageOpenTest.openScene(row);
    if (error) throw Error(error);
  }, row);
  await waitTiles(page);
  return page.evaluate(label => {
    const trace = window.rasterAudit, rec = window.imageOpenTest.store.activeRec;
    const marks = Object.fromEntries(Object.entries({ ...trace.marks, T6: performance.now() })
      .map(([key, t]) => [key, t - trace.marks.T0]));
    return { label, marks, thumb: [rec.thumb.width,rec.thumb.height], format: rec.previewPlan.mode,
      reads: trace.requests.filter(r => r.start >= trace.marks.T0),
      resources: performance.getEntriesByType('resource')
        .filter(r => r.startTime >= trace.marks.T0 && /\/api\/image-open\/assets/.test(r.name))
        .map(r => ({ start:r.startTime-trace.marks.T0, elapsed:r.duration,
          bodyMs:r.responseStart ? r.responseEnd-r.responseStart : null, transferBytes:r.transferSize })),
      decode: trace.decode.filter(r => r.at >= trace.marks.T0) };
  }, label);
}

async function configure(page, mode, row, other) {
  await page.evaluate(async ({ mode, row, other }) => {
    const f = window.imageOpenTest, s = f.store;
    if (s.compareMode !== 'off') s.setCompareMode('off');
    const image = s.recs.find(r => r.sceneId === row.id);
    await s.activate(image.id);
    s.fit(); s.renderTick++;
    if (mode === 'split') {
      if (!s.recs.some(r => r.W === 2300)) await f.openAsset(other.id);
      const small = s.recs.find(r => r.W === 2300);
      image.sceneDir = small.sceneDir = '/synthetic/audit';
      await s.openPreviewPair(image.id, small.id);
      s.splitLinked = true;
    }
    if (mode !== 'fit' && mode !== 'tour') s.zoomNative();
    if (mode === 'mid') {
      const r = s.panes[0].rect;
      s.onWheel(r.w / 2,r.h / 2,.25);
    }
    if (mode === 'roi' && !s.getRois().length) {
      const t = image.thumb;
      s.pendingRect = { x0:t.width/2-40,y0:t.height/2-40,x1:t.width/2+40,y1:t.height/2+40 };
      s.commitRect();
    }
  }, { mode, row, other });
  await waitTiles(page);
}

async function drag(page, speed, mode) {
  const rect = await page.$eval('.view-canvas', (canvas, sweep) => {
    const r = canvas.getBoundingClientRect();
    const x = Math.min(r.width*.24, Math.max(10, r.width-sweep-20));
    return { x:r.left + x, y:r.top + r.height*.45, width:Math.min(sweep,r.width-x-10) };
  }, sweep);
  const before = await page.evaluate(() => {
    const s = window.imageOpenTest.store, cv = document.querySelector('.view-canvas');
    return { time: performance.now(), views: s.panes.map(p => ({...p.view})), surfaces: [cv.width,cv.height] };
  });
  await page.mouse.move(rect.x,rect.y);
  await page.mouse.down();
  const start = performance.now(), width = rect.width;
  let dx = 0, lastPointer = 0, events = 0;
  while (lastPointer < seconds * 1000) {
    lastPointer = Math.min(performance.now()-start, seconds*1000);
    const travelled = lastPointer * speed / 1000;
    dx = Math.round(width - Math.abs(travelled % (2*width) - width));
    await page.mouse.move(rect.x + dx, rect.y);
    events++;
    await delay(Math.max(0, start + events * 1000 / 60 - performance.now()));
  }
  await page.mouse.up();
  const ended = await page.evaluate(() => performance.now());
  await waitTiles(page);
  const samples = await page.evaluate(({ start, stop }) => {
    const t = window.rasterAudit, s = window.imageOpenTest.store, cv = document.querySelector('.view-canvas');
    return { frames: t.frames.filter(x=>x>=start&&x<=stop), long: t.long.filter(x=>x.at>=start&&x.at<=stop),
      pointer: t.pointerPaint.filter(x=>x.at>=start&&x.at<=stop).map(x=>x.ms),
      draw: t.draw.filter(x=>x.at>=start&&x.at<=stop),
      visible: t.coverage.filter(x=>x.at>=start&&x.at<=stop),
      requests: t.requests.filter(x=>x.tile&&x.start>=start&&x.start<=stop),
      views:s.panes.map(p=>({...p.view})), peak:Number(cv.dataset.tilePeakBytes),
      bitmapPeak:t.bitmapPeak, coverageMs:performance.now()-stop };
  }, { start: before.time, stop: ended });
  const gaps = samples.frames.slice(1).map((t,i) => t-samples.frames[i]);
  const drift = samples.views.map((v,i) => (v.ox-before.views[i].ox)-dx);
  assert.ok(Math.abs(drift[0]) <= 1, JSON.stringify({ drift, mode, speed, dx }));
  return { mode, speed, seconds, events, surfaces:before.surfaces, drift, frameCount:gaps.length,
    fps:gaps.length*1000/gaps.reduce((a,b)=>a+b,0), p50:p(gaps,.5),p95:p(gaps,.95),p99:p(gaps,.99),
    over16:gaps.filter(x=>x>16.7).length/gaps.length, over33:gaps.filter(x=>x>33.3).length/gaps.length,
    longTasks:samples.long.length,longMax:p(samples.long.map(x=>x.ms),1),pointerP95:p(samples.pointer,.95),
    drawCalls:samples.draw.length, drawP95:p(samples.draw.map(x=>x.ms),.95), settleMs:samples.coverageMs,
    tileRequests:samples.requests.length,cancelled:samples.requests.filter(r=>r.aborted).length,
    visibleCacheCoverage:samples.visible.reduce((n,x)=>n+x.loaded,0)/samples.visible.reduce((n,x)=>n+x.total,0),
    peak:samples.peak, decodedBitmapPeak:samples.bitmapPeak };
}

async function main() {
  await fs.mkdir(out,{recursive:true});
  const browser = await puppeteer.launch({ executablePath:process.env.CHROME_PATH,headless:true });
  try {
    const conn = await browser.target().createCDPSession();
    const system = await conn.send('SystemInfo.getInfo');
    const page = await browser.newPage();
    await page.setViewport({width:1440,height:880,deviceScaleFactor:dpr});
    await page.goto(fixture);
    await page.waitForFunction(()=> window.imageOpenTest);
    await instrument(page);
    const rows = await (await fetch(api+'/fixture/audit-scenes')).json();
    const result = {browser:await browser.version(),dpr,tag,viewport:[1440,880],
      gpu:{devices:system.gpu.devices,featureStatus:system.gpu.featureStatus}, opens:[],drags:[],memory:[],native:[] };
    async function memory(label) {
      const processes = (await conn.send('SystemInfo.getProcessInfo')).processInfo;
      const viewer = await page.evaluate(()=>{
        const s=window.imageOpenTest.store, c=document.querySelector('.view-canvas');
        return { liveBitmapBytes:window.rasterAudit.bitmapLive, viewportCanvasBytes:c.width*c.height*4,
          previewInputBytes:s.recs.reduce((sum,r)=>sum+(r.src?.byteLength||0),0),
          previewCanvasBytes:s.recs.reduce((sum,r)=>sum+(r.thumb?r.thumb.width*r.thumb.height*4:0),0) };
      });
      result.memory.push({label,viewer,js:await page.metrics(),browserRss:browserMemory(processes),
        backend:await (await fetch(api+'/fixture/memory')).json()});
    }
    await memory('before');
    for (const layout of layouts) {
      const row = rows.find(r=>r.name===layout+'.tif');
      assert.ok(row,layout);
      await page.evaluate(()=>window.imageOpenTest.store.clearRecs());
      result.opens.push(await open(page,row,layout+'/first'));
      await page.evaluate(()=>window.imageOpenTest.store.clearRecs());
      result.opens.push(await open(page,row,layout+'/repeat'));
      if (process.env.AUDIT_VERIFY_NATIVE === '1') result.native.push({layout,...await verifyNative(page)});
      if (layout!==layouts[0]) continue;
      for (const mode of modes) {
        await configure(page,mode,row,rows.find(r=>r.W===2300));
        if (mode==='tour') {
          const position = await page.evaluate(()=> {
            const {tour,store}=window.imageOpenTest;
            if(!tour.start()) throw Error(tour.reason);
            return {...store.view, at:performance.now()};
          });
          await delay(seconds*1000);
          const tour = await page.evaluate(position=>{
            const {tour,store}=window.imageOpenTest;
            const moved = store.view.ox!==position.ox||store.view.oy!==position.oy||store.view.scale!==position.scale;
            const activeFrames = window.rasterAudit.frames.filter(t=>t>=position.at);
            tour.stop();
            return {moved, frames:activeFrames.slice(1).map((t,i)=>t-activeFrames[i])};
          },position);
          assert.ok(tour.moved);
          result.drags.push({mode,moved:tour.moved,seconds,frameCount:tour.frames.length,
            p50:p(tour.frames,.5),p95:p(tour.frames,.95),p99:p(tour.frames,.99)});
          continue;
        }
        for (let round=1;round<=rounds;round++) for (const speed of [200,800,1600]) {
          const report = {...await drag(page,speed,mode),round};
          result.drags.push(report);
          console.log(tag, mode, speed, report.p95, report.drift);
        }
        if (mode==='native') await page.screenshot({path:path.join(out,tag+'-native.png')});
      }
    }
    await memory('before-clear');
    await page.evaluate(()=>window.imageOpenTest.store.clearRecs());
    await page.waitForFunction(()=>Number(document.querySelector('.view-canvas').dataset.tileBytes)===0);
    await page.createCDPSession().then(c=>c.send('HeapProfiler.collectGarbage'));
    await memory('after-clear');
    const switches = Number(process.env.AUDIT_LIFECYCLE || 0);
    for (let i=0;i<switches;i++) {
      for (const layout of layouts) {
        const row = rows.find(r=>r.name===layout+'.tif');
        await open(page,row,`cycle ${i+1}/${layout}`);
        await verifyNative(page);
        await memory(`cycle ${i+1}/${layout}`);
      }
      for (let j=0;j<18;j++) {
        await page.evaluate(j=>{
          const s=window.imageOpenTest.store, pane=s.panes[0], r=pane.rec, v=pane.view;
          const x=3072+(j%6)*7168, y=3072+Math.floor(j/6)*12288;
          s.setPaneView('A',{scale:v.scale,ox:pane.rect.w/2-x*r.thumb.width/r.W*v.scale,
            oy:pane.rect.h/2-y*r.thumb.height/r.H*v.scale});
        },j);
        await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
        await waitTiles(page,0);
      }
      await memory(`cycle ${i+1}/sweep`);
      assert.ok(result.memory.at(-1).viewer.liveBitmapBytes <= 128 * 1024 ** 2);
      await page.evaluate(()=>window.imageOpenTest.store.clearRecs());
      await page.waitForFunction(()=>Number(document.querySelector('.view-canvas').dataset.tileBytes)===0
        && window.rasterAudit.bitmapLive===0);
      await page.evaluate(()=>{
        const t = window.rasterAudit;
        for (const key of ['frames','requests','decode','draw','long','coverage','pointerPaint']) t[key].length=0;
        performance.clearResourceTimings();
      });
      await page.createCDPSession().then(c=>c.send('HeapProfiler.collectGarbage'));
      await memory(`cycle ${i+1}/clear`);
    }
    await fs.writeFile(path.join(out,tag+'.json'),JSON.stringify(result,null,2)+'\n');
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
