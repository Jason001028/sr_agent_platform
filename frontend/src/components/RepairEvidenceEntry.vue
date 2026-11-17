<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue';
const props=defineProps<{goalId:string}>();
const reportId=ref(''),error=ref(''),enabled=ref(false);
let token=0,controller:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined;
async function refresh(current=token) {
  if(!props.goalId)return;
  controller?.abort();controller=new AbortController();clearTimeout(timer);
  try {
    const response=await fetch(`/api/repair-evidence-reports/by-goal/${encodeURIComponent(props.goalId)}`,{signal:controller.signal});
    if(response.ok){
      const result=await response.json();if(current!==token)return;
      reportId.value=result.report?.report_id??'';enabled.value=!!result.enabled;
      if(result.enabled && (!result.report || ['processing','pending','superseded'].includes(result.report.status)))timer=setTimeout(()=>void refresh(current),5000);
    }
  }catch{/* Report availability never changes repair status. */}
}
async function create() {
  const current=token;
  try{
    const response=await fetch(`/api/repair-evidence-reports/by-goal/${encodeURIComponent(props.goalId)}`,{method:'POST'});
    if(current!==token)return;
    if(!response.ok){error.value='计算未结束或来源尚未就绪，请稍后生成';return;}
    await refresh(current);
  }catch{if(current===token)error.value='报告暂不可用';}
}
watch(()=>props.goalId,()=>{token++;reportId.value='';error.value='';enabled.value=false;void refresh();},{immediate:true});
onBeforeUnmount(()=>{token++;controller?.abort();clearTimeout(timer);});
</script>
<template><RouterLink v-if="reportId" :to="`/repair-reports/${reportId}`">图像证据报告 ↗</RouterLink><button v-else-if="enabled" @click="create">生成图像证据报告</button><span v-if="error" role="status">{{error}}</span></template>
