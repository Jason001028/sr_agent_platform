(async () => {
  const f=window.localLoadTest;
  const until=async predicate=>{
    const end=performance.now()+15000;
    while(!predicate()){if(performance.now()>end)throw Error('timeout');await new Promise(resolve=>setTimeout(resolve,20));}
  };
  f.pauseOnFrame=2; f.start(true);
  await until(()=>f.store.imageLoad.state==='paused');
  if(!f.store.recs[0].progressivePreview||f.store.recs[0].src)throw Error('not a provisional overview');
  const canvas=document.querySelector('.view-canvas'),r=canvas.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
  const scale=f.store.view.scale;
  canvas.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,clientX:x,clientY:y,bubbles:true,cancelable:true}));
  if(!(f.store.view.scale>scale))throw Error('overview zoom failed');
  const ox=f.store.view.ox,oy=f.store.view.oy;
  canvas.dispatchEvent(new MouseEvent('mousedown',{button:0,clientX:x,clientY:y,bubbles:true,cancelable:true}));
  window.dispatchEvent(new MouseEvent('mousemove',{buttons:1,clientX:x+20,clientY:y+10,bubbles:true}));
  window.dispatchEvent(new MouseEvent('mouseup',{button:0,clientX:x+20,clientY:y+10,bubbles:true}));
  if(f.store.view.ox!==ox+20||f.store.view.oy!==oy+10)throw Error('overview pan failed');
  const before={...f.store.view};
  const beforeWidth=f.store.recs[0].progressivePreview.width;
  f.pauseOnFrame=0; f.store.resumeImageLoad();
  await until(()=>!f.store.imageLoad.active);
  if(!f.store.recs[0].src)throw Error('no final DN');
  const expectedScale=before.scale*beforeWidth/f.store.recs[0].thumb.width;
  if(Math.abs(f.store.view.scale-expectedScale)>1e-8)throw Error('source scale reset');
  for(const key of ['ox','oy'])if(Math.abs(f.store.view[key]-before[key])>1e-8)throw Error('view reset '+key);
  return window.progressiveNavigationReport={zoom:true,pan:true,finalDnStillUnpublished:true,viewPreservedAfterFinal:true};
})()
