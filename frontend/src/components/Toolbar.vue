<script setup lang="ts">
/**
 * Toolbar.vue — 顶部工具栏（tif-viewer.html #toolbar 直译）
 * ------------------------------------------------------------------
 * 选影像（multiple，tif/tiff/jpg/jpeg）/ 拉伸下拉 / 像素定位 X/Y + 按钮 /
 * 绘制掩码 toggle / 生成掩码 / 提交 SR。
 * 最小原型删去了 HTML 的「输出目录」三态按钮与「自动JPG」勾选（前端不再导出 JPG）。
 */
import { computed, ref } from 'vue';
import { useViewerStore } from '../stores/viewer';
import { parseLocPair } from '../lib/viewMath';
import type { StretchMode } from '../lib/tifDecode';

const store = useViewerStore();
const fileInput = ref<HTMLInputElement | null>(null);
const locX = ref('');
const locY = ref('');

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

/** 粘进来的「30766.11,21862.51」拆到两个框里（认法见 lib/viewMath.parseLocPair）。
    只在**带分隔符**时才当坐标对：单个数是正在手输，管它就把输入打断了；带分隔符却
    认不出来的（多数是连编号一起拷的整行）必须出声，不能默默截一半去定位。
    两个框都挂这个处理 —— 粘到 Y 框里，文本仍然是「X,Y」的顺序。
    拆完不自动跳：手输了 X 再输 Y 的人也是按「定位」，两处行为保持一致。 */
function onLocInput() {
  for (const raw of [locX.value, locY.value]) {
    if (!/[,，\s]/.test(raw)) continue;          // 单个数 = 正在手输，放行
    const pair = parseLocPair(raw);
    if (!pair) {
      store.showErr('坐标对认不出来（要的是「X,Y」两个数，例如 30766.11,21862.51）');
      return;
    }
    locX.value = pair[0];
    locY.value = pair[1];
    return;
  }
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

    <!-- 两个框都是 text 而不是 number：number 框会把「30766.11,21862.51」这种整串
         判为非法、值直接变空，粘进来等于什么都没发生（见 onLocInput）。 -->
    <span class="loc" title="X、Y 可分开填；也可以把「X,Y」两个数粘进任意一个框，例如 30766.11,21862.51">
      X
      <input
        v-model="locX"
        type="text"
        inputmode="decimal"
        @input="onLocInput"
        @keydown.enter="doLocate"
      />
      Y
      <input
        v-model="locY"
        type="text"
        inputmode="decimal"
        @input="onLocInput"
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
/* 激活态（绘制掩码 on / 提交SR grad.on）：白底深绿字 + 白环 = 高亮选中 */
.outbtn.on, .outbtn.grad.on {
  color: var(--band-2);
  background: #fff;
  border-color: #fff;
  font-weight: 600;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.25);
}

.spacer { flex: 1; }
</style>
