<script setup lang="ts">
/**
 * 查看器页面（阶段2 = 最小管线 demo）
 * ------------------------------------------------------------------
 * 选/拖入 TIF → probeImage 懒读 IFD → 稀疏条带 或 geotiff 分块解码 →
 * 拉伸（默认 2% 线性）→ canvas 显示。切换拉伸不重读文件（解码时保留自然值 src+统计 st）。
 *
 * 完整查看器 UI（工具栏/文件列表/状态栏/输出目录授权/掩码绘制/导出）在阶段3 组件化，
 * 本页只证明「vendor + tifDecode 纯管线 + canvas」的浏览器端链路通（阶段2 验收）。
 */
import { ref, shallowRef } from 'vue';
import { FileSource } from '../lib/source';
import {
  probeImage,
  isSparseCandidate,
  parseStrips,
  sparseCollect,
  getSamplePlan,
  chunkedCollect,
  computeStats,
  stretchRgba,
  PREVIEW_MAX,
  SPARSE_PREVIEW_MAX,
} from '../lib/tifDecode';
import type { StretchMode, BandStats } from '../lib/tifDecode';
import { useViewerStore } from '../stores/viewer';

const viewer = useViewerStore();
const canvasRef = ref<HTMLCanvasElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const stretchMode = ref<StretchMode>('linear2');

/** 解码出的自然值 + 统计：切换拉伸时据此重绘，不重读文件 */
const last = shallowRef<{
  src: Float32Array;
  sw: number;
  sh: number;
  nb: number;
  st: BandStats[] | null;
  invert: boolean;
} | null>(null);

const STRETCH_OPTIONS: { value: StretchMode; label: string }[] = [
  { value: 'linear', label: '线性' },
  { value: 'linear2', label: '2% 线性' },
  { value: 'sqrt', label: '平方根' },
  { value: 'log', label: '对数' },
  { value: 'equal', label: '直方图均衡' },
];

function pickFile(e: Event) {
  const el = e.target as HTMLInputElement;
  const f = el.files?.[0];
  if (f) void decode(f);
  el.value = ''; // 允许重复选同一文件（触发 change）
}

async function decode(file: File) {
  viewer.reset();
  viewer.busy = true;
  viewer.setStatus(`读取 ${file.name}（${(file.size / 1048576).toFixed(1)} MB）…`);
  try {
    const probe = await probeImage(file);
    const source = new FileSource(file, file.name);

    let src: Float32Array;
    let sw: number;
    let sh: number;
    let nb: number;
    let st: BandStats[] | null;
    let invert: boolean;
    let route: 'sparse' | 'chunked';

    if (isSparseCandidate(probe)) {
      // 无压缩 + 条带 + 单波段大图 → 稀疏条带（秒级，长边 ≤8192≈原始 1/3）
      const sp = await parseStrips(source);
      if (!sp.ok) throw new Error('稀疏条带布局解析失败');
      route = 'sparse';
      const r = await sparseCollect(source, probe, sp, SPARSE_PREVIEW_MAX, (f) =>
        viewer.setStatus(`稀疏采样 ${Math.round(f * 100)}%`),
      );
      src = r.src;
      sw = r.sw;
      sh = r.sh;
      nb = r.nb;
      st = r.stats;
      invert = r.invert;
    } else {
      // 其余（16bit/浮点/多波段/压缩/大图）→ geotiff 分块降采样（长边 ≤2048）
      route = 'chunked';
      const plan = getSamplePlan(probe.image);
      const ps = Math.min(1, PREVIEW_MAX / Math.max(probe.W, probe.H));
      sw = Math.max(1, Math.round(probe.W * ps));
      sh = Math.max(1, Math.round(probe.H * ps));
      src = await chunkedCollect(probe.image, probe.W, probe.H, plan, ps, sw, sh, (f) =>
        viewer.setStatus(`分块读取 ${Math.round(f * 100)}%`),
      );
      nb = plan.comps;
      st = computeStats(src, sw, sh, nb);
      invert = plan.invert;
    }

    viewer.setRec({
      name: file.name,
      size: file.size,
      W: probe.W,
      H: probe.H,
      spp: probe.spp,
      bits: probe.bits,
      sampleFormat: probe.sampleFormat,
      photometric: probe.photometric,
      compression: probe.compression,
      layout: probe.layout,
      route,
      sw,
      sh,
    });
    last.value = { src, sw, sh, nb, st, invert };
    paint();
    viewer.setStatus('解码完成');
  } catch (err) {
    viewer.fail(err instanceof Error ? err.message : String(err));
  } finally {
    viewer.busy = false;
  }
}

function paint() {
  const c = canvasRef.value;
  const l = last.value;
  if (!c || !l) return;
  const rgba = stretchRgba(l.src, l.sw, l.sh, l.nb, l.st, stretchMode.value, l.invert);
  c.width = l.sw;
  c.height = l.sh;
  const ctx = c.getContext('2d');
  if (!ctx) return;
  // stretchRgba 返回 Uint8ClampedArray<ArrayBufferLike>，而 ImageData 构造器要求 ArrayBuffer 背板；
  // 此数组总是新建的 ArrayBuffer 背板，故安全窄化。
  ctx.putImageData(new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>, l.sw, l.sh), 0, 0);
}

function onModeChange(e: Event) {
  stretchMode.value = (e.target as HTMLSelectElement).value as StretchMode;
  paint();
}
</script>

<template>
  <div class="viewer-page">
    <div class="toolbar">
      <button type="button" :disabled="viewer.busy" @click="fileInput?.click()">选择 TIF…</button>
      <input
        ref="fileInput"
        type="file"
        accept=".tif,.tiff,.TIF,.TIFF"
        style="display: none"
        @change="pickFile"
      />
      <label class="stretch">
        拉伸
        <select :value="stretchMode" @change="onModeChange">
          <option v-for="o in STRETCH_OPTIONS" :key="o.value" :value="o.value">
            {{ o.label }}
          </option>
        </select>
      </label>
      <span v-if="viewer.busy" class="status">{{ viewer.status }}</span>
    </div>

    <div class="canvas-wrap">
      <canvas ref="canvasRef" class="tif-canvas"></canvas>
      <div v-if="!last && !viewer.busy" class="placeholder">
        选择一张 TIF 文件开始<br />
        <span class="dim">阶段2 最小管线 demo：probe → 稀疏/分块 → 拉伸 → canvas</span>
      </div>
    </div>

    <div v-if="viewer.rec" class="rec-bar">
      <span class="name">{{ viewer.rec.name }}</span>
      · {{ viewer.rec.W }}×{{ viewer.rec.H }} · bits={{ viewer.rec.bits }} spp={{ viewer.rec.spp }}
      sf={{ viewer.rec.sampleFormat }} ph={{ viewer.rec.photometric }} comp={{ viewer.rec.compression }}
      · 路由={{ viewer.rec.route }} · 预览 {{ viewer.rec.sw }}×{{ viewer.rec.sh }}
      <span class="layout">{{ viewer.rec.layout }}</span>
    </div>

    <div v-if="viewer.error" class="error">{{ viewer.error }}</div>
  </div>
</template>

<style scoped>
.viewer-page {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: none;
}

.toolbar button,
.toolbar select {
  padding: 6px 12px;
  border: 1px solid #c8d0d8;
  border-radius: 4px;
  background: #fff;
  font-size: 13px;
}

.toolbar button:disabled {
  opacity: 0.6;
  cursor: wait;
}

.status {
  color: #409eff;
}

.canvas-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  overflow: auto;
  background: #17191c;
  border-radius: 4px;
}

.tif-canvas {
  max-width: 100%;
  max-height: 100%;
  image-rendering: pixelated;
  background: #000;
}

.placeholder {
  color: #8a9199;
  text-align: center;
}

.placeholder .dim {
  font-size: 12px;
  opacity: 0.7;
}

.rec-bar {
  flex: none;
  font-size: 12px;
  color: #526069;
  background: #eef1f4;
  border: 1px solid #dfe4e9;
  border-radius: 4px;
  padding: 6px 10px;
  word-break: break-all;
}

.rec-bar .name {
  font-weight: 600;
}

.rec-bar .layout {
  color: #9aa5b1;
}

.error {
  flex: none;
  color: #e64545;
  background: #fdf0f0;
  border: 1px solid #f5c6c6;
  border-radius: 4px;
  padding: 6px 10px;
  word-break: break-all;
}
</style>
