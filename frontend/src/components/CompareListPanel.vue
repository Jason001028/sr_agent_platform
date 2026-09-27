<script setup lang="ts">
/**
 * CompareListPanel.vue — 点选清单（2026-09-20）
 * ------------------------------------------------------------------
 * 挂在 RoiToolsTab 里 **《待修复清单》上方**，`v-if="viewer.compareOn"`。
 *
 * 干什么：对比模式下已经看过的图排成一列，点一行就把那张换到**活动侧**；
 * 每行一个「清除」把它移出清单。与文件列表的分工 —— 文件列表是「打开过哪些文件」，
 * 本清单是「本次对比里拿哪些图来回比」，后者可以剪，剪完不影响文件列表条目、像素
 * 与屏幕上那张。
 *
 * **类名前缀 `.cmp-`，不复用 `.qc-*`**：`test-vue-viewer.js` 在数
 * `.qc-list .qc-row`、读 `.qc-row .qc-l2`、按文本找 `.qc-ob`，共用类名会让那些断言
 * 跟着这里的行数一起变 —— 两件事本来无关，不该绑在一起。
 */
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();

/** 一行是不是当前活动侧那张（高亮用；不参与任何判定）。 */
function isActive(id: number): boolean {
  return store.activeId === id;
}
</script>

<template>
  <section v-if="store.compareOn" class="cmp-sec" data-e2e="cmp-list">
    <h4 class="cmp-h">
      <span class="cmp-title">点选清单</span>
      <span class="cmp-count" data-e2e="cmp-count">{{ store.cmpListRecs.length }} 张</span>
    </h4>

    <p v-if="!store.cmpListRecs.length" class="cmp-empty">
      还没有可比的图。拖影像进画布，或用上方的场景芯片取同场景的三类图。
    </p>
    <div v-else class="cmp-list">
      <div
        v-for="rec in store.cmpListRecs"
        :key="rec.id"
        class="cmp-row"
        :class="{ on: isActive(rec.id) }"
        data-e2e="cmp-row"
        @click="store.activate(rec.id)"
      >
        <div class="cmp-l1">
          <span class="cmp-name" :title="rec.name">{{ rec.name }}</span>
          <span v-if="store.split" class="cmp-where">
            {{ store.paneA === rec.id ? '左' : store.paneB === rec.id ? '右' : '—' }}
          </span>
          <button
            type="button"
            class="cmp-ob"
            data-e2e="cmp-row-clear"
            title="从清单里移出（文件列表条目、像素、屏幕上那张都不动）"
            @click.stop="store.clearCompareEntry(rec.id)"
          >清除</button>
        </div>
      </div>
    </div>

    <p class="cmp-note">
      点一行 → 换到活动侧。「清除」只把它移出本清单，不关文件、不动画面。
    </p>
  </section>
</template>

<style scoped>
/* 与 .rt-sec / .qc-sec 同族（那两处是 scoped 的，跨组件拿不到，这里照写一份）。
   `.cmp-` 前缀是为了与《待修复清单》的 `.qc-*` 明确分开，见文件头注释。 */
.cmp-sec {
  flex: none;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  padding: 8px 10px;
  margin-bottom: 10px;
}
.cmp-h {
  margin: 0 0 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
}
.cmp-title { flex: 1; min-width: 0; }
.cmp-count {
  flex: none;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--accent-deep);
}
.cmp-empty { margin: 0; font-size: 11px; line-height: 1.7; color: var(--ink-sub); }

.cmp-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 168px;
  overflow-y: auto;
}
.cmp-row {
  padding: 5px 7px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}
.cmp-row:hover { border-color: var(--accent-2); }
.cmp-row.on { border-color: var(--accent-2); background: var(--accent-soft); }

.cmp-l1 { display: flex; align-items: center; gap: 6px; }
.cmp-name {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--ink-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 「在哪一侧」：分屏下点选清单里最要紧的一条信息 —— 同一张图可能就在另一侧 */
.cmp-where {
  flex: none;
  width: 18px;
  text-align: center;
  font-size: 10px;
  font-weight: 600;
  color: var(--accent-deep);
  background: var(--accent-soft);
  border-radius: var(--r-pill);
}

.cmp-ob {
  flex: none;
  height: 20px;
  padding: 0 7px;
  font-size: 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  background: var(--surface);
  color: var(--ink-sub);
  font-family: inherit;
  cursor: pointer;
}
.cmp-ob:hover { border-color: var(--err); color: var(--err); }

.cmp-note {
  margin: 6px 0 0;
  font-size: 10px;
  line-height: 1.6;
  color: var(--ink-faint);
}
</style>
