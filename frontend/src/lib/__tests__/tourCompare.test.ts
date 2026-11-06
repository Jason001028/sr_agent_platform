import {describe,it,expect} from 'vitest';
import {tourPairRefusal,uniqueTourPartner} from '../tourCompare';
import type {TourImage} from '../tourCompare';
const a:TourImage={id:1,sequence:'synthetic-1',role:'pan',width:2048,height:1024,sourceRevision:'a1',registration:{reference:'verified-fixture',crs:'local',extent:[0,0,100,50],orientation:[1,0,0,1]}};
const b:TourImage={...a,id:2,role:'nosr',width:1024,height:512,sourceRevision:'b1'};
describe('production sibling tour pairing',()=>{
 it('accepts explicitly registered proportional grids',()=>expect(tourPairRefusal(a,b)).toBeNull());
 it('accepts same-sequence proportional grids without GeoTIFF tags',()=>expect(tourPairRefusal({...a,registration:undefined},{...b,registration:undefined})).toBeNull());
 it('accepts a sibling when only one image has GeoTIFF tags',()=>expect(tourPairRefusal(a,{...b,registration:undefined})).toBeNull());
 it('accepts PAN body and NOSR at a rounded non-integer resolution ratio',()=>{
  // 生产实测 SC 本体/NOSR ≈ 1.96，重采样后尺寸取整；逐像素乘积不相等，
  // 但宽高比一致，仍应视为同一地面。
  const body={...a,width:30831,height:30948,registration:undefined};
  const nosr={...b,width:15768,height:15823,registration:undefined};
  expect(body.width*nosr.height).not.toBe(nosr.width*body.height);
  expect(tourPairRefusal(body,nosr)).toBeNull();
 });
 it('still rejects aspect ratios beyond the tolerance',()=>expect(tourPairRefusal({...a,registration:undefined},{...b,width:15768,height:16000,registration:undefined})).toContain('比例'));
 it('still rejects different aspect ratios without GeoTIFF tags',()=>expect(tourPairRefusal({...a,registration:undefined},{...b,height:500,registration:undefined})).toContain('比例'));
 it('rejects different sequences',()=>expect(tourPairRefusal(a,{...b,sequence:'other'})).toBeTruthy());
 it('rejects shifted extents',()=>expect(tourPairRefusal(a,{...b,registration:{...b.registration!,extent:[1,0,101,50]}})).toBeTruthy());
 it('rejects rotated frames',()=>expect(tourPairRefusal(a,{...b,registration:{...b.registration!,orientation:[0,1,-1,0]}})).toBeTruthy());
 it('rejects stale identity',()=>expect(tourPairRefusal(a,{...b,sourceRevision:''})).toBeTruthy());
 it('rejects invalid dimensions',()=>expect(tourPairRefusal(a,{...b,width:NaN})).toBeTruthy());
 it('never silently chooses among multiple candidates',()=>expect(uniqueTourPartner(a,[b,{...b,id:3}],'nosr')).toBeNull());
 it('keeps ori separate from input inference',()=>expect(uniqueTourPartner(a,[b],'ori')).toBeNull());
 it('accepts explicit ori identity',()=>expect(uniqueTourPartner(a,[{...b,role:'ori'}],'ori')?.id).toBe(2));
 it('accepts explicitly registered old as a separate role',()=>expect(tourPairRefusal(a,{...b,role:'old'})).toBeNull());
 it('accepts identified old without GeoTIFF tags',()=>expect(tourPairRefusal(a,{...b,role:'old',registration:undefined})).toBeNull());
});
