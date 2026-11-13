<script setup lang="ts">
/**
 * DiffCandidatesPanel.vue — 侧舱「差异候选」清单
 * ------------------------------------------------------------------
 * 分析结果与人工掩码严格分开：这里只读分析结果；「加入掩码」是显式动作，
 * 点一下才调用 viewer.addMaskPolygon，不会因为检出差异就自动写掩码。
 *
 * 点行 = 定位并选中该区域；选中后才展开「交替对比 / 加入掩码 / 精算」等动作，
 * 清单保持紧凑。显示方式与羽化在顶栏「差异定位」面板里改，不改这里的统计。
 */
import { computed, ref, watch } from 'vue';
import { useDiffStore } from '../stores/diff';
import type { DiffRegion } from '../lib/diffLocate';
import { differenceSourceLabel, sceneEvidenceLabel, frequencyEvidenceLabel, confidenceEvidenceLabel } from '../lib/smartCompareLabels';

const LIST_MAX = 80;
const diff = useDiffStore();
const expanded = ref(false);
watch(() => diff.result, () => { expanded.value = false; });

const limit = computed(() => diff.result?.method === 'smart' && !expanded.value ? 5 : LIST_MAX);
const regions = computed(() => {
  const top = diff.orderedRegions.slice(0, limit.value);
  const selected = diff.selectedRegion;
  return selected && !top.some(r=>r.id===selected.id) ? [...top,selected] : top;
});
const hiddenCount = computed(() => Math.max(0, diff.orderedRegions.length - regions.value.length));
const summary = computed(() => {
  const r = diff.result;
  if (!r) return '';
  const method = r.method === 'smart' ? '智能对比' : r.method === 'ssim' ? '结构差异 SSIM' : '像素差分';
  const src = differenceSourceLabel(r.source);
  return `${method} · ${src} · 阈值 ${r.parameters.threshold} · ${r.elapsedMs} ms`;
});

function pad(rank: number): string {
  return String(rank).padStart(2, '0');
}
function areaText(n: number): string {
  return n.toLocaleString('en-US');
}
function pct(v: number): string {
  return Math.round(v * 100) + '%';
}
function deltaText(d: number): string {
  return (d >= 0 ? '+' : '') + Math.round(d);
}
function choose(reg: DiffRegion) {
  diff.selectRegion(reg.id);
  diff.locateRegion(reg.id);
}
function toggle(reg: DiffRegion) {
  diff.selectRegion(diff.selectedId === reg.id ? null : reg.id);
}
</script>

<template>
  <section class="rt-sec diff-cand" aria-label="差异候选">
    <h4 class="rt-h hrow">
      <span>差异候选<template v-if="diff.result">（{{ diff.regions.length }}）</template></span>
      <button
        v-if="diff.result"
        type="button"
        class="ob"
        :disabled="diff.busy"
        title="清除差异分析结果（不影响已加入的掩码）"
        @click="diff.clearResults()"
      >清除</button>
    </h4>

    <p v-if="!diff.result" class="rt-empty">
      <template v-if="diff.busy">正在分析…</template>
      <template v-else>顶栏「差异定位」选两张影像并开始分析后，成片变化会列在这里。</template>
    </p>

    <template v-else>
      <p class="dc-summary">{{ summary }}</p>
      <div v-if="diff.result.method === 'smart'" class="dc-actions">
        <button class="dc-act" @click="diff.nextRegion(-1)">上一异常</button><button class="dc-act" @click="diff.nextRegion(1)">下一异常</button><button class="dc-act" @click="diff.restoreView()">恢复视野</button>
        <button class="dc-act" :disabled="diff.busy || diff.stale || !!diff.nativeRefineReason" :title="diff.nativeRefineReason" @click="diff.refineVisible()">视野 ROI 原图精检</button>
        <button class="dc-act" :disabled="diff.busy || diff.stale || !!diff.nativeRefineReason" :title="diff.nativeRefineReason" @click="diff.refineDrawnRoi()">框选 ROI 精检</button>
      </div>
      <button v-if="diff.result.method === 'smart' && diff.selectedRegionIds.length" class="dc-act" :disabled="diff.busy || !!diff.autoDrawReason" :title="diff.autoDrawReason" @click="diff.autoDraw()">将勾选候选加入修复掩码</button>
      <p v-if="diff.toneWeight" class="dc-note">
        已按亮度归因权重排序，热力图同步逐像素加权；灰度取显示层，不是原始 DN。
      </p>
      <p v-if="diff.refined || diff.result.source === 'source-tiled'" class="dc-refined">
        {{ diff.refined ? '当前仅显示原始 DN ROI 精检范围。' : '补扫保留原始尺度评分，概览轮廓仍需精检。' }}
        <button v-if="diff.canRestore" type="button" :disabled="diff.busy" @click="diff.restorePreview()">返回上次网格结果</button>
      </p>

      <ul v-if="regions.length" class="dc-list">
        <li
          v-for="reg in regions"
          :key="reg.id"
          class="dc-row"
          :class="{ on: diff.selectedId === reg.id, hot: diff.hoveredId === reg.id }"
        >
          <label><input type="checkbox" :checked="diff.selectedRegionIds.includes(reg.id)" :aria-label="'选择差异区域 '+reg.rank" @change="diff.toggleRegionSelection(reg.id)">选入讨论 / 草案</label>
          <button
            type="button"
            class="dc-main"
            :aria-pressed="diff.selectedId === reg.id"
            @pointerenter="diff.hoverRegion(reg.id)"
            @pointerleave="diff.hoverRegion(null)"
            @focus="diff.hoverRegion(reg.id)"
            @blur="diff.hoverRegion(null)"
            @click="choose(reg)"
          >
            <span class="dc-no">#{{ pad(reg.rank) }}</span>
            <span class="dc-text">
              <b>{{ reg.diagnostics ? (reg.diagnostics.classification === 'difference_only' ? '差异 · 模糊证据不足' : '疑似模糊 · 待复核') : '差异区域' }}</b>
              <small>面积 {{ areaText(reg.areaPx) }} px² · 平均强度 {{ reg.meanDiff.toFixed(3) }}</small>
              <template v-if="diff.toneWeight && reg.tone">
                <span class="dc-tone" :title="'复核权重 ' + reg.tone.weight.toFixed(2) + '×'">
                  <i class="dc-tone-track"><i class="dc-tone-fill" :style="{ width: pct(reg.tone.norm) }"></i></i>
                  <em>{{ reg.tone.weight.toFixed(2) }}×</em>
                </span>
                <small class="dc-tone-note">
                  灰 {{ Math.round(reg.tone.meanA) }} · 中间调 {{ pct(reg.tone.mid) }} · 平坦 {{ pct(reg.tone.flat) }} · 变化 {{ deltaText(reg.tone.delta) }}
                </small>
              </template>
            </span>
            <span class="dc-more" :title="'查看轮廓 #' + pad(reg.rank)" @click.stop="toggle(reg)">⋯</span>
          </button>
          <div v-if="diff.selectedId === reg.id" class="dc-actions">
            <button type="button" class="dc-act" @click="diff.openFlicker()" title="用这两张影像进入交替对比">交替对比</button>
            <button
              type="button"
              class="dc-act"
              :disabled="diff.stale || (diff.result.method === 'smart' && !reg.maskEligible) || reg.contourKind === 'bbox'
                || (reg.contourKind ?? 'exact') !== 'exact' || (reg.holes ?? 0) > 0"
              :title="diff.stale ? '结果已对应旧参数，请重新分析'
                : reg.contourKind === 'bbox' ? '只有外接矩形轮廓，不能直接作为精确区域'
                : (reg.holes ?? 0) > 0 ? '区域含孔洞，单多边形掩码会填掉孔洞'
                : (reg.contourKind ?? 'exact') !== 'exact' ? '轮廓简化后与真实掩码偏差较大，请手工绘制'
                : '把该区域轮廓加入结果对应影像的人工掩码'"
              @click="diff.addRegionToMask(reg.id)"
            >加入修复掩码</button>
            <button
              type="button"
              class="dc-act"
              :disabled="diff.busy || diff.stale || (diff.result.method === 'smart' && !!diff.nativeRefineReason)"
              :title="diff.result.method === 'smart' && diff.nativeRefineReason ? diff.nativeRefineReason : '按原始 TIFF 分辨率重算该区域（仅盘阵场景）'"
              @click="diff.refineRegion(reg.id)"
            >精算</button>
          </div>
          <div v-if="reg.diagnostics && diff.selectedId === reg.id" class="dc-evidence" aria-label="区域诊断证据">
            <p>方差比 {{ reg.diagnostics.varianceRatioMean.toFixed(2) }} · 纹理损失 {{ reg.diagnostics.textureLossMean.toFixed(2) }}</p>
            <p>强度均值 {{ reg.diagnostics.scoreMean.toFixed(3) }} · P95 {{ reg.diagnostics.scoreP95.toFixed(3) }} · 最大 {{ reg.diagnostics.scoreMax.toFixed(3) }}</p>
            <p>{{ confidenceEvidenceLabel(reg.diagnostics.confidence) }}；频域{{ frequencyEvidenceLabel(reg.diagnostics.frequency.status) }}。</p>
            <p v-for="(label,key) in {cloud:'云',water:'水',shadow:'阴影'}" :key="key">{{ label }}：{{ sceneEvidenceLabel(diff.result.maskAvailability?.[key] ?? 'unverified') }}<template v-if="reg.diagnostics.sceneOverlap[key] != null"> · 重叠 {{ pct(reg.diagnostics.sceneOverlap[key]) }}</template></p>
            <p v-if="diff.result.source === 'source-tiled'">统计来自概览像素对应的原始最高分样本，面积属于概览网格；候选仍需 ROI 精检，不能据此生成精确 Mask。</p>
            <p>{{ reg.maskEligible ? '轮廓具备加入草案资格，仍需人工复核。' : '尚不具备精确 Mask 资格，请精检并核对轮廓。' }}</p>
          </div>
        </li>
      </ul>
      <p v-else class="rt-empty">
        未发现超过阈值的连续区域。可提高差异灵敏度、降低最小区域面积，或改用「叠加」确认弱差异。
      </p>
      <button v-if="diff.result.method === 'smart' && !expanded && hiddenCount" type="button" class="dc-act" @click="expanded=true">展开更多候选（{{ hiddenCount }}）</button>
      <p v-else-if="hiddenCount" class="dc-note">另有 {{ hiddenCount }} 个候选未列出（最多显示 {{ LIST_MAX }} 个）。</p>

      <details v-if="diff.lastWarnings.length" class="dc-warn">
        <summary>分析口径（{{ diff.lastWarnings.length }}）</summary>
        <p v-for="(w, i) in diff.lastWarnings" :key="i">{{ w }}</p>
      </details>
      <p v-if="diff.stale" class="dc-stale">参数已修改，当前仍是上一次结果；重新分析后更新。此时不能精算或加入掩码。</p>
    </template>
    <p v-if="diff.notice" class="dc-notice">{{ diff.notice }}</p>
    <p v-if="diff.error" class="dc-err">{{ diff.error }}</p>
  </section>
</template>

<style scoped>
/* 子组件里的 .rt-h / .rt-empty 拿不到父组件 scoped 样式，这里补一份同值定义。 */
.diff-cand .rt-h{margin:0 0 6px;font-size:12px;font-weight:600;color:var(--ink)}
.diff-cand .rt-empty{margin:0;font-size:10.5px;line-height:16px;color:var(--ink-sub)}
.diff-cand .hrow{display:flex;align-items:center;justify-content:space-between;gap:8px}
.diff-cand .ob{height:22px;padding:0 8px;border:1px solid var(--line);border-radius:5px;background:var(--surface);color:var(--ink-sub);font:inherit;font-size:10px;cursor:pointer}
.diff-cand .ob:hover:enabled{background:var(--surface-2);color:var(--ink)}
.dc-summary{margin:0 0 6px;font-size:10px;line-height:15px;color:var(--ink-sub);overflow-wrap:anywhere}
.dc-refined{margin:0 0 6px;padding:5px 6px;border-radius:5px;background:var(--surface-2);font-size:10px;line-height:15px;color:var(--ink-sub)}
.dc-refined button{margin-left:4px;border:0;background:none;color:var(--accent-2,#2f8f89);font:inherit;font-size:10px;cursor:pointer;text-decoration:underline}
.dc-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:4px}
.dc-row{border:1px solid var(--line);border-radius:6px;background:var(--surface);overflow:hidden}
.dc-row.hot{border-color:#e0a294}
.dc-row.on{border-color:var(--tb-primary);background:var(--tb-active-bg)}
.dc-main{display:flex;align-items:center;gap:7px;width:100%;border:0;background:transparent;padding:6px 7px;text-align:left;font:inherit;cursor:pointer;color:var(--ink)}
.dc-main:hover{background:var(--surface-2)}
.dc-no{flex:none;font:600 10px/14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#b34a2a}
.dc-text{display:flex;flex-direction:column;gap:1px;min-width:0;flex:1}
.dc-text b{font-size:11px;font-weight:600}
.dc-text small{font-size:9.5px;line-height:13px;color:var(--ink-sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dc-tone{display:flex;align-items:center;gap:5px;margin-top:2px}
.dc-tone-track{flex:1;height:3px;border-radius:2px;background:var(--surface-2,#eef1ef);overflow:hidden}
.dc-tone-fill{display:block;height:100%;border-radius:2px;background:linear-gradient(90deg,#4e7ca8,#e05632)}
.dc-tone em{flex:none;font-style:normal;font-size:9px;line-height:11px;color:var(--ink-sub)}
.dc-tone-note{font-size:9px;line-height:12px;color:var(--ink-sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dc-more{flex:none;width:20px;height:20px;display:grid;place-items:center;border-radius:4px;color:var(--ink-sub);font-size:13px;line-height:1}
.dc-more:hover{background:var(--surface-2);color:var(--ink)}
.dc-actions{display:flex;flex-wrap:wrap;gap:4px;padding:0 7px 6px}
.dc-evidence{padding:0 7px 7px;font-size:10px;line-height:15px;color:var(--ink-sub);overflow-wrap:anywhere}
.dc-evidence p{margin:3px 0}
.dc-act{flex:1;height:23px;border:1px solid var(--line);border-radius:5px;background:var(--surface);color:var(--ink-sub);font:inherit;font-size:10px;cursor:pointer;padding:0 4px}
.dc-act:hover:enabled{background:var(--surface-2);color:var(--ink)}
.dc-note{margin:6px 0 0;font-size:9.5px;line-height:14px;color:var(--ink-sub)}
.dc-warn{margin-top:6px;font-size:10px;line-height:15px;color:var(--ink-sub)}
.dc-warn summary{cursor:pointer}
.dc-warn p{margin:4px 0 0}
.dc-stale{margin:6px 0 0;padding:5px 6px;border-radius:5px;background:#fdf3e3;color:#8a5a1f;font-size:10px;line-height:15px}
.dc-notice{margin:6px 0 0;padding:5px 6px;border-radius:5px;background:var(--surface-2);color:var(--ink-sub);font-size:10px;line-height:15px}
.dc-err{margin:6px 0 0;padding:5px 6px;border-radius:5px;background:#fbe9e6;color:#9d3b2f;font-size:10px;line-height:15px;overflow-wrap:anywhere}
button:disabled{opacity:.45;cursor:default}
</style>
