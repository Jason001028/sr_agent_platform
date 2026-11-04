<script setup lang="ts">
/**
 * Toolbar.vue — 顶部工具栏（tif-viewer.html #toolbar 直译）
 * ------------------------------------------------------------------
 * 选影像（multiple，tif/tiff/jpg/jpeg）/ 拉伸下拉 / 像素定位 X/Y + 按钮 /
 * 预览下采样档位拖动条 / 绘制掩码 toggle / 生成掩码 / 提交 SR。
 * 最小原型删去了 HTML 的「输出目录」三态按钮与「自动JPG」勾选（前端不再导出 JPG）。
 */
import { computed, ref, watch, onBeforeUnmount } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { useQcListStore } from '../stores/qclist';
import CoordinateInput from './CoordinateInput.vue';
import PreviewScaleControl from './PreviewScaleControl.vue';
import AutoTourButton from './AutoTourButton.vue';
import { isIntermediateStage } from '../lib/stage';
import type { StretchMode } from '../lib/tifDecode';
import AppIcon from './AppIcon.vue';
import SRSubmitFeedback from './SRSubmitFeedback.vue';
import { useQueueStore } from '../stores/queue';
import { useRouter } from 'vue-router';

const router = useRouter();
const queue = useQueueStore();
let queuePulse: Animation | undefined;
onBeforeUnmount(() => queuePulse?.cancel());
function submissionAccepted(_id: number, fresh: boolean) {
  if (!fresh || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const entry = document.querySelector<HTMLAnchorElement>('.nav-links a[href="/queue"]');
  if (!entry) return;
  const rect = entry.getBoundingClientRect();
  if (!rect.width || !rect.height || rect.bottom < 0 || rect.top >= innerHeight) return;
  queuePulse?.cancel();
  queuePulse = entry.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.045)' }, { transform: 'scale(1)' }], { duration: 240, delay: 180 });
}

const store = useViewerStore();
const qc = useQcListStore();
const fileInput = ref<HTMLInputElement | null>(null);
/** 定位框：一个框装「X,Y」两个数（原先是 X、Y 两个框，拆开填反而要用户自己数着填）。 */
const coordinateIssue = computed(() => qc.rowForScene(store.activeRec?.sceneDir ?? store.activeRec?.lqPath));
const importedCoordinates = computed<readonly [number, number] | null>(() => {
  const it = coordinateIssue.value;
  return it && it.row !== null && it.col !== null && Number.isFinite(it.row) && Number.isFinite(it.col)
    ? [it.row, it.col] : null;
});

/** 盘阵场景激活：只用来决定 title 文案（服务端生成的直方图均衡是二次拉伸的底图，
    均衡不可逆）。**不再**禁用下拉 —— 场景图照样可在显示层换模式，
    默认起手值也同样是直方图均衡（见 lib/scene.startStretch）。
    注意判据是 route==='jpg'：反推关联**命中**的本地 TIF 也会被就地升级成
    route='jpg'（拿的是服务端生成的预览 JPG，见 stores/viewer.tryLinkScenes），
    没命中的才保持本地 route。 */
const sceneActive = computed(() => store.activeRec?.route === 'jpg');
/** 「提交 SR」可用：这张图有盘阵目录（= 提交时 lq_path 的语义）。
    盘阵场景打开的有，反推关联上的本地 TIF 也**有** —— 判据从「是不是盘阵场景」
    放宽成「有没有盘阵目录」，否则本地关联那条路走通了按钮还是灰的。 */
const srReady = computed(() => !!store.activeRec?.lqPath && !intermediate.value);
/** 当前这张是不是场景里的中间产物（SR / NOSR）。中间产物也有盘阵目录，所以
    「有没有 lqPath」这一条判不出它 —— 得看环节。修复（画掩码 / 写掩码 / 提交 SR）
    一律只在**本体**上做：掩码与 SR 都建在本体影像的网格上，产物的尺寸是它的倍数，
    拿产物那张去写就会把一张产物尺寸的掩码盖到本体的掩码文件上（同样的道理见
    stores/viewer 里那三道门，这里置灰只是别让用户点了才知道）。 */
const intermediate = computed(() => isIntermediateStage(store.activeRec?.stageKind));
const roTitle = computed(() =>
  '本图像是中间产物（' + (store.activeRec?.stageLabel ?? '产物')
  + '），仅用于对比，不作修复 —— 请打开本体再修复与提交');
/** 下拉显示**当前这张图实际**的拉伸模式（store.activeStretch），不是全局那份：
    全局只决定新打开的本地图用什么起手，场景图另有起手值且各记各的。 */
const stretchValue = computed(() => store.activeStretch);
const stretchTitle = computed(() =>
  sceneActive.value
    ? '这张盘阵 JPG 已由服务端按直方图均衡预览生成，这里改的是显示层的二次拉伸'
      + '（均衡不可逆，拉不回来）'
    : '',
);

/** 对比模式的中文短名（按钮上那个后缀，与 CompareBar 的三选一同一套叫法）。 */
const cmpModeLabel = computed(() => {
  if (store.compareMode === 'split') return '分屏';
  if (store.compareMode === 'flicker') return '交替';
  return '点选';
});

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

</script>

<template>
  <div class="toolbar">
    <button
      type="button"
      class="btn"
      :disabled="store.busy"
      @click="fileInput?.click()"
    >
      <AppIcon name="image" :size="15" tint />
      选择影像…
    </button>
    <input
      ref="fileInput"
      type="file"
      accept=".tif,.tiff,.jpg,.jpeg,.TIF,.TIFF,.JPG,.JPEG"
      multiple
      style="display: none"
      @change="onPick"
    />

    <select
      class="stretch-sel"
      :value="stretchValue"
      :title="stretchTitle"
      @change="store.setStretch(($event.target as HTMLSelectElement).value as StretchMode)"
    >
      <option v-for="o in STRETCH_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>

    <CoordinateInput :scene-key="store.activeRec?.id ?? null" :coordinates="importedCoordinates"
      :scene-group="store.activeRec?.sceneDir ? store.sceneOrdinalOf(store.activeRec) : null"
      :import-revision="qc.coordinateImportRevision"
      :source-name="qc.sourceName" :batch-state="qc.bakeState"
      @locate="(x, y) => store.locatePixel(x, y)" @error="store.showErr" />

    <!-- 组间分隔：左段「打开 / 拉伸 / 定位」与中段「对比 / 预览档位」都是看图前先定的参数，
         但性质不同（对一张图动、对两张图动），用一条 1px 细线分开，不另加大面积容器。 -->
    <span class="tb-sep" aria-hidden="true"></span>

    <!-- 图像对比：开启位置在定位与预览档位之间（都是「看图方式」的控件）。
         点它展开/收起工具栏下方那条 CompareBar（三选一 + 场景芯片）。
         注意本条只在**关闭态**才收起，正对比时再点它只是折叠那条区域、不改模式。 -->
    <button
      type="button"
      class="cmp-btn"
      :class="{ on: store.compareOn }"
      data-e2e="cmp-open"
      :title="store.compareOn
        ? '图像对比已开启（' + cmpModeLabel + '）；点这里收起或展开对比条'
        : '图像对比：点选对比（拖入即覆盖当前这张）、分屏对比（左右各一张、缩放平移同步）或交替对比（同视口按节拍切换）'"
      @click="store.setCmpStripOpen(!store.cmpStripOpen)"
    >
      <AppIcon name="layers" :size="15" tint />
      图像对比{{ store.compareOn ? ' · ' + cmpModeLabel : '' }}
    </button>

    <!-- 预览下采样档位：紧邻定位组件，因为两者是同一类「看图前先定参数」的控件。
         文案「预览 1/N」而不是「缩放」：它改的是**服务端生成的那张 JPG**的分辨率，
         不是画布的显示缩放（那个在右下角，别混）。 -->
    <PreviewScaleControl :model-value="store.previewDiv" @update:model-value="store.setPreviewDiv" />
    <AutoTourButton />

    <span class="spacer"></span>

    <!-- 对比模式下**也能画**（2026-09-22 改，原来置灰）：掩码只画到活动侧那张 rec 上
         （分屏点哪半哪半就是活动侧，与掩码/云量/统计跟随活动侧同一条规则），所以不再是
         「画到哪一格不明确」。仍置灰的只有一件事：活动侧是本轮超分产物 / NOSR
         （掩码建在本体影像网格上，在产物上画，坐标写进掩码文件整片都是错的）。 -->
    <button
      type="button"
      class="outbtn"
      :class="{ on: store.drawMode }"
      :disabled="intermediate"
      :title="intermediate ? roTitle : ''"
      @click="store.drawMode ? store.exitDraw() : store.enterDraw()"
    >
      绘制掩码{{ store.drawMode ? ' ✓' : '' }}
    </button>
    <button type="button" class="btn" :disabled="store.busy" @click="store.genMask()">
      生成掩码
    </button>
    <button
      type="button"
      class="outbtn"
      :disabled="store.srBusy || !store.activeRec?.lqPath || intermediate"
      :title="intermediate ? roTitle
        : store.activeRec?.lqPath
          ? '把当前掩码写到盘阵场景目录（<输入名>_mask.tif，与提交时去找的那份同源）'
          : '这张图没有盘阵目录，掩码无处可写'"
      @click="store.bakeMaskToServer()"
    >
      <AppIcon name="upload" :size="15" tint />
      {{ store.srBusy ? '写入中…' : '保存掩码到盘阵' }}
    </button>
    <span class="tb-sep" aria-hidden="true"></span>
    <!-- 提交 SR：工具栏里**唯一**的实心主按钮，且只在真正可提交时才呈现这个权重
         （`srReady` 是业务判据，不是 class 判出来的）；不可提交时落到禁用样式。 -->
    <SRSubmitFeedback compact :submit="store.submitSr" :navigate-only="!store.autoSubmission"
      :disabled="store.srBusy || !srReady" :title="intermediate ? roTitle : store.autoSubmission ? '使用默认参数提交 SR，任务回执将在此显示' : '带出该场景的目录，跳转队列页确认后提交 SR'"
      @accepted="submissionAccepted" @query="queue.list()"
      @view="id => router.push({ path: '/queue', hash: `#queue-task-${id}` })" />

    <!-- 设置（右上角浮层）：对比模式后台预取开关 + 本地预览缓存的行与清空。
         摆在最右端而不是挤进中间那串看图控件里 —— 它是**偏好**，不是一次性动作。
         类名不用 `.btn`：qa-theme.js 用 `document.querySelector('.toolbar .btn')`
         取第一颗主按钮，别去动它。 -->
    <button
      type="button"
      class="set-btn"
      :class="{ on: store.settingsOpen }"
      data-e2e="set-open"
      title="设置：对比模式的后台预取、本地预览缓存"
      @click="store.setSettingsOpen(!store.settingsOpen)"
    >
      <AppIcon name="settings" :size="15" tint />
      设置
    </button>
  </div>
</template>

<style scoped>
/* ==========================================================================
   影像操作工具栏：雾白浅底 + 白底细描边控件（2026-10-23，Deep Forest × Mist White）
   --------------------------------------------------------------------------
   层级：品牌导航（--nav-bg 深松绿纯色）→ 本条（--tb-bg 雾白）→ 影像工作区。
   四种按钮视觉（token 见 style.css 的 --tb-*）：
   - Default      .btn / .outbtn / .cmp-btn / .loc-btn      白底、1px 描边、无投影；
   - Mode Active  .outbtn.on / .cmp-btn.on                  浅青绿底，持续可见（不靠 hover）；
   - Primary      .outbtn.primary:not(:disabled)            深青绿实心，只有「提交 SR」；
   - Disabled     :disabled                                 浅灰底灰绿字，不可点。
   `.btn` 仍在（首颗「选择影像」按钮 = e2e 的 `.toolbar .btn` 锚点），只是不再有独立权重。
   图标一律 `tint`（AppIcon 用遮罩按 currentColor 着色），颜色随上面四种状态走。
   ========================================================================== */
.toolbar {
  position: relative;
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  height: 48px;
  color: var(--tb-fg);
  background: var(--tb-bg);
  border-bottom: 1px solid var(--tb-divider);
  z-index: 5;
}

/* ---- 控件共通骨架：高度 / 圆角 / 描边 / 字号 / 过渡只写这一处 ---- */
.btn,
.outbtn,
.cmp-btn,
.set-btn,
.loc-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  gap: 6px;
  height: 32px;
  /* 10px 而不是 12px：1366 下「图像对比 · 分屏」带后缀时仍要放得下右端的设置键 */
  padding: 0 10px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  color: var(--tb-fg);
  background: var(--tb-surface);
  border: 1px solid var(--tb-border);
  border-radius: 8px;
  cursor: pointer;
  user-select: none;
  transition: background var(--motion-hover) var(--ease-standard),
              color var(--motion-hover) var(--ease-standard),
              border-color var(--motion-hover) var(--ease-standard);
}
.btn:hover:not(:disabled),
.outbtn:hover:not(:disabled),
.cmp-btn:hover,
.loc-btn:hover {
  background: var(--tb-hover);
}
.btn:focus-visible,
.outbtn:focus-visible,
.cmp-btn:focus-visible,
.set-btn:focus-visible,
.loc-btn:focus-visible {
  outline: 2px solid var(--tb-focus);
  outline-offset: 1px;
}
.btn:disabled,
.outbtn:disabled {
  background: var(--tb-disabled-bg);
  color: var(--tb-disabled-fg);
  border-color: var(--tb-disabled-border);
  cursor: not-allowed;
}

/* 组间分隔：1px 细线分组，不再靠大面积色块 */
.tb-sep {
  flex: none;
  width: 1px;
  height: 20px;
  margin: 0 2px;
  background: var(--tb-divider);
}

.spacer { flex: 1; }

/* ---- 拉伸下拉 ---- */
.stretch-sel {
  flex: none;
  height: 32px;
  padding: 0 10px;
  background: var(--tb-surface);
  color: var(--tb-fg);
  font-family: inherit;
  font-size: 13px;
  border: 1px solid var(--tb-border);
  border-radius: 8px;
  cursor: pointer;
  transition: background var(--motion-hover) var(--ease-standard),
              border-color var(--motion-hover) var(--ease-standard);
}
.stretch-sel:hover { background: var(--tb-hover); }
.stretch-sel:focus-visible { outline: 2px solid var(--tb-focus); outline-offset: 1px; }



/* ---- 预览档位：不再是一颗白 pill，直接坐在雾白底上（组内已用间距分开） ---- */
.outbtn.on,
.cmp-btn.on {
  background: var(--tb-active-bg);
  border-color: var(--tb-active-border);
  color: var(--tb-active-fg);
  font-weight: 600;
}
.outbtn.on:hover:not(:disabled),
.cmp-btn.on:hover {
  background: var(--tb-active-bg);
}

/* ---- 主要业务按钮：只有「提交 SR」且可提交时才是深青绿实心 ----
   放在共通 hover 规则之后，同特异度下以本段为准。 */
.outbtn.primary:not(:disabled) {
  background: var(--tb-primary);
  border-color: var(--tb-primary);
  color: var(--tb-primary-fg);
  font-weight: 600;
}
.outbtn.primary:not(:disabled):hover {
  background: var(--tb-primary-hover);
  border-color: var(--tb-primary-hover);
}

/* ---- 设置：Ghost 按钮（无底无框），打开浮层时给一层比模式激活更轻的浅青绿 ---- */
.set-btn {
  background: transparent;
  border-color: transparent;
  color: var(--tb-muted);
}
.set-btn:hover {
  background: var(--tb-hover);
  color: var(--tb-fg);
}
.set-btn.on {
  background: var(--tb-active-bg);
  border-color: transparent;
  color: var(--tb-active-fg);
}
.set-btn.on:hover { background: var(--tb-active-bg); }
</style>
