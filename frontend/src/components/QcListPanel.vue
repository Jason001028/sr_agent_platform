<script setup lang="ts">
/**
 * QcListPanel.vue — 查看器 ROI/工具 tab 置顶的「待修复清单」面板
 * ------------------------------------------------------------------
 * 导入质检部门给的《待修复清单.txt》（结构见 lib/qclist.ts），把「哪条伪影、在哪个
 * 坐标、谁提的、我走到哪一步」摆在同一屏，修完一键把处置结果写回同一份 .txt。
 *
 * 三条要点：
 * - 面板随清单存在与否两种形态：没导入只有一行「导入 .txt」；导入后是列表 + 编辑区。
 * - 「当前打开的图」自动选中对应行（按场景目录末段 = 生产全名匹配，口径见
 *   store.selectForScene —— 清单那列多半缺产品段，所以要按候选集合对，不能逐字比）。
 *   点该行会把画布跳到清单上的坐标 —— 清单那对数是 **(X=列, Y=行)**，与 locatePixel(x, y)
 *   同序，传参见 onRow（2026-09-28 真机落点实测订正：此前当成行在前，跳的是对角）。
 * - 「同步」（写回盘阵）写在页脚而不是每行：它写的是整份文档，不是某一行的状态。
 * - 「一键解析」（橘色那颗）按清单顺序逐景生成预览并往左侧暂存区落卡；点某一行会把左侧
 *   对应的卡点亮并滚到眼前（火候全在 viewer.lightCards / FileList 的 .lit）。
 */
import { computed, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useQcListStore } from '../stores/qclist';
import { useScenesStore } from '../stores/scenes';
import { useQueueStore } from '../stores/queue';
import { QC_STAGES, QC_FINALS, QC_STATUS_LABEL, isTerminal } from '../lib/qclist';
import type { QcIssue, QcStatus } from '../lib/qclist';
import { BAKE_STAGE_LABEL, sceneNameCandidates } from '../lib/qcbatch';

const viewer = useViewerStore();
const qc = useQcListStore();
const scenes = useScenesStore();
const queue = useQueueStore();

const fileInput = ref<HTMLInputElement | null>(null);
/** 只看没出终态的行（一批几十条时，修完的会一直占着屏幕）。 */
const onlyOpen = ref(false);
const opening = ref(false);

/** 当前打开的图对应的清单行名（没有 / 本地图 → 空串）。
 *
 *  取**场景目录**而不是 lqPath 的末段：中间产物（SR 产物 / NOSR）的 lqPath 可能为空，
 *  而场景目录一定有 —— 批量入列后左右两侧的联动全靠它对上。对上与否交给 store 的
 *  `rowForScene`（它才认「清单缺产品段」这件事，逐字比在这条路上恒不成立）。 */
const activeName = computed(() => {
  const rec = viewer.activeRec;
  const dir = rec?.sceneDir ?? rec?.lqPath;
  return qc.rowForScene(dir)?.name ?? '';
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

/* ---------------- 写回 ---------------- */

const syncing = ref(false);

function onPath(e: Event) {
  qc.setTarget((e.target as HTMLInputElement).value);
}

/** 把整份文档写回盘阵上那份 .txt。忙的时候挡住重复点（写盘是不能并行的事）。
 *  出错时 store 已经弹了后端原话，这里不重复报。 */
async function sync() {
  if (syncing.value || !qc.targetPath.trim()) return;
  syncing.value = true;
  try {
    await qc.syncToTarget();
  } finally {
    syncing.value = false;
  }
}

/** 点行：选中；左侧对应的卡点亮并滚到眼前；若这行就是当前打开的图，同时把视图跳过去。
 *
 *  点亮用**候选集合**（这一行的名字补上产品段后的几种形态）去对卡片的场景目录名，
 *  命中的是同一景的**全部**卡（本体 + NOSR 两张）。它与 `qc.select` 的 1:1 名字相等
 *  互不干扰：前者只给左栏上色 + 滚动，后者驱动这一行的选中态与「打开 / 当前」。
 *  没点亮任何一张时只出一声 toast —— 假阴性最坏是多说一句话，绝不会点亮错的一张。 */
function onRow(it: QcIssue) {
  qc.select(it.name);
  const ids = viewer.lightCards(sceneNameCandidates(it.name, it.imgType));
  if (!ids.length) viewer.showToast('左侧还没有这一景的卡片');
  if (it.name === activeName.value && it.row !== null && it.col !== null) {
    // 清单那对数是 (X=列, Y=行)，与 locatePixel(x, y) 同序 —— 照抄，别自己翻
    viewer.locatePixel(it.col, it.row);
  }
}

/* ---------------- 一键解析 ---------------- */

const GO_TITLE = '按清单顺序逐景解析并生成预览两份 jpg（本体 + NOSR），每景在左侧落两张卡；'
  + '失败逐条记账并继续下一景，随时可停。几十景要十几分钟。';

/** 按钮只有两种动作：待命时开跑、跑着时收手。正在收手（stopping）时再点没有意义
 *  —— 那一景的取图请求停不下来，得等它生成完。 */
function onBake() {
  if (qc.bakeState === 'running') qc.stopBake();
  else if (qc.bakeState !== 'stopping') void qc.bakeAll();
}

/** 去盘阵把这一行对应的场景开出来（不是当前图的行才显示这个入口）。 */
async function onOpen(it: QcIssue) {
  if (opening.value) return;
  opening.value = true;
  try {
    // 名字里**没有产品段**（清单第一列的常态：`…_001_L1`，盘阵上叫 `…_001_L1_PAN`），
    // 补哪一段由后端按这一行的影像类型定 —— 把它一起递过去（`imgType` 是 desc 里那格
    // 「影像类型:pan」，没写就是空串，后端见空串默认 _PAN）。
    // openByName 按「错误串」返回（'' = 成功）：本面板在 /viewer，scenes.error
    // 在那里没人渲染，得把原因接过来喂 viewer 的错误条，否则点了像没反应。
    const err = await scenes.openByName(it.name, it.imgType);
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

// 当前打开的图变了 → 自动选中对应的行（切图时不用再自己找一遍）。
// watch 的键与 activeName 同源（场景目录优先），否则两边会在中间产物上分歧。
// 这里**只**选中、不点亮：点亮是「人点了一行」的动作，自动选中跟着闪一下反而分不清
// 是「我点的」还是「它自己跳的」。
watch(
  () => viewer.activeRec?.sceneDir ?? viewer.activeRec?.lqPath,
  (dir) => qc.selectForScene(dir),
  { immediate: true },
);

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

    <!-- 一键解析：单占一行，不挤进 .qc-h（那一行 400px 里已有标题/计数/换一份/未完成/✕，
         再塞一颗要抢戏的橘色按钮，标题会被压成省略号）。
         **不做二次确认弹窗**：清空只清视图、盘上什么都没动，卡片重跑一遍就回来了。 -->
    <div v-if="qc.loaded" class="qc-go-row">
      <button
        type="button"
        class="qc-go"
        :class="qc.bakeState"
        :disabled="qc.bakeState === 'stopping'"
        :title="GO_TITLE"
        @click="onBake"
      >{{ qc.bakeHead }}</button>
      <span class="qc-go-txt">{{ qc.bakeLine }}</span>
    </div>

    <p v-if="!qc.loaded" class="qc-empty">
      导入待修复清单.txt，批量快速定位伪影模糊
    </p>

    <template v-else>
      <!-- 行列表 -->
      <ul v-if="shown.length" class="qc-list">
        <li
          v-for="it in shown"
          :key="it.name"
          class="qc-row"
          :class="{ on: qc.selName === it.name, 'bake-fail': !!qc.bakeFails[it.name] }"
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

      <!-- 一键解析的账：失败逐条给「第几步 + 后端原话」（原话里含试过哪些候选、各自
           为什么不行），「缺 NOSR」是盘上的事实、用中性色与红的失败分开。
           **不能把原因塞进 .qc-l2**：那是每行既有的一格，e2e 逐字断言它的内容与顺序。 -->
      <div v-if="qc.bakeFailList.length || qc.bakeNoteList.length" class="qc-fails">
        <p v-for="f in qc.bakeFailList" :key="'f' + f.name" class="qc-fail">
          <span class="qc-fail-n" :title="f.name">{{ f.name }}</span>
          <span class="qc-fail-s">{{ BAKE_STAGE_LABEL[f.stage] }}</span>
          <span class="qc-fail-r" :title="f.reason">{{ f.reason }}</span>
        </p>
        <p v-for="n in qc.bakeNoteList" :key="'n' + n.name" class="qc-note">
          <span class="qc-fail-n" :title="n.name">{{ n.name }}</span>
          <span class="qc-fail-r">{{ n.why }}</span>
        </p>
      </div>

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

      <!-- 页脚：写的是整份文档，所以不属于任何一行。
           目标是**粘路径**而不是文件选择器：清单在盘阵上，浏览器的选择器只能选到
           本机文件（真机页面还是 http，那个 API 干脆没有）。详注见 store。 -->
      <div class="qc-foot">
        <input
          class="qc-path"
          type="text"
          spellcheck="false"
          placeholder="W:\...\待修复清单.txt"
          title="盘阵上那份《待修复清单》的完整路径；W:\ 与 /DiskArray/ 两种形态都认"
          :value="qc.targetPath"
          :disabled="syncing"
          @input="onPath"
          @keyup.enter="sync"
        />
        <button
          type="button"
          class="qc-sync"
          :disabled="syncing || !qc.targetPath.trim()"
          :title="qc.targetPath.trim() ? '把整份文档写回这个文件（原地覆盖）' : '先填路径'"
          @click="sync"
        >{{ syncing ? '同步中…' : '同步' }}</button>
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

/* 一键解析：醒目橘色，用 --notice-job 那族令牌（**刻意非主题色** —— 主题的青绿已经被
   .file-item.active 与「盘阵」芯片占用，再借它就没有「这是一颗特别的按钮」的意思了）。 */
.qc-go-row { display: flex; align-items: center; gap: 6px; margin: 0 0 6px; }
.qc-go {
  flex: none;
  height: 24px;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  color: #fff;
  background: var(--notice-job);
  border: 1px solid var(--notice-job-ink);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  box-shadow: 0 2px 5px rgba(194, 116, 58, 0.28);
  transition: filter 0.15s ease, background 0.15s ease;
}
.qc-go:hover:not(:disabled) { filter: brightness(1.06); }
/* 跑着时这颗按钮是「停止」：底色压深一档，与待命态一眼分得开 */
.qc-go.running { background: var(--notice-job-ink); }
.qc-go.stopping {
  background: var(--surface-3);
  border-color: var(--line);
  color: var(--ink-faint);
  box-shadow: none;
}
.qc-go:disabled { cursor: default; }
.qc-go-txt {
  flex: 1;
  min-width: 0;
  font-size: 10px;
  line-height: 1.4;
  color: var(--notice-job-ink);
}

/* 跑批失败的行：一行红底 + 列表下方那份逐条原因（原因绝不放 .qc-l2） */
.qc-row.bake-fail { border-color: var(--err-line); background: var(--err-bg); }
.qc-fails {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 120px;
  overflow-y: auto;
}
.qc-fail, .qc-note { margin: 0; display: flex; align-items: baseline; gap: 5px; font-size: 10px; line-height: 1.5; }
.qc-fail { color: var(--err); }
.qc-note { color: var(--ink-faint); }
.qc-fail-n {
  flex: none;
  max-width: 46%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
}
.qc-fail-s { flex: none; }
.qc-fail-s::after { content: '——'; margin-left: 5px; color: var(--line); }
.qc-fail-r { flex: 1; min-width: 0; opacity: 0.85; }

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
/* 目标路径输入：一行的宽度要能塞下完整盘阵路径，所以占满剩余空间（10px 等宽，
   与 FileList 里那些路径同口径）。 */
.qc-path {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 10px;
}
.qc-path::placeholder { color: var(--ink-faint); }
.qc-path:focus { outline: none; border-color: var(--accent-2); }
.qc-path:disabled { color: var(--ink-faint); background: transparent; }
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
.qc-sync:hover:not(:disabled) { filter: brightness(1.05); }
.qc-sync:disabled {
  cursor: default;
  background: var(--surface-3);
  color: var(--ink-faint);
  box-shadow: none;
}
</style>
