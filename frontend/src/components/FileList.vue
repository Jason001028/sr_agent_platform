<script setup lang="ts">
/**
 * FileList.vue — 侧栏文件列表 + 收起/展开（tif-viewer.html #sidebar + addListItem 直译）
 * ------------------------------------------------------------------
 * 每项：名称 / 大小·W×H·布局 / 解码状态（绿红）/ × 移除 / active 高亮。
 * 收起 toggle 秒出无动画（HTML sideToggle）。
 * （HTML 的「JPG 导出状态 + 重新导出」一栏随浏览器 JPG 导出链路一并删去。）
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();

const collapseBtn = computed(() => (store.sidebarCollapsed ? '»' : '«'));
const collapseTitle = computed(() => (store.sidebarCollapsed ? '展开文件列表' : '收起文件列表'));

function fmtBytes(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}
</script>

<template>
  <aside class="sidebar" :class="{ collapsed: store.sidebarCollapsed }">
    <div v-if="!store.recs.length" class="tip">选择或拖入影像开始（.tif/.tiff/.jpg/.jpeg）</div>
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
      <!-- 反推关联失败的原因单独一行：rec.status 会被解码进度/结果覆盖，写在那里
           等于没写（用户只看得到「完成，解码耗时…」）。 -->
      <div v-if="rec.linkNote" class="link-note" :title="rec.linkNote">{{ rec.linkNote }}</div>
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
/* 关联失败原因：比 status 弱一档（它不是错误，是「这张图不在盘阵上」），
   但限两行，避免长候选清单把侧栏撑爆 —— 完整内容在 title 里。 */
.file-item .link-note {
  font-size: 10px;
  margin-top: 2px;
  color: var(--ink-faint);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
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
