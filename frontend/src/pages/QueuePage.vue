<script setup lang="ts">
/**
 * QueuePage.vue — 共享 SR 任务队列（阶段5，api-contract.md §3.3）
 * 顶部 = 提交 SR 作业表单（lq_path/mask_path/scale…，run_sr 参数）；掩码烘焙跳转
 * 预填（setDraft → form），用户确认才提交（Slurm 是真副作用，不自动提交）。
 * 下方 = 任务表：SSE job_update 实时刷 state 徽标（SUBMITTING→PENDING→RUNNING→COMPLETED/FAILED）。
 */
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useQueueStore, defaultForm, draftToForm, formToSubmit, stateTone } from '../stores/queue.js';
import type { QueueDraft, QueueForm } from '../stores/queue.js';
import type { QueueTask } from '../lib/api.js';

const queue = useQueueStore();
const showForm = ref(false);
const cancelling = ref<number | null>(null);
const formErr = ref('');
let lastSyncDraft: QueueDraft | null = null;

const f = reactive<QueueForm>(defaultForm());

const STATE_TEXT: Record<string, string> = {
  SUBMITTING: '提交中', PENDING: '排队', RUNNING: '运行中',
  COMPLETED: '完成', FAILED: '失败', UNKNOWN: '未知',
};

function stateText(t: QueueTask): string {
  return STATE_TEXT[t.state] ?? t.state;
}
function stateCls(t: QueueTask): string {
  return 'st-' + stateTone(String(t.state));
}
function isActive(t: QueueTask): boolean {
  return t.state === 'SUBMITTING' || t.state === 'PENDING' || t.state === 'RUNNING';
}
function shortFp(s: string): string {
  return s.length > 12 ? s.slice(0, 12) + '…' : s;
}
function pathLeaf(p: string | null): string {
  if (!p) return '';
  const parts = p.split(/[/\\]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}
function fmtTime(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : '—';
}

function syncFormFromDraft(d: QueueDraft): void {
  Object.assign(f, draftToForm(d));
  lastSyncDraft = d;
  formErr.value = '';
  showForm.value = true;
}

async function openSubmit(): Promise<void> {
  formErr.value = '';
  if (!f.lq_path) { formErr.value = '请填 lq_path（原图目录，须绝对路径）'; return; }
  if (!f.lq_path.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(f.lq_path)) {
    formErr.value = 'lq_path 须为绝对路径（盘阵挂载点，如 /DiskArray/…）';
    return;
  }
  try {
    const body = formToSubmit(f);
    await queue.submit(body);
    formErr.value = '';
  } catch (e) {
    formErr.value = e instanceof Error ? e.message : String(e);
  }
}

async function onCancel(t: QueueTask): Promise<void> {
  cancelling.value = t.task_id;
  try { await queue.cancel(t.task_id); } finally { cancelling.value = null; }
}

watch(() => queue.draft, (d) => {
  if (d && d !== lastSyncDraft) syncFormFromDraft(d);
});

onMounted(() => {
  if (queue.draft) syncFormFromDraft(queue.draft);
  void queue.list();
  queue.connect();
});
onUnmounted(() => queue.disconnect());
</script>

<template>
  <div class="queue-page">
    <div class="qp-head">
      <h2>共享任务队列 <span class="qp-sub">SR 作业（slurm / 假调度器）</span></h2>
      <div class="qp-actions">
        <span class="qp-dot" :class="{ on: queue.connected }"></span>
        <span class="qp-conn">{{ queue.connected ? 'SSE 已连接' : 'SSE 断开' }}</span>
        <button type="button" class="btn ghost" :disabled="queue.loading" @click="queue.list()">
          {{ queue.loading ? '刷新中…' : '刷新' }}
        </button>
        <button type="button" class="btn" @click="showForm = !showForm">
          {{ showForm ? '收起提交面板' : '提交 SR 作业' }}
        </button>
      </div>
    </div>

    <p v-if="queue.error || formErr" class="qp-err">
      <template v-if="queue.error">{{ queue.error }}</template>
      <template v-else>{{ formErr }}</template>
    </p>

    <!-- 提交表单（掩码烘焙后预填；确认才提交） -->
    <section v-if="showForm" class="qp-form">
      <div v-if="queue.draft" class="qp-draft-tip">
        已带入查看器掩码烘焙结果（<code>{{ pathLeaf(queue.draft.mask_path) }}</code>）——检查参数后点提交。
      </div>
      <div class="qp-grid">
        <label class="qp-cell wide">
          <span>lq_path（原图目录，绝对路径）</span>
          <input v-model="f.lq_path" type="text" spellcheck="false" placeholder="/DiskArray/GF07A03_xxx_L1_PAN" />
        </label>
        <label class="qp-cell wide">
          <span>mask_path（掩膜，可空=全图）</span>
          <input v-model="f.mask_path" type="text" spellcheck="false" placeholder="…_mask.tif" />
        </label>
        <label class="qp-cell">
          <span>SR 倍率</span>
          <input v-model.number="f.sr_scale" type="number" min="1" max="8" step="1" />
        </label>
        <label class="qp-cell">
          <span>后缀</span>
          <input v-model="f.suffix" type="text" spellcheck="false" placeholder="t / 空" />
        </label>
        <label class="qp-cell">
          <span>GPU 数</span>
          <input v-model.number="f.gpu" type="number" min="0" max="16" step="1" />
        </label>
        <label class="qp-cell">
          <span>云量上限 %</span>
          <input v-model.number="f.cloud_limit" type="number" min="0" max="100" step="1" />
        </label>
        <label class="qp-cell check">
          <input v-model="f.delete_ori" type="checkbox" />
          <span>完成删原图（delete_ori）</span>
        </label>
        <label class="qp-cell check">
          <input v-model="f.grid_align" type="checkbox" />
          <span>网格对齐（grid_align）</span>
        </label>
      </div>
      <div class="qp-submit-row">
        <button type="button" class="btn" :disabled="queue.loading" @click="openSubmit()">
          提交到 Slurm
        </button>
        <span class="qp-hint">提交是真实副作用（假调度器下也会跑完整状态机）</span>
      </div>
    </section>

    <!-- 任务表 -->
    <div class="qp-tbl-wrap">
      <table class="qp-tbl">
        <thead>
          <tr>
            <th>状态</th><th>task_id</th><th>job_id</th><th class="left">指纹</th>
            <th class="left">参数（lq_path / scale）</th>
            <th>创建时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in queue.tasks" :key="t.task_id">
            <td><span class="tag" :class="stateCls(t)">{{ stateText(t) }}</span></td>
            <td>{{ t.task_id }}</td>
            <td>{{ t.job_id ?? '—' }}</td>
            <td class="left mono" :title="t.fingerprint">{{ shortFp(t.fingerprint) }}</td>
            <td class="left" :title="t.params.lq_path">
              {{ pathLeaf(t.params.lq_path) }}
              <span v-if="t.params.mask_path" class="qp-mask" :title="t.params.mask_path">
                掩膜 {{ pathLeaf(t.params.mask_path) }}
              </span>
              <span class="qp-sub2">×{{ t.params.sr_scale }} · {{ t.params.suffix || '无后缀' }}</span>
            </td>
            <td>{{ fmtTime(t.created_at) }}</td>
            <td>
              <button v-if="isActive(t) && t.job_id" type="button" class="btn mini ghost"
                      :disabled="cancelling === t.task_id" @click="onCancel(t)">
                {{ cancelling === t.task_id ? '取消中…' : '取消' }}
              </button>
              <span v-else class="qp-muted">—</span>
            </td>
          </tr>
          <tr v-if="!queue.loading && !queue.tasks.length">
            <td colspan="7" class="empty">暂无 SR 任务。在查看器画完掩码「提交 SR」，或在上方手填提交。</td>
          </tr>
        </tbody>
      </table>
      <p v-if="queue.loading" class="qp-loading">加载中…</p>
    </div>
  </div>
</template>

<style scoped>
.queue-page { max-width: 1240px; margin: 0 auto; padding: 10px 20px 44px; }
.qp-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 14px; }
.qp-head h2 { margin: 0; font-size: 24px; font-weight: 700; color: var(--ink); display: flex; align-items: baseline; gap: 10px; }
.qp-sub { font-size: 12px; color: var(--ink-sub); font-weight: 400; }
.qp-actions { display: flex; align-items: center; gap: 10px; }
.qp-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-faint); display: inline-block; transition: background 0.2s ease; }
.qp-dot.on { background: var(--ok); box-shadow: 0 0 0 3px var(--ok-bg); }
.qp-conn { font-size: 12px; color: var(--ink-sub); }
.qp-err { color: var(--err); font-size: 13px; margin: 0 0 12px; }

.btn {
  height: 32px; padding: 0 14px;
  border: none; border-radius: var(--r-ctrl);
  background: var(--accent-grad); color: #fff; cursor: pointer; font-size: 13px;
  font-weight: 500; font-family: inherit;
  box-shadow: 0 2px 6px rgba(45, 164, 162, 0.22);
  transition: filter 0.15s ease;
}
.btn:hover { filter: brightness(1.05); }
.btn:disabled { opacity: .55; cursor: not-allowed; box-shadow: none; }
.btn.ghost { background: var(--surface); color: var(--ink-body); border: 1px solid var(--line); box-shadow: none; }
.btn.ghost:hover { color: var(--accent-deep); border-color: var(--accent-2); }
.btn.mini { height: 24px; padding: 0 10px; font-size: 12px; }

/* 提交表单：白色卡片 */
.qp-form {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  padding: 18px 20px;
  margin: 0 0 16px;
}
.qp-draft-tip {
  background: var(--ok-bg); border: 1px solid var(--ok-line); color: var(--ok);
  font-size: 12.5px; padding: 8px 12px; border-radius: var(--r-ctrl); margin-bottom: 14px;
}
.qp-draft-tip code { font-family: var(--font-mono); }
.qp-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px 14px; }
.qp-cell { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--ink-sub); }
.qp-cell.wide { grid-column: span 2; }
.qp-cell.check { flex-direction: row; align-items: center; gap: 7px; font-size: 13px; color: var(--ink-body); }
.qp-cell input[type="text"], .qp-cell input[type="number"] {
  height: 34px; padding: 0 10px;
  border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface-2); color: var(--ink); font-size: 13px; font-family: inherit;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
}
.qp-cell input::placeholder { color: var(--ink-faint); }
.qp-cell input:focus { border-color: var(--accent-3); background: var(--surface); box-shadow: 0 0 0 3px rgba(45, 164, 162, 0.14); }
.qp-cell input[type="checkbox"] { accent-color: var(--accent-3); width: 15px; height: 15px; }
.qp-submit-row { display: flex; align-items: center; gap: 10px; margin-top: 16px; }
.qp-submit-row .btn { height: 36px; padding: 0 22px; }
.qp-hint { font-size: 12px; color: var(--ink-faint); }

/* 任务表：白色卡片 */
.qp-tbl-wrap {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-panel);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.qp-tbl {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
  background: var(--surface);
  color: var(--ink-body);
}
.qp-tbl th, .qp-tbl td {
  padding: 9px 12px;
  text-align: center;
  border-bottom: 1px solid var(--line);
}
.qp-tbl th { background: var(--surface-2); color: var(--ink); font-weight: 600; white-space: nowrap; }
.qp-tbl td.left { text-align: left; }
.qp-tbl td.empty { color: var(--ink-sub); padding: 22px; text-align: center; }
.qp-tbl tbody tr:hover td { background: #fafcfa; }
.qp-tbl tbody tr:last-child td { border-bottom: none; }
.qp-tbl .mono { font-family: var(--font-mono); font-size: 12px; }
.tag {
  display: inline-block; font-size: 11px; padding: 2px 10px;
  border-radius: var(--r-pill); white-space: nowrap;
  border: 1px solid transparent;
}
.st-pending { background: var(--warn-bg); color: var(--warn); border-color: var(--warn-line); }
.st-run { background: var(--accent-soft); color: var(--accent-deep); border-color: rgba(61,169,164,0.3); }
.st-ok { background: var(--ok-bg); color: var(--ok); border-color: var(--ok-line); }
.st-fail { background: var(--err-bg); color: var(--err); border-color: var(--err-line); }
.st-muted { background: var(--surface-2); color: var(--ink-sub); border-color: var(--line); }
.qp-mask { color: var(--accent-deep); background: var(--accent-soft); padding: 0 6px; border-radius: 6px; margin-left: 6px; }
.qp-sub2 { color: var(--ink-sub); margin-left: 8px; font-size: 12px; }
.qp-muted { color: var(--ink-faint); }
.qp-loading { color: var(--ink-sub); font-size: 12px; }
</style>
