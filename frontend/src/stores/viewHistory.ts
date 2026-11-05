import {computed,nextTick,ref,watch} from 'vue';
import {defineStore} from 'pinia';
import {useViewerStore} from './viewer';
import {useAutoTourStore} from './autoTour';
import {sameHistoryView} from '../lib/viewHistory';
import type {ViewHistoryEntry} from '../lib/viewHistory';
export const useViewHistoryStore=defineStore('viewHistory',()=>{
 const viewer=useViewerStore(),tour=useAutoTourStore();
 const entries=ref<ViewHistoryEntry[]>([]),index=ref(-1),enabled=ref(false),restoring=ref(false),gesture=ref(false);
 let timer:ReturnType<typeof setTimeout>|undefined;
 const blocked=computed(()=>!enabled.value||tour.status!=='idle'||viewer.busy||viewer.drawMode||viewer.flickerPlaying||!viewer.canvasAttached);
 function cancel(){if(timer!==undefined)clearTimeout(timer);timer=undefined;}
 function record(){
  cancel();if(blocked.value||restoring.value||gesture.value||viewer.viewAnimating)return;
  const state=viewer.captureNavigationView();if(!state)return;
  if(index.value>=0&&sameHistoryView(entries.value[index.value],state))return;
  entries.value=entries.value.slice(0,index.value+1);entries.value.push(state);
  if(entries.value.length>80)entries.value.shift();index.value=entries.value.length-1;
 }
 function schedule(){cancel();if(blocked.value||restoring.value||gesture.value||viewer.viewAnimating)return;timer=setTimeout(record,280);}
 watch(()=>viewer.captureNavigationView(),schedule,{deep:true});
 watch(()=>viewer.compareMode,()=>{cancel();entries.value=[];index.value=-1;schedule();});
 watch([blocked,()=>viewer.viewAnimating],()=>{if(!viewer.viewAnimating&&restoring.value)restoring.value=false;schedule();});
 function target(direction:number){
  for(let i=index.value+direction;i>=0&&i<entries.value.length;i+=direction)if(viewer.navigationViewAvailable(entries.value[i]))return i;
  return -1;
 }
 const canBack=computed(()=>!blocked.value&&!restoring.value&&target(-1)>=0);
 const canForward=computed(()=>!blocked.value&&!restoring.value&&target(1)>=0);
 async function go(direction:-1|1){
  if(blocked.value||restoring.value)return;record();const next=target(direction);if(next<0)return;
  cancel();restoring.value=true;
  if(viewer.restoreNavigationView(entries.value[next]))index.value=next;
  await nextTick();if(!viewer.viewAnimating)restoring.value=false;
 }
 function beginGesture(){record();gesture.value=true;cancel();}
 function endGesture(){if(!gesture.value)return;gesture.value=false;schedule();}
 function connect(){enabled.value=true;schedule();}
 function disconnect(){cancel();enabled.value=false;gesture.value=false;restoring.value=false;viewer.stopViewAnim();}
 return {entries,index,restoring,canBack,canForward,go,beginGesture,endGesture,connect,disconnect};
});
