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
  </div>
</template>

<style scoped>
.toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px;
  height: 50px;
  background: #1b1f24;
  border-bottom: 1px solid #2c313a;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  padding: 0 16px;
  background: #2b7bdd;
  color: #fff;
  font-size: 14px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.btn:hover { background: #357fd8; }
.btn:disabled { opacity: 0.6; cursor: wait; }

.stretch-sel {
  height: 34px;
  padding: 0 8px;
  background: #22262c;
  color: #d5d9de;
  font-size: 13px;
  border: 1px solid #3a4149;
  border-radius: 6px;
  cursor: pointer;
  outline: none;
}
.stretch-sel:hover { border-color: #2b7bdd; }
.stretch-sel:disabled { opacity: 0.55; cursor: not-allowed; border-color: #3a4149; }

.loc {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  color: #9fb0c0;
  font-size: 12px;
}
.loc input {
  width: 60px;
  height: 30px;
  padding: 0 8px;
  background: #22262c;
  color: #d5d9de;
  border: 1px solid #3a4149;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}
.loc input:hover, .loc input:focus { border-color: #2b7bdd; }
.loc-btn {
  height: 30px;
  padding: 0 12px;
  background: #2b7bdd;
  color: #fff;
  font-size: 13px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.loc-btn:hover { background: #357fd8; }

.outbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  padding: 0 14px;
  background: #22262c;
  color: #9fb0c0;
  font-size: 12px;
  border: 1px solid #3a4149;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.outbtn:hover { border-color: #2b7bdd; color: #d5d9de; }
.outbtn.granted { color: #5bb974; border-color: #3a6b4d; }
.outbtn.pending { color: #e2a541; border-color: #6b5a2d; }
.outbtn.on { border-color: #2b7bdd; color: #d5d9de; background: #22304a; }

.chk {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #9fb0c0;
  font-size: 12px;
  flex: none;
  cursor: pointer;
}
.chk input { accent-color: #2b7bdd; }

.spacer { flex: 1; }
</style>
