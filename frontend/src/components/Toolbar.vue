<script setup lang="ts">
/**
 * Toolbar.vue — 顶部工具栏（tif-viewer.html #toolbar 直译）
 * ------------------------------------------------------------------
 * 选影像（multiple，tif/tiff/jpg/jpeg）/ 拉伸下拉 / 像素定位 X/Y + 按钮 /
 * 预览下采样档位拖动条 / 绘制掩码 toggle / 生成掩码 / 提交 SR。
 * 最小原型删去了 HTML 的「输出目录」三态按钮与「自动JPG」勾选（前端不再导出 JPG）。
 */
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { parseLocPair } from '../lib/viewMath';
import { SCENE_PREVIEW_DIVS, previewDivLabel } from '../lib/scene';
import type { StretchMode } from '../lib/tifDecode';

const store = useViewerStore();
const fileInput = ref<HTMLInputElement | null>(null);
/** 定位框：一个框装「X,Y」两个数（原先是 X、Y 两个框，拆开填反而要用户自己数着填）。 */
const locText = ref('');

/** 盘阵场景激活：只用来决定 title 文案（服务器烤的直方图均衡是二次拉伸的底图，
    均衡不可逆）。**不再**禁用下拉 —— 场景图照样可在显示层换模式，
    默认起手值也同样是直方图均衡（见 lib/scene.startStretch）。
    注意判据是 route==='jpg'：反推关联**命中**的本地 TIF 也会被就地升级成
    route='jpg'（拿的是服务端烘焙 JPG，见 stores/viewer.tryLinkScenes），
    没命中的才保持本地 route。 */
const sceneActive = computed(() => store.activeRec?.route === 'jpg');
/** 「提交 SR」可用：这张图有盘阵目录（= 提交时 lq_path 的语义）。
    盘阵场景打开的有，反推关联上的本地 TIF 也**有** —— 判据从「是不是盘阵场景」
    放宽成「有没有盘阵目录」，否则本地关联那条路走通了按钮还是灰的。 */
const srReady = computed(() => !!store.activeRec?.lqPath);
/** 下拉显示**当前这张图实际**的拉伸模式（store.activeStretch），不是全局那份：
    全局只决定新打开的本地图用什么起手，场景图另有起手值且各记各的。 */
const stretchValue = computed(() => store.activeStretch);
const stretchTitle = computed(() =>
  sceneActive.value
    ? '这张盘阵 JPG 在服务器端已按直方图均衡烘焙，这里改的是显示层的二次拉伸'
      + '（均衡不可逆，拉不回来）'
    : '',
);

/** 预览烘焙档位（各边 ÷N）：**平台级**设置，拖入 / 场景库 / 粘盘阵路径三条入口
    都按它烤。拖动条按**下标**走（`SCENE_PREVIEW_DIVS` 的次序），不是按档位值本身 ——
    ÷2…÷32 是等比数列，用下标每格等宽，用户按起来才是均匀的。 */
const DIVS = SCENE_PREVIEW_DIVS;
const divIndex = computed(() => {
  const i = (DIVS as readonly number[]).indexOf(store.previewDiv);
  return i < 0 ? (DIVS as readonly number[]).indexOf(4) : i;
});
const divLabel = computed(() => previewDivLabel(store.previewDiv));
function onDivInput(e: Event) {
  store.setPreviewDiv(DIVS[Number((e.target as HTMLInputElement).value)] ?? 4);
}

/** 对比模式的中文短名（按钮上那个后缀，与 CompareBar 的三选一同一套叫法）。 */
const cmpModeLabel = computed(() => (store.compareMode === 'split' ? '分屏' : '点选'));

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

/** 「定位」：框里要的是「X,Y」两个数（1000.23,3000.27 这种形态，认法见 lib/viewMath.parseLocPair，
    半角/全角逗号、空格、制表符分隔都收）。

    **只在点按钮 / 回车时校验，不边敲边管**：单框之后敲字的过程必然经过「只有一个数」
    「尾随一个逗号」这些中间态，input 事件上校验等于每输一个逗号就骂一次。
    认不出来（只给了一个数、或连编号一起拷了整行的三个数）一律出声不跳 ——
    把「1,30766.11,21862.51」截前两个去定位，比不跳更糟。

    认出来就把框统一回写成规范化的「X,Y」：全角逗号、空格、尾随分隔符都收掉了，
    用户能对照着看解析结果 —— 越界报错时也看得到底拿的是哪个点。 */
function doLocate() {
  const pair = parseLocPair(locText.value);
  if (!pair) {
    store.showErr('坐标对认不出来（要的是「X,Y」两个数，例如 1000.23,3000.27）');
    return;
  }
  locText.value = pair[0] + ',' + pair[1];
  store.locatePixel(pair[0], pair[1]);
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

    <!-- 单框 text 而不是 number：number 框会把「1000.23,3000.27」这种整串判为非法、
         值直接变空，粘进来等于什么都没发生（拆法见 doLocate / parseLocPair）。
         不再另挂「坐标」标签：占位文本自己就以「输入坐标」开头，两句话说的是同一件事，
         框一填上字标签又成了唯一线索 —— 不如把这份宽度给占位文本。 -->
    <span class="loc" title="填「X,Y」两个数，例如 1000.23,3000.27（逗号、空格分隔都收）">
      <input
        v-model="locText"
        type="text"
        inputmode="decimal"
        placeholder="输入坐标（X,Y）:123.456,456.123"
        @keydown.enter="doLocate"
      />
      <button type="button" class="loc-btn" @click="doLocate">定位</button>
    </span>

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
        : '图像对比：点选对比（拖入即覆盖当前这张）或分屏对比（左右各一张、缩放平移同步）'"
      @click="store.setCmpStripOpen(!store.cmpStripOpen)"
    >
      图像对比{{ store.compareOn ? ' · ' + cmpModeLabel : '' }}
    </button>

    <!-- 预览下采样档位：紧邻定位组件，因为两者是同一类「看图前先定参数」的控件。
         文案「预览 1/N」而不是「缩放」：它改的是**服务端烤出来的那张 JPG**的分辨率，
         不是画布的显示缩放（那个在右下角，别混）。 -->
    <span
      class="divsel"
      data-e2e="preview-div"
      :title="'服务端预览档位：长宽各为源图的 ' + divLabel + '。'
        + '档位越小越清晰、首次打开越慢（要读一遍大图）；'
        + '改档后已打开过的场景会在下次打开时按新档重新烘焙（原地覆盖，不堆积）。'"
    >
      预览 {{ divLabel }}
      <input
        type="range"
        :min="0"
        :max="DIVS.length - 1"
        step="1"
        :value="divIndex"
        aria-label="预览下采样档位"
        @input="onDivInput"
      />
    </span>

    <span class="spacer"></span>

    <!-- 对比模式只读（store.enterDraw 也有一道守卫）：分屏里画掩码会画到哪一格、
         写进哪一张 rec 都不明确，与其给出一个含糊的结果，不如把按钮明确置灰。 -->
    <button
      type="button"
      class="outbtn"
      :class="{ on: store.drawMode }"
      :disabled="store.compareOn"
      :title="store.compareOn ? '图像对比模式下不绘制掩码，请先切回「关闭」' : ''"
      @click="store.drawMode ? store.exitDraw() : store.enterDraw()"
    >
      绘制掩码{{ store.drawMode ? ' ✓' : '' }}
    </button>
    <button type="button" class="btn" :disabled="store.busy" @click="store.genMask()">生成掩码</button>
    <button
      type="button"
      class="outbtn"
      :disabled="store.srBusy || !store.activeRec?.lqPath"
      :title="store.activeRec?.lqPath
        ? '把当前掩码写到盘阵场景目录（<输入名>_mask.tif，与提交时去找的那份同源）'
        : '这张图没有盘阵目录，掩码无处可写'"
      @click="store.bakeMaskToServer()"
    >
      {{ store.srBusy ? '写入中…' : '保存掩码到盘阵' }}
    </button>
    <button
      type="button"
      class="outbtn grad"
      :class="{ on: srReady }"
      :disabled="store.srBusy || !srReady"
      :title="srReady
        ? '带出该场景的目录，跳转队列页确认后提交 SR'
        : '提交 SR 需要盘阵目录：用上面的「盘阵场景」栏打开，或让本地文件按文件名关联'"
      @click="store.submitSr()"
    >
      提交 SR
    </button>

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
      设置
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

.loc {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  color: rgba(255, 255, 255, 0.85);
  font-size: 12px;
}
.loc input {
  /* 一个框装两串数，宽度按**占位文本**定：「输入坐标（X,Y）:123.456,456.123」
     在 13px 下实测 200px，加左右 padding 16px 是 216px —— 取 240px 而不是贴着放，
     换个字体/回退字体就可能多吃几个像素，提示截成半句话比多占 24px 难看得多。
     1366 视口下工具栏要到输入框 ~470px 才横向溢出（实测），这点加宽不挤右端的按钮。 */
  width: 240px;
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

/* 预览档位拖动条：与 .loc 同一族的浅底 pill（白面浮深带）。整块给一个 pill 底，
   滑轨只占其中一段 —— 深带上一条裸滑轨的对比度不够，也跟右侧按钮不成系列。 */
.divsel {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
  height: 28px;
  padding: 0 10px;
  background: rgba(255, 255, 255, 0.95);
  color: var(--band-2);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 8px;
  white-space: nowrap;
  user-select: none;
  transition: border-color 0.15s ease;
}
.divsel:hover { border-color: #fff; }
.divsel input[type='range'] {
  width: 84px;
  height: 16px;
  margin: 0;
  background: transparent;
  accent-color: var(--band-2);
  cursor: pointer;
}
.divsel input[type='range']:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.18);
  border-radius: 8px;
}

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
/* 激活态（绘制掩码 on / 提交SR grad.on）：白底深绿字 + 白环 = 高亮选中 */
.outbtn.on, .outbtn.grad.on {
  color: var(--band-2);
  background: #fff;
  border-color: #fff;
  font-weight: 600;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25);
}

.spacer { flex: 1; }

/* 图像对比：与 .outbtn 同一套形态（深色工具栏上的描边按钮），单独一个类名是为了
   在「已开启」时换一套更醒目的白底 —— 对比模式是个**模式**，得一眼看出还开着。
   宽度预算：1366 下工具栏还有约 470px 余量（见下方注释），这颗约 92px / 带后缀约 140px。 */
.cmp-btn {
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
.cmp-btn:hover { background: rgba(255, 255, 255, 0.2); border-color: rgba(255, 255, 255, 0.6); }
.cmp-btn.on {
  color: var(--band-2);
  background: #fff;
  border-color: #fff;
  font-weight: 600;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25);
}

/* 设置：与 .outbtn 同一形态（深带上描边按钮），最右端一枚。
   宽度预算：1366 下右端这串还有百余像素余量，这颗「设置」约 56px。 */
.set-btn {
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
.set-btn:hover { background: rgba(255, 255, 255, 0.2); border-color: rgba(255, 255, 255, 0.6); }
.set-btn.on {
  color: var(--band-2);
  background: #fff;
  border-color: #fff;
  font-weight: 600;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25);
}
</style>
