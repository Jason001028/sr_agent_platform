// Run in the production-built image-open fixture against the real API fixture.
(async()=>{
  const f=window.imageOpenTest,api='http://127.0.0.1:8766';
  const until=async predicate=>{const end=performance.now()+30000;while(!predicate()){if(performance.now()>end)throw Error('timeout '+f.store.error);await new Promise(resolve=>setTimeout(resolve,20));}};
  const clickText=(selector,text)=>{const el=[...document.querySelectorAll(selector)].find(el=>el.textContent.includes(text));if(!el)throw Error('missing '+text);el.click();};
  f.store.clearRecs();f.store.setPreviewDiv(2);
  const ordinaryInput=document.querySelector('.toolbar input[type=file]');
  let pickerClicks=0;const originalClick=ordinaryInput.click;
  ordinaryInput.click=()=>{pickerClicks++;};
  document.querySelector('.toolbar .btn').click();ordinaryInput.click=originalClick;
  if(pickerClicks!==1||document.querySelector('dialog[open]')||f.requests.some(r=>r.url.includes('/image-open/tickets')))throw Error('default chooser opened component dialog or session');
  const blob=await(await fetch(api+'/fixture/source.tif')).blob(),file=new File([blob],'native-source.tif',{type:'image/tiff'});
  f.requests.length=0;f.pauseAt='上传影像至服务端';f.pausedOnce=false;
  const input=document.querySelector('input[type=file]'),dt=new DataTransfer();dt.items.add(file);input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));
  await until(()=>f.store.imageLoad.state==='paused');
  if(f.requests.some(r=>r.method==='PUT'))throw Error('upload started while paused');
  f.store.resumeImageLoad();await until(()=>!f.store.imageLoad.active&&f.store.recs[0]?.route==='jpg');f.pauseAt='';
  const uploaded=f.store.recs[0],chunks=f.requests.filter(r=>r.method==='PUT');
  if(!uploaded.previewAsset?.uploaded||uploaded.sceneId||uploaded.lqPath||!uploaded.previewOnly)throw Error('upload bypassed or invented scene authority');
  if(chunks.length!==3||chunks.some(r=>r.bytes>4194304))throw Error('upload not bounded');
  const assetId=uploaded.previewAsset.id;
  f.store.setPreviewDiv(4);await until(()=>!f.store.imageLoad.active&&uploaded.detailDiv===4);
  if(uploaded.previewAsset.id!==assetId||f.requests.filter(r=>r.method==='PUT').length!==3)throw Error('tier change reuploaded');
  const result={browserUpload:{bytes:file.size,chunks:chunks.length,maxChunk:Math.max(...chunks.map(r=>r.bytes)),serverJpg:true,sceneId:null,pauseBeforeUpload:true,changeTierWithoutUpload:true}};
  // Fixture capture prevents an actual OS protocol launch, then submits the
  // native component's exact selection callback to the real server.
  f.store.setSettingsOpen(true);await until(()=>document.querySelector('[data-e2e="set-image-picker"]'));
  if(!document.querySelector('[data-e2e="set-image-picker"] a[download="SrExplorer-Setup.zip"]'))throw Error('missing optional installer in settings');
  clickText('[data-e2e="set-image-picker"] button','使用组件选择影像');
  await until(()=>document.querySelector('dialog[open] a[href^="sr-explorer://pick/"]'));
  document.querySelector('dialog[open] a[href^="sr-explorer://pick/"]').click();
  const selection=await f.nativeSelect();if(selection.files[0].received!==0||selection.files[0].state!=='ready')throw Error('native path uploaded unnecessarily');
  await until(()=>f.store.recs.length===2&&f.store.recs[1]?.route==='jpg'&&!f.store.imageLoad.active);
  const direct=f.store.recs[1];if(direct.previewAsset.uploaded||direct.previewAsset.clientPath!=='X:\\native-source.tif'||direct.lqPath)throw Error('wrong native provenance');
  const before=await(await fetch(api+'/fixture/stats')).json();
  await(await fetch(`${api}/api/image-open/assets/${direct.previewAsset.id}/preview?div=4`)).blob();
  const after=await(await fetch(api+'/fixture/stats')).json();
  if(after.decodes!==before.decodes||after.sourceFiles.some(name=>name!=='native-source.tif'))throw Error('cache hit decoded TIFF or wrote source directory');
  result.nativePath={clientPath:direct.previewAsset.clientPath,uploadedBytes:0,serverJpg:true,cacheHitWithoutDecode:true,sourceDirectoryUntouched:true};
  // Stop is final: no fallback decoder may publish after cancelled upload.
  f.pauseAt='上传影像至服务端';f.pausedOnce=false;f.store.addFiles(new File([blob],'cancelled.tif',{type:'image/tiff'}));
  await until(()=>f.store.imageLoad.state==='paused');const stopped=f.store.recs.at(-1);
  f.store.stopImageLoad();await until(()=>!f.store.imageLoad.active);
  if(stopped.src||stopped.thumb)throw Error('stopped upload launched fallback');
  f.store.removeRec(stopped.id);f.pauseAt='';
  return window.imageOpenReport={...result,defaultOrdinaryChooserWithoutDialog:true,componentAvailableInSettings:true,stopDoesNotFallback:true};
})()
