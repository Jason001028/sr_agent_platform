/** Pair known production siblings on proportional full-image pixel grids. */
export interface TourImage {
 id: number;
 sequence: string;
 role: 'pan' | 'nosr' | 'ori' | 'old';
 width: number;
 height: number;
 sourceRevision: string;
 evidenceReason?: string;
 registration?: {
  reference: string;
  crs: string;
  extent: readonly [number, number, number, number];
  orientation: readonly [number, number, number, number];
 };
}
/** Scene siblings and frozen Manifest images carry the same server evidence. */
export function tourImageFromEvidence(id:number, evidence:Omit<TourImage,'id'>, revision?:string|null):TourImage {
 return {...evidence,id,sourceRevision:revision && revision!==evidence.sourceRevision?'':evidence.sourceRevision};
}
export function tourPairRefusal(a: TourImage, b: TourImage): string | null {
 if(a.id===b.id || a.role!=='pan' || !['nosr','ori','old'].includes(b.role)) return '需要明确的 PAN 与 NOSR、ori 或 old 身份';
 if(!a.sequence || a.sequence!==b.sequence) return '影像不属于同一生产序列';
 if(!a.sourceRevision || !b.sourceRevision) return '缺少影像源版本';
 if(![a.width,a.height,b.width,b.height].every(v=>Number.isSafeInteger(v)&&v>0)) return '像素尺寸未知或无效';
 const x=a.registration,y=b.registration;
 // Production TIFFs commonly omit GeoTIFF tags. Their absence is not a pairing gate.
 // When both images provide a mapping, still reject an explicit contradiction.
 if(x && y){
 if(!x.reference || x.reference!==y.reference || !x.crs || x.crs!==y.crs) return '空间参考不一致';
 if(x.extent.length!==4||y.extent.length!==4||x.orientation.length!==4||y.orientation.length!==4||![...x.extent,...y.extent,...x.orientation,...y.orientation].every(Number.isFinite)) return '空间映射无效';
 if(x.extent[2]<=x.extent[0] || x.extent[3]<=x.extent[1]) return '空间范围无效';
 if(x.extent.some((v,i)=>v!==y.extent[i]) || x.orientation.some((v,i)=>v!==y.orientation[i])) return '空间范围或方向不一致';
 if(x.orientation[0]*x.orientation[3]-x.orientation[1]*x.orientation[2]===0) return '方向映射不可逆';
 }
 if(a.width*b.height!==b.width*a.height) return '像素网格比例不一致';
 return null;
}
export function tourCandidates(a: TourImage, images: readonly TourImage[], role: 'nosr'|'ori'|'old') {
 return images.filter(b=>b.role===role && tourPairRefusal(a,b)===null);
}
/** Ambiguous candidates always require an explicit user choice. */
export function uniqueTourPartner(a: TourImage, images: readonly TourImage[], role: 'nosr'|'ori'|'old'): TourImage|null {
 const matches=tourCandidates(a,images,role);
 return matches.length===1?matches[0]:null;
}
