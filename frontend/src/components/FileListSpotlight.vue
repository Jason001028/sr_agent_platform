<script setup lang="ts">
/**
 * FileListSpotlight.vue — 左侧图组的场景聚焦遮罩（Spotlight Focus）
 * ------------------------------------------------------------------
 * 在《待修复清单》里选中一行，左侧与之同景的那**组卡**保持清晰并套一圈深松绿描边，
 * 侧栏滚动视口里其余部分（别的卡、卡间缝、留白）统一压一层遮罩。遮罩只活在
 * `.sidebar` 的滚动视口内：工具条（z-index 更高）、中间画布、右侧面板一概不碰。
 *
 * 层级（都在 `.sidebar` 的 isolate 局部层叠上下文里）：
 *   卡片/图文 = 0（普通流） < 本遮罩层 = 10（sticky 锚点） < FileListTools = 30。
 * 遮罩层 `pointer-events: none`，点选/拖拽/滚轮全部穿透。
 *
 * 坐标系（最容易错的一处）：
 *   锚点是侧栏首个子元素，`position: sticky; top: 0; height: 0` —— 它**不占布局**，
 *   滚动时钉在滚动口顶边（只在 scrollTop < padding-top 的那几像素里随内容走）。
 *   每次同步都现场量三样东西：
 *     1. 侧栏滚动口的视口矩形（`borderBox + clientLeft/Top`，即 padding 盒左上）；
 *     2. 锚点自身的视口矩形；
 *     3. 聚焦卡的视口矩形（只量这几张，绝不遍历整列）。
 *   镂空窗口一律换算成**遮罩层局部坐标**（视口坐标 − 滚动口原点），锚点的漂移
 *   由 `translate(dx, dy)` 抵消。滚动/缩放/分屏/折叠全部走同一条 `sync()`，
 *   不存在内容坐标与页面坐标混用。
 *
 * 镂空为什么是「矩形块」而不是 clip-path / mask：毛玻璃靠 `backdrop-filter`，
 * 镂空必须让那块**完全不画**，模糊才不会渗进窗口边缘、也不会有接缝。把
 * 「整块 − 若干窗口」拆成互不重叠的矩形块（`lib/spotlight.ts::buildMaskBlocks`），
 * 单个窗口正好是上/左/右/下四块。
 *
 * 动画：窗口移动用 rAF 插值（250ms，cubic-bezier(.22,1,.36,1)），滚动时改的是
 * 目标值而不是重启动画，所以窗口始终跟着卡片走；遮罩整体出现/消失用 240ms 透明度
 * 过渡。`prefers-reduced-motion` 下移动与淡入淡出都直接到位。
 *
 * 纯视觉：不改像素、缓存、选中态或 Agent 状态；列表里删卡/清空时 store 会把
 * `spotlightIds` 里的失效 id 滤掉，这里再对拿不到 DOM 的 id 兜一层。
 */
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useViewerStore } from '../stores/viewer';
import {
  SPOTLIGHT_MOVE_MS,
  buildMaskBlocks,
  easeSpotlight,
  lerpRect,
  mergeGroupRects,
} from '../lib/spotlight.js';
import type { SpotlightRect } from '../lib/spotlight.js';

const props = defineProps<{
  /** 侧栏滚动容器（`.sidebar` 本身）。 */
  viewport: HTMLElement | null;
  /** 卡片 id → 卡片根元素（FileList 的 `itemEls`）。 */
  elFor: (id: number) => HTMLElement | undefined;
  /** 列表结构版本（顺序/成员变化时变）。 */
  revision: string;
}>();

const store = useViewerStore();

const anchor = ref<HTMLElement | null>(null);

/** 遮罩是否该在场上（控制 Transition 的进入/离开；离开期间保留最后的几何做淡出）。 */
const visible = ref(false);
/** 当前**显示**的镂空窗口（局部坐标）。 */
const holes = ref<SpotlightRect[]>([]);
/** 遮罩层相对锚点的偏移与尺寸。 */
const box = reactive({ dx: 0, dy: 0, w: 0, h: 0 });

const layerStyle = computed(() => ({
  transform: `translate(${box.dx}px, ${box.dy}px)`,
  // 多铺 1px：clientHeight/clientWidth 是取整值，铺满可避免底边/右边出现发丝缝；
  // 超出的部分由侧栏自身的 overflow 裁掉，不会盖到画布上。
  width: `${box.w + 1}px`,
  height: `${box.h + 1}px`,
}));

const blocks = computed(() => (
  store.spotlightMode === 'none'
    ? []
    : buildMaskBlocks(box.w + 1, box.h + 1, holes.value)
));

function rectStyle(r: SpotlightRect): Record<string, string> {
  return {
    left: `${r.x}px`,
    top: `${r.y}px`,
    width: `${r.w}px`,
    height: `${r.h}px`,
  };
}

/* ---------------- 测量 ---------------- */

function prefersReduced(): boolean {
  try {
    return typeof matchMedia === 'function'
      && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch { return false; }
}

/** 与滚动口求交；完全在视口外返回 null。 */
function clipRect(r: SpotlightRect, w: number, h: number): SpotlightRect | null {
  const x = Math.max(0, r.x);
  const y = Math.max(0, r.y);
  const right = Math.min(w, r.x + r.w);
  const bottom = Math.min(h, r.y + r.h);
  if (right - x <= 0.5 || bottom - y <= 0.5) return null;
  return { x, y, w: right - x, h: bottom - y };
}

interface Measurement {
  dx: number; dy: number; w: number; h: number; holes: SpotlightRect[];
}

function measure(): Measurement | null {
  const sb = scroller;
  const a = anchor.value;
  if (!sb || !a || !sb.isConnected || !a.isConnected) return null;
  if (store.sidebarCollapsed) return null;
  const w = sb.clientWidth;
  const h = sb.clientHeight;
  if (!w || !h) return null;

  const sr = sb.getBoundingClientRect();
  const ar = a.getBoundingClientRect();
  // 滚动口（padding 盒）左上角：视口坐标。侧栏只有右边框，clientLeft/Top 一般是 0，
  // 仍照规范取一次，换了描边也不用改这里。
  const originX = sr.left + sb.clientLeft;
  const originY = sr.top + sb.clientTop;

  const rects: SpotlightRect[] = [];
  for (const id of store.spotlightIds) {
    const el = props.elFor(id);
    if (!el || !el.isConnected) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    rects.push({ x: r.left - originX, y: r.top - originY, w: r.width, h: r.height });
  }
  const merged = mergeGroupRects(rects)
    .map((r) => clipRect(r, w, h))
    .filter((r): r is SpotlightRect => r !== null);

  return { dx: originX - ar.left, dy: originY - ar.top, w, h, holes: merged };
}

/* ---------------- 窗口移动：rAF 插值 ---------------- */

let shownHoles: SpotlightRect[] = [];
let fromHoles: SpotlightRect[] = [];
let toHoles: SpotlightRect[] = [];
let moveStart = 0;
let moving = false;
let rafId = 0;

function nearlySame(a: readonly SpotlightRect[], b: readonly SpotlightRect[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((r, i) => Math.abs(r.x - b[i].x) < 0.5 && Math.abs(r.y - b[i].y) < 0.5
    && Math.abs(r.w - b[i].w) < 0.5 && Math.abs(r.h - b[i].h) < 0.5);
}

function setShown(next: readonly SpotlightRect[]): void {
  shownHoles = next.map((r) => ({ ...r }));
  holes.value = shownHoles.slice();
}

function stopMove(): void {
  moving = false;
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
}

function step(now: number): void {
  rafId = 0;
  if (!moving) return;
  const t = Math.min(1, (now - moveStart) / SPOTLIGHT_MOVE_MS);
  const e = easeSpotlight(t);
  shownHoles = fromHoles.map((f, i) => lerpRect(f, toHoles[i] ?? f, e));
  holes.value = shownHoles.slice();
  if (t < 1) rafId = requestAnimationFrame(step);
  else moving = false;
}

function startMove(next: readonly SpotlightRect[]): void {
  if (nearlySame(shownHoles, next)) return;
  fromHoles = shownHoles.slice();
  toHoles = next.map((r) => ({ ...r }));
  moveStart = performance.now();
  moving = true;
  if (!rafId) rafId = requestAnimationFrame(step);
}

/* ---------------- 同步 ---------------- */

/**
 * 唯一的写入口。`animate` 只在「用户换了选中的景」时为真 —— 滚动与缩放是用户
 * 自己拖着视口走，窗口必须**同帧贴住**卡片，不能慢半拍。
 */
function sync(opts: { animate: boolean }): void {
  if (!store.spotlightIds.length) {
    stopMove();
    visible.value = false;
    return;
  }
  const m = measure();
  if (!m) {
    // 分屏塌陷、侧栏收起、锚点未挂载：直接收场（不淡出，避免和折叠动画叠在一起）。
    stopMove();
    visible.value = false;
    return;
  }
  box.dx = m.dx;
  box.dy = m.dy;
  box.w = m.w;
  box.h = m.h;

  if (!visible.value) {
    // 首次出现：直接落位，渐入交给 Transition；不插值（没有「从哪来」）。
    setShown(m.holes);
    visible.value = true;
    return;
  }
  if (opts.animate && !prefersReduced() && m.holes.length === shownHoles.length) {
    startMove(m.holes);
    return;
  }
  if (moving) {
    // 插值进行中：滚动只改目标，不重启计时（否则每帧从 0 重新缓动＝永远追不上）。
    toHoles = m.holes;
    return;
  }
  setShown(m.holes);
}

/** 滚动事件每帧最多同步一次（被动监听 + rAF 合帧）。 */
let pendingSync = false;
function scheduleSync(): void {
  if (pendingSync) return;
  pendingSync = true;
  requestAnimationFrame(() => {
    pendingSync = false;
    sync({ animate: false });
  });
}

function onAfterLeave(): void {
  stopMove();
  shownHoles = [];
  holes.value = [];
}

/* ---------------- 监听挂接 ---------------- */

/** 真正的滚动祖先：优先用 FileList 显式传进来的 `.sidebar`（首帧 ref 还没落时
 *  沿 DOM 往上找第一个 `overflow-y: auto/scroll` 的祖先 —— 也就是 `.sidebar`）。 */
function scrollContainer(): HTMLElement | null {
  const given = props.viewport;
  if (given && given.isConnected) return given;
  let el: HTMLElement | null = anchor.value?.parentElement ?? null;
  while (el) {
    let oy = '';
    try { oy = getComputedStyle(el).overflowY; } catch { /* 取不到就继续往上走 */ }
    if (oy === 'auto' || oy === 'scroll' || oy === 'overlay') return el;
    el = el.parentElement;
  }
  return null;
}

let scroller: HTMLElement | null = null;
let ro: ResizeObserver | null = null;

function detach(): void {
  if (scroller) scroller.removeEventListener('scroll', scheduleSync);
  scroller = null;
  if (ro) { ro.disconnect(); ro = null; }
  window.removeEventListener('resize', scheduleSync);
}

function attach(): void {
  detach();
  const sb = scrollContainer();
  if (!sb) return;
  scroller = sb;
  sb.addEventListener('scroll', scheduleSync, { passive: true });
  if (typeof ResizeObserver === 'function') {
    // 侧栏自身尺寸变化（窗口缩放、折叠/展开、分屏改列宽）。
    ro = new ResizeObserver(scheduleSync);
    ro.observe(sb);
  }
  window.addEventListener('resize', scheduleSync);
  scheduleSync();
}

onMounted(attach);
watch(() => props.viewport, attach);
onUnmounted(() => { detach(); stopMove(); });

/* 选中的景变了：这里不主动滚动（滚动由 FileList 的 litTick 负责），只把窗口移过去。
   nextTick 等卡片上的 `.lit` 类与滚动起步都落下之后再量。 */
watch(() => store.spotlightIds, () => nextTick(() => sync({ animate: true })));
/* 列表结构变了（增删/收藏置顶）：卡片位移，窗口跟着量。 */
watch(() => props.revision, () => nextTick(scheduleSync));
/* 收起/展开：收起时量不到视口，直接收场；展开时重新落位。 */
watch(() => store.sidebarCollapsed, () => nextTick(() => sync({ animate: false })));
</script>

<template>
  <div ref="anchor" class="spot-anchor" aria-hidden="true">
    <Transition name="spot-fade" @after-leave="onAfterLeave">
      <div
        v-if="visible"
        class="spot-layer"
        :class="'mode-' + store.spotlightMode"
        :style="layerStyle"
        :data-spotlight-mode="store.spotlightMode"
        :data-spotlight-count="holes.length"
      >
        <div
          v-for="(b, i) in blocks"
          :key="'b' + i"
          class="spot-block"
          :data-spot-block="i"
          :style="rectStyle(b)"
        ></div>
        <div
          v-for="(h, i) in holes"
          :key="'h' + i"
          class="spot-ring"
          :data-spot-hole="i"
          :style="rectStyle(h)"
        ></div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* 零高度 sticky 锚点：钉在滚动口顶边，不占列表布局。局部层叠上下文由 `.sidebar`
   的 isolation 兜住；这里的 10 介于卡片（普通流）与工具条（30）之间。 */
.spot-anchor {
  position: sticky;
  top: 0;
  height: 0;
  z-index: 10;
  pointer-events: none;
}

.spot-layer {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
  will-change: transform;
}

.spot-block {
  position: absolute;
  pointer-events: none;
}
.spot-layer.mode-frosted .spot-block {
  background: var(--spot-frost);
  -webkit-backdrop-filter: blur(3px);
  backdrop-filter: blur(3px);
}
.spot-layer.mode-dim .spot-block {
  background: var(--spot-dim);
}

/* 描边单独画，不放在遮罩块上：块与块的边会互相裁，描边跨过缝就不连续了。
   1.5px 深松绿，落在镂空窗口外扩出来的 4px 留白里，不压字也不压图。 */
.spot-ring {
  position: absolute;
  pointer-events: none;
  border: 1.5px solid var(--spot-ring);
  border-radius: 14px;
}

/* 遮罩整体渐入渐出；窗口移动由 JS 负责，不是 transition。 */
.spot-fade-enter-active,
.spot-fade-leave-active {
  transition: opacity var(--motion-spotlight-fade) var(--ease-standard);
}
.spot-fade-enter-from,
.spot-fade-leave-to {
  opacity: 0;
}

/* 减少动态：淡入淡出也去掉（全局规则已把 transition-duration 压到 0.01ms，
   这里显式写一次，避免将来全局规则改动时把遮罩落下）。 */
@media (prefers-reduced-motion: reduce) {
  .spot-fade-enter-active,
  .spot-fade-leave-active {
    transition: none;
  }
}
</style>
