// @vitest-environment jsdom
import {beforeEach,afterEach,it,expect,vi} from 'vitest';
import {createPinia,setActivePinia} from 'pinia';
import {nextTick} from 'vue';
vi.mock('../../router/index.js',()=>({default:{}}));
import {useViewerStore} from '../viewer';
import {computeStats} from '../../lib/tifDecode';
import {browserKit} from '../../lib/browserKit';
import type {ViewerRec} from '../viewer';
const ctx={putImageData:vi.fn()};
function record(id:number,values:number[],route:'sparse'|'jpg'='sparse'):ViewerRec {
 const src=new Float32Array(values);
 return {id,file:new File([],'PAN.tif'),probe:null,name:'PAN '+id,size:1,W:values.length,H:1,
  thumb:{width:values.length,height:1,getContext:()=>ctx} as never,src,srcw:values.length,srch:1,nbands:1,invert:false,
  stats:computeStats(src,values.length,1,1),route,sceneId:null,lqPath:null,sceneDir:'/DiskArray/paired',layout:'',status:'',statusCls:'',paintedMode:'linear',maskRois:null,token:1,dnDomain:{bits:16,sampleFormat:1}};
}
beforeEach(()=>{setActivePinia(createPinia());localStorage.clear();vi.clearAllMocks();
 vi.spyOn(browserKit,'createCanvas').mockImplementation((w,h)=>({width:w,height:h,getContext:()=>ctx} as never));
 vi.spyOn(browserKit,'createImageData').mockImplementation((data,width,height)=>({data,width,height}) as ImageData);
});
afterEach(()=>{useViewerStore().clearRecs();vi.restoreAllMocks();});
it('retains independent settings and Camera, DN, stats, masks and analysis token',async()=>{
 const s=useViewerStore(),a=record(1,[0,100,200,300]),b=record(2,[0,200,400,600]);
 s.recs.push(a,b);s.activeId=1;s.setCanvasSize(400,200);s.view={scale:2,ox:11,oy:13};
 a.maskRois=[[[0,0],[1,0],[1,1]]];const rois=a.maskRois,geometry=JSON.stringify(rois);
 const raw=a.src!.slice(),stats=a.stats,token=a.token,view={...s.view};
 s.setStretchBounds(100,200,true);expect(s.recs[0].displayStretch?.custom).toBe(true);
 expect(s.recs[1].displayStretch).toBeUndefined();expect(s.recs[0].stretchPreview).toBeTruthy();
 expect(a.src).toEqual(raw);expect(a.stats).toBe(stats);expect(a.token).toBe(token);expect(s.view).toEqual(view);
 expect(a.maskRois).toBe(rois);expect(JSON.stringify(a.maskRois)).toBe(geometry);
 s.finishStretchInteraction(1);await vi.waitFor(()=>expect(s.recs[0].stretchPreview).toBeUndefined());
 expect(s.view).toEqual(view);s.resetHistogramStretch();expect(s.recs[0].displayStretch?.custom).toBe(false);
});
it('uses one shared numeric mapping, including one CDF, instead of just matching names',async()=>{
 const s=useViewerStore();s.recs.push(record(1,[0,25,50,100]),record(2,[100,150,175,200]));s.activeId=1;
 s.setCompareMode('split');s.paneA=1;s.paneB=2;await nextTick();
 expect(s.sharedStretchReason).toBe('');s.setStretchLink('shared');s.setStretch('equal');
 const [a,b]=s.recs;expect(a.displayStretch?.stats).toBe(b.displayStretch?.stats);
 expect(a.displayStretch?.low).toBe(0);expect(b.displayStretch?.high).toBe(200);
 s.setStretchBounds(50,150,true);expect(a.displayStretch?.low).toBe(b.displayStretch?.low);
 s.setStretchLink('independent');s.setStretchBounds(0,90,true);expect(b.displayStretch?.low).toBe(50);
 s.histogramTarget='B';s.setStretchBounds(60,140,true);
 expect(b.displayStretch?.low).toBe(60);expect(a.displayStretch?.low).toBe(0);
 s.setStretchLink('shared');b.srcRev='replacement';await nextTick();expect(s.stretchLink).toBe('independent');
});
it('retains curves across bound changes and shares the exact curve, then clears it on presets',async()=>{
 const s=useViewerStore();s.recs.push(record(1,[0,50,100]),record(2,[0,100,200]));s.activeId=1;
 s.setCompareMode('split');s.paneA=1;s.paneB=2;await nextTick();
 const curve=[{x:0,y:0},{x:.5,y:.8},{x:1,y:1}];s.setStretchCurve(curve,true);
 s.setStretchBounds(10,90,true);expect(s.recs[0].displayStretch?.curve).toEqual(curve);
 s.setStretchLink('shared');expect(s.recs[1].displayStretch?.curve).toEqual(curve);
 expect(s.recs[1].displayStretch?.low).toBe(10);
 s.setStretchCurve([{x:0,y:0},{x:0,y:1}],true);expect(s.recs[0].displayStretch?.curve).toEqual(curve);
 s.setStretch('sqrt');expect(s.recs[0].displayStretch?.curve).toBeUndefined();expect(s.recs[1].displayStretch?.curve).toBeUndefined();
 s.setStretchCurve(curve,true);s.resetHistogramStretch();expect(s.recs[0].displayStretch?.curve).toBeUndefined();
});
it('refuses mixed DN and irreversible JPG domains without granting a false shared state',async()=>{
 const s=useViewerStore();s.recs.push(record(1,[0,100]),record(2,[0,255],'jpg'));s.activeId=1;
 s.setCompareMode('split');s.paneA=1;s.paneB=2;await nextTick();
 expect(s.sharedStretchReason).toContain('数据来源');s.setStretchLink('shared');expect(s.stretchLink).toBe('independent');
 s.activeId=2;expect(s.histogramSnapshot()?.source).toContain('不能恢复原始 DN');
});
it('last interaction wins and removal prevents asynchronous paints from reviving a card',async()=>{
 const s=useViewerStore();s.recs.push(record(1,[0,100,200,300]));s.activeId=1;
 s.setStretchBounds(0,300);s.setStretchBounds(100,200);
 await vi.waitFor(()=>expect(s.recs[0].stretchPreview).toBeUndefined());
 expect(s.recs[0].displayStretch?.low).toBe(100);
 s.setStretchBounds(0,300);s.removeRec(1);await new Promise(r=>setTimeout(r,20));expect(s.recs).toHaveLength(0);
});
