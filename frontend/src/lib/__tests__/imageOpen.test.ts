// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { LoadControl } from '../loadControl';
vi.mock('../local_preview',()=>({sourceFingerprint:vi.fn().mockResolvedValue({name:'a.tif',size:9,samples:['YQ==','YQ==','YQ==']})}));
import { createPickerSession, fetchPreviewAsset, uploadPreviewFile, waitPicker } from '../imageOpen';
import type { PreviewAsset } from '../imageOpen';
const token='a'.repeat(64), id='b'.repeat(64);
const asset={id,name:'a.tif',W:20,H:10,size:9,uploaded:true,serverPath:null,sourceRev:'revision',fingerprint:{name:'a.tif',size:9,samples:[]}} satisfies PreviewAsset;
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
const response=(value:unknown)=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
it('encodes an exact callback URL as data in the native protocol',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({token,chunkBytes:4194304})));
  const session=await createPickerSession();
  const payload=session.uri.split('/').at(-1)!.replace(/-/g,'+').replace(/_/g,'/');
  expect(atob(payload)).toBe(new URL(`/api/image-open/tickets/${token}/selection`,location.href).href);
});
it('uploads bounded slices and returns a preview asset without inventing a scene ID',async()=>{
  const uploaded:Blob[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit={})=>{
    if(url.endsWith('/tickets'))return response({token});
    if(url.endsWith('/selection'))return response({state:'running',files:[{id,state:'upload'}]});
    if(url.includes('/chunks')){uploaded.push(init.body as Blob);return response({state:'running',files:[{id,state:'ready',asset}]});}
    return response({state:'cancelled',files:[]});
  }));
  const result=await uploadPreviewFile(new File(['abcdefghi'],'a.tif'),new LoadControl(),()=>{});
  expect(uploaded.map(part=>part.size)).toEqual([9]);expect(result.serverPath).toBeNull();expect(result).not.toHaveProperty('sceneId');
});
it('waits for full JPEG bytes and reports effective dimensions separately from the requested tier',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('jpeg',{headers:{'X-SR-Preview-W':'4096','X-SR-Preview-H':'2048'}})));
  const result=await fetchPreviewAsset(asset,2);
  expect(result.width).toBe(4096);expect(result.height).toBe(2048);expect(await result.blob.text()).toBe('jpeg');
});
it('does not wait forever after a native failure before any file was selected',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockImplementation(async()=>response({state:'error',error:'native failed',files:[]})));
  await expect(waitPicker(token,new LoadControl(),()=>{},async()=>{})).rejects.toThrow('native failed');
});
it('still opens ready files when another file in the same native selection fails',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockImplementation(async()=>response({state:'running',files:[
    {id:'bad',name:'bad.tif',state:'error',error:'too large'},
    {id,name:'a.tif',state:'ready',asset},
  ]})));
  const ready=vi.fn().mockResolvedValue(undefined);
  await expect(waitPicker(token,new LoadControl(),()=>{},ready)).rejects.toThrow('too large');
  expect(ready).toHaveBeenCalledWith(expect.objectContaining({id:asset.id}));
});

it('reports queued and running states while waiting, and stops polling once JPEG transfer starts',async()=>{
  vi.useFakeTimers();let finish!:(response:Response)=>void;let polls=0;
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.includes('/preview-status'))return Promise.resolve(response({state:++polls===1?'queued':'running'}));
    return new Promise<Response>(resolve=>{finish=resolve;});
  }));
  const phase=vi.fn(),loading=fetchPreviewAsset(asset,2,undefined,phase);
  await vi.advanceTimersByTimeAsync(800);expect(phase).toHaveBeenCalledWith(expect.stringContaining('排队'));
  await vi.advanceTimersByTimeAsync(1500);expect(phase).toHaveBeenCalledWith(expect.stringContaining('采样并生成'));
  finish(new Response('jpeg'));await loading;
  expect(phase).toHaveBeenLastCalledWith('正在下载服务端 JPG…');
  const count=vi.mocked(fetch).mock.calls.length;await vi.advanceTimersByTimeAsync(10000);
  expect(vi.mocked(fetch).mock.calls).toHaveLength(count);
});
