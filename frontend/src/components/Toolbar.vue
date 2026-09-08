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
/* 工具栏：白色浮层承托，分隔线极浅冷灰 */
.toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  height: 52px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
  box-shadow: 0 1px 4px rgba(46, 90, 78, 0.04);
  z-index: 5;
}

/* 主按钮：青绿渐变（仅主操作） */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  padding: 0 16px;
  background: var(--accent-grad);
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  border: none;
  border-radius: var(--r-ctrl);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  box-shadow: 0 2px 6px rgba(45, 164, 162, 0.25);
  transition: filter 0.15s ease, box-shadow 0.15s ease;
}
.btn:hover { filter: brightness(1.05); box-shadow: 0 3px 8px rgba(45, 164, 162, 0.32); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; box-shadow: none; filter: none; }

/* 拉伸下拉：控件同族（浅底/白底 + 细边框，聚焦青绿描边） */
.stretch-sel {
  height: 32px;
  padding: 0 10px;
  background: var(--surface-2);
  color: var(--ink-body);
  font-size: 13px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  outline: none;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.stretch-sel:hover { border-color: var(--accent-2); }
.stretch-sel:focus-visible { border-color: var(--accent-3); box-shadow: 0 0 0 3px rgba(45, 164, 162, 0.15); }
.stretch-sel:disabled { opacity: 0.55; cursor: not-allowed; }

.loc {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  color: var(--ink-sub);
  font-size: 12px;
}
.loc input {
  width: 60px;
  height: 28px;
  padding: 0 8px;
  background: var(--surface-2);
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: 8px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s ease;
}
.loc input:hover, .loc input:focus { border-color: var(--accent-2); }
.loc-btn {
  height: 28px;
  padding: 0 12px;
  background: var(--accent-soft);
  color: var(--accent-deep);
  font-size: 13px;
  font-weight: 500;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.loc-btn:hover { background: #d6ece9; }

/* 次级按钮：白底 + 1px 细边框 + 灰字（状态用青绿/琥珀着字） */
.outbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  padding: 0 12px;
  background: var(--surface);
  color: var(--ink-body);
  font-size: 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-ctrl);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}
.outbtn:hover:not(:disabled) { border-color: var(--accent-2); color: var(--accent-deep); }
.outbtn:disabled { opacity: 0.5; cursor: not-allowed; }
.outbtn.granted { color: var(--accent-deep); border-color: var(--ok-line); background: var(--ok-bg); }
.outbtn.pending { color: var(--warn); border-color: var(--warn-line); background: var(--warn-bg); }
/* 通用选中态（如绘制掩码 on）：浅青绿底 + 深青绿字，不做大色块 */
.outbtn.on { border-color: var(--accent-2); color: var(--accent-deep); background: var(--accent-soft); }
/* 提交 SR 就绪（grad.on）：转为主 CTA 青绿渐变 */
.outbtn.grad.on { color: #fff; background: var(--accent-grad); border-color: transparent; box-shadow: 0 2px 6px rgba(45, 164, 162, 0.28); }

.chk {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ink-body);
  font-size: 12px;
  flex: none;
  cursor: pointer;
}
.chk input { accent-color: var(--accent-3); }

.spacer { flex: 1; }
</style>
