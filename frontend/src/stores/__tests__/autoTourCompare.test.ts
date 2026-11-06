import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest';
import {reactive} from 'vue';
import {createPinia,setActivePinia} from 'pinia';
const mocks=vi.hoisted(()=>({viewer:null as any,api:vi.fn()}));
vi.mock('../viewer',()=>({useViewerStore:()=>mocks.viewer}));
vi.mock('../../lib/api',()=>({apiSceneSiblings:(...args:unknown[])=>mocks.api(...args)}));
vi.mock('../../lib/scene',()=>({loadSrConfig:()=>({})}));
import {useAutoTourStore} from '../autoTour';
const identity={sequence:'s',role:'pan' as const,width:24000,height:16000,sourceRevision:'v1',registration:{reference:'fixture',crs:'local',extent:[0,0,3,2] as const,orientation:[1,0,0,1] as const}};
let callback:FrameRequestCallback|undefined,now:number;
function tick(ms:number){for(let i=0;i<ms;i+=100){now+=100;const cb=callback;callback=undefined;cb?.(now);}}
beforeEach(()=>{
 setActivePinia(createPinia());now=100;callback=undefined;
 vi.stubGlobal('document',{hidden:false});vi.stubGlobal('matchMedia',()=>({matches:false}));
 vi.stubGlobal('requestAnimationFrame',(cb:FrameRequestCallback)=>{callback=cb;return 1;});vi.stubGlobal('cancelAnimationFrame',()=>{callback=undefined;});
 const a={id:1,sceneId:'a',thumb:{width:2400,height:1600},W:24000,H:16000,srcRev:'v1',tourIdentity:{...identity,id:1}};
 const b={...a,id:2,sceneId:'b',tourIdentity:{...identity,id:2,role:'nosr'}};
 const v=reactive({activeRec:a,panes:[{rec:a,rect:{w:800,h:600},view:{scale:1,ox:0,oy:0}}],flickerRefRec:a,flickerAltRec:b,flickerView:{scale:1,ox:0,oy:0},flickerShowingAlt:false,flickerReady:true,flickerReason:'',compareMode:'flicker',drawMode:false,busy:false,overlay:{visible:false},canvasAttached:true,canvasSize:{w:800,h:600},userIntentSeq:0,
 flickerPause:vi.fn(),stopViewAnim:vi.fn(),showToast:vi.fn(),noteUserIntent:vi.fn(),setTourLayer:vi.fn((alt:boolean)=>{v.flickerShowingAlt=alt;return true;}),setTourCamera:vi.fn((camera:any)=>{v.flickerView=camera;}),setPaneView:vi.fn(),loadTourImage:vi.fn(async(i:any)=>i.id==='a'?1:2),selectTourPair:vi.fn(()=>true)});
 mocks.viewer=v;mocks.api.mockReset();
});
afterEach(()=>vi.unstubAllGlobals());
describe('shared tour and comparison scheduler',()=>{
 it('teleports between stops by default and dwells 1.3s before flickering two rounds',()=>{
  const t=useAutoTourStore();t.compare=true;t.interval=600;expect(t.speed).toBe('teleport');expect(t.start()).toBe(true);
  const p=t.plan!;const stop=(i:number)=>{const q=p.points[i];return{scale:p.scale,ox:400-q.x*p.tw/p.width*p.scale,oy:300-q.y*p.th/p.height*p.scale};};
  // 到点后先静止 1.3 秒（连续推进是 0.3 秒），此时只有启动时的图层复位。
  tick(1000);expect(mocks.viewer.setTourLayer.mock.calls).toHaveLength(1);
  tick(3200);
  // 落位与块间切换都是精确停点，没有任何中间插值位置。
  expect(mocks.viewer.flickerView).toEqual(stop(1));
  // 第 1 次调用是启动时把图层复位到参考图，其后才是两轮四个切换。
  expect(mocks.viewer.setTourLayer.mock.calls.slice(1).map((c:any)=>c[0])).toEqual([true,false,true,false]);
  for(const [camera] of mocks.viewer.setTourCamera.mock.calls){
   expect([stop(0),stop(1)].some(v=>v.scale===camera.scale&&v.ox===camera.ox&&v.oy===camera.oy)).toBe(true);
  }
 });
 it('defaults to teleport only for stop compare and falls back on move',()=>{
  const t=useAutoTourStore();expect(t.speed).toBe('normal');
  t.compare=true;expect(t.speed).toBe('teleport');
  t.mode='move';expect(t.speed).toBe('normal');
  t.mode='stop';expect(t.speed).toBe('teleport');
  t.speed='fast';t.compare=false;expect(t.speed).toBe('fast');
 });
 it.each([600,1000,1500])('moves while alternating at %ims and freezes both on pause',interval=>{
  const t=useAutoTourStore();t.compare=true;t.mode='move';t.interval=interval;t.start();tick(1300);
  const first={...mocks.viewer.flickerView};tick(interval*2);expect(mocks.viewer.flickerView).not.toEqual(first);expect(mocks.viewer.setTourLayer.mock.calls.length).toBeGreaterThanOrEqual(3);
  t.pause();const paused={...mocks.viewer.flickerView},count=mocks.viewer.setTourLayer.mock.calls.length;tick(3000);expect(mocks.viewer.flickerView).toEqual(paused);expect(mocks.viewer.setTourLayer.mock.calls.length).toBe(count);
  t.resume();tick(500);expect(mocks.viewer.flickerView).not.toEqual(paused);t.stop();expect(callback).toBeUndefined();
 });
 it.each([['slow',40],['normal',60],['fast',80],['ultra',120]] as const)('uses %s moving speed', (speed,px)=>{
  const t=useAutoTourStore();t.compare=true;t.mode='move';t.speed=speed;t.start();tick(2500);const x=mocks.viewer.flickerView.ox;tick(1000);expect(Math.abs(mocks.viewer.flickerView.ox-x)).toBeCloseTo(px,5);
 });
 it('records only presented layers, pauses on intent, invalidates on revision change',()=>{
  const t=useAutoTourStore();t.compare=true;t.start();t.presented();expect(t.coverage.a.some(Boolean)).toBe(true);expect(t.coverage.b.some(Boolean)).toBe(false);
  mocks.viewer.userIntentSeq++;expect(t.status).toBe('paused');mocks.viewer.flickerAltRec.srcRev='v2';expect(t.plan).toBeNull();t.resume();expect(t.status).toBe('paused');
 });
 it('requires a user choice for multiple valid partners',async()=>{
  const a={id:'a',name:'PAN.tif',exists:true,srcRev:'v1',tourIdentity:identity};const b={...a,id:'b',name:'NOSR.tif',tourIdentity:{...identity,role:'nosr'}};
  mocks.api.mockResolvedValue({sceneId:'a',lqPath:'/fixture',tourItems:[a,b,{...b,id:'c'}]});
  const t=useAutoTourStore();await t.discoverPairs();expect(t.pairs).toHaveLength(2);expect(t.selected).toBe(-1);expect(await t.preparePair()).toBe(false);
  t.selected=1;expect(await t.preparePair()).toBe(true);expect(mocks.viewer.selectTourPair).toHaveBeenCalledWith(1,2);
 });
 it('discards late metadata and cancelled preparation',async()=>{
  let resolve!:(value:any)=>void;mocks.api.mockReturnValue(new Promise(r=>resolve=r));const t=useAutoTourStore();const pending=t.discoverPairs();t.cancelPreparation();resolve({tourItems:[]});await pending;expect(t.pairs).toEqual([]);expect(t.pairLoading).toBe(false);
 });
 it('keeps a pairing selectable when optional GeoTIFF tags are absent after refresh',async()=>{
  const a={id:'a',name:'PAN.tif',exists:true,srcRev:'v1',tourIdentity:identity},b={...a,id:'b',tourIdentity:{...identity,role:'nosr'}};
  const t=useAutoTourStore();mocks.api.mockResolvedValue({sceneId:'a',tourItems:[a,b]});await t.discoverPairs();expect(t.selected).toBe(0);
  mocks.api.mockResolvedValue({sceneId:'a',tourItems:[{...a,tourIdentity:{...identity,registration:undefined}},b]});await t.discoverPairs();expect(t.pairs).toHaveLength(1);expect(t.availablePairs).toHaveLength(1);expect(t.selected).toBe(0);expect(t.pairs[0].refusal).toBeNull();
 });
 it('does not select a pair after its asynchronous load was cancelled',async()=>{
  const a={id:'a',exists:true,srcRev:'v1',tourIdentity:identity},b={...a,id:'b',tourIdentity:{...identity,role:'nosr'}};
  const t=useAutoTourStore();mocks.api.mockResolvedValue({sceneId:'a',tourItems:[a,b]});await t.discoverPairs();
  let resolve!:(id:number)=>void;mocks.viewer.loadTourImage.mockImplementation(()=>new Promise(r=>resolve=r));const loading=t.preparePair();t.stop();resolve(1);expect(await loading).toBe(false);expect(mocks.viewer.selectTourPair).not.toHaveBeenCalled();
 });
 it('allows manual selection and loading of production siblings without GeoTIFF tags',async()=>{
  const a={id:'a',name:'sequence_PAN.tif',exists:true,srcRev:'v1',tourIdentity:{...identity,registration:undefined}};
  const b={...a,id:'b',name:'sequence_PAN_NOSR.tif',tourIdentity:{...a.tourIdentity,role:'nosr'}};
  const old={...b,id:'c',name:'sequence_PAN_old.tif',tourIdentity:{...a.tourIdentity,role:'old'}};
  const t=useAutoTourStore();mocks.api.mockResolvedValue({tourItems:[a,b,old]});await t.discoverPairs();
  expect(t.pairs).toHaveLength(2);expect(t.availablePairs).toHaveLength(2);expect(t.selected).toBe(-1);expect(t.pairMessage).toContain('手动选择');
  t.selected=0;expect(await t.preparePair()).toBe(true);expect(mocks.viewer.loadTourImage).toHaveBeenCalledTimes(2);
 });
 it('passes the explicitly opened same-scene IDs for server classification',async()=>{
  mocks.viewer.activeRec.sceneDir='/fixture';mocks.viewer.recs=[mocks.viewer.activeRec,{sceneId:'old',sceneDir:'/fixture'},{sceneId:'foreign',sceneDir:'/other'}];
  mocks.api.mockResolvedValue({tourItems:[]});const t=useAutoTourStore();await t.discoverPairs();expect(mocks.api).toHaveBeenCalledWith({},'a',undefined,true,['a','old']);
 });
 it('distinguishes an older backend without tour support from a registration refusal',async()=>{
  mocks.api.mockResolvedValue({items:[]});const t=useAutoTourStore();await t.discoverPairs();expect(t.pairMessage).toContain('前后端');expect(t.selected).toBe(-1);
 });
});
