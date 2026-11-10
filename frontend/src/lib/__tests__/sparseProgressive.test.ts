import { expect, it } from 'vitest';
import { sparseSample } from '../tifDecode';
import type { SparseLayoutOk, CanvasKit, ProbeInfo } from '../tifDecode';
import { decodeSparseProgressively } from '../sparseProgressive';
import type { Source } from '../source';

it('publishes navigation from the requested samples with exactly the original read set and DN', async () => {
  const W=1025,H=1025,bytes=Uint8Array.from({length:16+W*H},(_,i)=>i%251);
  const layout: SparseLayoutOk={ok:true,le:true,W,H,bits:8,sf:1,rps:1,bpp:1,rowBytes:W,strips:H,
    offs:Array.from({length:H},(_,y)=>16+y*W),lens:Array(H).fill(W)};
  const baselineReads: string[]=[],reads: string[]=[];
  const source=(record:string[]):Source=>({size:bytes.length,async read(offset,length){
    record.push(`${offset}:${length}`);return bytes.slice(offset,offset+length).buffer;
  }});
  const baseline=await sparseSample(source(baselineReads),layout,512);
  let atOverview=Infinity,overviews=0;
  const kit:CanvasKit={createCanvas:(width,height)=>({width,height,getContext:()=>null}),createImageData:()=>{throw Error('not needed');}};
  const result=await decodeSparseProgressively(source(reads),layout,{photometric:1} as ProbeInfo,kit,2,'equal',async()=>{},()=>{},async frame=>{
    if(frame.kind==='overview'){overviews++;atOverview=reads.length;expect(frame.rgba.length).toBeLessThanOrEqual(256*32*4);}
  });
  expect(overviews).toBe(1); expect(atOverview).toBeLessThan(baselineReads.length/4);
  expect([...reads].sort()).toEqual([...baselineReads].sort());
  expect(result.src).toEqual(baseline.src);
});
