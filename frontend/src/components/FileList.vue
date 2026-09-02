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
.sidebar {
  width: 260px;
  flex: none;
  background: #161a1f;
  border-right: 1px solid #2c313a;
  overflow-y: auto;
  padding: 8px;
}
.sidebar.collapsed {
  width: 0;
  padding: 0;
  border-right: none;
  overflow: hidden;
}
.tip { color: #5c6670; font-size: 12px; padding: 10px 8px; }

.side-toggle {
  flex: none;
  width: 22px;
  background: #1b1f24;
  border-right: 1px solid #2c313a;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #8a93a0;
  font-size: 13px;
  user-select: none;
}
.side-toggle:hover { color: #d5d9de; background: #22304a; }

.file-item {
  padding: 8px 10px;
  margin-bottom: 6px;
  background: #1d2229;
  border: 1px solid #2c313a;
  border-radius: 6px;
  cursor: pointer;
}
.file-item.active { border-color: #2b7bdd; background: #22304a; }
.file-item .name { font-weight: 600; color: #e6e9ed; word-break: break-all; }
.file-item .name .scn {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  font-size: 10px;
  font-weight: 600;
  color: #1b1f24;
  background: #7aa7ff;
  border-radius: 3px;
  vertical-align: 1px;
}
.file-item .meta { color: #8a93a0; font-size: 11px; margin-top: 3px; }
.file-item .status { font-size: 11px; margin-top: 3px; color: #e2a541; }
.file-item .status.err { color: #e05c5c; }
.file-item .status.ok { color: #5bb974; }
.file-item .jpg {
  font-size: 11px;
  margin-top: 3px;
  color: #7aa7ff;
  cursor: pointer;
  word-break: break-all;
}
.file-item .jpg:hover { text-decoration: underline; }
.file-item .jpg.err { color: #e05c5c; cursor: default; }
.file-item .jpg.ok { color: #7ab98a; cursor: pointer; }
.file-item .jpg .rex { color: #2b7bdd; margin-left: 6px; }
.file-item .close {
  float: right;
  color: #6b7480;
  cursor: pointer;
  font-size: 14px;
  padding: 0 4px;
}
.file-item .close:hover { color: #ff6b6b; }
</style>
