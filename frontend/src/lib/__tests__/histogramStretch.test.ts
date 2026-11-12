import {it,expect} from 'vitest';
import {computeStats,stretchMap,stretchRgba} from '../tifDecode';
import {planeStats,samplePlane,sharedStats} from '../histogramStretch';
it('maps a custom tonal curve in the black/white interval without changing DN',()=>{
  const src=new Float32Array([0,100,150,200,250,300,400]),before=src.slice(),stats=computeStats(src,7,1,1);
  const range={low:100,high:300,curve:[{x:0,y:0},{x:.25,y:.1},{x:.5,y:.7},{x:.75,y:.9},{x:1,y:1}]};
  const rgba=stretchRgba(src,7,1,1,stats,'linear',false,undefined,range);
  expect([rgba[0],rgba[4],rgba[8],rgba[12],rgba[16],rgba[20],rgba[24]]).toEqual([0,0,26,178,230,255,255]);
  expect(stretchMap(175,0,stats,'linear',range)).toBeCloseTo(.4*255);
  expect(src).toEqual(before);
});
it('uses the existing linear mapping with explicit black/white bounds and preserves DN',()=>{
  const src=new Float32Array([0,100,200,300,400]),original=src.slice(),stats=computeStats(src,5,1,1);
  const rgba=stretchRgba(src,5,1,1,stats,'linear',false,undefined,{low:100,high:300});
  expect([rgba[0],rgba[4],rgba[8],rgba[12],rgba[16]]).toEqual([0,0,128,255,255]);
  expect(src).toEqual(original);
  expect(stretchMap(200,0,stats,'sqrt',{low:100,high:300})).toBeCloseTo(Math.sqrt(.5)*255);
});
it('samples a visible cached rectangle without including pixels outside it',()=>{
  const p={src:new Float32Array([0,1,2,3,100,101,102,103]),w:4,h:2,nb:1,stats:null,invert:false};
  const stats=planeStats(p,{x0:2,y0:0,x1:3,y1:1});
  expect(stats[0].min).toBe(2);expect(stats[0].max).toBe(103);expect(stats[0].total).toBe(4);
  const small=samplePlane(p,undefined,2);expect(small.src.length).toBeLessThanOrEqual(2);
});
it('rebins two histograms into identical shared bounds and CDF',()=>{
  const a=computeStats(new Float32Array([0,25,50,100]),4,1,1)[0],b=computeStats(new Float32Array([100,150,175,200]),4,1,1)[0];
  const st=sharedStats(a,b);expect(st.min).toBe(0);expect(st.max).toBe(200);expect(st.total).toBe(8);
  expect(stretchMap(50,0,[st],'equal')).toBeLessThan(stretchMap(150,0,[st],'equal'));
  expect(st.cdf[1023]).toBe(8);expect(a.max).toBe(100);expect(b.min).toBe(100);
});
it('clips an equalization CDF against the original numeric axis when explicit limits are supplied',()=>{
  const stats=computeStats(new Float32Array([0,0,0,0,20,40,60,80,100]),9,1,1);
  expect(stretchMap(20,0,stats,'equal',{low:20,high:80})).toBe(0);
  expect(stretchMap(40,0,stats,'equal',{low:20,high:80})).toBeCloseTo(85);
  expect(stretchMap(80,0,stats,'equal',{low:20,high:80})).toBe(255);
});
