// Evaluate against the production-built /tests/local-load.html fixture.
(async () => {
  const f = window.localLoadTest;
  const until = async predicate => {
    const deadline = performance.now() + 30000;
    while (!predicate()) {
      if (performance.now() > deadline) throw Error('timeout '+JSON.stringify(f.status()));
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  };
  f.pauseOnFrame = 2; f.start(true);
  await until(() => f.store.imageLoad.state === 'paused');
  const rec = f.store.recs[0], canvas = rec.progressivePreview;
  if (!canvas || rec.src || f.frames < 2) throw Error('no incremental preview before final DN');
  const ctx = canvas.getContext('2d'), before = ctx.getImageData(0,0,canvas.width,canvas.height).data;
  await new Promise(resolve => setTimeout(resolve, 150));
  const after = ctx.getImageData(0,0,canvas.width,canvas.height).data;
  if (before.some((value,i) => value !== after[i])) throw Error('paused preview mutated');
  f.store.resumeImageLoad();
  await until(() => !f.store.imageLoad.active);
  if (!rec.src || !rec.localPreviewJpeg || !rec.serverSourceVerified || rec.progressivePreview) throw Error('bad completed preview');
  if (!(f.firstFrameMs < f.pixelsMs) || f.frames < 2) throw Error('not progressive');
  const result = { frames:f.frames, firstFrameMs:f.firstFrameMs, pixelsMs:f.pixelsMs, jpegBytes:rec.localPreviewJpeg.size,
    verified:true, pauseStable:true };
  // Publishing final pixels must not wait for JPG export.
  f.pauseOnFrame=0; f.start(true,'\u751f\u6210\u672c\u5730 JPG');
  await until(() => f.store.imageLoad.state === 'paused');
  if (!f.store.recs[0].src || f.store.recs[0].localPreviewJpeg) throw Error('JPG blocks display');
  f.store.stopImageLoad(); await until(() => !f.store.imageLoad.active);
  if (!f.store.recs[0].src) throw Error('stopping export removed completed image');
  // Stop during row backfill must not publish final DN or late frames.
  f.pauseOnFrame=2; f.start(false);
  await until(() => f.store.imageLoad.state === 'paused');
  const stopped = f.store.recs[0], savedCanvas = stopped.progressivePreview;
  f.store.stopImageLoad(); await until(() => !f.store.imageLoad.active);
  await new Promise(resolve=>setTimeout(resolve,100));
  if (stopped.src || stopped.localPreviewJpeg || stopped.progressivePreview !== savedCanvas) throw Error('late stopped result');
  f.pauseOnFrame=0; f.pauseAt=''; await f.store.activate(stopped.id);
  if (!stopped.src || !stopped.localPreviewJpeg || stopped.sceneId) throw Error('retry failed or identity invented');
  // Decode a real DEFLATE / uint16 / 64-row-strip TIFF through the other route.
  const compressed = await (await fetch('/tests/deflate-rows.tif')).blob();
  f.open(new File([compressed],'deflate-rows.tif'),1);
  await until(() => !f.store.imageLoad.active);
  const c = f.store.recs[0];
  if(c.route!=='chunked'||!c.localPreviewJpeg||f.frames!==0)throw Error('compressed route repeated a preview pass '+JSON.stringify(f.status()));
  for(const [x,y] of [[0,0],[2048,2048],[19,64],[64,256],[113,1024]]){
    if(c.src[y*2049+x]!==((y*7+x*3)&65535))throw Error('compressed DN mismatch');
  }
  return window.progressiveLoadReport={...result, pixelsBeforeJpg:true, stoppedExportKeepsPixels:true, stopAndRetry:true,
    compressed:{route:c.route,frames:f.frames,pixelsMs:f.pixelsMs,dnSamplesCorrect:true,noExtraPreviewPass:true}};
})()
