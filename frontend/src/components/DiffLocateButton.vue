<script setup lang="ts">
/**
 * DiffLocateButton.vue — 顶栏「差异定位」入口 + 紧凑参数面板
 * ------------------------------------------------------------------
 * 与自动巡览同一套按钮/浮层几何：32px 高、336px 宽、右对齐。
 * 面板常开不挤占画布；只负责**设参数与发起分析**，结果显示在 Viewer 叠加层
 * 与侧舱「差异候选」里（见 DiffOverlay.vue / DiffCandidatesPanel.vue）。
 *
 * 切换方法 / 显示方式不会关闭面板；显示方式 / 羽化只是前端图层的开关，
 * 不会重新发包（见 stores/diff.ts）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useDiffStore } from '../stores/diff';
import { useViewerStore } from '../stores/viewer';
import { DIFF_THRESHOLD_PRESETS } from '../lib/diffLocate';
import DiffColorLegend from './DiffColorLegend.vue';
import AppIcon from './AppIcon.vue';

const diff = useDiffStore();
const viewer = useViewerStore();
const entry = ref<HTMLElement | null>(null);

const autoThreshold = computed(() =>
  DIFF_THRESHOLD_PRESETS[diff.method][diff.sensitivity]);
const thresholdText = computed({
  get: () => diff.advanced.threshold == null ? '' : String(diff.advanced.threshold),
  set: (v: string) => {
    const n = Number(v);
    diff.advanced.threshold = v.trim() === '' || !Number.isFinite(n) ? null : n;
  },
});
const minAreaText = computed({
  get: () => String(diff.minAreaPx),
  set: (v: string) => {
    const n = Math.round(Number(v));
    diff.minAreaPx = Number.isFinite(n) ? Math.max(1, Math.min(1_000_000, n)) : 1;
  },
});

const optionsFor = (slot: 'a' | 'b') => {
  const other = slot === 'a' ? diff.bId : diff.aId;
  return diff.pairOptions.map((o) => ({
    ...o,
    // 另一侧还没选时不限制 —— 否则第一次打开面板两个下拉全是灰的。
    disabled: other != null && !diff.compatible(o.id, other),
  }));
};

const summary = computed(() => {
  const r = diff.result;
  if (!r) return null;
  const g = r.analysisGrid;
  return {
    count: r.regions.length,
    truncated: r.truncated,
    grid: `${g.width}×${g.height}`,
    coverage: Math.round((r.coverage?.validRatio ?? 0) * 1000) / 10,
    ms: r.elapsedMs,
    threshold: r.parameters.threshold,
    source: r.source === 'source-roi' ? '原始 ROI' : '预览',
    method: r.method === 'ssim' ? '结构差异' : '像素差分',
  };
});

async function run() {
  await diff.analyze();
}

function clear() {
  diff.clearResults();
}

function escape(e: KeyboardEvent) {
  if (e.key === 'Escape') diff.closePanel();
}
function outside(e: Event) {
  if (!(e.target as Element).closest('.diff-entry')) diff.closePanel();
}
onMounted(() => {
  document.addEventListener('keydown', escape);
  document.addEventListener('pointerdown', outside);
});
onBeforeUnmount(() => {
  document.removeEventListener('keydown', escape);
  document.removeEventListener('pointerdown', outside);
});
</script>

<template>
  <span class="diff-entry" ref="entry">
    <button
      class="diff-button"
      :class="{ on: diff.open || !!diff.result }"
      title="智能差异定位：对比两张同范围影像，找成片的显著变化区域"
      aria-label="智能差异定位"
      :aria-expanded="diff.open"
      data-e2e="diff-open"
      @click="diff.open ? diff.closePanel() : diff.openPanel()"
    >
      <AppIcon name="scan-search" :size="16" tint />
      <span>差异定位</span>
    </button>

    <div v-if="diff.open" class="diff-popover" role="dialog" aria-label="差异定位设置">
      <header class="diff-heading">
        <span class="heading-icon"><AppIcon name="scan-search" :size="16" tint /></span>
        <div>
          <strong>智能差异定位</strong>

        </div>
        <button class="diff-close" type="button" aria-label="关闭差异定位设置" title="关闭设置"
          @click="diff.closePanel">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m5 5 6 6M11 5l-6 6" /></svg>
        </button>
      </header>

      <div class="diff-body">
        <section class="diff-section" aria-label="影像组合">
          <div class="diff-section-title">影像与分析参数</div>
          <div class="diff-grid">
            <label class="diff-field">
              <span>影像 A</span>
              <select v-model.number="diff.aId" aria-label="影像 A" :disabled="diff.busy">
                <option :value="null">请选择</option>
                <option v-for="o in optionsFor('a')" :key="o.id" :value="o.id" :disabled="o.disabled">
                  {{ o.name }}<template v-if="o.stage"> · {{ o.stage }}</template>
                </option>
              </select>
            </label>
            <label class="diff-field">
              <span>影像 B</span>
              <select v-model.number="diff.bId" aria-label="影像 B" :disabled="diff.busy">
                <option :value="null">请选择</option>
                <option v-for="o in optionsFor('b')" :key="o.id" :value="o.id" :disabled="o.disabled">
                  {{ o.name }}<template v-if="o.stage"> · {{ o.stage }}</template>
                </option>
              </select>
            </label>
          </div>
          <div class="pair-actions">
            <button type="button" :disabled="diff.busy" @click="diff.selectPairFromActive(); diff.ensurePair()">用当前图作 A</button>
            <button type="button" :disabled="diff.busy || !diff.aRec || !diff.bRec || !!diff.precisionReason" @click="diff.openFlicker()">精确交替</button>
            <button type="button" :disabled="diff.busy || !diff.aRec || !diff.bRec || diff.aId===diff.bId" @click="viewer.openPreviewPair(diff.aId!,diff.bId!)">并排预览</button>
          </div>

        </section>

        <section class="diff-section" aria-label="分析方法">
          <div class="seg" role="radiogroup" aria-label="分析方法">
            <button type="button" :class="{ on: diff.method === 'absdiff' }" role="radio"
              :aria-checked="diff.method === 'absdiff'" :disabled="diff.busy"
              @click="diff.method = 'absdiff'">像素差分</button>
            <button type="button" :class="{ on: diff.method === 'ssim' }" role="radio"
              :aria-checked="diff.method === 'ssim'" :disabled="diff.busy"
              @click="diff.method = 'ssim'">结构差异 SSIM</button>
          </div>
        </section>

        <section class="diff-section" aria-label="分析范围">
          <div class="diff-grid">
            <label class="diff-field">
              <span>分析范围</span>
              <select v-model="diff.scope" aria-label="分析范围" :disabled="diff.busy">
                <option value="common">公共覆盖范围</option>
                <option value="visible">当前视野 ROI</option>
              </select>
            </label>
            <label class="diff-field">
              <span>差异灵敏度</span>
              <select v-model="diff.sensitivity" aria-label="差异灵敏度" :disabled="diff.busy">
                <option value="low">低（阈值高，只留大变化）</option>
                <option value="standard">标准</option>
                <option value="high">高（阈值低，变化更细）</option>
              </select>
            </label>
          </div>
          <label class="diff-field diff-inline">
            <span>最小区域面积</span>
            <input v-model="minAreaText" type="number" min="1" step="1" inputmode="numeric"
              aria-label="最小区域面积（px²）" :disabled="diff.busy" />
            <em>px²</em>
          </label>
        </section>

        <section class="diff-section diff-display-section" :class="{ ready: !!diff.result }" aria-label="显示方式">
          <div class="diff-section-title">显示方式<span v-if="diff.result">{{ diff.resultVisible ? '显示中' : '已隐藏' }}</span></div>
          <div class="seg" role="radiogroup" aria-label="显示方式">
            <button type="button" :class="{ on: diff.display === 'regions' }" role="radio"
              :aria-checked="diff.display === 'regions'" @click="diff.display = 'regions'">原图标记</button>
            <button type="button" :class="{ on: diff.display === 'heat' }" role="radio"
              :aria-checked="diff.display === 'heat'" @click="diff.display = 'heat'">独立热力图</button>
            <button type="button" :class="{ on: diff.display === 'overlay' }" role="radio"
              :aria-checked="diff.display === 'overlay'" @click="diff.display = 'overlay'">热力叠加</button>
          </div>
          <template v-if="diff.display === 'overlay'">
            <label class="diff-field diff-inline">
              <span>叠加不透明度</span>
              <input v-model.number="diff.opacity" type="range" min="0" max="1" step="0.05" aria-label="热力叠加不透明度" />
              <em>{{ Math.round(diff.opacity * 100) }}%</em>
            </label>
            <label class="diff-field diff-inline">
              <span>边缘羽化</span>
              <select v-model="diff.feather" aria-label="羽化强度">
                <option value="off">关闭</option>
                <option value="light">轻度</option>
                <option value="medium">中度</option>
              </select>
            </label>
          </template>
          <DiffColorLegend v-if="diff.result && diff.display !== 'regions'" class="panel-legend" />
        </section>

        <details class="diff-advanced">
          <summary>高级设置</summary>
          <div class="diff-grid">
            <label class="diff-field">
              <span>开运算核</span>
              <select v-model.number="diff.advanced.openKernel" aria-label="开运算核">
                <option :value="1">1×1（不处理）</option>
                <option :value="3">3×3</option>
                <option :value="5">5×5</option>
              </select>
            </label>
            <label class="diff-field">
              <span>闭运算核</span>
              <select v-model.number="diff.advanced.closeKernel" aria-label="闭运算核">
                <option :value="1">1×1（不处理）</option>
                <option :value="3">3×3</option>
                <option :value="5">5×5</option>
              </select>
            </label>
            <label class="diff-field">
              <span>SSIM 窗口</span>
              <select v-model.number="diff.advanced.ssimWindow" aria-label="SSIM 窗口"
                :disabled="diff.method !== 'ssim'">
                <option :value="5">5×5</option>
                <option :value="7">7×7</option>
                <option :value="9">9×9</option>
              </select>
            </label>
            <label class="diff-field">
              <span>差异阈值（0–1）</span>
              <input v-model="thresholdText" type="number" min="0.001" max="0.999" step="0.01"
                inputmode="decimal" :placeholder="'自动 ' + autoThreshold.toFixed(2)"
                aria-label="差异阈值" />
            </label>
          </div>
          <label class="diff-toggle">
            <span>轻度高斯平滑（3×3）</span>
            <input v-model="diff.advanced.blur" class="diff-switch" type="checkbox" role="switch"
              aria-label="轻度高斯平滑" />
          </label>
          <label class="diff-toggle" :class="{ muted: diff.method !== 'ssim' }">
            <span>SSIM 使用高斯权重</span>
            <input v-model="diff.advanced.ssimGaussian" class="diff-switch" type="checkbox" role="switch"
              aria-label="SSIM 使用高斯权重" :disabled="diff.method !== 'ssim'" />
          </label>
          <p class="diff-note">阈值留空 = 按灵敏度自动取值（当前 {{ autoThreshold.toFixed(2) }}）。</p>
        </details>

        <p v-if="diff.notice" class="diff-notice" role="status">{{ diff.notice }}</p>
        <p v-if="diff.precisionReason" role="status">精确分析暂不可用：{{ diff.precisionReason }}；可继续并排预览。</p>
        <p v-if="diff.error" class="diff-error" role="alert">{{ diff.error }}</p>
        <p v-else-if="diff.stale" class="diff-notice" role="status">
          参数已修改，当前仍显示上一次结果；重新分析后生效。
        </p>
        <p v-if="summary" class="diff-summary" role="status">
          {{ summary.method }} · {{ summary.source }} {{ summary.grid }} ·
          {{ summary.count }} 个候选<template v-if="summary.truncated">（已截断）</template> ·
          阈值 {{ summary.threshold }} · {{ summary.ms }} ms · 有效 {{ summary.coverage }}%
        </p>
        <details v-if="diff.lastWarnings.length" class="diff-warnings">
          <summary>分析口径说明（{{ diff.lastWarnings.length }}）</summary>
          <p v-for="(w, i) in diff.lastWarnings" :key="i">{{ w }}</p>
        </details>
      </div>

      <footer class="diff-footer">
        <button class="diff-start" type="button" :disabled="diff.busy || !diff.aRec || !diff.bRec || diff.aId === diff.bId || !!diff.precisionReason"
          data-e2e="diff-analyze" @click="run">
          {{ diff.busy ? '分析中…' : diff.result ? '重新分析' : '开始分析' }}
        </button>
        <button class="diff-clear" type="button" :disabled="diff.busy || !diff.result" @click="clear">
          清除结果
        </button>
      </footer>
    </div>
  </span>
</template>

<style scoped>
.diff-entry{position:relative;display:inline-flex;flex:none}
.diff-button{display:inline-flex;align-items:center;justify-content:center;gap:6px;flex:none;width:100px;height:32px;padding:0 10px;box-sizing:border-box;white-space:nowrap;font-family:inherit;font-size:13px;font-weight:500;border:1px solid var(--tb-border);border-radius:8px;background:var(--tb-surface);color:var(--tb-fg);cursor:pointer;transition:background var(--motion-hover),color var(--motion-hover)}
.diff-button:hover,.diff-button.on{background:var(--tb-active-bg);color:var(--tb-primary)}
.diff-button:focus-visible{outline:2px solid var(--tb-focus)}
.diff-popover{position:absolute;top:calc(100% + 8px);right:0;width:336px;max-width:calc(100vw - 24px);max-height:calc(100dvh - 116px);overflow:auto;box-sizing:border-box;z-index:40;background:var(--surface);border:1px solid var(--tb-border);border-radius:12px;box-shadow:0 1px 2px #173e3110,0 4px 12px #173e3114,0 12px 32px #173e3114;font-size:12px;color:var(--ink);scrollbar-width:thin}
.diff-heading{display:flex;align-items:center;gap:8px;padding:10px 13px;border-bottom:1px solid var(--tb-divider)}
.heading-icon{display:grid;place-items:center;width:29px;height:29px;border:1px solid color-mix(in srgb,var(--tb-primary) 15%,var(--tb-border));border-radius:8px;background:var(--tb-active-bg);color:var(--tb-primary)}
.diff-heading strong{display:block;font-size:12px;line-height:17px;font-weight:650;letter-spacing:.03em}
.heading-caption{display:block;font-size:12px;line-height:15px;color:var(--ink-sub)}
.diff-close{margin-left:auto;align-self:flex-start;border:0;background:transparent;display:grid;place-items:center;width:24px;height:24px;border-radius:5px;color:var(--ink-sub);cursor:pointer}
.diff-close svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.2;stroke-linecap:round}
.diff-close:hover{background:var(--tb-active-bg);color:var(--ink)}
.diff-body{padding:11px 13px 4px}
.diff-section{margin-bottom:9px}
.diff-section-title{display:flex;justify-content:space-between;font-size:12px;font-weight:600;color:var(--ink-sub);line-height:14px;margin-bottom:6px}
.diff-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.diff-field{display:flex;flex-direction:column;gap:5px;min-width:0}
.diff-field>span{font-size:12px;color:var(--ink-sub);line-height:14px}
.diff-field select,.diff-field input{width:100%;min-width:0;height:30px;box-sizing:border-box;border:1px solid var(--tb-border);border-radius:6px;background:var(--tb-surface);padding:0 7px;color:var(--ink);font:inherit;font-size:12px;cursor:pointer}
.diff-field input{cursor:text}
.diff-inline{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:7px;margin-top:8px}
.diff-inline>span{font-size:12px;color:var(--ink-sub);white-space:nowrap}
.diff-inline em{font-style:normal;font-size:9px;color:var(--ink-sub);white-space:nowrap}
.pair-actions{display:flex;gap:5px;margin-top:8px}
.pair-actions button{flex:1;min-width:0;height:30px;box-sizing:border-box;font:inherit;font-size:12px;border:1px solid var(--tb-border);border-radius:5px;background:var(--tb-surface);color:var(--ink-sub);padding:0 5px;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard)}
.pair-actions button:hover:enabled{background:var(--tb-active-bg);color:var(--tb-primary)}
.seg{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:0;border:1px solid var(--tb-border);border-radius:6px;overflow:hidden;background:var(--tb-surface)}
.seg button{height:30px;border:0;border-right:1px solid var(--tb-border);background:transparent;color:var(--ink-sub);font:inherit;font-size:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 4px}
.seg button:last-child{border-right:0}
.seg button:hover{background:var(--tb-active-bg)}
.seg button.on{background:var(--tb-active-bg);color:var(--tb-primary);font-weight:600;box-shadow:inset 0 -2px 0 var(--tb-primary)}
.diff-note{margin:7px 0 0;font-size:9.5px;line-height:14px;color:var(--ink-sub);overflow-wrap:anywhere}
.diff-display-section{border-top:1px solid var(--tb-divider);padding-top:9px}
.diff-display-section.ready{padding:9px;border:1px solid color-mix(in srgb,var(--tb-primary) 30%,var(--tb-border));border-radius:8px;background:color-mix(in srgb,var(--tb-active-bg) 35%,var(--surface))}
.diff-section-title span{font-size:11px;font-weight:400;color:var(--tb-primary)}
.panel-legend{margin-top:8px}
.diff-field input[type="range"]{padding:0;accent-color:var(--tb-primary);border:0;background:transparent}
.diff-advanced{margin:0 0 10px;border-top:1px solid var(--tb-divider);padding-top:9px}
.diff-advanced summary{cursor:pointer;font-size:12px;font-weight:600;color:var(--ink);margin-bottom:8px;list-style:none}
.diff-advanced summary::before{content:'▸';display:inline-block;width:12px;color:var(--ink-sub);transition:transform var(--motion-hover) var(--ease-standard)}
.diff-advanced[open] summary::before{transform:rotate(90deg)}
.diff-toggle{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:25px;cursor:pointer;margin-top:4px}
.diff-toggle.muted{opacity:.55}
.diff-switch{appearance:none;-webkit-appearance:none;flex:none;position:relative;width:28px;height:16px;border:1px solid var(--tb-border);border-radius:20px;margin:0;background:color-mix(in srgb,var(--ink-sub) 18%,var(--surface));cursor:pointer;transition:background var(--motion-hover) var(--ease-standard),border-color var(--motion-hover) var(--ease-standard)}
.diff-switch::after{content:'';position:absolute;left:2px;top:2px;width:10px;height:10px;border-radius:50%;background:var(--surface);box-shadow:0 1px 2px #173e3129;transition:transform var(--motion-hover) var(--ease-standard)}
.diff-switch:checked{background:var(--tb-primary);border-color:var(--tb-primary)}
.diff-switch:checked::after{transform:translateX(12px)}
.diff-error{margin:6px 0;padding:6px 7px;border-radius:5px;background:#fbe9e6;color:#9d3b2f;font-size:12px;line-height:15px;overflow-wrap:anywhere}
.diff-notice{margin:6px 0;padding:6px 7px;border-radius:5px;background:#fdf3e3;color:#8a5a1f;font-size:12px;line-height:15px}
.diff-summary{margin:6px 0;font-size:12px;line-height:15px;color:var(--ink-sub);overflow-wrap:anywhere}
.diff-warnings{margin:6px 0 0;font-size:12px;line-height:15px;color:var(--ink-sub)}
.diff-warnings summary{cursor:pointer;color:var(--ink-sub)}
.diff-warnings p{margin:4px 0 0}
.diff-footer{display:grid;grid-template-columns:1fr auto;gap:7px;padding:9px 13px 10px;border-top:1px solid var(--tb-divider);background:color-mix(in srgb,var(--tb-bg) 45%,var(--surface));border-radius:0 0 12px 12px}
.diff-start{display:flex;align-items:center;justify-content:center;min-height:31px;padding:6px;border:1px solid transparent;border-radius:7px;background:var(--tb-primary);color:white;font:inherit;font-size:12px;font-weight:600;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard)}
.diff-start:hover:enabled{background:color-mix(in srgb,var(--tb-primary) 88%,black)}
.diff-clear{min-height:31px;padding:6px 11px;border:1px solid var(--tb-border);border-radius:7px;background:var(--tb-surface);color:var(--ink-sub);font:inherit;font-size:12px;cursor:pointer;transition:background var(--motion-hover) var(--ease-standard)}
.diff-clear:hover:enabled{background:var(--tb-active-bg);color:var(--tb-primary)}
button:disabled,select:disabled,input:disabled{opacity:.45;cursor:default}
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--tb-focus);outline-offset:2px}
</style>
