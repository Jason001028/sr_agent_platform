<script setup lang="ts">
/**
 * SceneCacheBar.vue — 场景库「清除缓存」工具行（2026-09-22 新增）
 * ------------------------------------------------------------------
 * 挂在场景库表格卡片**头部**的一条工具行：左边是全选 + 已选计数，右边两颗按钮
 * 「清除选定」「全部清除」。清的是**服务端那份预览 JPG**（盘阵上的
 * `<源 stem>_preview.jpg`），不是浏览器缓存（那个在设置浮层里有自己的按钮）。
 *
 * 三件事都在这里交代清楚，因为它们都是「删生产数据目录里的文件」这件事的一部分：
 *
 * 1. **确认**。清除选定弹一条内联确认条（不引模态框组件：本页没有第二处需要它，
 *    引一个组件只为这一句话不值得）；全部清除**要求输入确认词**才放行 —— 它一次
 *    动几十上百个场景目录。确认词定死为「清除全部」，作用只是让人停一下，
 *    它不是安全凭据（真安全在服务端的判据里，见 backend/services/preview_clear.py）。
 * 2. **范围**。全部清除 = **当前列表里的那些行**（受筛选影响，且只到 limit 那条
 *    截断线为止）。截断线外还有多少、fake 行有多少，都在确认文案里写明。
 * 3. **结果**。汇总一行 + 明细可展开（失败的自动展开）。成功的行不需要钻取，
 *    明细只列跳过与失败 —— 那是用户唯一需要动作的两类。
 *
 * 组件直接读 scenes store（同 ScenePathBar 的页面局部组件做法），不引第三方库。
 */
import { computed, ref } from 'vue';
import { useScenesStore } from '../stores/scenes.js';

const scenes = useScenesStore();

/** 「全部清除」的确认词。 */
const CLEAR_ALL_WORD = '清除全部';

/** '' = 没在确认；'selected' / 'all' = 哪一颗按钮的确认条 */
type AskMode = '' | 'selected' | 'all';
const ask = ref<AskMode>('');
const word = ref('');

/** fake 行（未配 SR_SCENES_ROOT 的占位）在盘上没有文件，清不动也不该发过去。 */
const fakeCount = computed(() => scenes.rows.filter((r) => r.fake).length);
/** 命中数里没进列表的那部分：limit 截断 / 受筛选影响。它清不到，得说出来。 */
const hiddenCount = computed(() =>
  Math.max(0, scenes.count - scenes.rows.length));

const busy = computed(() =>
  scenes.clearing || scenes.loading || !!scenes.openingId);

function askSelected(): void { ask.value = 'selected'; word.value = ''; }
function askAll(): void { ask.value = 'all'; word.value = ''; }
function cancel(): void { ask.value = ''; word.value = ''; }

/** 确认词对齐了才能点（去空格；大小写无关在这里没意义，是中文）。 */
const wordOk = computed(() => word.value.trim() === CLEAR_ALL_WORD);

async function confirm(): Promise<void> {
  const m = ask.value;
  ask.value = '';
  word.value = '';
  if (m === 'selected') await scenes.clearSelected();
  else if (m === 'all') await scenes.clearAllRows();
}

/** 明细行：逐文件的跳过与失败，成功的不列。
 *
 *  判据是**「有没有逐文件条目」**，不是「整条结论是不是 cleared」—— 一个目录完全
 *  可能「删掉了两份、跳过了第三份（没有规则戳 / 是场景源）」，那时结论是 cleared，
 *  而那份没删的恰恰是用户该看见的（他不会为了查「有没有漏」再去展开明细）。
 *  没有逐文件条目的那两种才看结论：`skipped` / `failed` 带上整条的原因
 *  （id 不可访问 / 后台正在烘焙这一景）。 */
interface Problem { key: string; dir: string | null; text: string }
const problems = computed<Problem[]>(() => {
  const out: Problem[] = [];
  for (const r of scenes.clearResult?.results ?? []) {
    if (r.skipped.length || r.failed.length) {
      for (const d of [...r.failed, ...r.skipped]) {
        out.push({ key: `${r.id}|${d.dir}|${d.name}`, dir: d.dir ?? r.dir,
                   text: `${d.name} —— ${d.reason ?? ''}` });
      }
      continue;
    }
    if (r.status === 'skipped' || r.status === 'failed') {
      out.push({ key: r.id, dir: r.dir, text: r.reason ?? '' });
    }
  }
  return out;
});
const failedCount = computed(() =>
  scenes.clearResult?.summary.failed ?? 0);
</script>

<template>
  <div class="scb">
    <div class="scb-row">
      <label class="scb-all">
        <input type="checkbox" class="scb-check" :checked="scenes.allSelected"
               :disabled="busy || !scenes.selectableRows.length"
               @change="scenes.setAllSelected(!scenes.allSelected)" />
        <span>全选</span>
      </label>
      <span class="scb-count">
        已选 <strong>{{ scenes.selectedCount }}</strong> 项
        <span class="scb-sub">（本页共 {{ scenes.selectableRows.length }} 项可清<template
          v-if="fakeCount">，另有 {{ fakeCount }} 个 fake 占位行不在盘上</template>）</span>
      </span>
      <span class="scb-spacer" />
      <button type="button" class="btn mini" :disabled="busy || !scenes.selectedCount"
              title="清除勾选场景在盘阵上的预览 JPG（<源 stem>_preview.jpg）"
              @click="askSelected">清除选定</button>
      <button type="button" class="btn mini ghost"
              :disabled="busy || !scenes.selectableRows.length"
              title="清除当前检索结果里所有场景的预览 JPG（要输入确认词）"
              @click="askAll">全部清除</button>
    </div>

    <!-- 内联确认条（不引模态框）：普通确认 / 输入确认词 -->
    <div v-if="ask" class="scb-ask">
      <template v-if="ask === 'selected'">
        <span class="scb-ask-text">
          清除选定的 <strong>{{ scenes.selectedCount }}</strong> 项？
          删掉的是盘阵上的预览 JPG（<strong>不进回收站</strong>），
          下次打开会重新烘焙（要读一遍大图，可能要等几十秒）。
        </span>
        <button type="button" class="btn mini danger" :disabled="busy"
                @click="confirm">确认清除</button>
        <button type="button" class="btn mini ghost" @click="cancel">取消</button>
      </template>
      <template v-else>
        <span class="scb-ask-text">
          将清除<strong>当前列表里的 {{ scenes.selectableRows.length }} 项</strong>
          （受筛选影响，不是盘阵上所有场景）。删掉的是盘阵上的预览 JPG，
          <strong>不进回收站</strong>，下次打开会重新烘焙。<template
            v-if="hiddenCount">另有 {{ hiddenCount }} 项命中不在这份列表里（列表最多显示
            {{ scenes.rows.length }} 条），这次清不到，要一并清请缩小筛选后再来。</template>
        </span>
        <label class="scb-word">
          <span>输入确认词</span>
          <input v-model="word" class="scb-in" type="text" spellcheck="false"
                 :placeholder="CLEAR_ALL_WORD" :disabled="busy" />
        </label>
        <button type="button" class="btn mini danger" :disabled="busy || !wordOk"
                @click="confirm">确认清除</button>
        <button type="button" class="btn mini ghost" @click="cancel">取消</button>
      </template>
    </div>

    <p v-if="scenes.clearError" class="scb-err">{{ scenes.clearError }}</p>

    <!-- 结果：汇总一行 + 明细（失败自动展开） -->
    <div v-if="scenes.clearResult" class="scb-res">
      <span class="scb-sum">{{ scenes.clearSummary }}</span>
      <details v-if="problems.length" class="scb-det" :open="failedCount > 0">
        <summary>明细（{{ problems.length }} 条跳过 / 失败）</summary>
        <ul class="scb-list">
          <li v-for="p in problems" :key="p.key">
            <span class="scb-where">{{ p.dir ?? '—' }}</span>
            <span>{{ p.text }}</span>
          </li>
        </ul>
      </details>
      <button type="button" class="btn mini ghost" title="收起这一条结果"
              @click="scenes.clearResult = null">知道了</button>
    </div>
  </div>
</template>

<style scoped>
/* 表格卡片头部的一条工具行：与表格同处一张白卡，靠下边框与表体分开 */
.scb {
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.scb-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
  padding: 10px 12px;
}
.scb-all {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
  cursor: pointer;
  user-select: none;
}
.scb-check { width: 14px; height: 14px; margin: 0; cursor: pointer; }
.scb-count { font-size: 13px; color: var(--ink-body); }
.scb-count strong { color: var(--ink); }
.scb-sub { color: var(--ink-sub); font-size: 12px; }
.scb-spacer { flex: 1 1 auto; }

.scb-ask {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
  padding: 10px 12px;
  background: var(--warn-bg);
  border-top: 1px solid var(--warn-line);
}
.scb-ask-text { flex: 1 1 320px; font-size: 12px; line-height: 1.7; color: var(--ink-body); }
.scb-ask-text strong { color: var(--warn); }
.scb-word { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-sub); }
.scb-in {
  height: 30px;
  width: 120px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  font-size: 12px;
  color: var(--ink);
  background: var(--surface);
  outline: none;
  font-family: inherit;
}
.scb-in:focus { border-color: var(--accent-3); box-shadow: 0 0 0 3px rgba(45, 164, 162, 0.14); }

.scb-err { margin: 0; padding: 0 12px 10px; color: var(--err); font-size: 12px; }

.scb-res {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
  background: var(--surface-2);
}
.scb-sum { flex: 1 1 320px; font-size: 12px; line-height: 1.6; color: var(--ink-body); }
.scb-det { flex: 1 1 100%; font-size: 12px; color: var(--ink-sub); }
.scb-det summary { cursor: pointer; color: var(--accent-deep); }
.scb-list { margin: 6px 0 0; padding-left: 18px; line-height: 1.7; word-break: break-all; }
.scb-where { color: var(--ink-sub); margin-right: 6px; }

/* 按钮（与 ScenesPage 同一套观感；组件自带样式，不依赖页面样式表） */
.btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid transparent;
  border-radius: var(--r-ctrl);
  background: var(--accent-grad);
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  transition: filter 0.15s ease, box-shadow 0.15s ease;
  box-shadow: 0 2px 6px rgba(45, 164, 162, 0.22);
}
.btn:hover { filter: brightness(1.05); }
.btn:disabled { opacity: .5; cursor: not-allowed; box-shadow: none; }
.btn.ghost {
  background: var(--surface);
  color: var(--ink-body);
  border-color: var(--line);
  box-shadow: none;
}
.btn.ghost:hover { color: var(--accent-deep); border-color: var(--accent-2); }
.btn.danger { background: var(--err); box-shadow: 0 2px 6px rgba(200, 80, 80, 0.22); }
.btn.mini { height: 28px; padding: 0 12px; font-size: 12px; font-weight: 500; }
</style>
