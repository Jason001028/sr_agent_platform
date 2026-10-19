<script setup lang="ts">
/**
 * CompareOverlay.vue — 分屏/落位提示的 DOM 叠加层（2026-09-20）
 * ------------------------------------------------------------------
 * slot 进 TifCanvas 的 `.stage`，与 DrawPanel / DecodeOverlay 并列。整层
 * `pointer-events: none`，**只有分隔线那条抓取带**恢复 `auto` —— 它要能拖。
 *
 * 为什么提示与分隔线用 DOM 而不是画到 canvas 上：e2e 可以直接断言文本与几何，
 * 不必采样像素去猜；也不必在 `render()` 里再插一段与影像判读无关的绘制。
 *
 * **分隔线拖动用的画布矩形取自本组件自己的根元素**：它是 `position:absolute; inset:0`
 * 铺在 `.stage` 上的，而两块画布是 `.stage` 的 100%×100% —— 三者的 rect 逐像素相同。
 * 这么取就不必在 store 里存一份「画布左上角」（那份状态会在滚动/换行/收起侧栏后变陈旧，
 * 而每次 dragover 或 pointermove 都去 getBoundingClientRect 又太频）。
 */
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';

const store = useViewerStore();
const rootRef = ref<HTMLDivElement | null>(null);
/** 正在拖分隔线（拖的时候不显示落位提示，光标也不跟着影像动）。 */
const draggingDivider = ref(false);

const isSplit = computed(() => store.compareMode === 'split');
/** 落位提示：只在有明确归属（指针在画布内）时显示。 */
const hintSide = computed(() => (store.dragHint.active ? store.dragHint.side : null));

/** 两侧的占位/文件名标签。rec 为 null = 空侧；有 rec 但没 thumb = 还在解码。 */
const sideA = computed(() => store.recForSide('A'));
const sideB = computed(() => store.recForSide('B'));

function emptyText(rec: { thumb: unknown } | null): string {
  return rec ? '等待解码…' : '把影像拖到这一侧';
}

/** 分隔线在画布内的像素位置。用 `Math.round(w * ratio)` —— 与 `viewMath.splitRects`
     算左格宽是同一条表达式，所以分隔线、裁剪矩形、落点判据三者不会差一像素。 */
const splitPx = computed(() => Math.round(store.canvasSize.w * store.splitRatio));

/** 左半 / 右半的内联位置（分隔线一动就跟着动）。 */
const halfA = computed(() => ({ left: '0px', width: splitPx.value + 'px' }));
const halfB = computed(() => ({
  left: splitPx.value + 'px',
  width: 'calc(100% - ' + splitPx.value + 'px)',
}));

/** 判定阈值（px）：按在抓取带上但没挪过这么远 = 没打算改分隔，这一下什么都不做。
    没有阈值时，按下就先按指针位置改一次比例（线被吸到指针上）；而刚进分屏时线正好在
    画布正中，随手在中间按下拖动很容易压在抓取带上 —— 那一下既不平移、比例又跟着指针
    跳，看起来就是「图不动、左右在换」。过阈值之后按**位移增量**改比例，线不再吸指针。 */
const DIVIDER_DRAG_THRESHOLD = 4;

function onDividerDown(e: PointerEvent) {
  const el = rootRef.value;
  if (!el) return;
  const target = e.currentTarget as HTMLElement;
  target.setPointerCapture(e.pointerId);
  const startX = e.clientX;
  const startY = e.clientY;
  const startRatio = store.splitRatio;      // 按下那一刻的比例，后面按位移加
  let armed = false;                        // 挪过阈值才算真的在拖分隔线

  const move = (ev: PointerEvent) => {
    if (!armed) {
      if (Math.abs(ev.clientX - startX) < DIVIDER_DRAG_THRESHOLD
        && Math.abs(ev.clientY - startY) < DIVIDER_DRAG_THRESHOLD) return;
      armed = true;
      draggingDivider.value = true;         // 拖分隔线期间收落位提示、换光标
    }
    // 拖的过程中可能刚换过布局（收起侧栏 / 展开对比条）→ 每次都重新取
    const r = el.getBoundingClientRect();
    store.setSplitRatio(startRatio + (ev.clientX - startX) / r.width);
  };
  const up = () => {
    draggingDivider.value = false;
    try { target.releasePointerCapture(e.pointerId); } catch { /* 捕获已丢，忽略 */ }
    target.removeEventListener('pointermove', move);
    target.removeEventListener('pointerup', up);
    target.removeEventListener('pointercancel', up);
  };
  target.addEventListener('pointermove', move);
  target.addEventListener('pointerup', up);
  target.addEventListener('pointercancel', up);
}

/** 双击分隔线 = 回正（与「回正」按钮同一动作）。 */
function onDividerDblClick(e: MouseEvent) {
  e.stopPropagation();       // 别让 .stage 的 dblclick(fit) 也触发
  store.resetSplit();
}
</script>

<template>
  <div
    v-if="store.compareOn"
    ref="rootRef"
    class="cmp-overlay"
    :class="{ 'cmp-dragging': draggingDivider }"
    data-e2e="cmp-overlay"
  >
    <!-- 分屏专属：空侧占位 / 分隔线 / 两侧标签 -->
    <template v-if="isSplit">
      <div class="cmp-half" :style="halfA">
        <div v-if="!sideA || !sideA.thumb" class="cmp-empty">
          <span class="cmp-empty-text">{{ emptyText(sideA) }}</span>
        </div>
      </div>
      <div class="cmp-half" :style="halfB">
        <div v-if="!sideB || !sideB.thumb" class="cmp-empty">
          <span class="cmp-empty-text">{{ emptyText(sideB) }}</span>
        </div>
      </div>

      <div
        class="cmp-divider"
        :style="{ left: splitPx + 'px' }"
        data-e2e="cmp-divider"
        title="拖动改分隔位置；双击回正"
        @pointerdown="onDividerDown"
        @dblclick="onDividerDblClick"
      ></div>

      <!-- 两侧文件名条：分屏里两张图的身份必须一直看得见（占位是虚的，名字是实的） -->
      <div class="cmp-tag cmp-tag-a" :class="{ on: store.activeSide === 'A' }" data-e2e="cmp-tag-a">
        <span class="cmp-tag-side">左</span>
        <span class="cmp-tag-name">{{ sideA ? sideA.name : '空' }}</span>
      </div>
      <div class="cmp-tag cmp-tag-b" :class="{ on: store.activeSide === 'B' }" data-e2e="cmp-tag-b">
        <span class="cmp-tag-side">右</span>
        <span class="cmp-tag-name">{{ sideB ? sideB.name : '空' }}</span>
      </div>
    </template>

    <!-- 落位提示（拖拽中才有）：整块白纱压住影像 + 目标半幅的淡色块 + 一句话。
         画在最上层，所以放在最后。 -->
    <div
      v-if="hintSide && !draggingDivider"
      class="cmp-hint"
      data-e2e="cmp-hint"
      :data-side="hintSide"
    >
      <div
        class="cmp-hint-zone"
        :class="hintSide === 'A' ? 'zone-a' : 'zone-b'"
        :style="isSplit ? (hintSide === 'A' ? halfA : halfB) : undefined"
      ></div>
      <span class="cmp-hint-text">
        {{ isSplit ? (hintSide === 'A' ? '放在左侧' : '放在右侧') : '覆盖当前这张' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.cmp-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;      /* 整层透明；只有分隔线抓取带恢复 auto */
  z-index: 5;                /* 在 .draw-canvas 之上；.decode-mask 是 20，仍在它之下 */
  overflow: hidden;
}

/* 落位提示：整块白纱（压住影像，字仍可读）+ 目标半幅的淡色块 */
.cmp-hint {
  position: absolute;
  inset: 0;
  background: var(--cmp-veil);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cmp-hint-zone {
  position: absolute;
  top: 0;
  bottom: 0;
  transition: left 0.08s ease, width 0.08s ease;
}
/* 点选对比只有一块画布 → 落位区就是整块，用左色即可 */
.cmp-hint-zone.zone-a { left: 0; right: 0; background: var(--cmp-hint-a); }
.cmp-hint-zone.zone-b { background: var(--cmp-hint-b); }
.cmp-hint-text {
  position: relative;        /* 压在色块之上 */
  padding: 6px 16px;
  border-radius: var(--r-pill);
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: var(--shadow-card);
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

/* 半幅容器：只负责定位，占位块在它里面 inset 8px */
.cmp-half {
  position: absolute;
  top: 0;
  bottom: 0;
}
.cmp-empty {
  position: absolute;
  inset: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--cmp-empty-line);
  border-radius: var(--r-ctrl);
  background: var(--cmp-empty-bg);
}
.cmp-empty-text { color: var(--ink-faint); font-size: 13px; }

/* 分隔线 = 1px 白线 + 深色发丝描边（亮图暗图上都看得见），外面套一条 9px 抓取带 */
.cmp-divider {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 9px;
  margin-left: -4px;         /* 让 1px 白线正好落在 splitPx 上 */
  pointer-events: auto;
  cursor: col-resize;
  background: transparent;
}
.cmp-divider::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--cmp-divider);
  box-shadow: 0 0 0 0.5px var(--cmp-divider-edge);
}
.cmp-divider:hover::before { box-shadow: 0 0 0 1px var(--cmp-active); }

/* 两侧标签：贴在各自半幅的左上角 */
.cmp-tag {
  position: absolute;
  top: 8px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 45%;
  padding: 3px 9px;
  border-radius: var(--r-pill);
  background: var(--surface);
  border: 1px solid var(--line);
  color: var(--ink-sub);
  font-size: 11px;
  box-shadow: var(--shadow-card);
}
.cmp-tag-a { left: 8px; }
.cmp-tag-b { right: 8px; }
/* 活动侧：加一圈同色描边 —— 「哪一侧在响应掩码/云量/任务状态」得看得出来 */
.cmp-tag.on { color: var(--ink); border-color: var(--cmp-active); box-shadow: 0 0 0 2px rgba(63, 109, 168, 0.18); }
.cmp-tag-side { font-weight: 600; }
.cmp-tag-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cmp-tag-a .cmp-tag-side { color: var(--cmp-a); }
.cmp-tag-b .cmp-tag-side { color: var(--cmp-b); }

/* 拖分隔线时不要在影像上显示抓取光标 */
.cmp-overlay.cmp-dragging { cursor: col-resize; }
</style>
