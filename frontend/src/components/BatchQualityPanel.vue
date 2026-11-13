<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue';
import { useBatchQualityStore } from '../stores/batchQuality';
import { batchQualityRequest } from '../lib/batchDock';
const quality=useBatchQualityStore();
const canConfirm=computed(()=>!!quality.doc?.regions.length&&quality.doc.regions.every(r=>r.decision==='approved'));
const editable=computed(()=>quality.doc?.scene_decision==='pending'&&quality.comparisonReady&&!quality.busy);
const label=(v:string)=>({approved:'通过',rejected:'驳回',pending:'待复核'}[v]??v);
const regionIndex=computed(()=>{
  const regions=quality.doc?.regions??[];
  return Math.max(1,regions.findIndex(r=>r.region_id===quality.regionId)+1);
});
const regionInfo=computed(()=>{
  const region=quality.selectedRegion;if(!region)return '';
  const [x0,y0,x1,y1]=region.bbox;
  return `区域 ${regionIndex.value} · x ${x0}–${x1}，y ${y0}–${y1} · ${region.kind==='polygon'?'多边形':'矩形'} · ${label(quality.doc?.regions.find(r=>r.region_id===region.candidate_id)?.decision??'pending')}`;
});
const pendingRegions=computed(()=>quality.doc?.regions.filter(r=>r.decision==='pending').length??0);
function nextPending(){
  const regions=quality.doc?.regions??[];if(!regions.length)return;
  const current=regions.findIndex(r=>r.region_id===quality.regionId);
  const order=[...regions.slice(current+1),...regions.slice(0,current+1)];
  const next=order.find(r=>r.decision==='pending')??order[0];
  if(next&&next.region_id!==quality.regionId)void quality.openRegion(next.region_id);
}
/** One direct landing from the Agent card / another entry.  The request names
 *  an Item; the existing store then opens the NOSR/SR pair and the first
 *  pending region.  No second review state is created here. */
let mounted=false;
async function applyRequest(){
  const request=batchQualityRequest.value;if(!request)return;
  try{
    if(!quality.reviews.length)await quality.refresh();
    const target=request.itemId
      ??quality.reviews.find(r=>['AWAITING_REVIEW','REVIEW_REJECTED'].includes(r.status))?.item_id
      ??quality.reviews[0]?.item_id;
    if(!target)return;
    await quality.select(target);
    if(request.regionId&&quality.doc)await quality.openRegion(request.regionId);
  }finally{
    // One request is one landing; a later remount must not replay it.
    if(batchQualityRequest.value?.token===request.token)batchQualityRequest.value=null;
  }
}
onMounted(()=>{mounted=true;if(batchQualityRequest.value)void applyRequest();else void quality.refresh();});
onBeforeUnmount(()=>{mounted=false;});
watch(()=>batchQualityRequest.value?.token,()=>{if(mounted)void applyRequest();});
</script>
<template>
  <section v-if="quality.reviews.length||quality.error" class="quality-panel" data-e2e="batch-quality-panel">
    <header><strong>待验收 · 人工质量确认</strong><button @click="quality.refresh()">刷新</button></header>
    <p class="counts">{{ quality.totals.scenes }} 景待验收 · {{ quality.totals.regions }} 区域（待复核 {{ quality.totals.pending }}）· 通过 {{ quality.totals.approved }} · 驳回 {{ quality.totals.rejected }}</p>
    <div class="quality-tasks">
      <button v-for="r in quality.reviews" :key="r.review_id" :class="{selected:quality.doc?.review_id===r.review_id}"
        :title="r.reasons.join('；')||r.scene_name||r.name" @click="quality.select(r.item_id)">
        <span>#{{ r.ordinal }} {{ r.scene_name||r.name }}</span><small>Job {{ r.job_id }} · {{ r.current?label(r.scene_decision):'版本失效' }} · {{ r.counts.approved }}/{{ r.counts.approved+r.counts.rejected+r.counts.pending }}</small>
      </button>
    </div>
    <p v-if="quality.loading">正在核对版本并载入双图…</p>
    <p v-if="quality.error" class="error" role="alert">{{ quality.error }}</p>
    <template v-if="quality.doc">
      <div class="region-chips" role="group" aria-label="区域快速定位" data-e2e="quality-region-chips">
        <button v-for="(r,i) in quality.doc.regions" :key="r.region_id"
          :class="{on: quality.regionId===r.region_id, approved: r.decision==='approved', rejected: r.decision==='rejected'}"
          :aria-pressed="quality.regionId===r.region_id"
          @click="quality.openRegion(r.region_id)">区域 {{ i+1 }} · {{ label(r.decision) }}</button>
      </div>
      <div class="quality-tools">
        <select :value="quality.regionId" aria-label="选择待验收区域" @change="quality.openRegion(($event.target as HTMLSelectElement).value)">
          <option v-for="(r,i) in quality.doc.regions" :key="r.region_id" :value="r.region_id">区域 {{ i+1 }} · {{ label(r.decision) }}</option>
        </select>
        <button @click="quality.openRegion(quality.regionId)">定位双图</button>
        <button data-e2e="quality-next-pending" :disabled="!pendingRegions||quality.busy||quality.loading" @click="nextPending">下一待审区域</button>
        <button :disabled="!quality.comparisonReady" @click="quality.flicker()">闪烁对比</button>
        <select v-model="quality.overlay" aria-label="审核 Mask 显示"><option value="fill">半透明 Mask</option><option value="outline">Mask 轮廓</option><option value="off">隐藏 Mask</option></select>
      </div>
      <p class="hint" data-e2e="quality-region-info">Job {{ quality.doc.job_id }} · {{ regionInfo }}</p>
      <p class="hint">左侧为本次 Job 的 PAN 产物，右侧为对应 NOSR；同步缩放和平移。请逐区域确认后再整景确认。</p>
      <p v-if="!quality.comparisonReady" class="hint">当前双图或版本未匹配，禁止提交审核；请重新定位。</p>
      <input v-model="quality.reviewer" aria-label="审核人" placeholder="审核人（必填）" maxlength="100">
      <textarea v-model="quality.reason" aria-label="审核原因" placeholder="审核原因；驳回必须填写" maxlength="2000" rows="2" />
      <div class="quality-actions">
        <button :disabled="!editable||!quality.reviewer.trim()" @click="quality.decide('region','approved')">本区域通过</button>
        <button :disabled="!editable||!quality.reviewer.trim()||!quality.reason.trim()" @click="quality.decide('region','rejected')">驳回</button>
        <button :disabled="!editable||!quality.reviewer.trim()" @click="quality.decide('region','pending')">待复核</button>
      </div>
      <div class="quality-actions">
        <button :disabled="!editable||!canConfirm||!quality.reviewer.trim()" @click="quality.decide('scene','approved')">整景确认通过</button>
        <button :disabled="!editable||!quality.reviewer.trim()||!quality.reason.trim()" @click="quality.decide('scene','rejected')">整景驳回 · 转人工处理</button>
      </div>
      <details v-if="quality.doc.audit.length"><summary>审核记录（{{ quality.doc.audit.length }}）</summary><p v-for="a in quality.doc.audit" :key="a.request_key" class="hint">{{ a.payload.reviewer }} · {{ a.payload.action }} · {{ a.payload.decision }} · {{ a.payload.reason }}</p></details>
    </template>
  </section>
</template>
<style scoped>
.quality-panel{display:grid;gap:7px;padding:9px;border:1px solid var(--tb-border);border-radius:8px;background:var(--tb-bg);font-size:11px;color:var(--tb-fg)}
header,.quality-tools,.quality-actions{display:flex;align-items:center;gap:5px;flex-wrap:wrap}header{justify-content:space-between}
p{margin:0;line-height:1.6}.counts{color:var(--accent-deep)}.hint{color:var(--ink-sub)}.error{color:var(--err);overflow-wrap:anywhere}
button,select,input,textarea{font:inherit;border:1px solid var(--tb-border);border-radius:5px;background:var(--surface);color:var(--tb-fg);padding:5px;box-sizing:border-box}
button{cursor:pointer;min-height:28px}button:disabled{opacity:.45;cursor:not-allowed}input,textarea{width:100%}
.quality-tasks{display:grid;gap:4px;max-height:145px;overflow:auto}.quality-tasks button{text-align:left;display:grid;gap:3px}.quality-tasks span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.quality-tasks small{color:var(--ink-sub)}.selected{border-color:var(--accent-deep);background:var(--accent-soft)}
.region-chips{display:flex;flex-wrap:wrap;gap:4px;max-height:76px;overflow:auto}
.region-chips button{font-size:10px;padding:3px 7px;min-height:24px;border-radius:999px}
.region-chips button.on{border-color:var(--accent-deep);background:var(--accent-soft);color:var(--accent-deep)}
.region-chips button.approved:not(.on){border-color:var(--ok-line);color:var(--ok)}
.region-chips button.rejected:not(.on){border-color:var(--warn-line);color:var(--warn)}
</style>
