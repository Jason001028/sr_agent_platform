import { computeStats } from './tifDecode';
import type { BandStats, StretchMode, StretchRange } from './tifDecode';
export interface DisplayStretch extends StretchRange {
  mode:StretchMode;custom:boolean;stats:BandStats[];version:number;
  domain?:string;sourceRev?:string|null;
}
export interface PixelPlane {src:Float32Array;w:number;h:number;nb:number;stats:BandStats[]|null;invert:boolean}
export interface PixelRect {x0:number;y0:number;x1:number;y1:number}
/** Bounded work, entirely from cached DN; no IO and no stretched canvas reads. */
export function samplePlane(p:PixelPlane,rect:PixelRect={x0:0,y0:0,x1:p.w-1,y1:p.h-1},max=256*256) {
  const width=Math.max(0,rect.x1-rect.x0+1),height=Math.max(0,rect.y1-rect.y0+1);
  const scale=Math.min(1,Math.sqrt(max/Math.max(1,width*height)));
  const w=Math.max(1,Math.floor(width*scale)),h=Math.max(1,Math.floor(height*scale));
  const src=new Float32Array(w*h*p.nb);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
    const sy=Math.min(p.h-1,rect.y0+Math.floor(y*height/h)),sx=Math.min(p.w-1,rect.x0+Math.floor(x*width/w));
    for(let b=0;b<p.nb;b++)src[(y*w+x)*p.nb+b]=p.src[(sy*p.w+sx)*p.nb+b];
  }
  return {...p,src,w,h};
}
export function planeStats(p:PixelPlane,rect?:PixelRect):BandStats[] {
  if(!rect&&p.stats)return p.stats;
  const sample=samplePlane(p,rect);
  return computeStats(sample.src,sample.w,sample.h,p.nb);
}
/** Rebin cached histograms to one numeric domain; both images use this CDF. */
export function sharedStats(a:BandStats,b:BandStats):BandStats {
  const min=Math.min(a.min,b.min),max=Math.max(a.max,b.max),hist=new Float64Array(1024),cdf=new Float64Array(1024);
  for(const s of [a,b])for(let i=0;i<s.hist.length;i++){
    const value=s.min+(i+.5)*(s.max-s.min)/s.hist.length;
    const bin=max>min?Math.max(0,Math.min(1023,Math.floor((value-min)/(max-min)*1024))):0;
    hist[bin]+=s.hist[i];
  }
  let total=0;for(let i=0;i<1024;i++){total+=hist[i];cdf[i]=total;}
  const percentile=(fraction:number)=>{let i=0;while(i<1023&&cdf[i]<total*fraction)i++;return min+(i+.5)*(max-min)/1024;};
  return {min,max,hist,cdf,total,p2:percentile(.02),p98:percentile(.98)};
}
export const STRETCH_LABELS:Record<StretchMode,string>={linear:'线性',linear2:'2% 截断',equal:'均衡',sqrt:'平方根',log:'对数'};
export function presetRange(stats:BandStats,mode:StretchMode):StretchRange {
  return {low:mode==='linear2'?stats.p2:stats.min,high:mode==='linear2'?stats.p98:stats.max};
}
