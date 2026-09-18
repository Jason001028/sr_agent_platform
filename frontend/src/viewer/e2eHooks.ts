/**
 * viewer/e2eHooks.ts — window.__viewer 全局测试钩子（tif-viewer.html 直译）
 * ------------------------------------------------------------------
 * 浏览器回归 + 真机验收都靠它，**始终暴露**（不只在 dev/build 注入）。
 * HTML 版 `window.__viewer`（1275-1285 行）的行为对齐：
 *   - 解码：planExport
 *   - 掩码：enterDraw / exitDraw / buildMaskJson / exportMaskJson / thumbToOrig /
 *     getRois / genMask / wandSelect / maskGen(MaskGen)
 *   - 新增（Vue 无 HTML 的全局 recs 数组，测试观测需要）：recs() / activeRec() 摘要快照。
 *   - 待修复清单（HTML 版没有这个功能）：qcImport / qcClose / qcSetStatus / qcOutput /
 *     qcState / qcOpenByName。
 *   - setSparseMin：改写 tifDecode 的 SPARSE_MIN（默认 1e8 不动），让浏览器回归用小
 *     fixture 走稀疏路由。
 *   - 删去（最小原型取消了浏览器 JPG 导出/输出目录一条链路）：bakeJpg / getSaver /
 *     fsIO / setSaver / collectForExport / kickExport / reExportJpg /
 *     scanPendingExports / dateDirName / JPG_MAX / JPG_QUALITY。
 *
 * 全部委托 store 动作 + lib 纯函数。
 */
import { browserKit } from '../lib/browserKit.js';
import { planExport, setSparseMin } from '../lib/tifDecode.js';
import type { StretchMode } from '../lib/tifDecode.js';
import { thumbToOrig } from '../lib/viewMath.js';
import MaskGen from '../lib/maskgen.js';
import type { SceneOpenMeta } from '../lib/scene.js';
import { useViewerStore } from '../stores/viewer.js';
import type { ViewerRec } from '../stores/viewer.js';
import { useQcListStore } from '../stores/qclist.js';
import { useScenesStore } from '../stores/scenes.js';
import type { QcStatus } from '../lib/qclist.js';

/** rec 摘要快照（浏览器测试观测用；HTML 直接读全局 recs 的等价物） */
export interface ViewerRecSummary {
  id: number;
  name: string;
  size: number;
  status: string;
  W: number;
  H: number;
  route: ViewerRec['route'];
  thumbW: number;
  thumbH: number;
  layout: string;
  /** 盘阵目录（提交 SR 的判据；反推关联成功后才非空） */
  lqPath: string | null;
  /** 掩码写进服务端后的路径（保存掩码到盘阵成功才非空） */
  serverMaskPath: string | null;
  /** 拉伸后的缩略图画布（页内采样用；等价 HTML rec.thumb） */
  thumb: HTMLCanvasElement | null;
  /** 这张图**实际**画出来的拉伸模式（null = 还没画过）。 */
  paintedMode: StretchMode | null;
  /** 不透明场景 id（route='jpg' 才有；升级/打开盘阵场景后非空）。 */
  sceneId: string | null;
  /** 反推关联**失败**时服务端给的原因（成功或没试过则 undefined）。
   *  单独暴露：rec.status 会被解码进度覆盖，失败原因在别处看不到。 */
  linkNote?: string;
}

export interface ViewerHook {
  // 解码
  planExport: typeof planExport;
  // 掩码
  enterDraw: () => void;
  exitDraw: () => void;
  buildMaskJson: () => ReturnType<ReturnType<typeof useViewerStore>['buildMaskJson']>;
  exportMaskJson: () => void;
  thumbToOrig: typeof thumbToOrig;
  getRois: () => ReturnType<ReturnType<typeof useViewerStore>['getRois']>;
  genMask: () => Promise<void>;
  wandSelect: (sx: number, sy: number) => void;
  maskGen: typeof MaskGen;
  setSparseMin: (n: number) => void;
  // 掩码直接驱动（等效画布点击路径，浏览器回归用；HTML 钩子没有，因 HTML 无对应测试）
  setDrawTool: (t: import('../stores/viewer.js').DrawTool) => void;
  commitRect: (r: { x0: number; y0: number; x1: number; y1: number }) => void;
  undoRoi: () => void;
  clearRois: () => void;
  mergeRois: () => Promise<void>;
  delClick: (tx: number, ty: number) => void;
  // 阶段5：盘阵场景打开（route='jpg'，浏览器回归注入合成 JPG）+ 掩码→SR 提交
  openSceneJpg: (meta: SceneOpenMeta, blob: Blob) => Promise<void>;
  // 本地 .jpg/.jpeg 打开（route='img'，浏览器回归用小 JPG fixture 走同一像素管线）
  openLocalImage: (file: File) => Promise<void>;
  submitSr: () => void;
  // 盘阵任意场景目录（手工路径）：打开 / 本地文件反推关联 / 掩码写进服务端
  openScenePath: (path: string) => Promise<boolean>;
  /** 反推关联当前图；**返回是否命中并已升级成盘阵 JPG**（命中即不做本地解码）。 */
  tryLinkScenes: () => Promise<boolean>;
  bakeMaskToServer: () => Promise<boolean>;
  /** 模态提示当前内容（没弹则 visible=false）。全局单例，与具体 rec 无关。 */
  modal: () => ReturnType<typeof useViewerStore>['modal'];
  hideModal: () => void;
  // 测试观测（Vue 无全局 recs → 摘要快照）
  recs: () => ViewerRecSummary[];
  activeRec: () => ViewerRecSummary | null;
  /** 当前图实际的拉伸模式（工具栏下拉显示的那个值）。 */
  activeStretch: () => StretchMode;
  /** 直接切拉伸（等效工具栏下拉 change）。 */
  setStretch: (m: StretchMode) => void;
  // 待修复清单（ROI/工具 置顶面板）：导入 / 标记 / 生成写回文本。
  // 写盘那一步（FSA 文件选择器）没法自动点，回归只验到 output() 为止。
  qcImport: (name: string, text: string) => boolean;
  qcClose: () => void;
  qcSetStatus: (name: string, s: QcStatus) => void;
  /** 要写回文档的全文（上半部分原文 + 终态行）。 */
  qcOutput: () => string;
  qcState: () => {
    loaded: boolean;
    sourceName: string;
    total: number;
    done: number;
    selName: string | null;
    statuses: Record<string, QcStatus>;
  };
  /** 按生产全名去盘阵开场景（真机验收用；外网开发机没有盘阵，必然报错）。 */
  qcOpenByName: (name: string) => Promise<string>;
}

declare global {
  interface Window {
    __viewer?: ViewerHook;
  }
}

function summarize(rec: ViewerRec): ViewerRecSummary {
  return {
    id: rec.id,
    name: rec.name,
    size: rec.size,
    status: rec.status,
    W: rec.W,
    H: rec.H,
    route: rec.route,
    thumbW: rec.thumb ? rec.thumb.width : 0,
    thumbH: rec.thumb ? rec.thumb.height : 0,
    layout: rec.layout,
    lqPath: rec.lqPath,
    serverMaskPath: rec.serverMaskPath ?? null,
    thumb: (rec.thumb as unknown as HTMLCanvasElement | null),
    paintedMode: rec.paintedMode,
    sceneId: rec.sceneId,
    linkNote: rec.linkNote,
  };
}

/** 构建并挂到 window.__viewer（幂等；始终暴露给 e2e 与真机验收） */
export function mountE2EHooks(): ViewerHook {
  const hook: ViewerHook = {
    planExport,
    enterDraw: () => useViewerStore().enterDraw(),
    exitDraw: () => useViewerStore().exitDraw(),
    buildMaskJson: () => useViewerStore().buildMaskJson(),
    exportMaskJson: () => useViewerStore().exportMaskJson(),
    thumbToOrig,
    getRois: () => useViewerStore().getRois(),
    genMask: () => useViewerStore().genMask(),
    wandSelect: (sx, sy) => useViewerStore().wandSelect(sx, sy),
    maskGen: MaskGen,
    setSparseMin,
    setDrawTool: (t) => useViewerStore().setDrawTool(t),
    commitRect: (r) => {
      const store = useViewerStore();
      store.pendingRect = { x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1 };
      store.commitRect();
    },
    undoRoi: () => useViewerStore().undoRoi(),
    clearRois: () => useViewerStore().clearRois(),
    mergeRois: () => useViewerStore().mergeRois(),
    delClick: (tx, ty) => useViewerStore().delClick(tx, ty),
    openSceneJpg: (meta, blob) => useViewerStore().openSceneJpg(meta, blob),
    openLocalImage: (file) => useViewerStore().openLocalImage(file),
    submitSr: () => useViewerStore().submitSr(),
    openScenePath: (p) => useViewerStore().openScenePath(p),
    tryLinkScenes: () => useViewerStore().tryLinkScenes(),
    bakeMaskToServer: () => useViewerStore().bakeMaskToServer(),
    modal: () => useViewerStore().modal,
    hideModal: () => useViewerStore().hideModal(),
    recs: () => useViewerStore().recs.map(summarize),
    activeRec: () => {
      const rec = useViewerStore().activeRec;
      return rec ? summarize(rec) : null;
    },
    activeStretch: () => useViewerStore().activeStretch,
    setStretch: (m) => useViewerStore().setStretch(m),
    qcImport: (name, text) => useQcListStore().importText(name, text, 'utf-8'),
    qcClose: () => useQcListStore().close(),
    qcSetStatus: (name, s) => useQcListStore().setStatus(name, s),
    qcOutput: () => useQcListStore().output(),
    qcState: () => {
      const qc = useQcListStore();
      return {
        loaded: qc.loaded,
        sourceName: qc.sourceName,
        total: qc.counts.total,
        done: qc.counts.done,
        selName: qc.selName,
        statuses: { ...qc.statuses },
      };
    },
    qcOpenByName: (name) => useScenesStore().openByName(name),
  };
  if (window.__viewer !== hook) window.__viewer = hook;
  return hook;
}
