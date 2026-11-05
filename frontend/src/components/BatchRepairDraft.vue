<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { handoffBatch } from '../lib/batchHandoff';
import BatchItemsList from './BatchItemsList.vue';
import { useViewerStore } from '../stores/viewer';
import { useScenesStore } from '../stores/scenes';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useBatchDraftStore } from '../stores/batchDraft';
import { useChatStore } from '../stores/chat';
import { useQcListStore } from '../stores/qclist';
import { useDiffStore } from '../stores/diff';
import { apiBatchImport, apiCreateBatch, apiResolveScene, loadSrConfig } from '../lib/api';
import type { BatchImportResult } from '../lib/api';
const emit = defineEmits<{ created: [] }>();
const viewer = useViewerStore();
const draft = useBatchDraftStore();
const batch = useBatchGoalStore();
const chat = useChatStore();
const qc = useQcListStore(),diff=useDiffStore();
const { text,title,mode,strategy }=storeToRefs(draft);
const query = ref('');
const filter = ref('');
const selected = ref<string[]>([]);
const imported = ref<BatchImportResult | null>(null);
const busy = ref(false);
const error = ref('');
const authorized = ref(false);
const excludedLines = ref<number[]>([]);
const advanced = ref(false);
const formalItems = ref(false);
let frozenScope='';
const scopeKey=()=>JSON.stringify([draft.candidates,text.value,mode.value,strategy.value]);
const pending = computed(() => draft.candidates.filter(c => c.status !== '已排除'));
const reviewCount = computed(() => pending.value.filter(c => c.status === '需复核' || c.status === '待确认').length);
const keyRows = computed(() => [...pending.value].sort((a, b) => Number(b.status === '需复核') - Number(a.status === '需复核')).slice(0, 3));
// A frozen scope must be revalidated after any edit to its source candidates.
watch(() => JSON.stringify([draft.candidates,text.value,mode.value,strategy.value]), () => { imported.value = null; authorized.value = false; });
watch(()=>chat.sessionId,()=>{imported.value=null;authorized.value=false;});
const blockingErrors = computed(() => imported.value?.diagnostics.some(d => d.severity === 'error' && !excludedLines.value.includes(d.line)) ?? true);
function newKey() { const bytes = new Uint32Array(4); crypto.getRandomValues(bytes); return Array.from(bytes, n => n.toString(16)).join('-'); }
const requestKey = ref(newKey());
const rows = computed(() => draft.candidates.filter(c => (!filter.value || c.status === filter.value) && `${c.name} ${c.path} ${c.evidence}`.toLowerCase().includes(query.value.toLowerCase())));
const page = ref(0);
const pageRows = computed(() => rows.value.slice(page.value * 20, page.value * 20 + 20));
async function addViewer() {
  if (busy.value) return;
  const recs = viewer.selectedIds.length ? viewer.recs.filter(r => viewer.selectedIds.includes(r.id)) : viewer.activeRec ? [viewer.activeRec] : [];
  if(!recs.length){error.value='请先打开或选择影像';return;}
  draft.add(recs.map(r=>({path:r.lqPath||r.sceneDir||`viewer-local:${r.id}`,name:r.name,
    evidence:`Viewer 手动加入；${r.sceneId?'场景身份 '+r.sceneId:'本地影像，生产路径待关联'}；问题与区域待检测`})));error.value='';
}
function addQc(){if(qc.sourceText&&!text.value.includes(qc.sourceText))text.value=[text.value,qc.sourceText].filter(Boolean).join('\n');}
function addDifferences(){
 const r=diff.aRec;if(!r)return;
 const ids=diff.selectedRegionIds.length?diff.selectedRegionIds:diff.selectedId?[diff.selectedId]:[];
 const regions=diff.regions.filter(x=>!ids.length||ids.includes(x.id));
 draft.add([{path:r.lqPath||r.sceneDir||`viewer-local:${r.id}`,name:r.name,
   evidence:`差异候选（${diff.stale?'过期，需重算':'待人工解释'}）：`+regions.slice(0,80).map(x=>`#${x.rank} ${x.id} mean=${x.meanDiff}`).join('；')}]);
 const row=draft.candidates.find(c=>c.path===(r.lqPath||r.sceneDir||`viewer-local:${r.id}`));if(row){row.status='需复核';row.evidence=`差异候选：${regions.map(x=>'#'+x.rank+' '+x.id).join('；')}；需复核，不代表已确诊`;}
}

function mark(status: '已确认' | '已排除' | '需复核') {
  for (const c of draft.candidates) if (selected.value.includes(c.path)) c.status = status;
  imported.value = null;
  authorized.value = false;
}
async function locate(path: string) {
  const err = await useScenesStore().openQuiet(path, 'user');
  if (err) error.value = err;
}
async function freeze() {
  if (busy.value) return;
  busy.value = true; error.value = ''; imported.value = null; authorized.value = false;
  excludedLines.value = [];
  const sessionId=chat.sessionId;
  try {
    const scope = draft.candidates.filter(c => c.status !== '已排除');
    const sources:{path:string;name:string;evidence:string}[]=[];
    for(const c of scope){
      c.validationError='';
      try {
        if(c.path.startsWith('viewer-local:'))throw new Error('本地影像尚未关联可提交的生产路径');
        const resolved=await apiResolveScene(loadSrConfig(),{path:c.path});
        if(!resolved.resolved.sr_capable||resolved.resolved.preview_only)throw new Error('当前资源仅供预览，不能提交生产 SR');
        sources.push({path:resolved.resolved.input,name:c.name,evidence:c.evidence});
      }catch(e){c.validationError=String(e);c.status='需复核';}
    }
    if(scope.some(c=>c.validationError)){error.value='部分影像核验未通过，已标注原因；修正或移除后重新核验。';advanced.value=true;return;}
    const key=scopeKey();
    const result = await apiBatchImport(text.value, mode.value, sources, strategy.value);
    if(key!==scopeKey()||sessionId!==chat.sessionId){error.value='范围或会话已变化，请重新核验';return;}
    frozenScope=key;imported.value=result;
    requestKey.value = newKey();
  } catch (e) { error.value = String(e); } finally { busy.value = false; }
}
async function create() {
  if (!imported.value || !authorized.value || busy.value || blockingErrors.value || !imported.value.counts.items || frozenScope!==scopeKey()) return;
  busy.value = true; error.value = '';
  const sessionId = chat.sessionId;
  try {
    const goal = await apiCreateBatch({ manifest_ref: imported.value.manifest_ref, title: title.value, session_id: sessionId, request_key: requestKey.value, exclusions: excludedLines.value.map(line => ({ line, reason: '用户核验冻结清单后明确排除' })) });
    if (chat.sessionId !== sessionId) {
      authorized.value = false;
      error.value = `批次 ${goal.goal_id} 已在原会话创建，请返回原会话查看。`;
      return;
    }
    await batch.selectGoal(goal.goal_id);
    emit('created');
    authorized.value = false;
  } catch (e) { error.value = String(e); } finally { busy.value = false; }
}
</script>
<template>
  <div class="draft" data-e2e="batch-draft">
    <div class="draft-metrics"><div><strong>{{ pending.length }}</strong><span>待修复 · 工作清单</span></div><div><strong>{{ reviewCount }}</strong><span>需复核 · 含待确认</span></div></div>
    <p>当前窗口工作清单，尚未创建 Batch Item。</p>
    <div class="key-rows"><article v-for="c in keyRows" :key="c.path"><div class="key-row"><strong :title="c.name">{{ c.name }}</strong><span>{{ c.status }}</span></div><button class="locate" @click="locate(c.path)">查看影像</button></article></div>
    <button :disabled="busy" @click="addViewer">加入待修复清单（当前影像 / 已选组）</button>
    <button class="primary" :disabled="busy || (!text.trim() && !pending.length)" @click="freeze">{{ busy ? '处理中…' : '统一核验与冻结' }}</button>
    <button @click="handoffBatch('draft')">交给 Agent 整理</button>
    <details class="advanced" :open="advanced" @toggle="advanced = ($event.target as HTMLDetailsElement).open"><summary>清单、数据来源与执行设置</summary>
    <p>自由编辑并自动暂存；统一核验后确认一次范围与策略。准备、恢复草案均不启动 SR。</p>
    <div class="row"><button :disabled="!qc.sourceText || busy" @click="addQc">加入质检 TXT</button><button :disabled="!diff.result || busy" @click="addDifferences">加入差异候选</button></div>
    <div class="row"><input v-model="query" placeholder="搜索名称、身份、依据" @input="page = 0"><select v-model="filter" @change="page = 0"><option value="">全部状态</option><option>待确认</option><option>已确认</option><option>已排除</option><option>需复核</option></select></div>
    <div class="row"><button @click="selected = pageRows.map(c => c.path)">选择本页</button><button @click="selected = rows.map(c => c.path)">全选</button><button @click="selected = []">清选</button><button :disabled="busy" @click="mark('已排除')">排除</button><button :disabled="busy" @click="mark('需复核')">需复核</button><button :disabled="busy || !selected.length" @click="draft.remove(selected); selected=[]">移除</button></div>
    <div class="candidate-list">
      <article v-for="c in pageRows" :key="c.path">
        <label><input v-model="selected" type="checkbox" :value="c.path">{{ c.name }} · {{ c.status }}</label>
        <button class="locate" @click="locate(c.path)">查看影像</button>
        <button :disabled="busy" @click="draft.move(c.path,-1)">上移</button><button :disabled="busy" @click="draft.move(c.path,1)">下移</button>
        <p v-if="c.validationError" role="alert" class="error">{{ c.validationError }}</p>
        <label>影像引用 / 路径<input v-model="c.path" :disabled="busy"></label><label>建议与复核依据<input v-model="c.evidence" :disabled="busy"></label>
        <details><summary>身份与检测依据</summary><p>{{ c.path }}</p><p>{{ c.evidence }}</p><p>问题类型 / 区域数量：待检测 · 修复参数：SR ×2</p></details>
      </article>
    </div>
    <div v-if="rows.length > 20" class="row"><button :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }} / {{ Math.ceil(rows.length / 20) }}</span><button :disabled="(page + 1) * 20 >= rows.length" @click="page++">下一页</button></div>
    <label>或导入明确指定的质检 TXT<textarea v-model="text" rows="3" placeholder="粘贴质检清单；不会扫描盘阵" :disabled="busy" @input="imported = null; authorized = false" /></label>
    <div class="row"><input v-model="title" aria-label="批次名称" maxlength="200"><select v-model="mode" aria-label="执行环境" :disabled="busy" @change="imported = null; authorized = false"><option value="demo">DEMO / MOCK</option><option value="production">生产（当前未开放）</option></select></div>
    <label>执行策略<select v-model="strategy" :disabled="busy" @change="imported = null; authorized = false"><option value="automatic">自动执行（一次授权）</option><option value="review">人工复核（逐项确认掩码）</option></select></label><p>人工复核时，检测区域与掩码确认后才允许对应 Item 提交 SR。</p>
    </details>
    <div v-if="imported" class="frozen">
      <strong>冻结清单 · {{ imported.counts.items }} 景</strong><p class="identity">{{ imported.manifest_ref }}</p>
      <div class="candidate-list"><p v-for="i in imported.items" :key="i.item_id">#{{ i.ordinal }} {{ i.name }}<br><small>{{ i.scene_ref }}</small></p></div>
      <p v-for="(d, i) in imported.diagnostics" :key="i">第 {{ d.line }} 行 · {{ d.message }}<label v-if="d.severity === 'error' && d.line > 0"><input v-model="excludedLines" type="checkbox" :value="d.line" :disabled="busy" @change="authorized = false">确认排除此行</label></p>
      <label><input v-model="authorized" type="checkbox" :disabled="busy || blockingErrors">我确认以上范围及所选执行规则{{ mode === 'demo' ? '（仅演示）' : '（生产提交门仍关闭）' }}</label>
      <button :disabled="busy || !authorized || blockingErrors || !imported.counts.items" @click="create">创建 Batch Goal</button>
    </div>
    <details v-if="batch.goalId" @toggle="formalItems = ($event.target as HTMLDetailsElement).open"><summary>正式 Batch Item · 冻结执行清单 / 掩码复核</summary><BatchItemsList v-if="formalItems" /></details>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </div>
</template>
<style scoped>
.draft { padding: 10px; display: grid; gap: 8px; font-size: 12px; color: var(--ink-sub); }
p { margin: 0; font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.row { display: flex; gap: 5px; align-items: center; }
input:not([type=checkbox]) { min-width: 0; flex: 1; width: 100%; }
button, select, textarea, input:not([type=checkbox]) { font: inherit; color: var(--ink); border: 1px solid var(--line); background: var(--surface); border-radius: 8px; padding: 5px 7px; min-height: 28px; }
button { cursor: pointer; color: var(--accent-deep); }
button:disabled { opacity: .5; cursor: default; }
textarea { display: block; width: 100%; box-sizing: border-box; margin-top: 5px; }
.candidate-list { max-height: 260px; overflow: auto; }
article { padding: 8px 0; border-bottom: 1px solid var(--line); }
article label { font-weight: 600; overflow-wrap: anywhere; }
details { font-size: 11px; margin-top: 5px; }
.locate { padding: 2px 5px; margin-top: 4px; }
.frozen { display: grid; gap: 7px; padding: 8px; background: var(--surface-2); border-radius: 10px; }
.identity { font-family: monospace; }
.error { color: #9b5145; }
.draft { padding: 16px 12px; gap: 10px; }
.draft-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.draft-metrics div { display: grid; gap: 4px; } .draft-metrics strong { font-size: 26px; font-weight: 500; color: var(--ink); } .draft-metrics div:nth-child(2) strong { color: #B78C4D; } .draft-metrics span { font-size: 11px; }
.key-row { display: flex; gap: 8px; align-items: center; } .key-row strong { min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; color: var(--ink); } .key-row span { font-size: 10px; flex: none; }
.advanced[open] { display: grid; gap: 9px; } summary { cursor: pointer; min-height: 34px; align-content: center; }
button, select, input:not([type=checkbox]) { min-height: 34px; box-sizing: border-box; } select { max-width: 100%; } label { overflow-wrap: anywhere; }
button.primary { background: #245B50; color: white; border-color: #245B50; }
button.primary:disabled { opacity: 1; background: #eef1f0; color: #8a9692; border-color: var(--line); }
</style>
