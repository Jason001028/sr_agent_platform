import { loadSrConfig } from '../lib/scene';
import { computed, ref, watch } from 'vue';
import { defineStore } from 'pinia';
import { useChatStore } from './chat';
import { useViewerStore } from './viewer';
import { imageRef, resolveSceneIdentity, vertices, project, validGeometry } from '../lib/spatial';
import type { ImageRef, Annotation } from '../lib/spatial';
export const useAnnotationsStore = defineStore('annotations', () => {
 const chat=useChatStore(), viewer=useViewerStore();
 const selected=ref<string|null>(null), status=ref(''), demo=ref<Annotation[]>([]);
 const hidden=computed({get:()=>!viewer.agentAnnotationsVisible,set:(value:boolean)=>viewer.setAgentAnnotationsVisible(!value)});
 const hovered=ref<string|null>(null);
 watch(()=>viewer.agentAnnotationsVisible,()=>{selected.value=null;hovered.value=null;},{flush:'sync'});
 const selectedPane=ref<'A'|'B'>('A');
 const bindings=ref<Record<string,ImageRef>>({});
 const bindingKey=()=> 'sr.spatial.bindings.v1:'+loadSrConfig().apiBase+':'+(chat.sessionId??'unsaved');
 watch(()=>chat.sessionId,()=>{
  selected.value=null;hovered.value=null;demo.value=[];status.value='';bindings.value={};
  if(chat.sessionId)try{
   const saved=JSON.parse(localStorage.getItem(bindingKey())??'{}');
   for(const [key,value] of Object.entries(saved)) {
    const v=value as ImageRef;
    if(v && /^scene-v1:[a-f0-9]{64}:[a-f0-9]{64}$/.test(v.image_key)&&v.source_width>0&&v.source_height>0)bindings.value[key]=v;
   }
  }catch{/* Browser persistence unavailable: remain explicitly unbound. */}
 },{immediate:true});
 watch(()=>viewer.panes.map(p=>[p.rec?.id,p.rec?.sceneId,p.rec?.token,Boolean(p.rec?.thumb)].join(':')).join('|'),()=>{
   for(const p of viewer.panes)if(p.rec?.thumb)void resolveSceneIdentity(p.rec);
 },{immediate:true});
 const all=computed(() => {
   const map=new Map<string,Annotation>();
   for (const m of chat.messages) {
     if (m.toolName !== 'present_spatial_annotations' || m.ok === false) continue;
     const d=m.data as { data?: {spatial_annotations?:Annotation[]}; spatial_annotations?:Annotation[] } | undefined;
     for (const a of d?.data?.spatial_annotations ?? d?.spatial_annotations ?? []) {
       if (a.version===1 && validGeometry(a.geometry,a.image_ref.source_width,a.image_ref.source_height)) map.set(a.annotation_id,a);
     }
   }
   for (const a of demo.value) map.set(a.annotation_id,a);
   return [...map.values()].map(a=>bindings.value[a.image_ref.image_key]?{...a,image_ref:bindings.value[a.image_ref.image_key]}:a);
 });
 function focus(a:Annotation) {
   if(viewer.drawMode) { status.value='请先退出 ROI 绘制，再定位空间批注。'; return; }
   const matches=viewer.panes.filter(p => p.rec?.thumb && imageRef(p.rec)?.image_key===a.image_ref.image_key);
   const pane=matches.find(p=>p.side===viewer.activeSide)??matches[0];
   if (!pane?.rec?.thumb) {status.value='该批注关联的影像当前未打开；历史本地文件需要明确重新关联。';return;}
   hidden.value=false; selectedPane.value=pane.side; selected.value=a.annotation_id;status.value='';
   const points=vertices(a.geometry).map(p=>project(p,a.image_ref,pane.rec!.thumb!.width,pane.rec!.thumb!.height,pane.view,pane.rect));
   if(points.every(p=>p[0]>=pane.rect.x+20&&p[0]<=pane.rect.x+pane.rect.w-20&&p[1]>=pane.rect.y+20&&p[1]<=pane.rect.y+pane.rect.h-20))return;
   viewer.setActiveSide(pane.side);
   const pts=vertices(a.geometry), xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
   viewer.focusSourceBounds(pane.side,[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)]);
 }
 function mock() {
   const r=viewer.activeRec, im=r?.thumb && imageRef(r); if(!im)return;
   const w=im.source_width,h=im.source_height;
   demo.value=['疑似道路','建筑区域','河谷地貌'].map((title,i)=>({version:1,annotation_id:'mock:'+im.image_key+':'+i,image_ref:im,input_ref:'mock',source:'mock',verification:'unverified',title:'Mock · '+title,summary:'开发演示固定坐标，不是模型分析结果。',geometry:i===0?{type:'point',coordinates:[w*.25,h*.3]}:i===1?{type:'bbox',coordinates:[w*.45,h*.35,w*.6,h*.5]}:{type:'polygon',coordinates:[[w*.65,h*.6],[w*.8,h*.65],[w*.72,h*.8]]}}));
 }
 function rebind(a:Annotation) {
   const r=viewer.activeRec, im=r?.thumb&&imageRef(r);if(!im)return;
   if(im.source_width!==a.image_ref.source_width||im.source_height!==a.image_ref.source_height){
     status.value='源图尺寸不同，无法关联。';return;
   }
   if(!window.confirm('将历史批注关联到当前影像？请确认是同一份源图。盘阵关联按资源版本在本机保存，本地文件仅临时关联。'))return;
   bindings.value={...bindings.value,[a.image_ref.image_key]:im};
   if(chat.sessionId && im.image_key.startsWith('scene-v1:')) {
    try {localStorage.setItem(bindingKey(),JSON.stringify(Object.fromEntries(Object.entries(bindings.value).filter(([,v])=>v.image_key.startsWith('scene-v1:')))));}
    catch {status.value='已临时关联，但浏览器保存失败；刷新后需要重新确认。';}
   }
 }
 function conversation(a:Annotation, side:'A'|'B'=viewer.activeSide) {
   selectedPane.value=side;
   selected.value=a.annotation_id;viewer.setCtxRailOpen(true);
   window.dispatchEvent(new CustomEvent('annotation-conversation',{detail:a.annotation_id}));
 }
 return {all,selectedPane,hovered,selected,hidden,status,demo,focus,mock,conversation,rebind};
});
