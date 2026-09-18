<script setup lang="ts">
/**
 * QcListPanel.vue — 查看器 ROI/工具 tab 置顶的「待修复清单」面板
 * ------------------------------------------------------------------
 * 导入质检部门给的《待修复清单.txt》（结构见 lib/qclist.ts），把「哪条伪影、在哪个
 * 坐标、谁提的、我走到哪一步」摆在同一屏，修完一键把处置结果写回同一份 .txt。
 *
 * 三条要点：
 * - 面板随清单存在与否两种形态：没导入只有一行「导入 .txt」；导入后是列表 + 编辑区。
 * - 「当前打开的图」自动选中对应行（按 lqPath 末段 = 生产全名匹配，见 store.selectForScene）。
 *   点该行会把画布跳到清单上的坐标 —— 注意清单写的是 **(行, 列)**，而 locatePixel(x, y)
 *   是 **(列, 行)**，传参顺序见 onRow。
 * - 「同步至指定文档」写在页脚而不是每行：它写的是整份文档，不是某一行的状态。
 */
import { computed, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useQcListStore } from '../stores/qclist';
import { useScenesStore } from '../stores/scenes';
import { useQueueStore, pathLeafOf } from '../stores/queue';
import { QC_STAGES, QC_FINALS, QC_STATUS_LABEL, isTerminal } from '../lib/qclist';
import type { QcIssue, QcStatus } from '../lib/qclist';

const viewer = useViewerStore();
const qc = useQcListStore();
const scenes = useScenesStore();
const queue = useQueueStore();

const fileInput = ref<HTMLInputElement | null>(null);
/** 只看没出终态的行（一批几十条时，修完的会一直占着屏幕）。 */
const onlyOpen = ref(false);
const opening = ref(false);

/** 当前打开的图对应的清单行名（没有 / 本地图 → 空串）。 */
const activeName = computed(() => {
  const lp = viewer.activeRec?.lqPath;
  return lp ? pathLeafOf(lp) : '';
});

const shown = computed(() => {
  if (!onlyOpen.value) return qc.issues;
  return qc.issues.filter((i) => !isTerminal(qc.statusOf(i.name)));
});

/* 选中行的名字/状态各取一个局部 computed：模板里就不用对 store 属性做类型收窄
   （`v-if="qc.selIssue"` 之后 `qc.selIssue.name` 在 vue-tsc 眼里仍是可空的）。 */
const selName = computed(() => qc.selIssue?.name ?? '');
const selStatus = computed<QcStatus | undefined>(
  () => (selName.value ? qc.statusOf(selName.value) : undefined),
);
/** 选中行在三段式里的位置；-1 = 没标、或标的是「驳回 / 非模糊通过」这类非阶段终态。 */
const curStage = computed(() => (selStatus.value ? QC_STAGES.indexOf(selStatus.value) : -1));

function onPick(e: Event) {
  const el = e.target as HTMLInputElement;
  const f = el.files?.[0];
  if (f) void qc.importFile(f);
  el.value = '';                 // 允许重复选同一个文件
}

/** 点行：选中；若这行就是当前打开的图，顺手把视图跳过去。 */
function onRow(it: QcIssue) {
  qc.select(it.name);
  if (it.name === activeName.value && it.row !== null && it.col !== null) {
    // 清单是「行列号」(行, 列)，locatePixel(x, y) 要的是 (列, 行) —— 别照抄顺序
    viewer.locatePixel(it.col, it.row);
  }
}

/** 去盘阵把这一行对应的场景开出来（不是当前图的行才显示这个入口）。 */
async function onOpen(it: QcIssue) {
  if (opening.value) return;
  opening.value = true;
  try {
    // openByName 按「错误串」返回（'' = 成功）：本面板在 /viewer，scenes.error
    // 在那里没人渲染，得把原因接过来喂 viewer 的错误条，否则点了像没反应。
    const err = await scenes.openByName(it.name);
    if (err) viewer.showErr(err);
  } finally {
    opening.value = false;
  }
}

function locText(it: QcIssue): string {
  if (it.row === null || it.col === null) return '无行列号';
  return '行 ' + it.row + ' · 列 ' + it.col;
}

function toneCls(name: string): string {
  const s = qc.statusOf(name);
  if (s === 'fixed') return 'st-ok';
  if (s === 'rejected') return 'st-fail';
  if (s === 'no_blur') return 'st-muted';
  if (s === 'submitted') return 'st-run';
  if (s === 'drawn') return 'st-pending';
  return 'st-none';
}

// 当前打开的图变了 → 自动选中对应的行（切图时不用再自己找一遍）
watch(() => viewer.activeRec?.lqPath, (lp) => qc.selectForScene(lp), { immediate: true });

// 提交 SR 后把该行推进到「已提交任务」。tasks 每次都是**整个数组换掉**
// （queue.ts 的 list() 与 SSE 的 mergeJobUpdate 都赋值 tasks.value），所以浅 watch 够。
watch(
  () => queue.tasks,
  (ts) => qc.noteSubmittedDirs(ts.map((t) => t.params.lq_path)),
);
</script>

<template>
  <section class="qc-sec">
    <!-- 头部：没导入时只有标题 + 导入；导入后是 名字 / 计数 / 三个操作 -->
    <h4 class="qc-h">
      <span class="qc-title">
        待修复清单<template v-if="qc.loaded"> · {{ qc.sourceName }}</template>
      </span>
      <span v-if="qc.loaded" class="qc-count" title="已出终态的行 / 全部行">
        {{ qc.counts.done }}/{{ qc.counts.total }}
      </span>
      <button
        type="button"
        class="qc-ob"
        :title="qc.loaded ? '换一份清单' : '导入待修复清单 .txt'"
        @click="fileInput?.click()"
      >{{ qc.loaded ? '换一份' : '导入 .txt' }}</button>
      <button
        v-if="qc.loaded"
        type="button"
        class="qc-ob"
        :class="{ on: onlyOpen }"
        title="只看还没出终态的行"
        @click="onlyOpen = !onlyOpen"
      >未完成</button>
      <button
        v-if="qc.loaded"
        type="button"
        class="qc-ob qc-x"
        title="收起清单（导入新的）"
        @click="qc.close()"
      >✕</button>
      <input
        ref="fileInput"
        type="file"
        accept=".txt,text/plain"
        style="display: none"
        @change="onPick"
      />
    </h4>

    <p v-if="!qc.loaded" class="qc-empty">
      导入质检部门的《待修复清单.txt》：逐条看伪影坐标与责任人，标记进度，
      修完一键把结果写回同一份文档。
    </p>

    <template v-else>
      <!-- 行列表 -->
      <ul v-if="shown.length" class="qc-list">
        <li
          v-for="it in shown"
          :key="it.name"
          class="qc-row"
          :class="{ on: qc.selName === it.name }"
          @click="onRow(it)"
        >
          <div class="qc-l1">
            <span class="qc-dot" :class="toneCls(it.name)"></span>
            <span class="qc-name" :title="it.name">{{ it.name }}</span>
            <button
              v-if="it.name !== activeName"
              type="button"
              class="qc-mini"
              :disabled="opening"
              title="去盘阵把这个场景打开"
              @click.stop="onOpen(it)"
            >打开</button>
            <span v-else class="qc-here" title="就是当前打开的这张图">当前</span>
          </div>
          <div class="qc-l2">
            <span>{{ locText(it) }}</span>
            <template v-if="it.imgType"><span class="qc-sep">·</span>{{ it.imgType }}</template>
            <template v-if="it.owner"><span class="qc-sep">·</span>{{ it.owner }}</template>
            <span v-if="qc.statusOf(it.name)" class="qc-badge" :class="toneCls(it.name)">
              {{ qc.labelOf(qc.statusOf(it.name)) }}
            </span>
          </div>
        </li>
      </ul>
      <p v-else class="qc-empty">
        {{ onlyOpen ? '没有未完成的行了。' : '这份清单里没有解析出问题行。' }}
      </p>

      <p v-if="opening" class="qc-busy">{{ scenes.phase || '正在打开场景…' }}</p>

      <!-- 选中行的 SOP：三段式进度 + 两个人工终态 -->
      <div v-if="selName" class="qc-edit">
        <div class="qc-stages">
          <button
            v-for="(s, i) in QC_STAGES"
            :key="s"
            type="button"
            class="stg"
            :class="{ on: curStage === i, past: curStage > i }"
            :title="'标记为「' + QC_STATUS_LABEL[s] + '」；再点一次取消'"
            @click="qc.setStatus(selName, s)"
          >
            <span class="stg-bar"></span>
            <span class="stg-txt">{{ QC_STATUS_LABEL[s] }}</span>
          </button>
        </div>
        <div class="qc-finals">
          <button
            v-for="s in QC_FINALS"
            :key="s"
            type="button"
            class="fin"
            :class="[s, { on: selStatus === s }]"
            :title="'标记为「' + QC_STATUS_LABEL[s] + '」；再点一次取消'"
            @click="qc.setStatus(selName, s)"
          >{{ QC_STATUS_LABEL[s] }}</button>
        </div>
      </div>

      <!-- 页脚：写的是整份文档，所以不属于任何一行 -->
      <div class="qc-foot">
        <button type="button" class="qc-sync" @click="qc.syncToTarget()">同步至指定文档</button>
        <button
          v-if="qc.targetName"
          type="button"
          class="qc-target"
          title="点击换一份目标文档"
          @click="qc.forgetTarget()"
        >→ {{ qc.targetName }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* 与 RoiToolsTab 的 .rt-sec 同族（那是 scoped 的，跨组件拿不到，这里照写一份） */
.qc-sec {
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  padding: 8px 10px;
}
.qc-h {
  margin: 0 0 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}
.qc-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qc-count {
  flex: none;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent-deep);
}
.qc-ob {
  flex: none;
  height: 22px;
  padding: 0 8px;
  font-size: 11px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  color: var(--ink-sub);
  cursor: pointer;
  font-family: inherit;
  transition: border-color 0.12s ease, background 0.12s ease, color 0.12s ease;
}
.qc-ob:hover { border-color: var(--accent-2); }
.qc-ob.on { background: var(--accent-soft); border-color: var(--accent-2); color: var(--accent-deep); }
.qc-x:hover { border-color: var(--err-line); background: var(--err-bg); color: var(--err); }

.qc-empty { margin: 0; font-size: 11px; line-height: 1.7; color: var(--ink-sub); }
.qc-busy { margin: 6px 0 0; font-size: 11px; color: var(--accent-deep); }

/* 行列表：一批几十条时不能把下面的卡全顶出屏幕 */
.qc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 246px;
  overflow-y: auto;
}
.qc-row {
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  padding: 4px 7px;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}
.qc-row:hover { border-color: var(--accent-2); }
.qc-row.on { border-color: var(--accent-2); background: var(--accent-soft); }
.qc-l1 { display: flex; align-items: center; gap: 6px; }
.qc-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--line);
  border: 1px solid var(--line);
}
/* 圆点与下方徽标同一套色（沿用 QueuePage / RoiToolsTab 的 st-* 语义色） */
.qc-dot.st-ok, .qc-badge.st-ok { background: var(--ok); }
.qc-dot.st-fail, .qc-badge.st-fail { background: var(--err); }
.qc-dot.st-run, .qc-badge.st-run { background: var(--accent-2); }
.qc-dot.st-pending, .qc-badge.st-pending { background: var(--warn); }
.qc-dot.st-muted, .qc-badge.st-muted { background: var(--ink-faint); }
.qc-dot.st-none { background: transparent; }

.qc-name {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qc-mini {
  flex: none;
  height: 18px;
  padding: 0 6px;
  font-size: 10px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface-2);
  color: var(--ink-sub);
  cursor: pointer;
  font-family: inherit;
}
.qc-mini:hover:not(:disabled) { border-color: var(--accent-2); color: var(--accent-deep); }
.qc-mini:disabled { opacity: 0.5; cursor: default; }
.qc-here {
  flex: none;
  font-size: 10px;
  color: var(--accent-deep);
  background: var(--accent-soft);
  border-radius: var(--r-pill);
  padding: 0 6px;
}

.qc-l2 {
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: var(--ink-faint);
}
.qc-sep { color: var(--line); }
.qc-badge {
  margin-left: auto;
  font-size: 10px;
  color: #fff;
  border-radius: var(--r-pill);
  padding: 0 6px;
  white-space: nowrap;
}

/* 选中行的 SOP */
.qc-edit {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
}
.qc-stages { display: flex; gap: 4px; }
.stg {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 3px 0 4px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
}
.stg-bar {
  width: 100%;
  height: 4px;
  border-radius: 2px;
  background: var(--line);
  transition: background 0.12s ease;
}
.stg.past .stg-bar { background: var(--accent-3); }
.stg.on .stg-bar { background: var(--accent-2); box-shadow: 0 0 0 2px var(--accent-soft); }
.stg-txt {
  font-size: 10px;
  color: var(--ink-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.stg.on .stg-txt { color: var(--accent-deep); font-weight: 600; }
.stg:hover .stg-txt { color: var(--accent-deep); }

.qc-finals { display: flex; gap: 6px; margin-top: 6px; }
.fin {
  flex: 1;
  height: 24px;
  font-size: 11px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  color: var(--ink-sub);
  cursor: pointer;
  font-family: inherit;
  transition: border-color 0.12s ease, background 0.12s ease, color 0.12s ease;
}
.fin:hover { border-color: var(--accent-2); }
.fin.on.rejected { background: var(--err-bg); border-color: var(--err-line); color: var(--err); font-weight: 600; }
.fin.on.no_blur { background: var(--surface-3); border-color: var(--ink-faint); color: var(--ink); font-weight: 600; }

.qc-foot {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
  display: flex;
  align-items: center;
  gap: 6px;
}
.qc-sync {
  height: 26px;
  padding: 0 12px;
  border: none;
  border-radius: var(--r-ctrl);
  background: var(--accent-grad);
  color: #fff;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  box-shadow: 0 2px 5px rgba(45, 164, 162, 0.22);
  transition: filter 0.15s ease;
}
.qc-sync:hover { filter: brightness(1.05); }
.qc-target {
  flex: 1;
  min-width: 0;
  height: 22px;
  padding: 0 6px;
  font-size: 10px;
  text-align: left;
  border: 1px dashed var(--line);
  border-radius: var(--r-ctrl);
  background: transparent;
  color: var(--ink-sub);
  cursor: pointer;
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qc-target:hover { border-color: var(--accent-2); color: var(--accent-deep); }
</style>
