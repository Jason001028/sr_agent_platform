import { loadSrConfig, joinBase } from './scene';
import { origToThumb, thumbToScreen } from './viewMath';
import type { ViewState, PaneRect } from './viewMath';
export type XY = [number, number];
export type Geometry = { type: 'point'; coordinates: XY } | { type: 'bbox'; coordinates: [number, number, number, number] } | { type: 'polygon'; coordinates: XY[] };
export interface ImageRef { image_key: string; source_width: number; source_height: number }
export interface VisualInput { input_ref: string; image_ref: ImageRef; width: number; height: number; crop: [number, number, number, number] }
export interface Annotation { version: 1; annotation_id: string; image_ref: ImageRef; input_ref: string; geometry: Geometry; title: string; summary: string; source: 'agent' | 'user' | 'mock'; verification: 'unverified' | 'verified' }
// No cross-reload match without a trusted resource version or explicit user association.
let sequence = 0;
/** Display IDs, not authentication tokens. getRandomValues also works on HTTP. */
export function spatialId(): string {
  const bytes = new Uint32Array(4);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let i=0;i<bytes.length;i++) bytes[i]=Math.floor(Math.random()*0x100000000);
  return Date.now().toString(36)+'-'+(++sequence).toString(36)+'-'+Array.from(bytes,n=>n.toString(16).padStart(8,'0')).join('');
}
const identities = new WeakMap<object, string>();
export function imageRef(r: { W: number; H: number; file: object; token: number; spatialIdentity?: string; spatialIdentityToken?: number }): ImageRef | null {
  if (!(r.W > 0 && r.H > 0)) return null;
  if(r.spatialIdentity && r.spatialIdentityToken===r.token) return {image_key:r.spatialIdentity,source_width:r.W,source_height:r.H};
  let key = identities.get(r.file);
  if (!key) { key = 'local-session:' + spatialId(); identities.set(r.file, key); }
  return { image_key: key + ':' + r.token, source_width: r.W, source_height: r.H };
}
export function vertices(g: Geometry): XY[] {
  if (g.type === 'point') return [g.coordinates];
  if (g.type === 'polygon') return g.coordinates;
  const [x,y,r,b] = g.coordinates;
  return [[x,y],[r,y],[r,b],[x,b]];
}
export function validGeometry(g: Geometry, w: number, h: number): boolean {
  if (!(w > 0 && h > 0) || !g || !['point','bbox','polygon'].includes(g.type)) return false;
  if (!Array.isArray(g.coordinates)) return false;
  if (g.type === 'point' && g.coordinates.length !== 2 || g.type === 'bbox' && g.coordinates.length !== 4) return false;
  const pts = vertices(g);
  if (!pts.every(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && p[0]>=0 && p[1]>=0 && (g.type==='point'?p[0]<w:p[0]<=w) && (g.type==='point'?p[1]<h:p[1]<=h))) return false;
  if (g.type === 'bbox') return g.coordinates[0] < g.coordinates[2] && g.coordinates[1] < g.coordinates[3];
  if (g.type === 'polygon') {
    if (pts.length < 3 || pts.length > 128) return false;
    const area = pts.reduce((s,p,i) => { const q=pts[(i+1)%pts.length]; return s+p[0]*q[1]-q[0]*p[1]; },0);
    return Math.abs(area)>0;
  }
  return true;
}
export function project(p: XY, image: ImageRef, tw: number, th: number, view: ViewState, rect: PaneRect): XY {
  const t=origToThumb(...p,tw,th,image.source_width,image.source_height);
  const s=thumbToScreen(view,...t); return [s[0]+rect.x,s[1]+rect.y];
}
export function inputToSource(p: XY, m: VisualInput): XY {
  return [m.crop[0]+p[0]*m.crop[2]/m.width,m.crop[1]+p[1]*m.crop[3]/m.height];
}

const identityRequests = new WeakMap<object,{token:number;id:string;promise:Promise<void>}>();
export function resolveSceneIdentity(r: {sceneId:string|null;token:number;spatialIdentity?:string;spatialIdentityToken?:number}):Promise<void> {
 if(!r.sceneId) return Promise.resolve();
 const old=identityRequests.get(r);if(old?.token===r.token&&old.id===r.sceneId)return old.promise;
 const token=r.token, id=r.sceneId;
 r.spatialIdentity=undefined;r.spatialIdentityToken=undefined;
 const promise=(async()=>{
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
  try {
   const response=await fetch(joinBase(loadSrConfig().apiBase,'/api/scenes/'+encodeURIComponent(id)+'/spatial-identity'),{signal:controller.signal});
   if(!response.ok)return;const value=await response.json();
   if(r.token===token&&r.sceneId===id&&typeof value.image_key==='string'&&/^scene-v1:[a-f0-9]{64}:[a-f0-9]{64}$/.test(value.image_key)) {
    r.spatialIdentity=value.image_key;r.spatialIdentityToken=token;
   }
  } catch { /* Missing/versionless resources remain unbound; pixel viewing still works. */ }
  finally{clearTimeout(timer);if(r.token===token&&r.sceneId===id&&r.spatialIdentityToken!==token)identityRequests.delete(r);}
 })();
 identityRequests.set(r,{token,id,promise});return promise;
}
