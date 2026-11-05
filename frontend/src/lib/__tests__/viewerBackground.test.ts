import {afterEach,describe,expect,it,vi} from 'vitest';
import {readViewerBackground,saveViewerBackground,VIEWER_BACKGROUND_KEY,VIEWER_BACKGROUNDS} from '../viewerBackground';
afterEach(()=>vi.unstubAllGlobals());
describe('viewer environment preference',()=>{
 it('uses engineering grid on first use and ignores obsolete values',()=>{
  const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null});
  expect(readViewerBackground()).toBe('grid');values.set(VIEWER_BACKGROUND_KEY,'obsolete');expect(readViewerBackground()).toBe('grid');
 });
 it.each(VIEWER_BACKGROUNDS)('restores $value across sessions',({value})=>{
  const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,v:string)=>values.set(key,v)});
  saveViewerBackground(value);expect(readViewerBackground()).toBe(value);
 });
 it('remains usable with blocked browser storage',()=>{
  vi.stubGlobal('localStorage',{getItem:()=>{throw Error('blocked');},setItem:()=>{throw Error('blocked');}});
  expect(readViewerBackground()).toBe('grid');expect(()=>saveViewerBackground('graphite')).not.toThrow();
 });
});
