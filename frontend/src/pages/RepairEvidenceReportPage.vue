<script setup lang="ts">
import { ref, onBeforeUnmount, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useRepairEvidenceStore } from '../stores/repairEvidence';
import { requestEvidenceComparison, type EvidenceComparison } from '../lib/evidenceViewer';
import { evidenceFetch } from '../lib/repairEvidence';
import { evidenceBase, evidenceStateLabel } from '../lib/repairEvidence';
import { useRoute, useRouter } from 'vue-router';
import { requestQualityReview } from '../lib/batchDock';
import { useViewerStore } from '../stores/viewer';
const route = useRoute();
const router = useRouter();
let locatorToken = 0;
async function locate(row: any) {
  const token = ++locatorToken;
  if (useViewerStore().drawMode) { error.value = '请先完成或取消手绘草稿并退出编辑'; return; }
  try {
    const response = await fetch(`${base()}/rows/${row.row_id}/locator`);
    if (!response.ok) throw new Error('来源版本失效或服务暂不可用，请查看归档证据');
    const target = await response.json();
    if (token !== locatorToken) return;
    await router.push('/viewer');
    requestQualityReview(target.item_id, target.region_id, target.goal_id, target.review_id);
  } catch (e) { error.value = String(e); }
}
async function compareBefore(row: any) {
  const token=++locatorToken;
  if(useViewerStore().drawMode){error.value='请先完成或取消手绘草稿';return;}
  try {
    const comparison=await evidenceFetch<EvidenceComparison>(`${base()}/rows/${row.row_id}/comparison`);
    if(token!==locatorToken)return;
    requestEvidenceComparison(comparison);
    await router.push('/viewer');
  } catch { error.value='原始影像版本失效或不存在，请查看归档裁片'; }
}
const evidence = useRepairEvidenceStore();
const { report, rows, offset, total, reportTotal, error, loading, evidenceFilter, analysisFilter, sort } = storeToRefs(evidence);
const expanded = ref<string | null>(null);
const base = () => evidenceBase(String(route.params.reportId));
const load = () => evidence.load();
function asset(row: any, window: any, role: string) { return `${base()}/rows/${row.row_id}/assets/${window.window_id}/${role}`; }
async function retryRow(row: any, stage: string) {
  const response = await fetch(`${base()}/rows/${row.row_id}/retry`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage }) });
  if (!response.ok) error.value = '来源版本失效或服务暂不可用，请查看归档证据';
  else await load();
}
function turn(delta: number) { expanded.value = null; evidence.turn(delta); }
watch(() => route.params.reportId, id => { expanded.value = null; evidence.start(String(id)); }, { immediate: true });
onBeforeUnmount(() => { locatorToken++; evidence.stop(); });
</script>
<template>
  <main class="evidence-report">
    <header><h1>图像证据报告 <small>实验功能</small></h1><button @click="load">刷新</button></header>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="report">
      <p>批次 {{ report.goal_id }} · 版本 {{ report.revision }} · 计算 {{ report.compute_state }} · {{ new Date(report.created_at * 1000).toLocaleString() }}</p>
      <p>{{ report.coverage.total_scenes }} 景 · {{ total }} 个最终修复区域 · 证据就绪 {{ report.evidence_counts.ready ?? 0 }} 区域 · 已分析 {{ report.coverage.analyzed }} 区域 · 未分析 {{ report.coverage.unanalyzed }} 区域</p>
      <p>模型意见仅供参考；未配置模型时显示暂未分析。证据用于辅助人工复核，候选外区域未检查。</p>
      <a :href="`${base()}/export`" download="repair-evidence.json">下载版本化 JSON</a>
      <div class="filters">
        <label>证据状态 <select v-model="evidenceFilter"><option value="">全部</option><option v-for="s in ['ready','queued','failed','unsupported','stale']" :key="s" :value="s">{{ evidenceStateLabel(s) }}</option></select></label>
        <label>分析状态 <select v-model="analysisFilter"><option value="">全部</option><option v-for="s in ['ready','queued','not_requested','failed','unavailable']" :key="s" :value="s">{{ evidenceStateLabel(s) }}</option></select></label>
        <label>排序 <select v-model="sort"><option value="scene">按景分组</option><option value="priority">优先处理异常</option></select></label>
      </div>
      <p>筛选后 {{ total }} 行 / 全部 {{ reportTotal }} 行</p>
      <p v-if="loading" role="status">正在加载报告…</p>
      <p v-else-if="!total">{{ reportTotal ? '没有符合筛选条件的区域。' : '没有可报告的最终修复区域。' }}</p>
      <article v-for="row in rows" :key="`${report.report_id}:${row.row_id}`">
        <h2 v-if="rows.indexOf(row) === 0 || rows[rows.indexOf(row)-1]?.item_id !== row.item_id" :title="row.plan.scene_name || row.item_id">{{ row.plan.scene_name || row.item_id }} · 区域 {{ row.region_id }}</h2>
        <div class="report-row">
          <div class="pair">
            <figure v-for="role in ['nosr','output']" :key="role"><figcaption>{{ role === 'nosr' ? 'NOSR' : '修复产物' }}</figcaption>
              <a v-if="row.evidence_state === 'ready' && row.manifest.windows?.length" :href="asset(row, row.manifest.windows![0], role)" target="_blank" rel="noopener"><img loading="lazy" width="192" height="192" :src="asset(row, row.manifest.windows![0], role)" :alt="role"></a>
              <div v-else class="placeholder">{{ evidenceStateLabel(row.evidence_state) }} {{ row.error_code }}</div>
            </figure>
          </div>
          <div><h3>修复观察与建议</h3><template v-if="row.analysis"><p>{{ row.analysis.observation }}</p><p>{{ row.analysis.recommendation }}</p><p>{{ row.analysis.limitations }}</p></template><p v-else>暂未分析 · {{ evidenceStateLabel(row.analysis_state) }}</p><p>{{ row.plan.coverage === 'sampled' ? '抽样细节，区域未完整覆盖' : '完整上下文' }} · {{ row.plan.windows.length }} 个窗口</p>
            <button v-if="['failed','unsupported','stale'].includes(row.evidence_state)" @click="retryRow(row,'evidence')">重试证据</button>
            <button v-if="row.evidence_state === 'ready'" @click="retryRow(row,'analysis')">重试分析</button>
            <button @click="locate(row)">定位复核</button>
            <button v-if="row.manifest.windows?.some(w=>w.assets.some(a=>a.role==='before'))" @click="compareBefore(row)">修复变化只读对照</button>
            <button @click="expanded = expanded === row.row_id ? null : row.row_id">{{ expanded === row.row_id ? '收起证据' : '展开证据与限制' }}</button>
          </div>
        </div>
        <section v-if="expanded === row.row_id">
          <p>同景比例网格，未证明严格空间配准；无真实高分辨率真值。数值不代表质量通过。</p>
          <template v-for="window in row.manifest.windows ?? []" :key="window.window_id">
            <h3>{{ window.window_id }}</h3><div class="pair"><figure v-for="a in window.assets" :key="a.role"><figcaption>{{ a.role === 'before' ? '修复前 PAN' : a.role }}</figcaption><a :href="asset(row,window,a.role)" target="_blank" rel="noopener"><img loading="lazy" width="192" height="192" :src="asset(row,window,a.role)" :alt="a.role"></a><pre>{{ a.reader.actual_window }}
有效比例 {{ a.metrics.valid_fraction }}
DN 均值 {{ a.metrics.mean }}</pre></figure></div>
          </template>
        </section>
      </article>
      <nav><button :disabled="offset === 0 || loading" @click="turn(-30)">上一页</button><span>第 {{ Math.floor(offset / 30) + 1 }} 页 · 本页 {{ rows.length }} / 共 {{ total }} 行</span><button :disabled="offset + 30 >= total || loading" @click="turn(30)">下一页</button></nav>
    </template>
  </main>
</template>
<style scoped>
.evidence-report{max-width:1100px;margin:auto;padding:20px;color:var(--ink);overflow-wrap:anywhere}header,nav{display:flex;align-items:center;gap:16px;flex-wrap:wrap}h1{font-size:24px}small{font-size:12px;color:var(--ink-sub)}h2{font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}h3{font-size:14px}article{padding:16px 0;border-bottom:1px solid var(--tb-border)}.report-row{display:grid;grid-template-columns:420px 1fr;gap:20px}.filters{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}.pair{display:flex;gap:12px;flex-wrap:wrap}figure{margin:0;max-width:100%}img,.placeholder{width:192px;height:192px;object-fit:contain;background:#eee;max-width:100%}.placeholder{display:grid;place-items:center}figcaption{font-size:12px;margin-bottom:6px}pre{white-space:pre-wrap;font-size:11px}nav{padding:20px 0}button{cursor:pointer}button:disabled{cursor:default;opacity:.5}@media(max-width:700px){.report-row{grid-template-columns:1fr}.evidence-report{padding:10px}.pair{gap:8px}.pair figure{flex:1;min-width:120px}img{width:100%;height:160px}}
</style>
