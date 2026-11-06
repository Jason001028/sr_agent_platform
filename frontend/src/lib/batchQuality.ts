import { joinBase, loadSrConfig } from './scene';
import type { SceneSibling } from './api';
export type QualityDecision = 'approved' | 'rejected' | 'pending';
export interface QualitySummary {
  scene_name?:string;ordinal?:number;
  review_id:string;item_id:string;name:string;job_id:string;status:string;scene_decision:QualityDecision;
  version:number;current:boolean;reasons:string[];counts:Record<QualityDecision,number>;
}
export interface QualityRegion {region_id:string;decision:QualityDecision;reason:string;reviewer:string}
export interface QualityDocument {
  review_id:string;goal_id:string;item_id:string;job_id:string;version:number;scene_decision:QualityDecision;
  regions:QualityRegion[];images:(SceneSibling & {image_key:string})[];scene_path:string;
  binding:{mask:{width:number;height:number;sha256:string};
    regions:{candidate_id:string;kind:'bbox'|'polygon';bbox:[number,number,number,number];points:[number,number][]|null}[];
    output:{image_key:string};nosr:{image_key:string}};
  audit:{request_key:string;payload:Record<string,unknown>;created_at:number}[];
}
async function request<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T> {
  const res=await fetch(joinBase(loadSrConfig().apiBase,'/api/goals/'+path),{
    method:body?'POST':'GET',headers:body?{'content-type':'application/json'}:undefined,
    body:body?JSON.stringify(body):undefined,signal:signal??AbortSignal.timeout(10000),cache:'no-store',
  });
  const value=await res.json();
  if(!res.ok)throw new Error(String(value.detail??`HTTP ${res.status}`));
  return value as T;
}
export const qualityList=(goal:string,signal?:AbortSignal)=>request<{reviews:QualitySummary[]}>(encodeURIComponent(goal)+'/quality-reviews',undefined,signal);
export const qualityDocument=(goal:string,item:string,signal?:AbortSignal)=>request<QualityDocument>(encodeURIComponent(goal)+'/items/'+encodeURIComponent(item)+'/quality-review',undefined,signal);
export const qualityDecide=(goal:string,item:string,body:unknown)=>request(encodeURIComponent(goal)+'/items/'+encodeURIComponent(item)+'/quality-review',body);
