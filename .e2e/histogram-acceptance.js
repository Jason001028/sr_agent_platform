(async()=>{
 const f=window.histTest,s=f.store;
 const until=async p=>{const end=performance.now()+30000;while(!p()){if(performance.now()>end)throw Error('timeout '+s.error);await new Promise(r=>setTimeout(r,16));}};
 const click=text=>{const b=[...document.querySelectorAll('.hist-panel button')].find(b=>b.textContent.trim()===text);if(!b)throw Error('missing '+text);b.click();};
 const a=s.activeRec,original=a.src.slice(),token=a.token,view=JSON.stringify(s.view),stats=a.stats;
 if(!a.displayStretch?.custom)throw Error('real pointer drag did not create a custom mapping');
 await until(()=>!a.stretchPreview);
 if(a.src.some((v,i)=>v!==original[i])||a.stats!==stats||a.token!==token||JSON.stringify(s.view)!==view)throw Error('display operation changed source identity or Camera');
 click('均衡');await until(()=>a.paintedMode==='equal'&&!a.stretchPreview);
 const second=f.pair();await new Promise(r=>setTimeout(r,50));
 click('共享');s.setStretch('equal');
 if(s.recs[0].displayStretch.stats!==s.recs[1].displayStretch.stats)throw Error('shared CDF differs');
 s.setStretchBounds(2000,5000,true);
 if(s.recs[1].displayStretch.low!==2000||s.recs[1].displayStretch.high!==5000)throw Error('shared limits differ');
 click('独立');s.setStretchBounds(2100,5100,true);
 if(s.recs[1].displayStretch.low!==2000)throw Error('independent setting leaked');
 const total=s.histogramSnapshot('whole').stats[0].total;
 s.setPaneView('A',{scale:2,ox:-400,oy:-200});
 click('当前视野');await new Promise(r=>setTimeout(r,50));
 const visible=s.histogramSnapshot('view');if(!visible||visible.stats[0].total>=total)throw Error('view statistics did not crop cached pixels');
 click('整图');click('恢复默认');if(a.displayStretch.custom)throw Error('reset remained custom');
 const report={actualPointerDrag:true,sourceDnUnchanged:true,sourceStatsUnchanged:true,cameraUnchanged:true,sharedSameCdf:true,independentIsolation:true,viewSampleCount:visible.stats[0].total,wholeSampleCount:total,reset:true};
 // A source-domain mismatch must explicitly prevent shared DN mapping.
 const b=s.recs.find(r=>r.id===second);b.route='jpg';if(!s.sharedStretchReason)throw Error('mixed DN/JPG domain was accepted');b.route='sparse';
 if(!s.openComparePair(a.id,b.id))throw Error('could not enter flicker pair');
 await new Promise(r=>setTimeout(r,30));click('共享');s.setStretchBounds(1800,5200,true);
 if(a.displayStretch.version!==b.displayStretch.version)throw Error('flicker mapping is not shared');
 s.setTourLayer(true);s.setTourLayer(false);report.flickerShared=true;
 click('独立');const aLow=a.displayStretch.low;click('B');s.setStretchBounds(2300,4900,true);
 if(a.displayStretch.low!==aLow||b.displayStretch.low!==2300||!s.flickerShowingAlt)throw Error('independent flicker B adjustment did not isolate its target');
 report.flickerIndependentB=true;click('A');
 const largeId=f.large();await new Promise(r=>setTimeout(r,30));
 const large=s.activeRec,raw=large.src,pose=JSON.stringify(s.view),costs=[];
 for(let i=0;i<12;i++){
  await new Promise(requestAnimationFrame);const start=performance.now();s.setStretchBounds(1300+i*30,5100-i*20,true);costs.push(performance.now()-start);
 }
 if(large.stretchPreview.width*large.stretchPreview.height>512*512)throw Error('drag preview exceeded pixel budget');
 s.finishStretchInteraction(largeId);await until(()=>!large.stretchPreview);
 if(raw!==large.src||JSON.stringify(s.view)!==pose)throw Error('large display update changed DN/Camera');
 report.large={pixels:large.srcw*large.srch,dragFrames:costs.length,maxEventMs:Math.max(...costs),meanEventMs:costs.reduce((a,b)=>a+b,0)/costs.length,previewMaxPixels:512*512,fullPaintFinished:true};
 return window.histogramReport=report;
})()
