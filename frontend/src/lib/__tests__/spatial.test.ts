import {describe,it,expect,vi,afterEach} from 'vitest';
import {project,inputToSource,validGeometry,imageRef,spatialId} from '../spatial';
const im={image_key:'test',source_width:12000,source_height:8000};
describe('spatial coordinates',()=>{
 it.each([1,2,3])('pane geometry uses canvas CSS pixels independently of DPR %s',()=>{
  expect(project([6000,4000],im,1200,800,{scale:2,ox:-100,oy:-50},{x:400,y:0,w:500,h:600})).toEqual([1500,750]);
 });
 it('maps actual cropped attachment back to source',()=>{
  const m={input_ref:'a',image_ref:im,width:1000,height:500,crop:[2000,1000,4000,2000] as [number,number,number,number]};
  expect(inputToSource([500,250],m)).toEqual([4000,2000]);
 });
 it.each([NaN,Infinity,-1,12000])('rejects invalid x %s',x=>expect(validGeometry({type:'point',coordinates:[x,1]},12000,8000)).toBe(false));
 it('rejects inverted and zero-area geometry',()=>{
  expect(validGeometry({type:'bbox',coordinates:[3,2,1,4]},10,10)).toBe(false);
  expect(validGeometry({type:'polygon',coordinates:[[0,0],[1,1],[2,2]]},10,10)).toBe(false);
  expect(validGeometry({type:'point',coordinates:[0,0]},0,0)).toBe(false);
 });
 it('keeps identities isolated even for equal filenames',()=>{
  const a={W:10,H:10,file:{},token:1},b={...a,file:{}};
  expect(imageRef(a)).toEqual(imageRef(a));expect(imageRef(a)).not.toEqual(imageRef(b));
 });
});

describe('HTTP-safe spatial identifiers',()=>{
 afterEach(()=>vi.unstubAllGlobals());
 it('works when randomUUID is missing on an insecure HTTP origin',()=>{
  vi.stubGlobal('crypto',{getRandomValues:(a:Uint32Array)=>{a.fill(7);return a;}});
  expect(spatialId()).not.toBe(spatialId());
  expect(imageRef({W:10,H:10,file:{},token:1})?.image_key).toContain('local-session:');
 });
 it('works without a crypto object',()=>{vi.stubGlobal('crypto',undefined);expect(spatialId()).not.toBe(spatialId());});
 it('only uses the server revision for the currently decoded token',()=>{
  const r={W:10,H:10,file:{},token:2,spatialIdentity:'scene-v1:revision',spatialIdentityToken:2};
  expect(imageRef(r)?.image_key).toBe('scene-v1:revision');r.token=3;
  expect(imageRef(r)?.image_key).toContain('local-session:');
 });
});

it('accepts source boundary edges but rejects boundary point centers',()=>{
 expect(validGeometry({type:'bbox',coordinates:[0,0,12000,8000]},12000,8000)).toBe(true);
 expect(validGeometry({type:'point',coordinates:[12000,8000]},12000,8000)).toBe(false);
 expect(validGeometry({type:'bbox',coordinates:[0,0,12001,8000]},12000,8000)).toBe(false);
});
