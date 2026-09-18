<script setup lang="ts">
/**
 * FileList.vue — 侧栏文件列表 + 收起/展开（tif-viewer.html #sidebar + addListItem 直译）
 * ------------------------------------------------------------------
 * 每项：小图预览 / 名称 / 大小·W×H·布局 / 状态标签行（绿红）/ × 移除 / active 高亮。
 * 收起 toggle 秒出无动画（HTML sideToggle）。
 * （HTML 的「JPG 导出状态 + 重新导出」一栏随浏览器 JPG 导出链路一并删去。）
 */
import { computed } from 'vue';
import { useViewerStore } from '../stores/viewer';
import FileThumb from './FileThumb.vue';

const store = useViewerStore();

const collapseBtn = computed(() => (store.sidebarCollapsed ? '»' : '«'));
const collapseTitle = computed(() => (store.sidebarCollapsed ? '展开文件列表' : '收起文件列表'));

function fmtBytes(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

/** 关联失败的短标签。后端那句以「没找到合法场景目录 —— 」开头、后面还跟着整条候选
 *  路径与原因，卡片上放不下，整句仍进 title。字节数不符是「目录在、文件对不上」，
 *  与「目录不存在」不是一回事，分开说。 */
function linkTag(note: string): string {
  return note.includes('字节数') ? '文件对不上' : '未找到目录';
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
      <FileThumb :rec="rec" />
      <!-- × 是 float:right，必须留在**名字行里面**：放到缩略图之前会让浮动框去挤
           横幅那个块（横幅是 BFC，会为了避开浮动而整体缩窄），视觉上就是缩略图缺一角。 -->
      <div class="name">
        <span class="close" title="移除" @click.stop="store.removeRec(rec.id)">×</span>
        <span v-if="rec.route === 'jpg'" class="scn" title="盘阵场景：服务器烘焙 JPG">盘阵</span>{{ rec.name }}
      </div>
      <div class="meta">
        {{ fmtBytes(rec.size) }}
        <template v-if="rec.W"> · {{ rec.W }}×{{ rec.H }}</template>
        <template v-if="rec.layout"> · {{ rec.layout }}</template>
      </div>
      <!-- 状态与关联结果同排一行、都做成短标签（2026-09-18）：原先两者各占一行，
           且各自又把文件名抄了一遍 —— 一张卡三行里三处同名。
           关联原因仍然单独一个字段：rec.status 会被解码进度/结果覆盖，写在那里
           等于没写（用户只看得到「完成，解码耗时…」）。整句理由进 title。 -->
      <div class="tags">
        <span class="status" :class="rec.statusCls">{{ rec.status }}</span>
        <span v-if="rec.linkNote" class="link-tag" :title="rec.linkNote">{{ linkTag(rec.linkNote) }}</span>
      </div>
    </div>
  </aside>
  <div class="side-toggle" :title="collapseTitle" @click="store.sidebarCollapsed = !store.sidebarCollapsed">
    {{ collapseBtn }}
  </div>
</template>

<style scoped>
/* 侧栏：浅莫兰迪 chrome，白文件卡浮其上；选中青绿描边。
   宽度 400：生产全名（约 40 字）与下面那行尺寸/布局说明在这个宽度里能排开，
   再窄就得靠 break-all 从词中间断开，一行文件名看着像坏了；右侧栏同样是 400，
   两栏对齐。小图预览横幅要的横向空间也在这里出（见 FileThumb.vue）。 */
.sidebar {
  width: 400px;
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
/* 状态与关联结果同排一行，都是短标签（见模板注释）。 */
.file-item .tags { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 4px; }
.file-item .status, .file-item .link-tag {
  font-size: 10px;
  line-height: 1.7;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--surface-2);
  border: 1px solid var(--line);
}
.file-item .status { color: var(--warn); }
.file-item .status.err { color: var(--err); }
.file-item .status.ok { color: var(--ok); }
/* 关联失败：比 status 弱一档（它不是错误，是「这张图不在盘阵上」）——
   完整原因（哪条候选、缺什么）在 title 里。 */
.file-item .link-tag { color: var(--ink-faint); cursor: help; }
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
