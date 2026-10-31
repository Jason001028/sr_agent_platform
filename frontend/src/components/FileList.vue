<script setup lang="ts">
/**
 * FileList.vue — 侧栏文件列表 + 收起/展开（tif-viewer.html #sidebar + addListItem 直译）
 * ------------------------------------------------------------------
 * 每项为横向紧凑布局：左侧 96×96 方形缩略图（contain，浅灰绿底），右侧自上而下
 * 环节标签 / 两行省略的文件名 / W×H·大小·布局 / 状态标签行；× 移除浮在卡片右上角。
 * 收起 toggle 秒出无动画（HTML sideToggle）。
 * （HTML 的「JPG 导出状态 + 重新导出」一栏随浏览器 JPG 导出链路一并删去。）
 *
 * 「点亮」（.lit）：点右侧《待修复清单》里的一行时，同一景的卡片加橘色描边并滚到眼前
 * （`viewer.lightCards` → 这里的 `litTick` 看守）。**只用 outline** —— 它不占布局，
 * 与 `.active` 的 box-shadow 正交，所以一张卡可以同时是「正在编辑」和「刚被点亮」。
 * `.name` 的 textContent 有 e2e 断言（按文件名找卡）：小标与文件名之间**不留空白文本
 * 节点**，`.fname` 只负责两行截断。
 */
import { computed, ref, watch, onMounted, onUnmounted } from 'vue';
import type { ComponentPublicInstance } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useChatStore } from '../stores/chat.js';
import { useChatShot } from '../composables/useChatShot.js';
import { SHOT_MAX_COUNT } from '../lib/chatShot.js';
import { isIntermediateStage } from '../lib/stage.js';
import FileListTools from './FileListTools.vue';
import FileListSpotlight from './FileListSpotlight.vue';
import FileThumb from './FileThumb.vue';
import FileListCard from './FileListCard.vue';
import AppIcon from './AppIcon.vue';
import FavoriteStar from './FavoriteStar.vue';

const store = useViewerStore();
const chat = useChatStore();
const demoPaused = ref(false);
const demoHovered = ref(false);
// The cards are inert; hit-test their bounds without enabling business actions.
function hoverDemoCard(event: PointerEvent): void {
  const root = event.currentTarget as HTMLElement;
  demoHovered.value = [...root.querySelectorAll('.demo-card')].some(card => {
    const r = card.getBoundingClientRect();
    return event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
  });
}
function syncDemoVisibility(): void { demoPaused.value = document.hidden; }
onMounted(() => {
  syncDemoVisibility();
  document.addEventListener('visibilitychange', syncDemoVisibility);
});
onUnmounted(() => document.removeEventListener('visibilitychange', syncDemoVisibility));
const showExamples = computed(() => !store.recsOrdered.length && !store.busy && !store.overlay.visible && store.panes.every(p => !p.rec));
watch(showExamples, () => { demoHovered.value = false; });
const demoPan = `${import.meta.env.BASE_URL}demo_pan.webp`;
const demoNosr = `${import.meta.env.BASE_URL}demo_nosr.webp`;
const examples = [
  { name: 'demo_pan.tif', src: demoPan, size: 151740, stage: 'PAN' },
  { name: 'demo_nosr.tif', src: demoNosr, size: 18570, stage: 'NOSR' },
];
const { busy: shotBusy, add: addShotAction } = useChatShot();

/** 待发截图已满：相机键置灰并说明原因（点下去也不该有反应）。 */
const shotsFull = computed(() => chat.pendingImages.length >= SHOT_MAX_COUNT);

/** 卡片相机键：截当前视野 → 聊天托板。不改视口（见 viewer.captureForChat）。 */
async function addShot(recId: number): Promise<void> {
  const r = await addShotAction(recId);
  if (!r.ok) store.showErr(r.message);
}

/** 多选开着时点卡片 = 勾选；否则照旧激活换图。 */
function onCardClick(rec: { id: number; pending?: boolean }): void {
  if (rec.pending) return;
  if (store.selectMode) { store.toggleSelected(rec.id); return; }
  void store.activate(rec.id);
}

/* 工具条的键盘面：Esc 退出多选、Delete 移除选中、Ctrl/⌘+Z 撤回。
   在输入框 / 可编辑区里一律不接管（聊天框里按退格不能清掉影像卡）。 */
function inTypingTarget(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  return t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT';
}

function onWindowKey(e: KeyboardEvent): void {
  if (e.defaultPrevented || inTypingTarget(e)) return;
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
    if (store.canUndo) { e.preventDefault(); store.undo(); }
    return;
  }
  if (e.key === 'Escape' && store.selectMode) {
    e.preventDefault();
    store.toggleSelectMode();
    return;
  }
  if ((e.key === 'Delete' || e.key === 'Backspace')
      && store.selectMode && store.selectedIds.length) {
    e.preventDefault();
    store.removeSelected();
  }
}

onMounted(() => window.addEventListener('keydown', onWindowKey));
onUnmounted(() => window.removeEventListener('keydown', onWindowKey));

const collapseBtn = computed(() => (store.sidebarCollapsed ? '»' : '«'));
const collapseTitle = computed(() => (store.sidebarCollapsed ? '展开文件列表' : '收起文件列表'));

/* ---------------- 点亮：上色（模板 class）+ 滚到眼前（这里） ---------------- */

/** 卡片 id → 根元素。函数 ref 收（`:ref` 的写法见模板），卸载时 Vue 会拿 null 回调。 */
const itemEls = new Map<number, HTMLElement>();

function setItemEl(id: number, el: Element | ComponentPublicInstance | null): void {
  const element = el && '$el' in el ? el.$el : el;
  if (element instanceof HTMLElement) itemEls.set(id, element);
  else itemEls.delete(id);
}

/** 场景聚焦遮罩用：按 id 取卡片 DOM（只量这几张，不遍历整列）。 */
function itemEl(id: number): HTMLElement | undefined {
  return itemEls.get(id);
}

/* 聚焦遮罩的「结构版本」：成员或顺序一变就变（收藏置顶、增删、撤回）。
   做成字符串而不是数组，watch 按值比较才不会每帧重启。 */
const spotRevision = computed(() => store.recsOrdered.map((r) => r.id).join(','));

/** 侧栏滚动容器本身（聚焦遮罩的坐标基准，也是 e2e 读 scrollTop 的那个元素）。 */
const scrollEl = ref<HTMLElement | null>(null);

/** 点亮的两套动画类**奇偶交替挂**：同名 keyframes 反复挂同一个类不会重放，换一个名字
 *  才重放 —— 这样既不用重建元素，也不必强制 reflow。只有命中的卡才有类。 */
function litCls(id: number): string {
  if (!store.litIds.includes(id)) return '';
  return store.litTick % 2 ? 'lit lit-b' : 'lit lit-a';
}

/** 滚动行为：用户关了动画就别平滑滚（与系统偏好一致是本项目的既有口径）。 */
function scrollBehavior(): ScrollBehavior {
  try {
    if (typeof matchMedia === 'function'
        && matchMedia('(prefers-reduced-motion: reduce)').matches) return 'auto';
  } catch { /* 老浏览器没有 matchMedia：按默认平滑滚 */ }
  return 'smooth';
}

/** 每次点亮滚一次：目标是**排在最前的那张**（`lightCards` 把活动卡排在最前，
 *  用户最可能想看的就是它）。
 *
 *  **收起时不滚**：`.sidebar.collapsed` 是 `width: 0; overflow: hidden`，
 *  `scrollIntoView` 会把整个页面横向推一下 —— 而收起是用户的显式动作，不该被一次
 *  点行改写。这种情况下只上色（类照样挂，展开后一眼看见）。 */
watch(() => store.litTick, () => {
  if (!store.litIds.length || store.sidebarCollapsed) return;
  itemEls.get(store.litIds[0])?.scrollIntoView({ block: 'center', behavior: scrollBehavior() });
});

/* 三颗小标各说一件事，用法（尤其「序号」是会话内编号而不是盘阵上的编号）写进
   title —— 不然用户会以为那颗数字是盘阵给景编的号，回头去别处找同一个号。 */
const SCENE_TITLE = '盘阵场景：服务端生成的预览 JPG（这张图在盘阵上有对应场景目录）';
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
  <aside ref="scrollEl" class="sidebar" :class="{ collapsed: store.sidebarCollapsed }">
    <!-- 场景聚焦遮罩的零高度锚点：必须是滚动容器的首个子元素（见组件头注释）。 -->
    <FileListSpotlight :viewport="scrollEl" :el-for="itemEl" :revision="spotRevision" />
    <FileListTools />
    <div v-if="showExamples" class="demo-list" :class="{ 'demo-paused': demoPaused || demoHovered || store.sidebarCollapsed }" aria-label="影像示例" @pointermove="hoverDemoCard" @pointerleave="demoHovered = false">
      <FileListCard v-for="example in examples" :key="example.name" class="demo-card" :class="example.stage === 'PAN' ? 'demo-pan' : 'demo-nosr'" inert :draggable="false" aria-disabled="true">
        <template #thumbnail><FileThumb :size="96" :preview-src="example.src" :detail-src="example.stage === 'NOSR' ? demoPan : undefined" /></template>
        <template #body>
          <div class="name"><span class="stage">{{ example.stage }}</span><span class="example-badge">示例</span><span class="fname">{{ example.name }}</span></div>
          <div class="meta">{{ example.stage === 'PAN' ? '1024×1024' : '512×512' }} · {{ fmtBytes(example.size) }} · TIF</div>
          <div class="tags"><span class="status muted">{{ example.stage === 'PAN' ? '清晰纹理' : '未超分预览' }}</span></div>
        </template>
      </FileListCard>
      <span class="demo-guide" aria-hidden="true"><span class="demo-dot"></span></span>
      <p class="demo-tip">拖入影像开始工作</p>
    </div>
    <template v-for="(rec, idx) in store.recsOrdered" :key="rec.id">
      <!-- 收藏组与普通组之间一条细线：置顶靠排序，这条线让人一眼看出「上面那几张不动」。 -->
      <div
        v-if="idx === store.favCount && store.favCount > 0 && store.favCount < store.recsOrdered.length"
        class="fav-divider"
        aria-hidden="true"
      ></div>
      <FileListCard
        :ref="(el) => setItemEl(rec.id, el)"
        :data-rec-id="rec.id"
        class="file-item"
        :class="[
          {
            active: rec.id === store.activeId,
            pending: rec.pending,
            picked: store.isSelected(rec.id),
            entering: rec.enterDelay !== undefined,
          },
          litCls(rec.id),
        ]"
        :style="rec.enterDelay !== undefined ? { animationDelay: rec.enterDelay + 'ms' } : undefined"
        :draggable="!rec.pending && !store.selectMode"
        @click="onCardClick(rec)"
        @dragstart="store.startRecDrag(rec, $event)"
        @dragend="store.endRecDrag()"
      >
        <template #thumbnail><FileThumb :rec="rec" :size="96" /></template>
        <!-- 多选的眼睛：勾选态一眼可见。卡片整块可点，这颗只是指示器。 -->
        <span
          v-if="store.selectMode && !rec.pending"
          class="pick"
          :class="{ on: store.isSelected(rec.id) }"
          aria-hidden="true"
        >
          <AppIcon v-if="store.isSelected(rec.id)" name="check" :size="12" />
        </span>
        <!-- 收藏标：点一下取消收藏。收藏的卡置顶，且批量清理/一键解析都不会动它。 -->
        <button
          v-if="rec.fav && !store.selectMode"
          type="button"
          class="fav-chip"
          :title="'已收藏（点一下取消收藏；收藏的卡不会被批量清理）'"
          aria-label="取消收藏"
          @click.stop="store.toggleFavorite([rec.id])"
        >
          <FavoriteStar :size="12" :filled="true" />
        </button>
        <!-- 截图键：叠在缩略图右上角，悬停/聚焦/当前卡才显形（opacity 而非 display，
             免得悬停时布局跳动）。@click.stop 不触发卡片激活。多选时让位给勾选。 -->
        <button
          v-if="!rec.pending && !store.selectMode"
          type="button"
          class="shot-add"
          data-e2e="file-shot-add"
          aria-label="加入对话（截取当前视野）"
          :disabled="shotsFull || shotBusy"
          :title="shotsFull ? `每条消息最多 ${SHOT_MAX_COUNT} 张截图，先在对话里删一张` : '加入对话（截取当前视野）'"
          @click.stop="addShot(rec.id)"
        >
          <AppIcon name="camera" :size="14" />
        </button>
        <!-- × 浮在卡片右上角（不占右侧文字列）；多选与占位卡不给删除入口。 -->
        <span
          v-if="!rec.pending && !store.selectMode"
          class="close"
          title="移除"
          @click.stop="store.removeRec(rec.id)"
        >×</span>
        <template #body>
          <!-- title 挂整条文件名：两行省略后靠悬停仍能看全称。 -->
          <div class="name" :title="rec.name">
            <!-- 三颗小标挤在文件名上方、**不留换行**：换行会被 Vue 编成空白文本节点，
                 名字的 textContent 会多出空格（e2e 有整串比对名字的断言）。间距一律
                 由 CSS 的 margin-right 给。 -->
            <template v-if="rec.route === 'jpg'"><span class="scn" :title="SCENE_TITLE">盘阵</span><span v-if="rec.sceneDir" class="ord" :title="ORD_TITLE">{{ store.sceneOrdinalOf(rec) }}</span><span class="stage" :class="rec.stageKind" :title="STAGE_TITLE">{{ rec.stageLabel }}</span></template><span class="fname">{{ rec.name }}</span>
          </div>
          <div class="meta">
            <!-- 占位卡没有尺寸可言，但这一行**留在原地**：补齐时不至于整卡跳一下。 -->
            <template v-if="rec.pending">—</template>
            <template v-else><template v-if="rec.W">{{ rec.W }}×{{ rec.H }} · </template>{{ fmtBytes(rec.size) }}<template v-if="rec.layout"> · {{ rec.layout }}</template></template>
          </div>
          <!-- 状态与关联结果同排一行、都做成短标签（2026-09-18）：原先两者各占一行，
               且各自又把文件名抄了一遍 —— 一张卡三行里三处同名。
               关联原因仍然单独一个字段：rec.status 会被解码进度/结果覆盖，写在那里
               等于没写（用户只看得到「完成，解码耗时…」）。整句理由进 title。 -->
          <div class="tags">
            <span class="status" :class="rec.statusCls" :title="rec.pendingError">{{ rec.status }}</span>
            <!-- 中间产物：说清它与本体的关系。卡片上那三颗小标只说「哪一景、哪个环节」，
                 这一条说的是「所以你不能对它做什么」。 -->
            <span
              v-if="isIntermediateStage(rec.stageKind)"
              class="link-tag ro-tag"
              :title="RO_TITLE"
            >仅对比，不作修复</span>
            <span v-if="rec.linkNote" class="link-tag" :title="rec.linkNote">{{ linkTag(rec.linkNote) }}</span>
          </div>
        </template>
      </FileListCard>
    </template>
  </aside>
  <div class="side-toggle" :title="collapseTitle" @click="store.sidebarCollapsed = !store.sidebarCollapsed">
    {{ collapseBtn }}
  </div>
</template>

<style scoped>
/* 侧栏：浅莫兰迪 chrome，白文件卡浮其上；选中青绿描边。
   宽度 400：左侧 96 方块之后仍留出约 270px 给文件名（两行截断）与元信息；
   右侧栏同样是 400，两栏对齐。缩略图的方形尺寸见 FileThumb.vue 的 compact 形态。 */
.sidebar {
  width: 400px;
  flex: none;
  background: var(--chrome);
  border-right: 1px solid var(--line);
  /* 横向必须显式 hidden：只写 overflow-y:auto 时另一轴按规范也是 auto，
     收缩的 100ms 里内容比栏宽，会闪出一条横向滚动条并挤动纵向布局。 */
  overflow-x: hidden;
  overflow-y: auto;
  /* Keep centered actions stationary when clearing/restoring a scrolling list. */
  scrollbar-gutter: stable;
  padding: 10px;
  /* 场景聚焦遮罩的 z-index 只在本侧栏内比较：卡片 0 < 遮罩 10 < 工具条 30。
     没有这行，侧栏里那个 10 会跑去和页面其他浮层（如聊天面板）抢层。 */
  isolation: isolate;
  transition: width var(--motion-fold) var(--ease-linear), padding var(--motion-fold) var(--ease-linear);
}
.sidebar.collapsed {
  width: 0;
  padding: 0;
  border-right: none;
  overflow: hidden;
}
/* 空列表：提示在工具条以下的剩余空间里上下左右居中。只在空态把侧栏改成
   flex 列（有卡时仍是普通块流，卡片高度与滚动不受影响）。 */
.demo-list { --demo-cycle: 12s; position: relative; margin-top: 16px; }
.demo-card { opacity: .85; cursor: default; }
/* Created together on the document timeline: identical duration, zero delay,
   shared pause rules. Percentages below are seconds / 12 * 100. */
.demo-card, .demo-dot { animation-duration: var(--demo-cycle); animation-delay: 0s; animation-iteration-count: infinite; animation-timing-function: ease-in-out; }
.demo-pan { animation-name: demo-pan-focus; }
.demo-nosr { animation-name: demo-nosr-focus; }
/* Right gutter links the card midpoints without changing their 114px height / 6px gap. */
.demo-guide { position: absolute; top: 56px; right: -4px; width: 1px; height: 120px; background: rgba(80, 155, 137, .42); pointer-events: none; }
.demo-dot { position: absolute; top: 0; left: -2.5px; width: 6px; height: 6px; border-radius: 50%; background: #459d88; box-shadow: 0 0 6px rgba(69, 157, 136, .35); opacity: 0; animation-name: demo-flow; animation-timing-function: linear; }
.demo-paused .demo-card, .demo-paused .demo-dot { animation-play-state: paused; }
@keyframes demo-pan-focus {
  /* Default 0–0.5s; enter 0.5–1s; hold 1–3.5s; leave 3.5–4s. */
  0%, 4.166666667%, 33.333333333%, 100% { opacity: .85; border-color: var(--line); box-shadow: 0 0 0 2px transparent, 0 4px 12px transparent, 0 12px 32px transparent; }
  8.333333333%, 29.166666667% { opacity: 1; border-color: #65b7a4; box-shadow: 0 0 0 2px rgba(61, 169, 144, .32), 0 4px 12px rgba(46, 90, 78, .20), 0 12px 32px rgba(46, 90, 78, .18); }
}
@keyframes demo-nosr-focus {
  /* Enter 5–5.5s; hold 5.5–8s; leave 8–8.5s; rest until 12s. */
  0%, 41.666666667%, 70.833333333%, 100% { opacity: .85; border-color: var(--line); box-shadow: 0 0 0 2px transparent, 0 4px 12px transparent, 0 12px 32px transparent; }
  45.833333333%, 66.666666667% { opacity: 1; border-color: #65b7a4; box-shadow: 0 0 0 2px rgba(61, 169, 144, .32), 0 4px 12px rgba(46, 90, 78, .20), 0 12px 32px rgba(46, 90, 78, .18); }
}
@keyframes demo-flow {
  /* Movement and short fades stay entirely within 4–5s. */
  0%, 33.333333333% { opacity: 0; transform: translateY(0); }
  34.166666667% { opacity: .8; transform: translateY(11.6px); }
  40.833333333% { opacity: .8; transform: translateY(104.4px); }
  41.666666667%, 100% { opacity: 0; transform: translateY(116px); }
}
@media (prefers-reduced-motion: reduce) {
  .demo-card, .demo-dot { animation: none; }
  .demo-guide { display: none; }
}
.example-badge { display: inline-block; margin-right: 4px; padding: 0 6px; font-size: 10px; border: 1px solid var(--line); border-radius: 4px; color: var(--ink-sub); background: var(--surface-2); }
.demo-tip { margin: 20px 0; text-align: center; font-size: 12.5px; color: var(--ink-sub); }
.tip {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 0 28px;
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.7;
}

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
  transition: color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}
.side-toggle:hover { color: var(--accent-deep); background: var(--accent-soft); }

/* 横向紧凑布局：左 96 方块 + 右信息列。信息列在方块高度里垂直居中，
   卡片高度由缩略图决定（96 + 上下内距 8×2 = 112 + 描边）。 */
.file-item {
  position: relative;               /* 勾选/收藏/相机/× 的定位基准 */
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  margin-bottom: 6px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  transition: border-color var(--motion-hover) var(--ease-standard), box-shadow var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}

.file-item:hover { border-color: #d6ddd9; }
/* 一键解析的占位卡：清单行已排上、还没有身份。不可点/拖/删，所以不给悬停反馈。 */
.file-item.pending { cursor: default; }
.file-item.pending:hover { border-color: var(--line); }
/* 批量入列：从右侧滑入的错峰淡入（延迟由 rec.enterDelay 内联给出；前 6 张按 300ms
   逐条入场，其后没有 enterDelay 的卡片直接显示）。`both` 保证延迟期间不可见，
   否则卡片会先整块出现、再闪一次。 */
.file-item.entering { animation: card-in var(--motion-fade) var(--ease-enter) both; }
@keyframes card-in {
  from { opacity: 0; transform: translateX(16px); }
  to { opacity: 1; transform: none; }
}
.file-item.active {
  border-color: var(--accent-2);
  background: var(--accent-soft);
  box-shadow: 0 0 0 1px rgba(61, 169, 164, 0.15);
}
/* 多选勾中的卡：与 active 同一族青绿，但只描边不换底 —— 两者可能同时出现
   （正在看的这张也被勾上），不必让它们抢同一块颜色。 */
.file-item.picked {
  border-color: var(--accent-3);
  box-shadow: 0 0 0 1px rgba(61, 169, 164, 0.25);
}
/* 收藏组与普通组的分界：一条短横线，不占字。 */
.fav-divider {
  height: 1px;
  margin: 2px 4px 10px;
  background: var(--line);
}

/* 多选指示器：缩略图左上角（方块从 8px 处起，内缩 4px）。空心圈 = 未选，
   实心青绿 + 勾 = 已选。 */
.file-item .pick {
  position: absolute;
  top: 12px; left: 12px;
  width: 20px; height: 20px;
  display: flex; align-items: center; justify-content: center;
  border: 1.5px solid var(--line-strong, #c9d2cd);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.92);
  pointer-events: none;
  transition: background var(--motion-hover) var(--ease-standard),
              border-color var(--motion-hover) var(--ease-standard);
}
.file-item .pick.on {
  background: var(--accent-2);
  border-color: var(--accent-2);
}
/* 收藏标：缩略图左上角的小金星底座。点它取消收藏（多选时让位给勾选圈）。 */
.file-item .fav-chip {
  position: absolute;
  top: 12px; left: 12px;
  width: 20px; height: 20px;
  display: flex; align-items: center; justify-content: center;
  padding: 0;
  border: 1px solid var(--warn-line);
  border-radius: 5px;
  background: var(--warn-bg);
  cursor: pointer;
  /* 常显：这颗星是「为什么这张卡在上面」的唯一说明，不该等悬停才出现。 */
  transition: background var(--motion-fade) var(--ease-standard);
}
.file-item .fav-chip:hover { background: var(--warn-bg-strong, #f6e3c8); }
.file-item .fav-chip:focus-visible {
  outline: 2px solid var(--accent-3);
  outline-offset: 1px;
}
/* 点亮（点清单里的一行 → 同一景的卡）：橘色描边，与「一键解析」那颗按钮同一族令牌。
   **只用 outline**：不占布局、与 .active 的 box-shadow 正交 —— 一张卡可以同时是
   「正在编辑」和「刚被点亮」，两个状态谁也不盖谁。
   `lit-a` / `lit-b` 是两套**逐字相同**的动画，奇偶交替挂，只为让同名动画重放。 */
.file-item.lit { outline: 2px solid var(--notice-job); outline-offset: 1px; }
.file-item.lit-a { animation: sr-lit-a var(--motion-emphasis) var(--ease-enter); }
.file-item.lit-b { animation: sr-lit-b var(--motion-emphasis) var(--ease-enter); }
@keyframes sr-lit-a {
  from { outline-color: rgba(194, 116, 58, 0.15); outline-offset: 8px; }
  to { outline-color: var(--notice-job); outline-offset: 1px; }
}
@keyframes sr-lit-b {
  from { outline-color: rgba(194, 116, 58, 0.15); outline-offset: 8px; }
  to { outline-color: var(--notice-job); outline-offset: 1px; }
}
/* 截图键：平时隐形，悬停/键盘聚焦/当前卡才露出来。用 opacity 不用 display ——
   显示态切换会让按钮在悬停瞬间挤动周围内容。方块 96 从 (8,8) 起，
   所以 left:76 top:12 正好落在小图右上角（内缩 4px）。 */
.file-item .shot-add {
  position: absolute;
  top: 12px; left: 76px;
  width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.9);
  cursor: pointer;
  opacity: 0; pointer-events: none;
  transition: opacity var(--motion-fade) var(--ease-standard), border-color var(--motion-fade) var(--ease-standard), background var(--motion-fade) var(--ease-standard);
}
.file-item:hover .shot-add,
.file-item:focus-within .shot-add,
.file-item.active .shot-add { opacity: 1; pointer-events: auto; }
.file-item .shot-add:hover:not(:disabled) {
  background: var(--accent-soft);
  border-color: var(--accent-2);
}
.file-item .shot-add:disabled { opacity: .45; cursor: not-allowed; }
.file-item .shot-add:focus-visible {
  opacity: 1; pointer-events: auto;
  outline: 2px solid var(--accent-3); outline-offset: 1px;
}

.file-item .name {
  min-width: 0;
  font-weight: 600;
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.45;
  word-break: break-all;
}
/* 文件名最多两行，超出省略；完整名在 .name 的 title 里。 */
.file-item .fname {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
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
/* 分辨率 · 大小 · 布局：一行到底，放不下就省略（密度优先）。 */
.file-item .meta {
  color: var(--ink-sub);
  font-size: 11px;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 状态与关联结果同排一行，都是短标签（见模板注释）。 */
.file-item .tags { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
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
/* 排队中 / 解析中 / 未开始：还没结果，比 warn 更弱一档，别让满列黄字抢眼。 */
.file-item .status.muted { color: var(--ink-faint); }
/* 关联失败：比 status 弱一档（它不是错误，是「这张图不在盘阵上」）——
   完整原因（哪条候选、缺什么）在 title 里。 */
.file-item .link-tag { color: var(--ink-faint); cursor: help; }
/* 只读（中间产物）：比「未找到目录」重一档 —— 那条是「没关联上」，这条是
   「关联上了，但这份不是可修复对象」，得让眼睛停一下。 */
.file-item .ro-tag { color: var(--warn); border-color: var(--warn-line); background: var(--warn-bg); }
/* × 在卡片右上角，悬停才上色；absolute 不参与信息列的排布。 */
.file-item .close {
  position: absolute;
  top: 5px;
  right: 5px;
  z-index: 1;
  color: var(--ink-faint);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 1px 4px;
  border-radius: 4px;
  transition: color var(--motion-hover) var(--ease-standard), background var(--motion-hover) var(--ease-standard);
}
.file-item .close:hover { color: var(--err); background: var(--err-bg); }
</style>
