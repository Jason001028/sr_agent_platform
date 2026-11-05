/** Preparation only. Never represents an executing or server-persisted Goal. */
import { defineStore } from 'pinia';
import { ref, watch } from 'vue';
import { registerViewerObservation } from '../lib/viewerContext';
export interface RepairCandidate {
  path: string;
  name: string;
  evidence: string;
  status: '待确认' | '已确认' | '已排除' | '需复核';
  validationError?: string;
}
const STORAGE_KEY='sr.batch.draft.v1';
export const useBatchDraftStore = defineStore('batchDraft', () => {
  const candidates = ref<RepairCandidate[]>([]);
  const text=ref(''),title=ref('批量影像修复'),mode=ref<'demo'|'production'>('demo'),strategy=ref<'automatic'|'review'>('automatic');
  function add(rows: { path: string; name: string; evidence?: string }[]) {
    for (const row of rows) {
      if (!row.path || candidates.value.some(c => c.path === row.path) || candidates.value.length>=500) continue;
      candidates.value.push({path:row.path.slice(0,4096),name:row.name.slice(0,512),evidence:(row.evidence || '用户从 Viewer 手动加入；问题与区域待检测').slice(0,4096),status:'待确认'});
    }
  }
  function remove(paths:string[]) { candidates.value=candidates.value.filter(c=>!paths.includes(c.path)); }
  function move(path:string,delta:number) {const i=candidates.value.findIndex(c=>c.path===path),j=i+delta;if(i<0||j<0||j>=candidates.value.length)return;const [row]=candidates.value.splice(i,1);candidates.value.splice(j,0,row);}
  try {
    const saved=JSON.parse(localStorage.getItem(STORAGE_KEY)||'null');
    if(saved?.version===1){
      add((Array.isArray(saved.candidates)?saved.candidates:[]).filter((r:RepairCandidate)=>typeof r.path==='string'&&typeof r.name==='string'&&(!r.evidence||typeof r.evidence==='string')));
      for(const row of candidates.value){const old=saved.candidates.find((r:RepairCandidate)=>r.path===row.path);if(['待确认','已确认','已排除','需复核'].includes(old?.status))row.status=old.status;}
      text.value=typeof saved.text==='string'?saved.text.slice(0,500000):'';
      title.value=typeof saved.title==='string'?saved.title.slice(0,200):title.value;
      mode.value=saved.mode==='production'?'production':'demo';strategy.value=saved.strategy==='review'?'review':'automatic';
    }
  } catch { /* Draft recovery never performs network or production operations. */ }
  watch([candidates,text,title,mode,strategy],()=>{try{localStorage.setItem(STORAGE_KEY,JSON.stringify({version:1,candidates:candidates.value,text:text.value,title:title.value,mode:mode.value,strategy:strategy.value}));}catch{/* Optional persistence. */}},{deep:true,flush:'sync'});
  registerViewerObservation('batch_draft',()=>({submitted:false,count:candidates.value.filter(c=>c.status!=='已排除').length,
    candidates:candidates.value.slice(0,24).map(c=>({...c})),truncated:candidates.value.length>24,strategy:strategy.value}));
  return { candidates, text,title,mode,strategy,add,remove,move };
});
