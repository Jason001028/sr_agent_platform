<script setup lang="ts">
/**
 * RoiToolsTab.vue — 上下文侧舱 [ROI / 工具] tab（阶段6 L1，确定性、纯前端）
 * ------------------------------------------------------------------
 * - ROI 列表：当前文件所有掩码 ROI（编号 / 原图尺度 bbox 尺寸 / 估算面积），
 *   点行选中 → 侧舱显示该 ROI 在「当前显示层（stretch 后 8bit thumb 像素）」的统计；
 *   选中 ROI 由 viewer store 按对象身份维护（TifCanvas 非绘制模式画高亮）。
 * - 统计全走 lib/roiStats.buildStats（与掩码栅格化同源判定；阈值 200/250 模块常量）。
 * - 任务状态：当前盘阵场景的最近 SR 队列任务（关联 lqPath + <stem>_mask.tif），
 *   queue store 实时 SSE；无 sceneId / 离开页面 → 整块隐藏/断开。
 * - 空态：无图 / 无 ROI 时给操作提示；无 sceneId 时任务块隐藏。
 */
import { computed, onUnmounted, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import {
  useQueueStore, tasksForScene, stateTone, pathLeafOf,
} from '../stores/queue';
import type { QueueTask } from '../lib/api.js';
import { roiOrigGeom, STAT_HI, STAT_CLIP } from '../lib/roiStats.js';

const viewer = useViewerStore();
const queue = useQueueStore();

const rec = computed(() => viewer.activeRec);
const rois = computed(() => rec.value?.maskRois ?? []);
const selIndex = computed(() => viewer.roiSelIndex());
const selValid = computed(() => selIndex.value >= 0);
const selStats = computed(() => (selValid.value ? viewer.roiStats : null));

/** ROI 几何（原图尺度 bbox + 鞋带面积）；thumb/元数据未就绪 → null。 */
const rows = computed(() => {
  const r = rec.value;
  if (!r) return [];
  const tw = r.thumb ? r.thumb.width : 0;
  const th = r.thumb ? r.thumb.height : 0;
  const list = r.maskRois ?? [];
  const haveGeom = Boolean(r.W && r.H && tw && th);
  return list.map((p, i) => {
    const g = haveGeom ? roiOrigGeom(p, r.W, r.H, tw, th) : null;
    return { i, g };
  });
});

const selGeom = computed(() => {
  const i = selIndex.value;
  if (i < 0) return null;
  return rows.value[i]?.g ?? null;
});

/* ---------------- 任务状态（仅盘阵场景） ---------------- */
const hasScene = computed(() => Boolean(rec.value?.sceneId));
const sceneTasks = computed(() => {
  const r = rec.value;
  if (!r?.lqPath) return [];
  return tasksForScene(queue.tasks, { lqPath: r.lqPath, stem: r.name }).slice(0, 8);
});

const STATE_TEXT: Record<string, string> = {
  SUBMITTING: '提交中', PENDING: '排队', RUNNING: '运行中',
  COMPLETED: '完成', FAILED: '失败', UNKNOWN: '未知',
};
function stateText(t: QueueTask): string {
  return STATE_TEXT[t.state] ?? t.state;
}
function toneCls(t: QueueTask): string {
  return 'st-' + stateTone(String(t.state));
}
function failReason(t: QueueTask): string {
  if (t.state !== 'FAILED') return '';
  const live = queue.failReason[t.task_id];
  if (live) return '失败原因：' + live;
  return t.log_dir
    ? '失败（历史记录，原因见目录 ' + pathLeafOf(t.log_dir) + '）'
    : '任务失败';
}

// 任务实时跟踪只在「盘阵场景在场」时连（本地 TIF / 离线 / 面板未开 = 不发起网络）：
// 一旦 activeRec 带 sceneId → SSE 订阅 + 拉一次（connect 幂等，切场景只刷新列表）；
// 回到本地 / 页面离开 → 断开。
watch(
  () => viewer.activeRec?.sceneId,
  (id) => {
    if (id) {
      queue.connect();
      queue.list().catch(() => { /* list 内部已置 error，无需再抛 */ });
    } else {
      queue.disconnect();
    }
  },
  { immediate: true },
);
onUnmounted(() => queue.disconnect());

/* ---------------- 格式化 ---------------- */
function f1(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return '—';
  const v = Math.round(n * 10) / 10;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}
function f2p(n: number): string {
  return (Math.round(n * 10) / 10).toFixed(1);
}
function areaText(a: number): string {
  if (a >= 1e6) return (a / 1e6).toFixed(2) + ' Mpx²';
  if (a >= 1e3) return (a / 1e3).toFixed(1) + ' kpx²';
  return a + ' px²';
}
function fmtTime(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : '—';
}
</script>

<template>
  <div class="rt">
    <p class="rt-intro">统计作用于当前显示层（stretch 后 8bit 显示像元）；画 ROI 后在此查看/点选。</p>

    <!-- ROI 列表 -->
    <section class="rt-sec">
      <h4 class="rt-h">掩码区域<template v-if="rec">（{{ rois.length }}）</template></h4>
      <p v-if="!rec" class="rt-empty">先在左侧打开一张图（或盘阵场景）。</p>
      <div v-else-if="!rois.length" class="rt-empty">
        <p>图上还没有 ROI。点「绘制掩码」用矩形 / 多边形 / 魔棒框选目标区域。</p>
        <button type="button" class="cbtn" @click="viewer.enterDraw()">开始绘制</button>
      </div>
      <ul v-else class="roi-list">
        <li
          v-for="row in rows"
          :key="row.i"
          class="roi-item"
          :class="{ on: selIndex === row.i }"
          @click="viewer.selectRoi(row.i)"
        >
          <span class="roi-no">#{{ row.i + 1 }}</span>
          <span class="roi-geom">
            <template v-if="row.g">{{ row.g.w }}×{{ row.g.h }} px</template>
            <template v-else>…</template>
          </span>
          <span class="roi-area">
            <template v-if="row.g">{{ areaText(row.g.area) }}</template>
          </span>
          <span class="roi-sel">{{ selIndex === row.i ? '✓' : '' }}</span>
        </li>
      </ul>
    </section>

    <!-- 选中 ROI 的确定性统计 -->
    <section class="rt-sec">
      <h4 class="rt-h">统计<template v-if="selValid"> · ROI #{{ selIndex + 1 }}</template></h4>

      <div v-if="selValid && selGeom" class="stats-card">
        <p class="st-geom">{{ selGeom.w }}×{{ selGeom.h }} px · 面积 ≈ {{ areaText(selGeom.area) }}</p>
        <template v-if="selStats && selStats.n > 0">
          <dl class="st-grid">
            <div><dt>像元 n</dt><dd>{{ selStats.n.toLocaleString() }}</dd></div>
            <div><dt>min</dt><dd>{{ f1(selStats.min) }}</dd></div>
            <div><dt>max</dt><dd>{{ f1(selStats.max) }}</dd></div>
            <div><dt>mean</dt><dd>{{ f1(selStats.mean) }}</dd></div>
            <div><dt>std</dt><dd>{{ f1(selStats.std) }}</dd></div>
            <div class="pct"><dt>亮像元 ≥{{ STAT_HI }}</dt><dd>{{ f2p(selStats.hiPct) }}%</dd></div>
            <div class="pct"><dt>过曝 ≥{{ STAT_CLIP }}</dt><dd>{{ f2p(selStats.clipPct) }}%</dd></div>
          </dl>
          <p v-if="selStats.sampled" class="st-note">
            行抽样统计（每 {{ selStats.stride }} 行取 1 行，覆盖 {{ selStats.rowsSampled }}/{{ selStats.rowSpan }} 行）
          </p>
        </template>
        <p v-else class="rt-empty">该 ROI 内无有效显示像元（空选区 / 全透明）。</p>
      </div>
      <p v-else class="rt-empty">未选中 ROI —— 点上方「掩码区域」行看统计。</p>
    </section>

    <!-- 任务状态（仅盘阵场景；本地 TIF / 离开页面隐藏） -->
    <section v-if="hasScene" class="rt-sec">
      <h4 class="rt-h hrow">
        <span>任务状态</span>
        <router-link class="goq" to="/queue">去队列页 →</router-link>
      </h4>
      <p v-if="queue.error" class="rt-err">{{ queue.error }}</p>
      <ul v-if="sceneTasks.length" class="qt-list">
        <li v-for="t in sceneTasks" :key="t.task_id" class="qt">
          <div class="qt-l1">
            <span class="tag" :class="toneCls(t)">{{ stateText(t) }}</span>
            <span class="qt-id">#{{ t.task_id }}</span>
            <span class="qt-scale">×{{ t.params.sr_scale }}</span>
          </div>
          <div class="qt-l2" :title="t.params.mask_path ?? ''">
            掩膜 {{ pathLeafOf(t.params.mask_path ?? '') }}
            <span class="qt-time">{{ fmtTime(t.updated_at) }}</span>
          </div>
          <p v-if="t.state === 'FAILED'" class="qt-reason">{{ failReason(t) }}</p>
          <details v-if="t.log_dir || t.fingerprint" class="qt-more">
            <summary>明细</summary>
            <p class="qt-mono">fp: {{ t.fingerprint }}</p>
            <p class="qt-mono">log_dir: {{ t.log_dir }}</p>
          </details>
        </li>
      </ul>
      <div v-else-if="!queue.loading" class="rt-empty">
        <p>该场景暂无 SR 提交。在「绘制掩码」框选后点工具栏「提交 SR」，即在此实时跟踪。</p>
        <router-link class="goq" to="/queue">去队列页填写 →</router-link>
      </div>
      <p v-else class="rt-empty">加载队列中…</p>
    </section>
  </div>
</template>

<style scoped>
.rt {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 8px 10px 14px;
  box-sizing: border-box;
  background: var(--surface);
}
.rt-intro { margin: 0; font-size: 11px; color: var(--ink-faint); line-height: 1.6; }

.rt-sec { background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-ctrl); padding: 8px 10px; }
.rt-h { margin: 0 0 6px; font-size: 12px; font-weight: 600; color: var(--ink); }
.rt-h.hrow { display: flex; align-items: center; justify-content: space-between; }
.goq { font-size: 11px; color: var(--accent-ink); text-decoration: none; font-weight: 500; }
.goq:hover { text-decoration: underline; }

.rt-empty { margin: 0; color: var(--ink-sub); font-size: 12px; line-height: 1.7; }
.rt-empty p { margin: 0 0 6px; }
.rt-err { margin: 0 0 6px; color: var(--err); font-size: 12px; }

.cbtn {
  height: 26px; padding: 0 12px;
  border: none; border-radius: var(--r-ctrl);
  background: var(--accent-grad); color: #fff; cursor: pointer; font-size: 12px;
  font-weight: 500; font-family: inherit;
  box-shadow: 0 2px 5px rgba(45, 164, 162, 0.22);
  transition: filter 0.15s ease;
}
.cbtn:hover { filter: brightness(1.05); }

/* ROI 列表 */
.roi-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.roi-item {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px; border: 1px solid var(--line); border-radius: var(--r-ctrl);
  background: var(--surface); cursor: pointer; font-size: 12px;
  transition: border-color 0.12s ease, background 0.12s ease;
}
.roi-item:hover { border-color: var(--accent-2); }
.roi-item.on {
  border-color: var(--accent-2); background: var(--accent-soft);
  box-shadow: 0 0 0 1px rgba(61, 169, 164, 0.2);
}
.roi-no { font-weight: 700; color: var(--accent-deep); font-family: var(--font-mono); }
.roi-geom { flex: 1; color: var(--ink-body); }
.roi-area { color: var(--ink-sub); font-size: 11px; }
.roi-sel { color: var(--accent-deep); width: 12px; text-align: center; }

/* 统计卡 */
.stats-card { }
.st-geom { margin: 0 0 6px; font-size: 11px; color: var(--ink-sub); }
.st-grid {
  margin: 0; display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 10px;
}
.st-grid > div {
  display: flex; align-items: baseline; justify-content: space-between;
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  padding: 4px 8px;
}
.st-grid dt { font-size: 11px; color: var(--ink-sub); }
.st-grid dd { margin: 0; font-size: 12.5px; font-weight: 600; color: var(--ink); font-family: var(--font-mono); }
.st-grid .pct dd { color: var(--accent-deep); }
.st-note { margin: 6px 0 0; font-size: 11px; color: var(--ink-faint); }

/* 任务列表 */
.qt-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.qt { border: 1px solid var(--line); border-radius: var(--r-ctrl); background: var(--surface); padding: 6px 8px; }
.qt-l1 { display: flex; align-items: center; gap: 7px; }
.tag {
  display: inline-block; font-size: 10px; padding: 1px 8px;
  border-radius: var(--r-pill); white-space: nowrap; border: 1px solid transparent;
}
.st-pending { background: var(--warn-bg); color: var(--warn); border-color: var(--warn-line); }
.st-run { background: var(--accent-soft); color: var(--accent-deep); border-color: rgba(61,169,164,0.3); }
.st-ok { background: var(--ok-bg); color: var(--ok); border-color: var(--ok-line); }
.st-fail { background: var(--err-bg); color: var(--err); border-color: var(--err-line); }
.st-muted { background: var(--surface-2); color: var(--ink-sub); border-color: var(--line); }
.qt-id { font-family: var(--font-mono); font-size: 11px; color: var(--ink-sub); }
.qt-scale { font-size: 11px; color: var(--ink-faint); }
.qt-l2 {
  margin-top: 3px; font-size: 11px; color: var(--ink-body);
  display: flex; justify-content: space-between; align-items: center; gap: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.qt-time { color: var(--ink-faint); font-size: 10px; flex: none; }
.qt-reason { margin: 4px 0 0; font-size: 11px; color: var(--err); word-break: break-all; }
.qt-more { margin-top: 4px; font-size: 11px; color: var(--ink-sub); }
.qt-more summary { cursor: pointer; color: var(--ink-sub); }
.qt-mono { margin: 2px 0 0; font-size: 10px; font-family: var(--font-mono); color: var(--ink-faint); word-break: break-all; }
</style>
