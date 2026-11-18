<script setup lang="ts">
import { computed } from 'vue';
import BatchProgressTracks from './BatchProgressTracks.vue';
const props=defineProps<{data:Record<string,unknown>|null}>();
const rows=computed(()=>(props.data?.items??[]) as {item_id:string;name:string;status:string;candidate_count?:number;reason?:string;images?:{pan?:string;nosr?:string;difference?:string}}[]);
const failed=computed(()=>rows.value.filter(r=>r.status==='failed').length);
</script>
<template>
  <section class="inspection-card">
    <header><strong>质检结果 · {{ data?.total ?? 0 }} 景</strong><span>{{ data?.has_more ? '分页检测中' : '本页检测完成' }}</span></header>
    <p>仅质检 · {{ {absdiff:'绝对差异',ssim:'结构差异 SSIM',smart:'LTD 纹理退化'}[String(data?.detection_method) as 'absdiff'|'ssim'|'smart'] }}</p>
    <BatchProgressTracks inspection-only :counts="{eligible:rows.length,detected:rows.length-failed}" />
    <p v-if="failed">{{ failed }} 景检测失败，详见下方原因。</p>
    <details><summary>查看本页 {{ rows.length }} 景结果</summary><ul><li v-for="row in rows" :key="row.item_id"><b>{{ row.name }}</b><span>{{ row.status==='failed'?row.reason:row.candidate_count+' 处候选' }}</span></li></ul></details>
    <details><summary>?????? ? ?? {{ rows.length }} ?</summary><table><thead><tr><th>?? / ??</th><th>PAN</th><th>NOSR</th><th>???</th></tr></thead><tbody><tr v-for="row in rows" :key="row.item_id"><td>{{ row.name }}<br>{{ row.status==='failed'?row.reason:row.candidate_count+' ???' }}</td><td v-for="role in (['pan','nosr','difference'] as const)" :key="role"><img v-if="row.images?.[role]" :src="row.images[role]" :alt="role" loading="lazy" /><span v-else>??</span></td></tr></tbody></table></details>
    <small>候选需人工判断；仅检测，不提交修复。</small>
  </section>
</template>
<style scoped>
.inspection-card{min-width:0;padding:12px;border:1px solid var(--tb-border);border-radius:12px;background:var(--tb-bg);color:var(--tb-fg);font-size:12px}header{display:flex;justify-content:space-between;gap:8px}header span,p,small{color:var(--tb-muted);font-size:11px}details{margin:12px 0}summary{cursor:pointer}ul{list-style:none;padding:0;max-height:180px;overflow:auto}li{display:grid;gap:4px;padding:6px 0;overflow-wrap:anywhere}b{font-weight:500}small{display:block}
table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:10px}th,td{padding:5px 3px;text-align:left;vertical-align:top;border-bottom:1px solid var(--tb-border);overflow-wrap:anywhere}td img{display:block;width:100%;height:72px;object-fit:contain;background:#eef2ef;border-radius:4px}
</style>
