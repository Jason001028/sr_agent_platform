import { joinBase, loadSrConfig } from './scene';
import { sourceFingerprint } from './local_preview';
import { LoadControl } from './loadControl';

export interface PreviewAsset {
  id: string; name: string; W: number; H: number; size: number; uploaded: boolean;
  sourceRev: string; serverPath: string | null;
  clientPath?:string;
  fingerprint: Awaited<ReturnType<typeof sourceFingerprint>>;
}
export interface PickerFile { id: string; name: string; size: number; received: number; state: string; asset?: PreviewAsset; error?: string; originalPath?:string }
export interface PickerState { state: string; files: PickerFile[]; error?:string }
const api = (path: string) => joinBase(loadSrConfig().apiBase,'/api/image-open'+path);
export class ImageOpenError extends Error { constructor(message:string,public status:number){super(message);} }
async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(api(path),init);
  if (!response.ok) {
    const body = await response.json().catch(()=>({detail:`HTTP ${response.status}`}));
    throw new ImageOpenError(body.detail ?? `HTTP ${response.status}`,response.status);
  }
  return response.json();
}
const post = (value: unknown, signal?: AbortSignal): RequestInit => ({ method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),signal });
export function createPreviewAsset(value: { path?: string; sceneId?: string; fingerprint?: PreviewAsset['fingerprint'] }, signal?: AbortSignal) {
  return json<PreviewAsset>('/assets',post(value,signal));
}
export async function createPickerSession() {
  const value = await json<{token:string;chunkBytes:number}>('/tickets',{method:'POST'});
  if (!/^[a-f0-9]{64}$/.test(value.token)) throw new Error('服务端尚未支持影像路径组件，请更新后端');
  const callback = new URL(api(`/tickets/${value.token}/selection`),location.href).href;
  const payload=btoa(String.fromCharCode(...new TextEncoder().encode(callback))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  return {...value,uri:`sr-explorer://pick/${payload}`};
}
export const pickerState = (token: string, state: 'paused'|'running'|'cancelled') => json<PickerState>(`/tickets/${token}/state`,post({state}));
export async function waitPicker(token: string, control: LoadControl, progress: (value: PickerState)=>void, ready: (asset:PreviewAsset)=>Promise<void>) {
  const displayed = new Set<string>();
  const failures=new Map<string,string>();
  let updates=Promise.resolve();
  const off=control.subscribe(()=>{ updates=updates.then(()=>pickerState(token,control.signal.aborted?'cancelled':control.paused?'paused':'running')).then(()=>{},()=>{}); });
  try {
    for (;;) {
      await control.checkpoint('等待组件选择／上传影像');
      const value=await control.wait(json<PickerState>(`/tickets/${token}?seen=${[...displayed].join(',')}`,{signal:control.signal}));
      progress(value);
      if(value.state==='cancelled')return;
      for(const file of value.files) {
        if(file.state==='error')failures.set(file.id,`${file.name}：${file.error??'上传失败'}`);
        if(file.asset&&!displayed.has(file.asset.id)){
          displayed.add(file.asset.id);
          try{await ready({...file.asset,clientPath:file.originalPath});}
          catch(error){if(control.signal.aborted||(error as Error)?.name==='AbortError')throw error;failures.set(file.id,`${file.name}：${error instanceof Error?error.message:String(error)}`);}
        }
      }
      if(value.state==='error')throw new Error([...failures.values()].join('；')||value.error||'本地组件选择或上传失败');
      if(value.files.length&&value.files.every(file=>['ready','error'].includes(file.state))){
        if(failures.size)throw new Error([...failures.values()].join('；'));return;
      }
      await control.wait(new Promise(resolve=>setTimeout(resolve,500)));
    }
  } finally { off();void pickerState(token,'cancelled').catch(()=>{}); }
}
export async function uploadPreviewFile(file: File, control: LoadControl, progress:(fraction:number)=>void) {
  await control.checkpoint('准备服务端 JPG（上传影像）');
  const fingerprint=await control.wait(sourceFingerprint(file));
  const session=await control.wait(createPickerSession());
  try {
    let value=await control.wait(json<PickerState>(`/tickets/${session.token}/selection`,post({files:[{name:file.name,size:file.size,fingerprint}]},control.signal)));
    const entry=value.files[0];
    if(!entry)throw new Error('上传会话无效');
    for(let offset=0;offset<file.size;) {
      await control.checkpoint(`上传影像至服务端 ${Math.floor(offset/file.size*100)}%`);
      const chunk=file.slice(offset,Math.min(file.size,offset+4*1024*1024));
      value=await control.wait(json<PickerState>(`/tickets/${session.token}/files/${entry.id}/chunks?offset=${offset}`,{
        method:'PUT',body:chunk,signal:control.signal,headers:{'Content-Type':'application/octet-stream'},
      }));
      offset+=chunk.size;progress(offset/file.size);
    }
    const result=value.files[0];
    if(!result?.asset)throw new Error(result?.error??'服务端未接收完整影像');
    return result.asset;
  } finally { void pickerState(session.token,'cancelled').catch(()=>{}); }
}
export async function fetchPreviewAsset(asset: PreviewAsset, div: number, signal?: AbortSignal, onPhase?:(text:string)=>void) {
  const statusAbort=new AbortController();let finished=false,downloading=false;
  let timer:ReturnType<typeof setTimeout>|undefined;
  const started=Date.now();
  const stop=()=>{finished=true;clearTimeout(timer);statusAbort.abort();};
  const poll=async()=>{
    try {
      const status=await json<{state:string}>(`/assets/${asset.id}/preview-status?div=${div}`,{signal:statusAbort.signal});
      if(!finished&&!downloading){
        const phase=status.state==='queued'?'服务端预览正在排队':status.state==='running'?'服务端正在采样并生成 JPG':'正在核对服务端 JPG 缓存';
        onPhase?.(`${phase} · 已等待 ${Math.floor((Date.now()-started)/1000)} 秒`);
      }
    }catch{/* Advisory status never starts a second preview or hides its error. */}
    if(!finished&&!downloading)timer=setTimeout(()=>{void poll();},1500);
  };
  signal?.addEventListener('abort',stop,{once:true});
  if(signal?.aborted)stop();
  if(onPhase&&!finished){onPhase('正在核对服务端 JPG 缓存…');timer=setTimeout(()=>{void poll();},800);}
  try {
    const response=await fetch(api(`/assets/${asset.id}/preview?div=${div}`),{signal});
    if(!response.ok){const body=await response.json().catch(()=>({detail:`HTTP ${response.status}`}));throw new ImageOpenError(body.detail??'服务端 JPG 生成失败',response.status);}
    downloading=true;clearTimeout(timer);statusAbort.abort();onPhase?.('正在下载服务端 JPG…');
    const blob=await response.blob();
    return {blob,width:Number(response.headers.get('X-SR-Preview-W')),height:Number(response.headers.get('X-SR-Preview-H'))};
  }finally{stop();signal?.removeEventListener('abort',stop);}
}
