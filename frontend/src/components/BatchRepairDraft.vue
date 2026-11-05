<script setup lang="ts">
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useScenesStore } from '../stores/scenes';
import { useBatchGoalStore } from '../stores/batchGoal';
import { useBatchDraftStore } from '../stores/batchDraft';
import { useChatStore } from '../stores/chat';
import { apiBatchImport, apiCreateBatch, apiResolveScene, loadSrConfig } from '../lib/api';
import type { BatchImportResult } from '../lib/api';
const emit = defineEmits<{ created: [] }>();
const viewer = useViewerStore();
const draft = useBatchDraftStore();
const batch = useBatchGoalStore();
const chat = useChatStore();
const text = ref('');
const query = ref('');
const filter = ref('');
const selected = ref<string[]>([]);
const title = ref('批量影像修复');
const mode = ref<'demo' | 'production'>('demo');
const imported = ref<BatchImportResult | null>(null);
const busy = ref(false);
const error = ref('');
const authorized = ref(false);
function newKey() { const bytes = new Uint32Array(4); crypto.getRandomValues(bytes); return Array.from(bytes, n => n.toString(16)).join('-'); }
const requestKey = ref(newKey());
const strategy = ref<'automatic' | 'review'>('automatic');
const rows = computed(() => draft.candidates.filter(c => (!filter.value || c.status === filter.value) && `${c.name} ${c.path} ${c.evidence}`.toLowerCase().includes(query.value.toLowerCase())));
const page = ref(0);
const pageRows = computed(() => rows.value.slice(page.value * 20, page.value * 20 + 20));
async function addViewer() {
  if (busy.value) return;
  const recs = viewer.selectedIds.length ? viewer.recs.filter(r => viewer.selectedIds.includes(r.id)) : viewer.activeRec ? [viewer.activeRec] : [];
  const eligible = recs.filter(r => !!r.lqPath);
  if (!eligible.length) { error.value = '请先打开具有可信场景路径的本体影像。'; return; }
  busy.value = true; error.value = '';
  // lqPath is a directory, not a raster. Only the trusted resolver may choose input.
  const results = await Promise.allSettled(eligible.map(async r => {
    const resolved = await apiResolveScene(loadSrConfig(), { path: r.lqPath! });
    if (!resolved.resolved.sr_capable || resolved.resolved.preview_only) throw new Error('当前资源仅供预览');
    return { path: resolved.resolved.input, name: resolved.row.name || r.name };
  }));
  for (const result of results) {
    if (result.status === 'fulfilled') draft.add([result.value]);
    else error.value += `${String(result.reason)}\n`;
  }
  imported.value = null; authorized.value = false; busy.value = false;
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
  busy.value = true; error.value = ''; imported.value = null; authorized.value = false;
  try {
    const confirmed = draft.candidates.filter(c => c.status === '已确认');
    imported.value = await apiBatchImport(text.value, mode.value, confirmed.map(c => ({ path: c.path, name: c.name, evidence: c.evidence })), strategy.value);
    requestKey.value = newKey();
  } catch (e) { error.value = String(e); } finally { busy.value = false; }
}
async function create() {
  if (!imported.value || !authorized.value || busy.value) return;
  busy.value = true; error.value = '';
  try {
    const goal = await apiCreateBatch({ manifest_ref: imported.value.manifest_ref, title: title.value, session_id: chat.sessionId, request_key: requestKey.value, exclusions: [] });
    await batch.selectGoal(goal.goal_id);
    emit('created');
    authorized.value = false;
  } catch (e) { error.value = String(e); } finally { busy.value = false; }
}
</script>
<template>
  <div class="draft" data-e2e="batch-draft">
    <p>本页草案仅保留在当前窗口；核验后形成服务端冻结清单，创建后从 SQLite 恢复执行记录。</p>
    <button :disabled="busy" @click="addViewer">加入待修复清单（当前影像 / 已选组）</button>
    <div class="row"><input v-model="query" placeholder="搜索名称、身份、依据" @input="page = 0"><select v-model="filter" @change="page = 0"><option value="">全部状态</option><option>待确认</option><option>已确认</option><option>已排除</option><option>需复核</option></select></div>
    <div class="row"><button @click="selected = pageRows.map(c => c.path)">选择本页</button><button :disabled="busy" @click="mark('已确认')">确认</button><button :disabled="busy" @click="mark('已排除')">排除</button><button :disabled="busy" @click="mark('需复核')">需复核</button></div>
    <div class="candidate-list">
      <article v-for="c in pageRows" :key="c.path">
        <label><input v-model="selected" type="checkbox" :value="c.path">{{ c.name }} · {{ c.status }}</label>
        <button class="locate" @click="locate(c.path)">查看影像</button>
        <details><summary>身份与检测依据</summary><p>{{ c.path }}</p><p>{{ c.evidence }}</p><p>问题类型 / 区域数量：待检测 · 修复参数：SR ×2</p></details>
      </article>
    </div>
    <div v-if="rows.length > 20" class="row"><button :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }} / {{ Math.ceil(rows.length / 20) }}</span><button :disabled="(page + 1) * 20 >= rows.length" @click="page++">下一页</button></div>
    <label>或导入明确指定的质检 TXT<textarea v-model="text" rows="3" placeholder="粘贴质检清单；不会扫描盘阵" :disabled="busy" @input="imported = null; authorized = false" /></label>
    <div class="row"><input v-model="title" aria-label="批次名称" maxlength="200"><select v-model="mode" aria-label="执行环境" :disabled="busy" @change="imported = null; authorized = false"><option value="demo">DEMO / MOCK</option><option value="production">生产（当前未开放）</option></select></div>
    <label>执行策略<select v-model="strategy" :disabled="busy" @change="imported = null; authorized = false"><option value="automatic">自动执行（一次授权）</option><option value="review">人工复核（逐项确认掩码）</option></select></label><p>人工复核时，检测区域与掩码确认后才允许对应 Item 提交 SR。</p>
    <button :disabled="busy || (!text.trim() && !draft.candidates.some(c => c.status === '已确认'))" @click="freeze">{{ busy ? '处理中…' : '核验并冻结执行范围' }}</button>
    <div v-if="imported" class="frozen">
      <strong>冻结清单 · {{ imported.counts.items }} 景</strong><p class="identity">{{ imported.manifest_ref }}</p>
      <div class="candidate-list"><p v-for="i in imported.items" :key="i.item_id">#{{ i.ordinal }} {{ i.name }}<br><small>{{ i.scene_ref }}</small></p></div>
      <p v-for="(d, i) in imported.diagnostics" :key="i">第 {{ d.line }} 行 · {{ d.message }}</p>
      <label><input v-model="authorized" type="checkbox" :disabled="busy || imported.rejected">我确认以上范围及所选执行规则{{ mode === 'demo' ? '（仅演示）' : '（生产提交门仍关闭）' }}</label>
      <button :disabled="busy || !authorized || imported.rejected || !imported.counts.items" @click="create">创建 Batch Goal</button>
    </div>
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
</style>
