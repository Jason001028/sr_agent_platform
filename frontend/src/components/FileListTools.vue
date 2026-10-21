<script setup lang="ts">
/**
 * FileListTools.vue — 左侧列表顶栏（只有图标，说明全在 title 里）
 * ------------------------------------------------------------------
 * 六颗键同在一颗内容宽的白色胶囊里：勾选与收藏 / 三种移除 / 撤回，
 * 间距一律 4px，不再画竖分隔线（那会让相邻间隔变成两种节奏）。
 * 图标键不写可见文字：一排小键上面再压一行字会把列表顶得很高，而每个动作
 * 在悬停 title 里都有一句完整说明（也兼作 aria-label）。
 *
 * 「一键解析」跑批期间整排禁用：批量清理由列表结构驱动，跑着时移除卡片会让
 * 占位卡的原地补齐落空（见 stores/qclist.ts 的 bakeAll），不该从这个口子进。
 */
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useQcListStore } from '../stores/qclist';
import AppIcon from './AppIcon.vue';
import FavoriteStar from './FavoriteStar.vue';

const store = useViewerStore();
const qc = useQcListStore();
const favPulseKey = ref(0);

const baking = computed(
  () => qc.bakeState === 'running' || qc.bakeState === 'stopping',
);
const selCount = computed(() => store.selectedIds.length);

/** 选中里全收藏了 → 这颗键变成「取消收藏」（同一颗键两个方向，少一颗）。 */
const selAllFav = computed(() => {
  if (!selCount.value) return false;
  const targets = store.selectedIds
    .map((id) => store.recs.find((r) => r.id === id))
    .filter((r) => !!r);
  return targets.length > 0 && targets.every((r) => r!.fav);
});

const redundantCount = computed(
  () => store.recs.filter((r) => r.statusCls === 'err' && !r.fav).length,
);
const clearableCount = computed(() => store.recs.filter((r) => !r.fav).length);

const bakeHint = '一键解析进行中，先等它跑完或按停止';

const selectTitle = computed(() => {
  if (baking.value) return bakeHint;
  if (!store.recs.length) return '还没有影像：先选择或拖入影像';
  return store.selectMode ? '退出多选（Esc）' : '多选：点卡片勾选，再收藏或移除';
});
const favTitle = computed(() => {
  if (baking.value) return bakeHint;
  if (!store.selectMode) return '收藏选中卡：先点「多选」勾选卡片';
  if (!selCount.value) return '收藏选中卡：先在列表里勾选卡片';
  return selAllFav.value
    ? `取消收藏（${selCount.value} 张）`
    : `收藏选中卡（${selCount.value} 张，置顶且不会被批量清理）`;
});
const removeSelTitle = computed(() => {
  if (baking.value) return bakeHint;
  if (!store.selectMode) return '移除选中：先点「多选」勾选卡片';
  if (!selCount.value) return '移除选中：先在列表里勾选卡片';
  return `移除选中的 ${selCount.value} 张（Delete）`;
});
const redundantTitle = computed(() => {
  if (baking.value) return bakeHint;
  return redundantCount.value
    ? `清理冗余：移除 ${redundantCount.value} 张失败的卡（收藏的保留）`
    : '清理冗余：没有失败的卡';
});
const clearAllTitle = computed(() => {
  if (baking.value) return bakeHint;
  return clearableCount.value
    ? `清理全部：移除 ${clearableCount.value} 张未收藏的卡（收藏的保留）`
    : '清理全部：没有可清理的卡';
});
const undoTitle = computed(() => {
  if (baking.value) return bakeHint;
  return store.canUndo ? `撤回上一次操作：${store.undoLabel}` : '没有可撤回的列表操作';
});
function toggleFavorite(): void {
  store.toggleFavorite();
  favPulseKey.value++;
}
</script>

<template>
  <div class="file-tools" data-e2e="file-tools" :class="{ baking }">
    <div class="ft-pill">
      <button
        type="button"
        class="ft-btn"
        :class="{ on: store.selectMode }"
        data-e2e="ft-select"
        :disabled="baking || !store.recs.length"
        :aria-pressed="store.selectMode"
        :aria-label="selectTitle"
        :title="selectTitle"
        @click="store.toggleSelectMode()"
      >
        <AppIcon name="list-plus" :size="16" />
      </button>
      <button
        type="button"
        class="ft-btn"
        data-e2e="ft-fav"
        :disabled="baking || !store.selectMode || !selCount"
        :aria-label="favTitle"
        :title="favTitle"
        @click="toggleFavorite"
      >
        <FavoriteStar :size="16" :filled="selAllFav" :pulse-key="favPulseKey" />
      </button>
      <button
        type="button"
        class="ft-btn"
        data-e2e="ft-remove-selected"
        :disabled="baking || !store.selectMode || !selCount"
        :aria-label="removeSelTitle"
        :title="removeSelTitle"
        @click="store.removeSelected()"
      >
        <AppIcon name="x-circle" :size="16" />
      </button>
      <button
        type="button"
        class="ft-btn"
        data-e2e="ft-redundant"
        :disabled="baking || !redundantCount"
        :aria-label="redundantTitle"
        :title="redundantTitle"
        @click="store.removeRedundant()"
      >
        <AppIcon name="brush-cleaning" :size="16" />
      </button>
      <button
        type="button"
        class="ft-btn"
        data-e2e="ft-clear-all"
        :disabled="baking || !clearableCount"
        :aria-label="clearAllTitle"
        :title="clearAllTitle"
        @click="store.removeAll()"
      >
        <AppIcon name="trash" :size="16" />
      </button>
      <button
        type="button"
        class="ft-btn"
        data-e2e="ft-undo"
        :disabled="baking || !store.canUndo"
        :aria-label="undoTitle"
        :title="undoTitle"
        @click="store.undo()"
      >
        <AppIcon name="undo" :size="16" />
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 顶栏贴住侧栏上沿：负边距抵消 aside 的 10px 内距；滚起来时由这层 chrome 底色
   挡住卡片。胶囊自己再靠白底 + 三层阴影浮起来，所以这层不再画分隔线。 */
.file-tools {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  justify-content: center;
  margin: -10px -10px 8px;
  padding: 8px 10px;
  background: var(--chrome);
}
/* 三层阴影：近处细而清（1px）、中层过渡（4px）、远处更淡更大（12px）。 */
.ft-pill {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface);
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.04),
    0 4px 12px rgba(0, 0, 0, 0.06),
    0 12px 32px rgba(0, 0, 0, 0.08);
}
.ft-btn {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;                 /* 胶囊内用正圆回应外层的全圆角 */
  background: transparent;
  cursor: pointer;
  /* 图标是黑色素材：<img> 里的 currentColor 取不到按钮的 color，所以状态只靠
     底色与描边表达，别再给 color 写令牌。 */
  transition: background var(--motion-hover) var(--ease-standard),
              border-color var(--motion-hover) var(--ease-standard);
}
/* AppIcon 自带 width/height:auto，会盖掉 size 属性（24 视图的旧素材因此不缩），
   这里把顶栏图标一律钉成 16px。 */
.ft-btn :deep(.app-icon) {
  width: 16px;
  height: 16px;
}
.ft-btn:hover:not(:disabled) {
  background: var(--accent-soft);
  border-color: var(--accent-1);
}
.ft-btn:focus-visible {
  outline: 2px solid var(--accent-3);
  outline-offset: 1px;
}
/* 多选开着：这颗键保持按下态，一眼知道卡片为什么点了不换图。 */
.ft-btn.on {
  background: var(--accent-soft);
  border-color: var(--accent-1);
}
.ft-btn:disabled { opacity: .4; cursor: default; }
</style>
