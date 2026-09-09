<script setup lang="ts">
/**
 * FileList.vue — 侧栏文件列表 + 收起/展开（tif-viewer.html #sidebar + addListItem 直译）
 * ------------------------------------------------------------------
 * 每项：名称 / 大小·W×H·布局 / 解码状态（绿红）/ JPG 导出状态 + 重新导出 / × 移除 / active 高亮。
 * 收起 toggle 秒出无动画（HTML sideToggle）。
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';
import type { ViewerRec } from '../stores/viewer';

const store = useViewerStore();

const collapseBtn = computed(() => (store.sidebarCollapsed ? '»' : '«'));
const collapseTitle = computed(() => (store.sidebarCollapsed ? '展开文件列表' : '收起文件列表'));

function fmtBytes(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

function onJpgClick(e: MouseEvent, rec: ViewerRec) {
  e.stopPropagation();
  const el = e.target as HTMLElement;
  if (el.classList.contains('rex')) store.reExportJpg(rec.id);
}
</script>

<template>
  <aside class="sidebar" :class="{ collapsed: store.sidebarCollapsed }">
    <div v-if="!store.recs.length" class="tip">选择或拖入 TIF 文件开始</div>
    <div
      v-for="rec in store.recs"
      :key="rec.id"
      class="file-item"
      :class="{ active: rec.id === store.activeId }"
      @click="store.activate(rec.id)"
    >
      <span class="close" title="移除" @click.stop="store.removeRec(rec.id)">×</span>
      <div class="name">
        <span v-if="rec.route === 'jpg'" class="scn" title="盘阵场景：服务器烘焙 JPG">盘阵</span>{{ rec.name }}
      </div>
      <div class="meta">
        {{ fmtBytes(rec.size) }}
        <template v-if="rec.W"> · {{ rec.W }}×{{ rec.H }}</template>
        <template v-if="rec.layout"> · {{ rec.layout }}</template>
      </div>
      <div class="status" :class="rec.statusCls">{{ rec.status }}</div>
      <div
        v-if="rec.jpgStatus"
        class="jpg"
        :class="rec.jpgCls"
        @click.stop="onJpgClick($event, rec)"
      >
        {{ rec.jpgStatus }}
        <span v-if="rec._jpgDone" class="rex" title="按当前拉伸模式重新生成 JPG">（重新导出）</span>
      </div>
    </div>
  </aside>
  <div class="side-toggle" :title="collapseTitle" @click="store.sidebarCollapsed = !store.sidebarCollapsed">
    {{ collapseBtn }}
  </div>
</template>

<style scoped>
/* 侧栏：浅莫兰迪 chrome，白文件卡浮其上；选中青绿描边 */
.sidebar {
  width: 268px;
  flex: none;
  background: var(--chrome);
  border-right: 1px solid var(--line);
  overflow-y: auto;
  padding: 10px;
}
.sidebar.collapsed {
  width: 0;
  padding: 0;
  border-right: none;
  overflow: hidden;
}
.tip { color: var(--ink-sub); font-size: 12px; padding: 12px 8px; }

.side-toggle {
  flex: none;
  width: 26px;
  background: var(--surface-2);
  border-left: 1px solid var(--line);
  border-right: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--ink-sub);
  font-size: 13px;
  user-select: none;
  transition: color 0.15s ease, background 0.15s ease;
}
.side-toggle:hover { color: var(--accent-deep); background: var(--accent-soft); }

.file-item {
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.file-item:hover { border-color: #d6ddd9; }
.file-item.active {
  border-color: var(--accent-2);
  background: var(--accent-soft);
  box-shadow: 0 0 0 1px rgba(61, 169, 164, 0.15);
}
.file-item .name { font-weight: 600; color: var(--ink); word-break: break-all; }
.file-item .name .scn {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  background: var(--accent-grad);
  border-radius: 4px;
  vertical-align: 1px;
}
.file-item .meta { color: var(--ink-sub); font-size: 11px; margin-top: 3px; }
.file-item .status { font-size: 11px; margin-top: 3px; color: var(--warn); }
.file-item .status.err { color: var(--err); }
.file-item .status.ok { color: var(--ok); }
.file-item .jpg {
  font-size: 11px;
  margin-top: 4px;
  color: var(--accent-ink);
  cursor: pointer;
  word-break: break-all;
}
.file-item .jpg:hover { text-decoration: underline; }
.file-item .jpg.err { color: var(--err); cursor: default; }
.file-item .jpg.ok { color: var(--ok); cursor: pointer; }
.file-item .jpg .rex { color: var(--accent-ink); margin-left: 6px; }
.file-item .close {
  float: right;
  color: var(--ink-faint);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 0 4px;
  border-radius: 4px;
}
.file-item .close:hover { color: var(--err); background: var(--err-bg); }
</style>
