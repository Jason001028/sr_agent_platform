// Run in the built frontend/tests/local-load.html fixture using browser eval.
(async () => {
  const fixture = window.localLoadTest;
  const phases = ['\u8bfb\u53d6 TIFF \u5c5e\u6027', '\u7a00\u758f\u6761\u5e26', '\u7edf\u8ba1\u50cf\u7d20',
    '\u62c9\u4f38\u9884\u89c8', '\u751f\u6210\u672c\u5730 JPG', '\u88c5\u8f7d\u672c\u5730\u9884\u89c8'];
  const until = async predicate => {
    const end = Date.now() + 20000;
    while (!predicate()) {
      if (Date.now() > end) throw Error('timeout ' + JSON.stringify(fixture.status()));
      await new Promise(resolve => setTimeout(resolve, 25));
    }
  };
  const results = [];
  for (const phase of phases) {
    fixture.start(true, phase);
    await until(() => fixture.store.imageLoad.state === 'paused');
    if (!fixture.store.imageLoad.stage.includes(phase)) throw Error('wrong pause stage');
    // Final pixels are intentionally published BEFORE background JPG encoding.
    if (!phase.includes('JPG') && fixture.store.recs[0].src) throw Error('paused job published final pixels');
    fixture.store.resumeImageLoad();
    await until(() => !fixture.store.imageLoad.active);
    const rec = fixture.store.recs[0];
    if (rec.route !== 'sparse' || !rec.localPreviewJpeg || !rec.serverSourceVerified) {
      throw Error('invalid result ' + JSON.stringify(fixture.status()));
    }
    results.push({ phase, paused: true, verified: true, route: rec.route, jpegBytes: rec.localPreviewJpeg.size });
  }
  fixture.start(false, phases[1]);
  await until(() => fixture.store.imageLoad.state === 'paused');
  const id = fixture.store.recs[0].id;
  fixture.store.stopImageLoad();
  await until(() => !fixture.store.imageLoad.active);
  if (fixture.store.recs[0].src || fixture.store.recs[0].localPreviewJpeg) throw Error('stopped job published');
  fixture.pauseAt = '';
  await fixture.store.activate(id);
  if (!fixture.store.recs[0].localPreviewJpeg || fixture.store.recs[0].sceneId !== null) throw Error('retry or local identity failed');
  return window.localLoadReport = { phases: results, stopAndRetry: true, unlinkedStaysLocal: true };
})()
