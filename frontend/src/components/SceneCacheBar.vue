<script setup lang="ts">
/**
 * SceneCacheBar.vue — 场景库「清除缓存」工具行（2026-09-22 新增）
 * ------------------------------------------------------------------
 * 挂在场景库表格卡片**头部**的一条工具行：左边是全选 + 已选计数，右边两颗按钮
 * 「清除选定」「全部清除」。清的是**服务端那份预览 JPG**（盘阵上的
 * `<源 stem>_preview.jpg`），不是浏览器缓存（那个在设置浮层里有自己的按钮）。
 *
 * 两个操作均长按确认。全部清除以当前列表为范围，结果汇总可展开查看跳过与失败项。
 */
import { computed } from 'vue';
import { useScenesStore } from '../stores/scenes.js';
import HoldConfirmButton from './HoldConfirmButton.vue';

const scenes = useScenesStore();

/** fake 行（未配 SR_SCENES_ROOT 的占位）在盘上没有文件，清不动也不该发过去。 */
const fakeCount = computed(() => scenes.rows.filter((r) => r.fake).length);
const offListCount = computed(() =>
  Math.max(0, scenes.count - scenes.rows.length));
const selectedKey = computed(() => JSON.stringify([...scenes.selected]));
const allKey = computed(() => JSON.stringify(scenes.selectableRows.map(row => row.id)));

const busy = computed(() =>
  scenes.clearing || scenes.loading || !!scenes.openingId);

/** 明细行：逐文件的跳过与失败，成功的不列。
 *
 *  判据是**「有没有逐文件条目」**，不是「整条结论是不是 cleared」—— 一个目录完全
 *  可能「删掉了两份、跳过了第三份（没有规则签名 / 是场景源）」，那时结论是 cleared，
 *  而那份没删的恰恰是用户该看见的（他不会为了查「有没有漏」再去展开明细）。
 *  没有逐文件条目的那两种才看结论：`skipped` / `failed` 带上整条的原因
 *  （id 不可访问 / 后台正在生成这一景的预览）。 */
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
      <HoldConfirmButton :key="'selected-' + selectedKey" class="btn mini" :disabled="busy || !scenes.selectedCount"
        :label="`清除选中的 ${scenes.selectedCount} 项预览 JPG`"
        @confirm="scenes.clearSelected()">清除选定</HoldConfirmButton>
      <HoldConfirmButton :key="'all-' + allKey" class="btn mini ghost"
        :disabled="busy || !scenes.selectableRows.length"
        :label="`清除当前列表的 ${scenes.selectableRows.length} 项预览 JPG${offListCount ? `（另有 ${offListCount} 项在列表外）` : ''}`"
        @confirm="scenes.clearAllRows()">全部清除</HoldConfirmButton>
    </div>

    <Transition name="inline-fade">
      <p v-if="scenes.clearError" class="scb-err">{{ scenes.clearError }}</p>
    </Transition>

    <!-- 结果：汇总一行 + 明细（失败自动展开） -->
    <Transition name="inline-fade">
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
    </Transition>
  </div>
</template>

<style scoped>
/* 表格卡片头部的一条工具行：与表格同处一张白卡，靠下边框与表体分开 */
.scb {
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.inline-fade-enter-active,
.inline-fade-leave-active { transition: opacity var(--motion-fade) var(--ease-standard), transform var(--motion-fade) var(--ease-standard); }
.inline-fade-enter-from,
.inline-fade-leave-to { opacity: 0; transform: translateY(-3px); }
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
  transition: filter var(--motion-hover) var(--ease-standard), box-shadow var(--motion-hover) var(--ease-standard);
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
.btn.mini { height: 28px; padding: 0 12px; font-size: 12px; font-weight: 500; }
</style>
