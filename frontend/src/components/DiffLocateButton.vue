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
import { isDeveloperMode } from '../lib/developerMode';
import { differenceSourceLabel } from '../lib/smartCompareLabels';

const diff = useDiffStore();
const viewer = useViewerStore();
const entry = ref<HTMLElement | null>(null);

const autoThreshold = computed(() =>
  DIFF_THRESHOLD_PRESETS[diff.method][diff.sensitivity]);
/** 下拉收口说明：跟着左侧选中的影像，只列同一场景的图。 */
const sceneNote = computed(() => {
  const rec = viewer.activeRec;
  if (!rec || (!rec.sceneDir && !rec.lqPath)) return '';
  const group = rec.sceneDir ? viewer.sceneOrdinalOf(rec) : 0;
  return `影像 A / B 只列左侧当前场景${group ? `（影像组 ${group}）` : ''}的图；`
    + '未打开的同景图也可直接选择，分析时自动载入。';
});
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
    source: differenceSourceLabel(r.source),
    method: r.method === 'smart' ? '智能对比' : r.method === 'ssim' ? '结构差异' : '像素差分',
  };
});

async function run() {
  await diff.analyze();
}
async function selectMethod(next:'absdiff'|'ssim'|'smart') {
  if(diff.comparison && !diff.stale) {
    if(!diff.comparison.results[next]) {diff.error=diff.comparison.summary.find(s=>s.method===next)?.error??'该方法没有可用结果';return;}
    await diff.showComparison(next);return;
  }
  diff.method=next;
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
          <p v-if="sceneNote" class="diff-note">{{ sceneNote }}</p>
          <div class="pair-actions">
            <button type="button" :disabled="diff.busy" @click="diff.selectPairFromActive(); diff.ensurePair()">用当前图作 A</button>
            <button type="button" :disabled="diff.busy || !diff.aRec || !diff.bRec || !!diff.flickerPairReason" :title="diff.flickerPairReason || '同景影像按比例交替查看'" @click="diff.openFlicker()">交替对比</button>
            <button type="button" :disabled="diff.busy || !diff.aRec || !diff.bRec || diff.aId===diff.bId" @click="viewer.openPreviewPair(diff.aId!,diff.bId!)">并排预览</button>
          </div>

        </section>

        <section class="diff-section" aria-label="分析方法">
          <div class="seg" role="radiogroup" aria-label="分析方法">
            <button type="button" :class="{ on: diff.method === 'absdiff' }" role="radio"
              :aria-checked="diff.method === 'absdiff'" :disabled="diff.busy"
              @click="selectMethod('absdiff')">像素差分</button>
            <button type="button" :class="{ on: diff.method === 'ssim' }" role="radio"
              :aria-checked="diff.method === 'ssim'" :disabled="diff.busy"
              @click="selectMethod('ssim')">结构差异 SSIM</button>
            <button type="button" :class="{ on: diff.method === 'smart' }" role="radio"
              :aria-checked="diff.method === 'smart'" :disabled="diff.busy" data-e2e="diff-smart"
              @click="selectMethod('smart')">智能对比</button>
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
          <label v-if="diff.method === 'smart'" class="diff-field diff-inline">
            <span>异常显示阈值</span><input v-model.number="diff.displayThreshold" type="range" min="0" max="1" step="0.01" aria-label="异常显示阈值" />
            <em>{{ diff.displayThreshold.toFixed(2) }}</em>
          </label>
          <label class="diff-toggle"><span>显示异常边界</span><input v-model="diff.boundariesVisible" type="checkbox" aria-label="显示异常边界" /></label>
        </section>

        <section v-if="diff.method !== 'smart'" class="diff-section" aria-label="候选复核">
          <div class="diff-section-title">候选复核</div>
          <label class="diff-toggle">
            <span>亮度归因权重（非线性）</span>
            <input v-model="diff.toneWeight" class="diff-switch" type="checkbox" role="switch"
              aria-label="亮度归因权重" data-e2e="diff-tone-weight" />
          </label>
          <p class="diff-note">
            开启后按原始灰度带、提亮方向与平坦度逐像素连续调权：热力图强度乘以局部权重
            （滑动窗口随分析网格自适应），候选同时按复核权重重排、轮廓由冷到暖。
            只影响显示与排序，不改差异数值、面积和掩码资格；灰度取显示层，不是原始 DN。
          </p>
        </section>

        <details class="diff-advanced">
          <summary>高级设置</summary>
          <div v-if="diff.method === 'smart'" class="diff-grid">
            <label class="diff-field"><span>纹理窗口</span><select v-model.number="diff.smartOptions.window" aria-label="纹理窗口"><option :value="7">7×7</option><option :value="15">15×15</option><option :value="31">31×31</option></select></label>
            <label class="diff-field"><span>分析精度</span><select v-model="diff.smartPrecision" aria-label="智能对比分析精度"><option value="source-grid">原始 DN 网格</option><option value="preview">预览检测</option></select></label>
            <label class="diff-field"><span>传感器配置</span><select v-model="diff.smartOptions.sensor" aria-label="传感器配置"><option v-for="s in ['generic','JL1KF01','JL1KF02','JL1GF04']" :key="s">{{ s }}</option></select></label>
            <label class="diff-field"><span>固定校准尺度（可选）</span><input :value="diff.smartOptions.calibrationScale ?? ''" type="number" min="0.01" step="0.1" aria-label="固定校准尺度" @change="diff.smartOptions.calibrationScale = ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : undefined" /></label>
          </div>
          <template v-if="diff.method === 'smart'">
            <label class="diff-toggle"><span>场景掩码降权（可用时）</span><input v-model="diff.smartOptions.sceneSuppression" type="checkbox" aria-label="场景掩码降权" /></label>
            <label class="diff-toggle"><span>频域复核（实验性）</span><input v-model="diff.smartOptions.frequencyReview" type="checkbox" aria-label="频域复核" /></label>
            <button type="button" :disabled="diff.busy || !!diff.nativeRefineReason" :title="diff.nativeRefineReason" @click="diff.scanNativeVisible()">当前视野原始尺度补扫</button>
            <p class="diff-note">补扫保留细小异常，每次最多 64 个 512² 块；范围过大时请放大视野。补扫概览仍需原图精检后才能加入掩码。</p>
            <p class="diff-note">当前参数未完成生产校准；缺少云水阴影掩码时会明确标为未验证。窗口单位为分析网格像素。</p>
          </template>
          <label v-if="isDeveloperMode()" class="diff-toggle"><span>三算法并行对照（共享读取）</span><input v-model="diff.parallelCompare" type="checkbox" aria-label="三算法对照" /></label>
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
                :disabled="diff.method !== 'ssim' && !diff.parallelCompare">
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
              aria-label="轻度高斯平滑" :disabled="diff.method === 'smart' && !diff.parallelCompare" />
          </label>
          <label class="diff-toggle" :class="{ muted: diff.method !== 'ssim' && !diff.parallelCompare }">
            <span>SSIM 使用高斯权重</span>
            <input v-model="diff.advanced.ssimGaussian" class="diff-switch" type="checkbox" role="switch"
              aria-label="SSIM 使用高斯权重" :disabled="diff.method !== 'ssim' && !diff.parallelCompare" />
          </label>
          <p class="diff-note">阈值留空 = 按灵敏度自动取值（当前 {{ autoThreshold.toFixed(2) }}）。</p>
        </details>

        <p v-if="diff.notice" class="diff-notice" role="status">{{ diff.notice }}</p>
        <p v-if="diff.precisionReason" role="status">差异分析暂不可用：{{ diff.precisionReason }}；可继续并排预览。</p>
        <p v-else-if="diff.alignmentNote" class="diff-note" role="status">{{ diff.alignmentNote }}</p>
        <p v-if="diff.error" class="diff-error" role="alert">{{ diff.error }}</p>
        <p v-if="diff.method === 'smart' && diff.smartPairReason" class="diff-note">{{ diff.smartPairReason }}</p>
        <p v-if="diff.method === 'smart'" role="status">{{ diff.analysisState }}</p>
        <details v-if="diff.comparison"><summary>三算法对照</summary>
          <button type="button" :disabled="diff.stale || diff.busy" data-e2e="diff-three-open" @click="diff.comparisonView=true;diff.closePanel()">三图同步对照</button>
          <div v-for="item in diff.comparison.summary" :key="item.method" class="compare-row">
            <button type="button" :disabled="diff.busy || diff.stale || item.status !== 'completed'" @click="diff.showComparison(item.method)">{{ item.method }} · {{ item.status === 'completed' ? item.regions + ' 区域' : '检测失败' }}</button>
            <small v-if="item.status === 'completed'">{{ item.areaPixels }} px² · 有效覆盖 {{ (item.coverage*100).toFixed(2) }}% · {{ item.elapsedMs }} ms</small>
            <small v-else role="status">{{ item.error }}</small>
          </div>
          <p v-for="pair in diff.comparison.overlaps" :key="pair.methods.join('/')">{{ pair.methods.join(' / ') }} IoU {{ pair.iou == null ? '不可用' : pair.iou.toFixed(3) }}</p>
          <p>Smart 独有候选像素 {{ diff.comparison.smartUniquePixels ?? '不可用（对照不完整）' }}</p>
        </details>
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
        <button v-if="diff.busy" type="button" @click="diff.cancelAnalysis()">取消</button>
      </footer>
    </div>
  </span>
</template>

<style scoped>
.diff-entry{position:relative;display:inline-flex;flex:none}
.compare-row{display:flex;flex-direction:column;gap:3px;margin:7px 0;color:var(--ink-sub)}
.compare-row button{font:inherit;color:var(--ink);background:var(--surface-2);border:1px solid var(--line);border-radius:5px;padding:5px;cursor:pointer}
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
