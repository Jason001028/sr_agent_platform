<script setup lang="ts">
import { ref, computed, nextTick, onBeforeUnmount, watch } from 'vue';
import { storeToRefs, createPinia } from 'pinia';
import RepairEvidenceRow from '../components/RepairEvidenceRow.vue';
import { useRepairEvidenceStore } from '../stores/repairEvidence';
import { requestEvidenceComparison, type EvidenceComparison } from '../lib/evidenceViewer';
import { evidenceFetch } from '../lib/repairEvidence';
import { evidenceBase, evidenceStateLabel, evidenceAnalysisErrorLabel } from '../lib/repairEvidence';
import { useRoute, useRouter } from 'vue-router';
import { requestQualityReview } from '../lib/batchDock';
import { useViewerStore } from '../stores/viewer';
const props = defineProps<{ reportId?: string; embedded?: boolean }>();
const wide = ref(false);
const expandButton = ref<HTMLButtonElement | null>(null);
const reportPanel = ref<HTMLElement | null>(null);
async function setWide(value: boolean) {
  if (wide.value === value) return;
  wide.value = value;
  await nextTick();
  if (value) reportPanel.value?.focus();
  else expandButton.value?.focus();
}
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
const evidence = useRepairEvidenceStore(createPinia());
const { report, rows, offset, total, reportTotal, error, loading, evidenceFilter, analysisFilter, sort } = storeToRefs(evidence);
const expanded = ref<string | null>(null);
const base = () => evidenceBase(String(props.reportId || route.params.reportId));
const load = () => evidence.load();
function asset(row: any, window: any, role: string) { return `${base()}/rows/${row.row_id}/assets/${window.window_id}/${role}`; }
async function retryRow(row: any, stage: string) {
  const response = await fetch(`${base()}/rows/${row.row_id}/retry`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage }) });
  if (!response.ok) error.value = '来源版本失效或服务暂不可用，请查看归档证据';
  else { await load(); if (evidence.historyRowId === row.row_id) await evidence.loadHistory(row.row_id); }
}
function turn(delta: number) { expanded.value = null; evidence.turn(delta); }
watch(() => props.reportId || route.params.reportId, id => { expanded.value = null; evidence.start(String(id)); }, { immediate: true });
const range = computed(() => total.value ? `${String(offset.value + 1).padStart(2,'0')}–${String(Math.min(offset.value + 5,total.value)).padStart(2,'0')} / ${total.value}` : '00 / 0');
function filter(value: string) { analysisFilter.value = value === 'pending' ? 'not_requested' : ''; sort.value = value === 'priority' ? 'priority' : 'scene'; }
const selected = computed(() => analysisFilter.value ? 'pending' : sort.value === 'priority' ? 'priority' : 'all');
onBeforeUnmount(() => { locatorToken++; evidence.stop(); });
</script>
<template>
    <Teleport to="body" :disabled="!wide">
      <div v-if="wide" class="evidence-shade" :class="{ standalone: !embedded }" @click="setWide(false)"></div>
      <main ref="reportPanel" class="evidence-report" :class="{ 'is-wide': wide, standalone: !embedded }" :role="wide ? 'dialog' : undefined" :aria-label="wide ? '图像证据审阅' : '图像证据报告'" @keydown.esc="setWide(false)" tabindex="-1">
        <header class="report-heading"><h1><span class="report-icon">▦</span>{{ wide ? '图像证据' : '图像证据报告' }}</h1><button ref="expandButton" class="expand" :aria-expanded="wide" @click="setWide(!wide)">{{ wide ? '收起' : '横向展开' }}</button></header>
        <p v-if="error" class="notice" role="alert">{{ error }}</p>
        <p v-if="report" class="coverage"><b>{{ report.coverage.total_scenes }}</b> 景 <i>·</i> <b>{{ report.coverage.analyzed }}</b> 已分析 <i>·</i> <b>{{ report.coverage.unanalyzed }}</b> 待分析</p>
        <div class="report-toolbar"><div class="tabs" aria-label="报告筛选"><button v-for="tab in [{id:'all',label:'全部'},{id:'priority',label:'优先复核'},{id:'pending',label:'待分析'}]" :key="tab.id" :class="{active:selected === tab.id}" :aria-pressed="selected === tab.id" @click="filter(tab.id)">{{ tab.label }}</button></div>
        <details class="more-filter"><summary>筛选</summary><label>证据状态<select v-model="evidenceFilter"><option value="">全部</option><option v-for="s in ['ready','queued','failed','unsupported','stale']" :key="s" :value="s">{{ evidenceStateLabel(s) }}</option></select></label><label>分析状态<select v-model="analysisFilter"><option value="">全部</option><option v-for="s in ['ready','queued','not_requested','failed','unavailable']" :key="s" :value="s">{{ evidenceStateLabel(s) }}</option></select></label></details></div>
        <nav class="pager"><strong>证据速览</strong><span>{{ range }}</span><button aria-label="上一页" :disabled="offset === 0 || loading" @click="turn(-5)">‹</button><button aria-label="下一页" :disabled="offset + 5 >= total || loading" @click="turn(5)">›</button></nav>
        <div class="column-head"><span>区域</span><span>同位置双图</span><span>修复观察与建议</span><span>操作</span></div>
        <div class="report-rows" :aria-busy="loading">
          <p v-if="loading && !rows.length" class="notice" role="status">正在加载报告…</p><p v-else-if="!rows.length" class="notice">{{ reportTotal ? '没有符合筛选条件的区域' : '暂无可报告的修复区域' }}</p>
          <template v-for="row in rows" :key="row.row_id">
            <RepairEvidenceRow :row="row" :wide="wide" :base="base()" :expanded="expanded === row.row_id" @locate="locate(row)" @toggle="expanded = expanded === row.row_id ? null : row.row_id" />
            <section v-if="expanded === row.row_id" class="row-details">
              <h3>分析依据与限制</h3><p>{{ row.analysis?.observation || '暂未分析' }}</p><p v-if="row.analysis">{{ row.analysis.recommendation }}</p><p>{{ row.analysis?.limitations }}</p><p>{{ row.plan.coverage === 'sampled' ? '抽样细节，区域未完整覆盖' : '完整上下文' }} · {{ row.plan.windows.length }} 个窗口</p>
              <p v-if="row.error_code">{{ evidenceAnalysisErrorLabel(row.error_code) }}</p>
              <template v-for="window in row.manifest.windows ?? []" :key="window.window_id"><h4>{{ window.window_id }}</h4><div class="detail-assets"><figure v-for="a in window.assets" :key="a.role"><figcaption>{{ a.role === 'before' ? '修复前 PAN' : a.role === 'output' ? '修复产物' : 'NOSR' }}</figcaption><a :href="asset(row,window,a.role)" target="_blank" rel="noopener"><img loading="lazy" :src="asset(row,window,a.role)" :alt="a.role"></a><p>有效比例 {{ a.metrics.valid_fraction }} · DN 均值 {{ a.metrics.mean ?? '未知' }}</p></figure></div><p>{{ window.alignment_warning || (window.alignment_source === 'geotiff_affine' ? '同 CRS 仿射映射；未验证影像内容配准精度' : '同景比例网格，未证明严格空间配准') }}</p></template>
              <div class="detail-actions"><button v-if="['failed','unsupported','stale'].includes(row.evidence_state)" @click="retryRow(row,'evidence')">重试证据</button><button v-if="row.evidence_state === 'ready'" @click="retryRow(row,'analysis')">重试分析</button><button v-if="row.manifest.windows?.some(w=>w.assets.some(a=>a.role==='before'))" @click="compareBefore(row)">修复变化只读对照</button><button :aria-expanded="evidence.historyRowId === row.row_id" @click="evidence.toggleHistory(row.row_id)">分析记录</button></div>
              <div v-if="evidence.historyRowId === row.row_id"><p v-if="evidence.historyLoading">正在读取分析记录…</p><p v-else-if="evidence.historyError">{{ evidence.historyError }}</p><p v-else-if="!evidence.history.length">尚无分析记录</p><p v-for="attempt in evidence.history" :key="attempt.attempt_id">{{ new Date(attempt.created_at * 1000).toLocaleString() }} · {{ attempt.status === 'cached' ? '缓存复用' : attempt.status === 'ready' ? '分析完成' : '分析未完成' }} {{ attempt.error_code ? evidenceAnalysisErrorLabel(attempt.error_code) : '' }} {{ attempt.result?.observation }}</p></div>
            </section>
          </template>
        </div>
        <footer><span>模型意见辅助复核 · 不替代人工验收</span><button @click="load">刷新</button><a :href="`${base()}/export`" download="repair-evidence.json">JSON</a></footer>
      </main>
    </Teleport>
</template>
<style scoped>
.evidence-report{--forest:#183f3b;--muted:#7f9589;--line:#e3ebe5;box-sizing:border-box;width:100%;min-width:0;border:1px solid #d6e3da;border-radius:14px;background:#fff;color:#29483c;font:13px/1.5 system-ui,sans-serif;overflow:hidden;text-align:left}.evidence-report *{box-sizing:border-box}.standalone{max-width:1100px;margin:24px auto}.report-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:16px 12px 8px}h1{display:flex;align-items:center;gap:8px;margin:0;font-size:17px;white-space:nowrap}.report-icon{display:grid;place-items:center;width:32px;height:32px;border-radius:8px;background:#eaf1ec;color:#35735a;font-size:24px}.evidence-report button,.evidence-report a{font:inherit;color:#35654e;cursor:pointer}.evidence-report button{border:0;background:transparent;padding:6px 8px}.evidence-report button:focus-visible,.evidence-report a:focus-visible{outline:2px solid #34795b;outline-offset:2px}.evidence-report .expand{border:1px solid #d6e3da;border-radius:8px;font-weight:600;white-space:nowrap}.coverage{color:var(--muted);margin:4px 14px 12px}.coverage b{color:#47705a}.coverage i{font-style:normal;padding:0 6px;color:#bdcec2}.report-toolbar{display:flex;align-items:center;justify-content:space-between;padding:0 10px 10px;gap:4px}.tabs{display:flex;gap:3px}.tabs button{white-space:nowrap}.tabs .active{background:#eaf2ed;border-radius:7px;color:#276149;font-weight:600}.more-filter{position:relative;color:var(--muted);font-size:11px}.more-filter summary{cursor:pointer}.more-filter[open]{max-width:125px}.more-filter label{display:block}select{max-width:100%;color:#29483c;background:#fff;border:1px solid #d6e3da;border-radius:4px}.pager{display:flex;align-items:center;gap:8px;background:#f6f9f6;border-block:1px solid var(--line);padding:8px 12px;color:var(--muted)}.pager strong{margin-right:auto}.pager button{border:1px solid #dce7df;border-radius:7px;background:#fff;font-size:24px;line-height:20px;padding:2px 10px}.pager button:disabled{opacity:.35;cursor:default}.column-head{display:grid;grid-template-columns:112px 1fr;padding:7px 12px;border-bottom:1px solid var(--line);font-size:11px;color:var(--muted)}.column-head span:first-child,.column-head span:last-child{display:none}.row-details{padding:14px;background:#f5f8f5;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.row-details h3{font-size:14px;margin:0}.row-details p{margin:6px 0}.detail-assets{display:flex;flex-wrap:wrap;gap:12px}.detail-assets figure{margin:0;flex:1;min-width:100px;max-width:220px}.detail-assets img{width:100%;height:140px;object-fit:contain;background:#edf1ed}.detail-actions{display:flex;flex-wrap:wrap}footer{display:flex;align-items:center;gap:8px;padding:10px 12px;font-size:10px;color:var(--muted)}footer span{margin-right:auto}.notice{padding:12px;color:#7c6451}.evidence-shade{position:fixed;inset:55px 400px 0 0;background:#183f3b30;z-index:1000}.is-wide{position:fixed;z-index:1001;top:76px;left:24px;width:calc(100vw - 448px);max-height:calc(100vh - 100px);display:flex;flex-direction:column;box-shadow:0 20px 70px #183f3b26;margin:0}.is-wide .report-heading{padding:20px 24px 8px}.is-wide h1{font-size:23px}.is-wide .coverage{margin-left:24px}.is-wide .report-toolbar{padding-inline:18px}.is-wide .column-head{grid-template-columns:100px minmax(180px,30%) minmax(0,1fr) 110px;gap:20px;padding:12px 24px;background:#f6f9f6}.is-wide .column-head span{display:block}.is-wide .report-rows{overflow:auto;min-height:0}.is-wide footer{flex-shrink:0}.is-wide .pager{flex-shrink:0}@media(max-width:1000px){.is-wide{left:12px;width:calc(100vw - 424px)}.is-wide .column-head{display:none}}@media(max-width:700px){.is-wide{inset:64px 12px auto;width:calc(100vw - 24px)}.evidence-shade{right:0}}
.is-wide.standalone{left:50%;right:auto;transform:translateX(-50%);width:min(1100px,calc(100vw - 24px));max-width:none}
.evidence-shade.standalone{right:0}
</style>
