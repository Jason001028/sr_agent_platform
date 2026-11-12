(async()=>{
 const f=window.localLoadTest,s=f.store;
 const until=async p=>{const end=performance.now()+30000;while(!p()){if(performance.now()>end)throw Error('timeout '+JSON.stringify(f.status()));await new Promise(r=>setTimeout(r,20));}};
 f.pauseOnFrame=2;f.start(false);await until(()=>s.imageLoad.state==='paused');
 const rec=s.recs[0];if(rec.src||!rec.progressiveDN)throw Error('DN overview missing or published as final pixels');
 const canvas=rec.progressivePreview;
 s.setStretchBounds(80,180,true);const version=rec.displayStretch.version;
 if(!rec.stretchPreview||rec.displayStretch.low!==80)throw Error('overview stretch did not apply');
 s.resumeImageLoad();await until(()=>!s.imageLoad.active&&!!rec.src&&!rec.stretchPreview);
 if(rec.displayStretch.version!==version||rec.displayStretch.low!==80||rec.displayStretch.high!==180)throw Error('final worker pixels replaced user limits');
 const ctx=rec.thumb.getContext('2d');
 for(const fraction of [.1,.3,.5,.7,.9]){
  const x=Math.floor(rec.srcw*fraction),y=Math.floor(rec.srch*fraction),v=rec.src[y*rec.srcw+x];
  const expected=Math.round(Math.max(0,Math.min(255,(v-80)/100*255)));
  const pixel=ctx.getImageData(x,y,1,1).data[0];if(Math.abs(pixel-expected)>1)throw Error('brightness seam or stale mapping '+pixel+' '+expected);
 }
 return window.histogramProgressiveReport={frames:f.frames,versionRetained:true,finalPixelSamples:5,displayOnly:true,width:rec.srcw,height:rec.srch,rawOverviewBytes:1024*1024*4};
})()
