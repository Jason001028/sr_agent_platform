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
import { isIntermediateStage } from '../lib/stage.js';
import FileThumb from './FileThumb.vue';

const store = useViewerStore();

const collapseBtn = computed(() => (store.sidebarCollapsed ? '»' : '«'));
const collapseTitle = computed(() => (store.sidebarCollapsed ? '展开文件列表' : '收起文件列表'));

/* 三颗小标各说一件事，用法（尤其「序号」是会话内编号而不是盘阵上的编号）写进
   title —— 不然用户会以为那颗数字是盘阵给景编的号，回头去别处找同一个号。 */
const SCENE_TITLE = '盘阵场景：服务器烘焙 JPG（这张图在盘阵上有对应场景目录）';
const ORD_TITLE = '同一景（同一个场景目录）的图共用这个序号 —— 本次会话内按出现顺序发号，不是盘阵上的编号';
/* NOSR 一名两义，取决于盘上那份叫什么（后端按输入影像的 stem 先找，见
   app.py::scene_siblings）。第一义是用户的口径，第二义是 writeTiff 改名留下的
   那份 —— 两种都如实标 NOSR，所以工具提示把两义都说出来，别默认只有一义。 */
const STAGE_TITLE = '环节：本体（PAN / 输入影像） / SR（本次超分产物） / NOSR（未超分那份；取自上一次产物名的那份则是上一次超分产物）';
const RO_TITLE = '中间产物仅用于与本体对比，不作修复 —— 掩码与 SR 都建在本体影像的网格上，请打开本体再修复与提交';

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
      draggable="true"
      @click="store.activate(rec.id)"
      @dragstart="store.startRecDrag(rec, $event)"
      @dragend="store.endRecDrag()"
    >
      <FileThumb :rec="rec" />
      <!-- × 是 float:right，必须留在**名字行里面**：放到缩略图之前会让浮动框去挤
           横幅那个块（横幅是 BFC，会为了避开浮动而整体缩窄），视觉上就是缩略图缺一角。 -->
      <div class="name">
        <span class="close" title="移除" @click.stop="store.removeRec(rec.id)">×</span>
        <!-- 三颗小标挤在一行里、**不留换行**：换行会被 Vue 编成空白文本节点，
             名字的 textContent 会多出空格（e2e 有整串比对名字的断言）。间距一律
             由 CSS 的 margin-right 给。 -->
        <template v-if="rec.route === 'jpg'"><span class="scn" :title="SCENE_TITLE">盘阵</span><span v-if="rec.sceneDir" class="ord" :title="ORD_TITLE">{{ store.sceneOrdinalOf(rec) }}</span><span class="stage" :class="rec.stageKind" :title="STAGE_TITLE">{{ rec.stageLabel }}</span></template>{{ rec.name }}
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
        <!-- 中间产物：说清它与本体的关系。卡片上那三颗小标只说「哪一景、哪个环节」，
             这一条说的是「所以你不能对它做什么」。 -->
        <span
          v-if="isIntermediateStage(rec.stageKind)"
          class="link-tag ro-tag"
          :title="RO_TITLE"
        >仅对比，不作修复</span>
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
/* 名字行里的小标（盘阵 / 序号 / 环节）：同一条基线、同样的圆角与内距，
   `vertical-align: 1px` 把 10px 的小方块抬到与名字的视觉中线上。
   间距只在每个小标自己的 margin-right 上 —— 模板里刻意没有换行。 */
.file-item .name .scn,
.file-item .name .ord,
.file-item .name .stage {
  display: inline-block;
  margin-right: 4px;
  padding: 0 6px;
  font-size: 10px;
  font-weight: 600;
  border-radius: 4px;
  vertical-align: 1px;
}
.file-item .name .scn { color: #fff; background: var(--accent-grad); }
/* 序号：中性面。它是索引不是状态，不该跟环节抢颜色。 */
.file-item .name .ord {
  color: var(--ink-sub);
  background: var(--surface-2);
  border: 1px solid var(--line);
  min-width: 8px;
  text-align: center;
}
/* 环节：三色区分。取 --cmp-a/--cmp-b 这一对（2026-09-20 挑的蓝/琥珀，对红绿色盲
   安全，且刻意避开了青绿族与 ok/warn/err 三个语义色 —— 环节是**位置标识**，
   不该让人读成「哪个更好」）；本体另用青绿软底，与「盘阵」那颗呼应。 */
.file-item .name .stage { color: var(--ink-sub); background: var(--surface-2); border: 1px solid var(--line); }
.file-item .name .stage.input { color: var(--accent-deep); background: var(--accent-soft); border-color: var(--accent-1); }
.file-item .name .stage.product { color: var(--cmp-a); background: var(--cmp-a-bg); border-color: var(--cmp-a-line); }
.file-item .name .stage.nosr { color: var(--cmp-b); background: var(--cmp-b-bg); border-color: var(--cmp-b-line); }
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
/* 只读（中间产物）：比「未找到目录」重一档 —— 那条是「没关联上」，这条是
   「关联上了，但这份不是可修复对象」，得让眼睛停一下。 */
.file-item .ro-tag { color: var(--warn); border-color: var(--warn-line); background: var(--warn-bg); }
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
