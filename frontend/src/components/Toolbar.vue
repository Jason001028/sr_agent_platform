<script setup lang="ts">
/**
 * Toolbar.vue — 顶部工具栏（tif-viewer.html #toolbar 直译）
 * ------------------------------------------------------------------
 * 选文件（multiple）/ 拉伸下拉 / 输出目录（未授权·待授权·已授权 三态）/ 自动JPG /
 * 像素定位 X/Y + 按钮 / 绘制掩码 toggle / 生成掩码。
 */
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import type { StretchMode } from '../lib/tifDecode';

const store = useViewerStore();
const fileInput = ref<HTMLInputElement | null>(null);
const locX = ref('');
const locY = ref('');

/** 盘阵场景激活：JPG 已烘焙，交互拉伸禁用（服务器只烤 2% 线性）。 */
const sceneActive = computed(() => store.activeRec?.route === 'jpg');
/** 「提交 SR」可用：盘阵场景已打开 + 已画掩码（sceneId 是烘焙前提）。 */
const srReady = computed(() =>
  sceneActive.value
  && !!store.activeRec?.sceneId
  && (store.activeRec?.maskRois?.length ?? 0) > 0,
);
/** 场景下拉固定显示「2% 线性」（烘焙值），与 store.stretchMode 解耦。 */
const stretchValue = computed(() => (sceneActive.value ? 'linear2' : store.stretchMode));
const stretchTitle = computed(() =>
  sceneActive.value
    ? '盘阵 JPG 已烘焙 2% 线性拉伸（本地 TIF 路径保留全部拉伸模式）'
    : '',
);

const STRETCH_OPTIONS: { value: StretchMode; label: string }[] = [
  { value: 'linear', label: '线性' },
  { value: 'linear2', label: '2% 线性' },
  { value: 'sqrt', label: '平方根' },
  { value: 'log', label: '对数' },
  { value: 'equal', label: '直方图均衡' },
];

function onPick(e: Event) {
  const el = e.target as HTMLInputElement;
  if (el.files) store.addFiles(el.files);
  el.value = '';   // 允许重复选同一文件（触发 change）
}

function doLocate() {
  store.locatePixel(locX.value, locY.value);
}

const outBtnCls = computed(() => {
  if (store.outDir.ready) return 'granted';
  if (store.outDir.name) return 'pending';
  return '';
});
const outBtnText = computed(() => {
  if (!store.outDir.name && !store.outDir.ready) return '输出目录: 未授权';
  if (store.outDir.ready) return '输出目录: ✓ ' + store.outDir.name;
  return '输出目录: ' + (store.outDir.name || '…') + '（待授权）';
});
const outBtnTitle = computed(() =>
  store.outDir.ready
    ? ''
    : store.outDir.name
      ? '点击重新授权该目录'
      : '点击选择输出目录（授权后自动按日期写 JPG 中间产物）',
);
</script>

<template>
  <div class="toolbar">
    <button
      type="button"
      class="btn"
      :disabled="store.busy"
      @click="fileInput?.click()"
    >
      选择 TIF…
    </button>
    <input
      ref="fileInput"
      type="file"
      accept=".tif,.tiff,.TIF,.TIFF"
      multiple
      style="display: none"
      @change="onPick"
    />

    <select
      class="stretch-sel"
      :value="stretchValue"
      :disabled="sceneActive"
      :title="stretchTitle"
      @change="store.setStretch(($event.target as HTMLSelectElement).value as StretchMode)"
    >
      <option v-for="o in STRETCH_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>

    <button
      type="button"
      class="outbtn"
      :class="outBtnCls"
      :title="outBtnTitle"
      @click="store.authorizeOutDir()"
    >
      {{ outBtnText }}
    </button>

    <label class="chk">
      <input v-model="store.autoExport" type="checkbox" @change="store.autoExport && store.scanPendingExports()" />
      自动JPG
    </label>

    <span class="loc">
      X
      <input
        v-model="locX"
        type="number"
        min="0"
        @keydown.enter="doLocate"
      />
      Y
      <input
        v-model="locY"
        type="number"
        min="0"
        @keydown.enter="doLocate"
      />
      <button type="button" class="loc-btn" @click="doLocate">定位</button>
    </span>

    <span class="spacer"></span>

    <button
      type="button"
      class="outbtn"
      :class="{ on: store.drawMode }"
      @click="store.drawMode ? store.exitDraw() : store.enterDraw()"
    >
      绘制掩码{{ store.drawMode ? ' ✓' : '' }}
    </button>
    <button type="button" class="btn" :disabled="store.busy" @click="store.genMask()">生成掩码</button>
    <button
      type="button"
      class="outbtn grad"
      :class="{ on: srReady }"
      :disabled="store.srBusy || !srReady"
      :title="sceneActive
        ? (store.activeRec?.sceneId
            ? '把掩码烘焙到盘阵原图目录，跳转队列页确认后提交 SR'
            : '此图非盘阵场景打开，无法服务端烘焙掩码')
        : '仅盘阵场景（先经「盘阵场景」打开）支持提交 SR'"
      @click="store.submitSr()"
    >
      {{ store.srBusy ? '烘焙中…' : '提交 SR' }}
    </button>
  </div>
</template>

<style scoped>
/* 工具栏：孔雀石绿深带（与顶部导航同族的结构带）；控件 = 白/浅底 pill 浮其上，形成强对比 */
.toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  height: 52px;
  background: var(--band-grad);
  border-bottom: 1px solid rgba(255, 255, 255, 0.14);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
  z-index: 5;
}

/* 主按钮：深带上白底 pill + 深孔雀石字（最高对比）；hover 微沉 */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  padding: 0 16px;
  background: #fff;
  color: var(--band-2);
  font-size: 13px;
  font-weight: 600;
  border: none;
  border-radius: var(--r-ctrl);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
  transition: filter 0.15s ease, box-shadow 0.15s ease;
}
.btn:hover { filter: brightness(0.97); box-shadow: 0 2px 5px rgba(0, 0, 0, 0.24); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; box-shadow: none; filter: none; }

/* 拉伸下拉：浅底 pill（白/浅面浮深带） */
.stretch-sel {
  height: 32px;
  padding: 0 10px;
  background: rgba(255, 255, 255, 0.95);
  color: var(--ink-body);
  font-size: 13px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  outline: none;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.stretch-sel:hover { border-color: #fff; }
.stretch-sel:focus-visible { border-color: #fff; box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.28); }
.stretch-sel:disabled { opacity: 0.55; cursor: not-allowed; }

.loc {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  color: rgba(255, 255, 255, 0.85);
  font-size: 12px;
}
.loc input {
  width: 60px;
  height: 28px;
  padding: 0 8px;
  background: rgba(255, 255, 255, 0.95);
  color: var(--ink);
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 8px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s ease;
}
.loc input:hover, .loc input:focus { border-color: #fff; }
.loc-btn {
  height: 28px;
  padding: 0 12px;
  background: rgba(255, 255, 255, 0.95);
  color: var(--band-2);
  font-size: 13px;
  font-weight: 500;
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.loc-btn:hover { background: #fff; }

/* 次级按钮：透明 ghost（白描边白字）；状态色以浅底 pill 表达 */
.outbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  padding: 0 12px;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 12px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.outbtn:hover:not(:disabled) { background: rgba(255, 255, 255, 0.2); color: #fff; border-color: rgba(255, 255, 255, 0.6); }
.outbtn:disabled { opacity: 0.5; cursor: not-allowed; }
/* 输出目录授权/待授权：浅状态 pill（区别于 ghost） */
.outbtn.granted { color: var(--accent-deep); border-color: transparent; background: var(--ok-bg); }
.outbtn.pending { color: var(--warn); border-color: transparent; background: var(--warn-bg); }
/* 激活态（绘制掩码 on / 提交SR grad.on）：白底深绿字 + 白环 = 高亮选中 */
.outbtn.on, .outbtn.grad.on {
  color: var(--band-2);
  background: #fff;
  border-color: #fff;
  font-weight: 600;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25);
}

.chk {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #fff;
  font-size: 12px;
  flex: none;
  cursor: pointer;
}
.chk input { accent-color: #C7F0EC; }

.spacer { flex: 1; }
</style>
