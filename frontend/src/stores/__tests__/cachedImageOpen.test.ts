// @vitest-environment jsdom
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { createApp, nextTick } from 'vue';
import { getActivePinia } from 'pinia';
import DecodeOverlay from '../../components/DecodeOverlay.vue';
vi.mock('../../router/index.js',()=>({default:{}}));
vi.mock('../../lib/api.js',async original=>({...await original<typeof import('../../lib/api.js')>(),apiResolveScene:vi.fn(),fetchSceneJpg:vi.fn(),ensureScenePreviewReady:vi.fn()}));
vi.mock('../../lib/imageOpen',async original=>({...await original<typeof import('../../lib/imageOpen')>(),createPreviewAsset:vi.fn(),uploadPreviewFile:vi.fn(),fetchPreviewAsset:vi.fn()}));
vi.mock('../../lib/local_preview',async original=>({...await original<typeof import('../../lib/local_preview')>(),sourceFingerprint:vi.fn().mockResolvedValue({name:'a.tif',size:3,samples:['','','']})}));
vi.mock('../../lib/localDecodeWorker',()=>({decodeLocalFile:vi.fn()}));
import { createPreviewAsset, uploadPreviewFile, fetchPreviewAsset } from '../../lib/imageOpen';
import type { PreviewAsset } from '../../lib/imageOpen';
import { apiResolveScene,fetchSceneJpg,ensureScenePreviewReady } from '../../lib/api';
import type { SceneResolveResult } from '../../lib/api';
import { decodeLocalFile } from '../../lib/localDecodeWorker';
import { browserKit } from '../../lib/browserKit';
import { sourceFingerprint } from '../../lib/local_preview';
import { useViewerStore } from '../viewer';
const asset=():PreviewAsset=>({id:'a'.repeat(64),name:'a.tif',W:64,H:32,size:3,uploaded:true,serverPath:null,sourceRev:'rev',fingerprint:{name:'a.tif',size:3,samples:['','','']}});
beforeEach(()=>{
  vi.clearAllMocks();localStorage.clear();setActivePinia(createPinia());
  vi.mocked(sourceFingerprint).mockResolvedValue({name:'a.tif',size:3,samples:['','','']});
  vi.stubGlobal('createImageBitmap',vi.fn().mockResolvedValue({width:4,height:2,close(){}}));
  vi.stubGlobal('ImageData',class{constructor(public data:unknown,public width:number,public height:number){}});
  vi.spyOn(browserKit,'createCanvas').mockImplementation((width,height)=>({width,height,getContext:()=>({drawImage(){},putImageData(){},getImageData:()=>({data:new Uint8ClampedArray(width*height*4)})})} as never));
  vi.mocked(apiResolveScene).mockRejectedValue(new Error('not a scene'));
  vi.mocked(uploadPreviewFile).mockResolvedValue(asset());vi.mocked(createPreviewAsset).mockResolvedValue(asset());
  vi.mocked(fetchPreviewAsset).mockResolvedValue({blob:new Blob(['jpeg']),width:4,height:2});
});
afterEach(()=>{useViewerStore().stopImageLoad();useViewerStore().clearRecs();vi.restoreAllMocks();vi.unstubAllGlobals();});
it('opens an unassociated local TIFF through server JPG instead of local sparse decoding',async()=>{
  const store=useViewerStore();store.addFiles(new File(['raw'],'a.tif'));
  await vi.waitFor(()=>expect(store.recs[0]?.route).toBe('jpg'));
  expect(uploadPreviewFile).toHaveBeenCalledOnce();expect(decodeLocalFile).not.toHaveBeenCalled();
  expect(store.recs[0].sceneId).toBeNull();expect(store.recs[0].lqPath).toBeNull();expect(store.recs[0].previewOnly).toBe(true);
  expect(store.recs[0].previewAsset?.id).toBe(asset().id);
});
it('native picker assets support cache-based tier changes without a scene identity',async()=>{
  const store=useViewerStore();await store.openPreviewAsset(asset());
  const rec=store.recs[0];expect(rec.sceneId).toBeNull();expect(rec.previewOnly).toBe(true);
  const div=store.previewDiv===2?4:2;store.setPreviewDiv(div);
  await vi.waitFor(()=>expect(rec.detailDiv).toBe(div));
  expect(fetchPreviewAsset).toHaveBeenLastCalledWith(expect.anything(),div,expect.any(AbortSignal));
  expect(uploadPreviewFile).not.toHaveBeenCalled();expect(decodeLocalFile).not.toHaveBeenCalled();
});
it('oversized linked sources still get bounded server JPG and preserve scene authority',async()=>{
  const large={...asset(),W:49474,H:49454,uploaded:false,serverPath:'/DiskArray/scene/a.tif'};
  vi.mocked(createPreviewAsset).mockResolvedValue(large);
  vi.mocked(apiResolveScene).mockResolvedValue({row:{id:'source',W:large.W,H:large.H,srcRev:'rev'},resolved:{dir:'/DiskArray/scene',sr_capable:true,preview_only:false,kind:'input'}} as SceneResolveResult);
  const store=useViewerStore();store.setPreviewDiv(2);store.addFiles(new File(['raw'],'a.tif'));
  await vi.waitFor(()=>expect(store.recs[0]?.route).toBe('jpg'));
  expect(store.recs[0].lqPath).toBe('/DiskArray/scene');expect(store.recs[0].sceneId).toBe('source');
  expect(uploadPreviewFile).not.toHaveBeenCalled();expect(decodeLocalFile).not.toHaveBeenCalled();
});
it('stopping a browser-image upload does not resurrect a local fallback image',async()=>{
  vi.mocked(uploadPreviewFile).mockImplementation((_file,control)=>control.wait(new Promise(()=>{})));
  const store=useViewerStore();store.addFiles(new File(['jpg'],'a.jpg'));
  await vi.waitFor(()=>expect(uploadPreviewFile).toHaveBeenCalled());store.stopImageLoad();
  await vi.waitFor(()=>expect(store.imageLoad.active).toBe(false));
  expect(store.recs).toHaveLength(1);expect(store.recs[0].thumb).toBeNull();expect(createImageBitmap).not.toHaveBeenCalled();
});

it('slow optional association never delays either JPG and grants authority only after source verification',async()=>{
  let complete!:(value:SceneResolveResult)=>void;
  vi.mocked(apiResolveScene).mockImplementationOnce(()=>new Promise(resolve=>{complete=resolve;}))
    .mockImplementationOnce(()=>new Promise(()=>{}));
  const store=useViewerStore();
  const direct={...asset(),uploaded:false,serverPath:'/DiskArray/scene/a.tif'};
  await store.openPreviewAsset(direct);
  expect(store.imageLoad.active).toBe(false);
  const first=store.recs[0];expect(first.route).toBe('jpg');expect(first.lqPath).toBeNull();
  await store.openPreviewAsset({...direct,id:'b'.repeat(64),name:'b.tif',serverPath:'/DiskArray/other/b.tif'});
  expect(store.imageLoad.active).toBe(false);expect(store.recs[1].route).toBe('jpg');
  expect(fetchPreviewAsset).toHaveBeenCalledTimes(2);expect(uploadPreviewFile).not.toHaveBeenCalled();
  complete({row:{id:'source',W:64,H:32,srcRev:'rev'},resolved:{dir:'/DiskArray/scene',sr_capable:true,preview_only:false,kind:'input'}} as SceneResolveResult);
  await vi.waitFor(()=>expect(first.lqPath).toBe('/DiskArray/scene'));
  expect(first.serverSourceVerified).toBe(true);expect(store.recs[1].lqPath).toBeNull();
  expect(store.activeRec?.name).toBe('b.tif');
});

it('removing a displayed asset cancels pending association and ignores its late response',async()=>{
  let complete!:(value:SceneResolveResult)=>void;let signal:AbortSignal|undefined;
  vi.mocked(apiResolveScene).mockImplementation((_cfg,_body,options)=>{
    signal=options?.signal;return new Promise(resolve=>{complete=resolve;});
  });
  const store=useViewerStore();await store.openPreviewAsset({...asset(),uploaded:false,serverPath:'/DiskArray/scene/a.tif'});
  const removed=store.recs[0];store.removeRec(removed.id);expect(signal?.aborted).toBe(true);
  complete({row:{id:'late',W:64,H:32,srcRev:'rev'},resolved:{dir:'/DiskArray/scene',sr_capable:true,preview_only:false,kind:'input'}} as SceneResolveResult);
  await Promise.resolve();expect(removed.lqPath).toBeNull();expect(store.recs).toHaveLength(0);
});

it('a changed source revision cannot grant authority after the JPG is already displayed',async()=>{
  vi.mocked(apiResolveScene).mockResolvedValue({row:{id:'changed',W:64,H:32,srcRev:'different'},resolved:{dir:'/DiskArray/scene',sr_capable:true,preview_only:false,kind:'input'}} as SceneResolveResult);
  const store=useViewerStore();await store.openPreviewAsset({...asset(),uploaded:false,serverPath:'/DiskArray/scene/a.tif'});
  await vi.waitFor(()=>expect(store.recs[0].linkNote).toContain('已变化'));
  expect(store.recs[0].route).toBe('jpg');expect(store.recs[0].lqPath).toBeNull();expect(store.recs[0].previewOnly).toBe(true);
});

it('shows real upload progress then pending server work, never a stuck or inherited percentage',async()=>{
  const store=useViewerStore(),root=document.createElement('div');document.body.append(root);
  const app=createApp(DecodeOverlay);app.use(getActivePinia()!);app.mount(root);
  let releaseUpload!:()=>void;
  vi.mocked(uploadPreviewFile).mockImplementation(async(_file,control,progress)=>{
    await control.checkpoint('上传影像至服务端');progress(.5);
    await control.wait(new Promise<void>(resolve=>{releaseUpload=resolve;}));return asset();
  });
  vi.mocked(fetchPreviewAsset).mockImplementation((_asset,_div,signal)=>new Promise((_resolve,reject)=>{
    signal?.addEventListener('abort',()=>reject(new DOMException('Stopped','AbortError')),{once:true});
  }));
  try {
    store.addFiles(new File(['raw'],'a.tif'));
    await vi.waitFor(()=>expect(root.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe('50'));
    releaseUpload();await vi.waitFor(()=>expect(fetchPreviewAsset).toHaveBeenCalled());await nextTick();
    const bar=root.querySelector('[role=progressbar]')!;
    expect(bar.hasAttribute('aria-valuenow')).toBe(false);
    expect(root.querySelector('.progress-pct')?.textContent).toBe('等待服务端处理…');
    expect(root.querySelector('.progress-bar.indeterminate')).not.toBeNull();
    store.pauseImageLoad();await nextTick();expect(root.querySelector('.progress-pct')?.textContent).toContain('暂停');
    store.stopImageLoad();await vi.waitFor(()=>expect(store.imageLoad.active).toBe(false));
    await vi.waitFor(()=>expect(root.querySelector('[role=progressbar]')).toBeNull());
  } finally {app.unmount();root.remove();}
});

it('requests full-tier smaller NOSR through bounded cache when an entire JPG would exceed the display budget',async()=>{
  const store=useViewerStore();store.setPreviewDiv(2);
  const large={...asset(),W:20000,H:20000};vi.mocked(createPreviewAsset).mockResolvedValue(large);
  const id=store.insertSceneCard({name:'NOSR',W:20000,H:20000,sceneId:'nosr-large',lqPath:null,
    sceneDir:'/DiskArray/pan',stageKind:'nosr',previewPanSize:{W:48000,H:48000}},
    {id:'nosr-large',name:'NOSR',W:20000,H:20000,size_bytes:3} as import('../../lib/scene').SceneRow);
  await store.activate(id);
  expect(createPreviewAsset).toHaveBeenCalledWith({sceneId:'nosr-large'},expect.any(AbortSignal));
  expect(fetchPreviewAsset).toHaveBeenLastCalledWith(large,1,expect.any(AbortSignal),expect.any(Function));
  expect(store.recs[0].detailDiv).toBe(1);expect(store.recs[0].layout).toContain('实际约');
  expect(decodeLocalFile).not.toHaveBeenCalled();
});

it('never borrows a PAN from a different scene to sharpen an unverified NOSR size',async()=>{
  const store=useViewerStore();store.setPreviewDiv(4);
  store.insertSceneCard({name:'PAN',W:200,H:100,sceneId:'pan-other',lqPath:null,sceneDir:'/other',stageKind:'input'},
    {id:'pan-other',name:'PAN',W:200,H:100} as import('../../lib/scene').SceneRow);
  await store.openPreviewAsset(asset());const rec=store.activeRec!;
  rec.stageKind='nosr';rec.sceneDir='/target';
  store.setPreviewDiv(2);await vi.waitFor(()=>expect(rec.detailDiv).toBe(2));
  expect(fetchPreviewAsset).toHaveBeenLastCalledWith(expect.anything(),2,expect.any(AbortSignal));
});

it('diff-style A/B preload shares each large card request without cancellation or legacy full-JPG preparation',async()=>{
  const store=useViewerStore();store.setPreviewDiv(4);
  const images=['pan','product'].map((name,i)=>({...asset(),id:String(i+1).repeat(64),name,W:49474,H:49454,uploaded:false,serverPath:'/DiskArray/scene/'+name+'.tif'}));
  vi.mocked(createPreviewAsset).mockImplementation(async value=>images[value.sceneId==='pan'?0:1]);
  const pending=new Map<string,(value:{blob:Blob;width:number;height:number})=>void>();
  const signals:AbortSignal[]=[];
  vi.mocked(fetchPreviewAsset).mockImplementation((value,_div,signal)=>{
    signals.push(signal!);return new Promise(resolve=>pending.set(value.name,resolve));
  });
  const ids=images.map((image,i)=>store.insertSceneCard({name:image.name,W:image.W,H:image.H,sceneId:image.name,
    lqPath:i===0?'/DiskArray/scene':null,sceneDir:'/DiskArray/scene',srcRev:'rev',stageKind:i===0?'input':'product'},
    {id:image.name,name:image.name,W:image.W,H:image.H,srcRev:'rev'} as import('../../lib/scene').SceneRow));
  const loads=ids.flatMap(id=>[store.ensureCardPixels(id),store.ensureCardPixels(id)]);
  await vi.waitFor(()=>expect(pending.size).toBe(2));
  expect(createPreviewAsset).toHaveBeenCalledTimes(2);expect(fetchPreviewAsset).toHaveBeenCalledTimes(2);
  expect(signals.every(signal=>!signal.aborted)).toBe(true);
  expect(fetchSceneJpg).not.toHaveBeenCalled();expect(ensureScenePreviewReady).not.toHaveBeenCalled();
  pending.get('product')!({blob:new Blob(['jpeg-b']),width:4096,height:4094});
  await vi.waitFor(()=>expect(store.recs[1].src).toBeTruthy());expect(store.recs[0].thumb).toBeNull();
  pending.get('pan')!({blob:new Blob(['jpeg-a']),width:4096,height:4094});
  expect(await Promise.all(loads)).toEqual([true,true,true,true]);
  expect(store.recs[0].lqPath).toBe('/DiskArray/scene');expect(store.recs[1].lqPath).toBeNull();
  expect(store.recs.every(rec=>rec.detailDiv===4&&rec.layout.includes('实际约'))).toBe(true);
  expect(decodeLocalFile).not.toHaveBeenCalled();
});

it('removing a large candidate aborts its private request without publishing pixels',async()=>{
  const store=useViewerStore();store.setPreviewDiv(4);
  vi.mocked(createPreviewAsset).mockResolvedValue({...asset(),W:49474,H:49454});
  let pendingSignal:AbortSignal|undefined;
  vi.mocked(fetchPreviewAsset).mockImplementation((_asset,_div,signal)=>new Promise((_resolve,reject)=>{
    pendingSignal=signal;signal?.addEventListener('abort',()=>reject(new DOMException('Stopped','AbortError')),{once:true});
  }));
  const id=store.insertSceneCard({name:'PAN',W:49474,H:49454,sceneId:'large',lqPath:null,stageKind:'input'},
    {id:'large',srcRev:'rev'} as import('../../lib/scene').SceneRow);
  const loading=store.ensureCardPixels(id);
  await vi.waitFor(()=>expect(pendingSignal).toBeDefined());store.removeRec(id);
  expect(pendingSignal!.aborted).toBe(true);expect(await loading).toBe(false);
  expect(store.recs).toHaveLength(0);expect(createImageBitmap).not.toHaveBeenCalled();
});

it.each(['ori','old'])('uses bounded cache for %s even below the old whole-JPG threshold, with phase feedback',async name=>{
  const store=useViewerStore();store.setPreviewDiv(4);
  const preview={...asset(),name,W:4000,H:4000,uploaded:false,serverPath:'/DiskArray/scene/'+name+'.tif'};
  vi.mocked(createPreviewAsset).mockResolvedValue(preview);
  let finish!:(value:{blob:Blob;width:number;height:number})=>void;
  vi.mocked(fetchPreviewAsset).mockImplementation((_asset,_div,_signal,phase)=>{
    phase?.('服务端预览正在排队 · 已等待 2 秒');return new Promise(resolve=>{finish=resolve;});
  });
  const id=store.insertSceneCard({name,W:4000,H:4000,sceneId:name,lqPath:null,sceneDir:'/DiskArray/scene',stageKind:'product',stageSuffix:name},
    {id:name,srcRev:'rev'} as import('../../lib/scene').SceneRow);
  const loading=store.ensureCardPixels(id);
  await vi.waitFor(()=>expect(finish).toBeDefined());expect(store.overlay.sub).toContain('排队');
  expect(store.recs[0].status).toContain('排队');
  finish({blob:new Blob(['jpeg']),width:1000,height:1000});expect(await loading).toBe(true);
  expect(fetchSceneJpg).not.toHaveBeenCalled();expect(apiResolveScene).not.toHaveBeenCalled();
  expect(store.recs[0].stageLabel?.toLowerCase()).toContain(name);expect(store.recs[0].lqPath).toBeNull();
});
